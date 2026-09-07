"""Wheel run coordinator.

Executes one atomic wheel refresh:

1. Persist a ``RefreshAttempt`` (queued -> refreshing with stage/progress).
2. Resolve the account identity (explicit configuration; never "first account").
3. Fetch portfolio/positions once, then run the wheel engine (CSP + CC + roll).
4. Build an immutable ``WheelRunSnapshot``, persist it in one SQLite
   transaction, then atomically publish it (in-memory + DB latest).
5. A failed attempt appends diagnostics but never relabels or erases the last
   successful snapshot.

Only one bounded background refresh worker exists; refreshes are serialized
through a lock.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid

from core.run_model import RefreshAttempt, RunMetadata, WheelRunSnapshot, utc_now_iso
from core.utils import is_market_open

logger = logging.getLogger("core.wheel_runner")

_refresh_lock = threading.Lock()


def opaque_account_id(account_id: str) -> str:
    """Non-sensitive identity: short hash of the broker account id."""
    if not account_id:
        return ""
    return hashlib.sha256(str(account_id).encode("utf-8")).hexdigest()[:12]


def resolve_account(conn, config: dict) -> str:
    """Resolve the account identity for this run.

    Rules:
    - REAL env REQUIRES an explicitly configured ``account_id`` that matches
      an available REAL account. Missing -> hard fail.
    - SIMULATE env requires exactly one unambiguous SIMULATE account when no
      explicit account_id is configured.
    - Never select "the first account returned".
    """
    env = str(config.get("portfolio_env", "SIMULATE")).strip().upper()
    configured = str(config.get("account_id", "") or "").strip()
    accounts = conn._get_available_accounts(refresh=True) or []

    from moomoo import TrdEnv

    if env == "REAL":
        if not configured:
            raise ValueError("REAL mode requires an explicitly configured MOOMOO_ACCOUNT_ID (account_id).")
        real_accounts = [a for a in accounts if a.get("trd_env") == TrdEnv.REAL]
        matched = [a for a in real_accounts if a.get("acc_id") == configured]
        if not matched:
            # S02: never enumerate raw available account ids in peristed/public errors.
            raise ValueError(
                f"REAL account resolution failed: configured account not found among "
                f"{len(real_accounts)} available REAL account(s). Configure MOOMOO_ACCOUNT_ID "
                f"to an available account."
            )
        return configured

    if configured:
        sim_accounts = [a for a in accounts if a.get("trd_env") == TrdEnv.SIMULATE]
        if any(a.get("acc_id") == configured for a in sim_accounts):
            return configured
        # S02: do not echo the configured id or enumerate available ones.
        raise ValueError(
            f"SIMULATE account resolution failed: configured account not found among "
            f"{len(sim_accounts)} available SIMULATE account(s). Configure account_id to an "
            f"available SIMULATE account."
        )

    sim_accounts = [a for a in accounts if a.get("trd_env") == TrdEnv.SIMULATE]
    if len(sim_accounts) != 1:
        raise ValueError(
            f"SIMULATE requires exactly one unambiguous paper account (found {len(sim_accounts)}); "
            "configure account_id explicitly."
        )
    return sim_accounts[0]["acc_id"]


class WheelRunner:
    """Runs the wheel engine and publishes immutable snapshots."""

    def __init__(
        self,
        db,
        options_service,
        config: dict,
        max_tradeable_age_sec: int = 300,
        roll_diagnostics_provider=None,
    ):
        self._db = db
        self._options_service = options_service
        self._config = config
        self._max_tradeable_age_sec = int(max_tradeable_age_sec or 300)
        # Injected api-layer callable (portfolio_context, conn) -> list[dict];
        # core must never depend on the api layer, so roll diagnostics are
        # composed there and injected here.
        self._roll_diagnostics_provider = roll_diagnostics_provider
        self._latest_snapshot: WheelRunSnapshot | None = None

    # -- attempt helpers -------------------------------------------------

    def _persist_attempt(self, attempt: RefreshAttempt):
        try:
            self._db.save_refresh_attempt(attempt)
        except Exception as exc:  # never let attempt bookkeeping kill a refresh
            logger.warning(f"Failed to persist refresh attempt: {exc}")

    # -- run -----------------------------------------------------------------

    def refresh(self) -> WheelRunSnapshot:
        attempt_id = uuid.uuid4().hex[:16]
        started = utc_now_iso()
        self._persist_attempt(RefreshAttempt(attempt_id=attempt_id, run_id=None, state="queued"))

        try:
            self._persist_attempt(
                RefreshAttempt(
                    attempt_id=attempt_id,
                    run_id=None,
                    state="refreshing",
                    stage="account",
                    progress=0.05,
                    started_at=started,
                )
            )
            env = str(self._config.get("portfolio_env", "SIMULATE")).strip().upper()
            conn = self._options_service._ensure_connection()
            if conn is None:
                raise RuntimeError("Moomoo OpenD connection unavailable")

            account_id = resolve_account(conn, self._config)
            opaque = opaque_account_id(account_id)

            self._persist_attempt(
                RefreshAttempt(
                    attempt_id=attempt_id,
                    run_id=None,
                    state="refreshing",
                    stage="portfolio",
                    progress=0.15,
                    started_at=started,
                )
            )
            portfolio_context = self._options_service._get_portfolio_context(refresh=True)

            self._persist_attempt(
                RefreshAttempt(
                    attempt_id=attempt_id,
                    run_id=None,
                    state="refreshing",
                    stage="scan",
                    progress=0.35,
                    started_at=started,
                )
            )
            result = self._options_service.recommendation_engine.get_top_recommendations(limit=3)

            if "error" in result:
                raise RuntimeError(result["error"])

            self._persist_attempt(
                RefreshAttempt(
                    attempt_id=attempt_id,
                    run_id=None,
                    state="refreshing",
                    stage="roll",
                    progress=0.9,
                    started_at=started,
                )
            )
            roll_decisions = self._build_roll_decisions(portfolio_context, conn)

            snapshot = self._build_snapshot(
                env=env,
                opaque_account=opaque,
                attempt_started=started,
                result=result,
                portfolio=portfolio_context,
                roll_decisions=roll_decisions,
            )

            # Persist snapshot, then publish (in-memory + DB latest).
            self._db.save_run_snapshot(snapshot)
            self._latest_snapshot = snapshot

            # Persist a portfolio snapshot for equity history and trade-event
            # inference. Never blocks or fails the run publish.
            self._persist_portfolio_state(
                run_id=snapshot.run.run_id,
                env=env,
                opaque_account=opaque,
                captured_at=snapshot.run.published_at,
                portfolio_context=portfolio_context,
            )

            self._persist_attempt(
                RefreshAttempt(
                    attempt_id=attempt_id,
                    run_id=snapshot.run.run_id,
                    state="succeeded",
                    stage="publish",
                    progress=1.0,
                    started_at=started,
                    finished_at=utc_now_iso(),
                )
            )
            logger.info("Wheel run %s published (status=%s)", snapshot.run.run_id, snapshot.run.status)
            return snapshot
        except Exception as exc:
            logger.exception("Wheel refresh failed")
            self._persist_attempt(
                RefreshAttempt(
                    attempt_id=attempt_id,
                    run_id=None,
                    state="failed",
                    stage="scan",
                    progress=0.0,
                    started_at=started,
                    finished_at=utc_now_iso(),
                    latest_error=str(exc),
                    latest_failure_at=utc_now_iso(),
                )
            )
            raise

    def _build_roll_decisions(self, portfolio_context, conn):
        """Roll/hold/close diagnostics for actual option positions."""
        if self._roll_diagnostics_provider is None:
            return []
        return self._roll_diagnostics_provider(portfolio_context, conn)

    def _build_snapshot(self, env, opaque_account, attempt_started, result, portfolio, roll_decisions):
        generated_at = result.get("generated_at") or utc_now_iso()
        coverage = result.get("scan_coverage", {}) or {}
        scanned = int(coverage.get("scanned", 0) or 0)
        total = int(coverage.get("total", 0) or 0)
        complete = bool(coverage.get("complete", total > 0 and scanned >= total))
        errors = result.get("errors") or []
        planning = result.get("state") == "planning"
        market_state = "open" if is_market_open() else "closed"

        if planning:
            status = "planning"
        elif errors:
            status = "partial"
        elif not complete:
            status = "partial"
        elif market_state != "open":
            status = "planning"
        else:
            status = "ready"

        symbols = list((result.get("watchlist_origins") or {}).keys())
        quote_fetched_at_by_symbol = {
            str(symbol): str(timestamp or "") for symbol, timestamp in (result.get("quote_fetched_at") or {}).items()
        }
        candidate_groups = [
            result.get("signals") or [],
            (result.get("watchlist_csps") or {}).get("signals", []) or [],
            (result.get("covered_calls") or {}).get("signals", []) or [],
        ]
        for candidate_group in candidate_groups:
            for candidate in candidate_group:
                if not isinstance(candidate, dict):
                    continue
                symbol = str(candidate.get("ticker", "") or "")
                fetched_at = str(
                    candidate.get("quote_fetched_at_utc")
                    or (candidate.get("wheel_decision") or {}).get("quote_fetched_at_utc", "")
                    or ""
                )
                if symbol and fetched_at:
                    quote_fetched_at_by_symbol[symbol] = fetched_at
        # Missing evidence remains explicitly missing; never substitute run
        # generation time for a broker fetch timestamp.
        quote_fetched_at = {sym: quote_fetched_at_by_symbol.get(sym, "") for sym in symbols}

        preset = result.get("preset") or {}
        run = RunMetadata(
            run_id=uuid.uuid4().hex[:16],
            generated_at=generated_at,
            published_at=utc_now_iso(),
            env=env,
            account_id=opaque_account,
            preset_key=preset.get("key", "balanced"),
            preset_version=int(preset.get("version", 1) or 1),
            market_state=market_state,
            status=status,
            errors=tuple(errors),
            partial_symbols=tuple(),
            stale_symbols=tuple(),
            quote_fetched_at=quote_fetched_at,
            max_tradeable_age_sec=self._max_tradeable_age_sec,
            coverage_scanned=scanned,
            coverage_total=total,
        )
        return WheelRunSnapshot(
            run=run,
            portfolio=portfolio,
            csp_picks=tuple(result.get("watchlist_csps", {}).get("signals", []) or []),
            cc_decisions=tuple(result.get("covered_calls", {}).get("signals", []) or []),
            roll_decisions=tuple(roll_decisions),
            rejected=tuple(result.get("blocked_signals", []) or []),
            preset=preset,
            watchlist_origins=result.get("watchlist_origins", {}) or {},
            signals=tuple(result.get("signals", []) or []),
        )

    def latest(self) -> WheelRunSnapshot | None:
        return self._latest_snapshot

    def _persist_portfolio_state(
        self,
        run_id: str,
        env: str,
        opaque_account: str,
        captured_at: str,
        portfolio_context: dict,
    ) -> None:
        """Persist one portfolio snapshot per completed run.

        Also diffs positions against the previous snapshot to infer trade
        events (entry/exit/roll/assignment). Best-effort: any failure is logged
        and never fails the run publish.
        """
        try:
            from core.portfolio_snapshot import build_portfolio_snapshot
            from core.position_diff import infer_trade_events

            # C04: the previous (baseline) book is scoped to this run's
            # environment and opaque account — never another account's book.
            previous = self._db.get_latest_portfolio_snapshot(env=env, account_id=opaque_account)
            snapshot = build_portfolio_snapshot(
                portfolio_context=portfolio_context,
                run_id=run_id,
                env=env,
                opaque_account=opaque_account,
                captured_at=captured_at or utc_now_iso(),
            )
            events = infer_trade_events(previous, snapshot)
            for event in events:
                event.setdefault("details", {})["run_id"] = run_id
                # C05: inference never knows fill prices — provenance is
                # "inferred" and pnl stays NULL (unknown), never fabricated.
                event["provenance"] = "inferred"
            # C12: snapshot baseline plus its event batch commit atomically, so a
            # missing transition is never silently lost between writes.
            if not self._db.save_portfolio_transition(snapshot, events):
                logger.warning("Portfolio state not persisted for run %s", run_id)
                return
            if events:
                logger.info("Inferred %d trade event(s) from position diff (run %s)", len(events), run_id)
        except Exception as exc:
            logger.warning("Portfolio state persistence skipped for run %s: %s", run_id, exc)


def start_background_refresh(runner: WheelRunner) -> bool:
    """Start one background refresh if none is running. Returns True if started."""
    if not _refresh_lock.acquire(blocking=False):
        logger.info("Refresh already running; skipping")
        return False

    def _run():
        try:
            runner.refresh()
        except Exception as exc:
            logger.error(f"Background refresh failed: {exc}")
        finally:
            _refresh_lock.release()

    threading.Thread(target=_run, name="wheel-refresh", daemon=True).start()
    return True
