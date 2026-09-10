"""Immutable wheel run model.

One refresh produces exactly one completed ``WheelRunSnapshot``, persisted to
SQLite and then atomically published. In-flight work is modeled separately as
``RefreshAttempt`` (queued/refreshing + progress + latest failure) so a failed
attempt can never overwrite or relabel the last successful snapshot.

Completed snapshot states:
- ready:    complete-union coverage, fresh Moomoo quote/chain data, no errors
- partial:  some symbols failed; diagnostics shown, no copy actions
- planning: preflight infeasible or market-closed preview; read-only
- stale:    last successful snapshot aged beyond the freshness window

Copy eligibility is computed at read time (never persisted) into per-response
mode: ``live`` (market session open + complete coverage + fresh per-symbol
broker quotes), ``staged`` (market session closed + complete coverage; the
last-session evidence is re-fetched directly from OpenD at copy time), or
``review_only`` (everything else). Staging never silently reuses cached data.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from core.utils import market_now

ACTIONABLE_STATES = ("ready",)

# Read-time session states computed from cached broker evidence + the US
# Eastern market clock. ``holiday_shortened`` means the wall clock says the
# session should be open but cached broker quotes are not fresh (holiday,
# shortened day, or a stale run) — live is blocked and staged is allowed only
# after the copy-time OpenD fetch confirms the broker is not trading now.
SESSION_OPEN = "open"
SESSION_CLOSED = "closed"
SESSION_HOLIDAY_SHORTENED = "holiday_shortened"
SESSION_UNKNOWN = "unknown"
SESSION_STAGED_STATES = (SESSION_CLOSED, SESSION_HOLIDAY_SHORTENED)

# Copy mode values attached to each signal at read time.
MODE_LIVE = "live"
MODE_STAGED = "staged"
MODE_REVIEW_ONLY = "review_only"


def _scheduled_market_open(now_et: datetime) -> bool:
    """US session by wall clock (Mon-Fri 9:30-16:00 ET); holidays not modeled."""
    if now_et.weekday() >= 5:
        return False
    open_dt = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    close_dt = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_dt <= now_et <= close_dt


def _fresh_quote_map(quote_fetched_at: dict, max_age_sec: int, now: datetime) -> tuple[bool, list[str]]:
    stale_symbols = []
    if not quote_fetched_at:
        return False, stale_symbols
    for symbol, fetched_at in quote_fetched_at.items():
        try:
            parsed = datetime.fromisoformat(str(fetched_at))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age = (now - parsed.astimezone(timezone.utc)).total_seconds()
        except (TypeError, ValueError):
            stale_symbols.append(str(symbol))
            continue
        if age > max_age_sec or age < 0:
            stale_symbols.append(str(symbol))
    return not stale_symbols, stale_symbols


def resolve_session_context(run: dict | None, now_et: datetime | None = None) -> tuple[str, list[str]]:
    """Read-time US market session context from cached broker evidence.

    Returns ``(state, reasons)`` with state one of ``open`` / ``closed`` /
    ``holiday_shortened`` / ``unknown``. ``unknown`` (no broker quote evidence
    at all) demotes both live and staged to review_only. ``holiday_shortened``
    is scheduled-open-by-clock but stale evidence: live is blocked there and
    staged remains eligible only for the copy-time OpenD confirmation.
    """
    run = run or {}
    evidence = {str(k): str(v) for k, v in (run.get("quote_fetched_at") or {}).items() if v}
    if not evidence:
        return SESSION_UNKNOWN, ["no broker session evidence to confirm the session"]
    now_et = now_et or market_now()
    if not _scheduled_market_open(now_et):
        return SESSION_CLOSED, []
    fresh, _ = _fresh_quote_map(
        evidence,
        int(run.get("max_tradeable_age_sec", 300) or 300),
        now_et.astimezone(timezone.utc),
    )
    if fresh:
        return SESSION_OPEN, []
    return SESSION_HOLIDAY_SHORTENED, [
        "scheduled market hours not confirmed by fresh broker quotes (holiday, shortened day, or stale run)"
    ]


def resolve_coverage_truth(run: dict | None, session_state: str) -> tuple[str, list[str]]:
    """Classify run coverage: complete / partial / planning_quota / unknown.

    ``status`` alone cannot separate a closed-market complete run (stageable)
    from a quota-truncated planning run (review_only), so coverage truth is
    derived from the coverage counters.
    """
    run = run or {}
    scanned = int(run.get("coverage_scanned", 0) or 0)
    total = int(run.get("coverage_total", 0) or 0)
    complete = bool(run.get("coverage_complete", False) or (total > 0 and scanned >= total))
    if total <= 0:
        return "unknown", ["no coverage record in this run"]
    if complete:
        return "complete", []
    if run.get("status") == "planning":
        return "planning_quota", [
            "planning/limited run — coverage incomplete (quota or preview) so nothing can be staged"
        ]
    return "partial", [f"partial coverage ({scanned}/{total} symbols) — copy blocked"]


def compute_signal_eligibility(
    candidate: dict | None,
    *,
    session_state: str,
    coverage_truth: str,
    quotes_fresh: bool,
    coverage_reasons: list[str] | None = None,
    session_reasons: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Pure per-candidate mode decision (display truth, no broker I/O).

    Returns ``(mode, reasons)``. Staged here is display-only intent; the
    authoritative last-session OpenD evidence is revalidated at copy time.
    """
    candidate = candidate or {}
    reasons: list[str] = []
    if not bool(candidate.get("copy_eligible")):
        reasons.append("candidate is not copy eligible")
    if int(candidate.get("recommended_contracts", 0) or 0) <= 0:
        reasons.append("no recommended contract quantity")
    if reasons:
        return MODE_REVIEW_ONLY, reasons

    if session_state == SESSION_UNKNOWN:
        reasons.extend(session_reasons or ["no broker session evidence"])
        return MODE_REVIEW_ONLY, reasons

    if session_state == SESSION_OPEN:
        if coverage_truth != "complete":
            return MODE_REVIEW_ONLY, list(coverage_reasons or ["coverage incomplete"])
        if not quotes_fresh:
            return MODE_REVIEW_ONLY, ["broker quote evidence is stale"]
        return MODE_LIVE, []

    # Market closed (evening/weekend/holiday): staged needs complete coverage
    # plus broker evidence that came from a live last session (not a persisted
    # fallback snapshot). The copy-time OpenD fetch re-verifies this.
    if coverage_truth != "complete":
        return MODE_REVIEW_ONLY, list(coverage_reasons or ["coverage incomplete — cannot stage"])
    source = str(candidate.get("chain_source") or candidate.get("data_source") or "").strip().lower()
    if source == "persisted-broker" or source == "persisted":
        reasons.append("evidence is a persisted broker fallback — staging blocked")
        return MODE_REVIEW_ONLY, reasons
    return MODE_STAGED, list(session_reasons or [])


def _max_tradeable_age_sec(run: dict) -> int:
    try:
        return max(0, int(run.get("max_tradeable_age_sec", 300) or 300))
    except (TypeError, ValueError):
        return 300


# Candidate lanes that share the same read-time eligibility + copy surface as
# the combined shortlist. ``roll_decisions`` is intentionally excluded: it is a
# position-management panel, not a copy-addressable wheel candidate.
CANDIDATE_LANES = ("signals", "csp_picks", "cc_decisions")


def _candidate_quote_age_sec(candidate: dict, now_utc: datetime) -> float | None:
    """Age (seconds) of a candidate's broker quote; None when unparseable."""
    ts = (
        candidate.get("quote_fetched_at_utc")
        or (candidate.get("wheel_decision") or {}).get("quote_fetched_at_utc", "")
        or candidate.get("quote_update_time", "")
        or ""
    )
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return round((now_utc - parsed.astimezone(timezone.utc)).total_seconds(), 1)


def _attach_candidate_capital_view(candidate: dict, now_utc: datetime) -> None:
    """Read-time capital metrics for a copy-addressable candidate (never persisted).

    Adds ``quote_age_sec`` for every lane and a lane-appropriate capital base:
    ``collateral`` (CSP cash secured) or ``available_shares`` (CC share capacity).
    Unavailable values stay ``None`` so the UI renders an em-dash, never a zero
    derived from missing data.
    """
    candidate["quote_age_sec"] = _candidate_quote_age_sec(candidate, now_utc)
    if str(candidate.get("option_type", "")).upper() == "CALL":
        candidate["available_shares"] = None
        candidate["collateral"] = None
        max_contracts = candidate.get("max_contracts")
        try:
            contracts = float(max_contracts)
        except (TypeError, ValueError):
            contracts = 0.0
        if contracts > 0:
            candidate["available_shares"] = round(contracts * 100, 2)
    else:
        candidate["available_shares"] = None
        cash_required = candidate.get("cash_required")
        try:
            candidate["collateral"] = round(float(cash_required), 2) if cash_required not in (None, "") else None
        except (TypeError, ValueError):
            candidate["collateral"] = None


def build_eligibility_view(view: dict, now_utc: datetime | None = None, now_et: datetime | None = None) -> dict:
    """Attach read-time eligibility to a snapshot view (never persisted).

    Adds ``view["eligibility"]`` (session context + coverage truth + freshness)
    and, on every candidate in ``signals`` / ``csp_picks`` / ``cc_decisions``, a
    per-candidate ``eligibility = {"mode", "reasons"}`` plus the read-time
    capital view (``quote_age_sec``, ``collateral`` / ``available_shares``) so
    candidates outside the combined top-3 shortlist are surfaced identically.
    """
    run = view.get("run") or {}
    if not run:
        return view
    now_utc = now_utc or datetime.now(timezone.utc)
    session_state, session_reasons = resolve_session_context(run, now_et)
    coverage_truth, coverage_reasons = resolve_coverage_truth(run, session_state)
    fresh, stale_symbols = _fresh_quote_map(
        run.get("quote_fetched_at") or {},
        _max_tradeable_age_sec(run),
        now_utc,
    )
    view["eligibility"] = {
        "run_id": run.get("run_id", ""),
        "session": {"state": session_state, "reasons": session_reasons},
        "coverage": {
            "truth": coverage_truth,
            "scanned": int(run.get("coverage_scanned", 0) or 0),
            "total": int(run.get("coverage_total", 0) or 0),
            "reasons": coverage_reasons,
        },
        "quote_freshness": {"fresh": bool(fresh), "stale_symbols": stale_symbols},
    }
    for lane in CANDIDATE_LANES:
        for candidate in view.get(lane) or []:
            if not isinstance(candidate, dict):
                continue
            mode, reasons = compute_signal_eligibility(
                candidate,
                session_state=session_state,
                coverage_truth=coverage_truth,
                quotes_fresh=fresh,
                coverage_reasons=coverage_reasons,
                session_reasons=session_reasons,
            )
            candidate["eligibility"] = {"mode": mode, "reasons": reasons}
            _attach_candidate_capital_view(candidate, now_utc)
    return view


def recompute_effective_snapshot(
    snapshot: dict | None, now: datetime | None = None, now_et: datetime | None = None
) -> dict | None:
    """Return a read-only effective view without changing persisted history."""
    if not isinstance(snapshot, dict):
        return snapshot
    view = copy.deepcopy(snapshot)
    run = view.get("run") or {}
    now = now or datetime.now(timezone.utc)
    fresh, stale_symbols = _fresh_quote_map(
        run.get("quote_fetched_at") or {},
        int(run.get("max_tradeable_age_sec", 300) or 300),
        now,
    )
    coverage_complete = bool(
        run.get("coverage_complete", False)
        or (
            int(run.get("coverage_total", 0) or 0) > 0
            and int(run.get("coverage_scanned", 0) or 0) >= int(run.get("coverage_total", 0) or 0)
        )
    )
    base_tradeable = run.get("status") in ACTIONABLE_STATES and not run.get("errors") and coverage_complete and fresh
    view["tradeable"] = base_tradeable
    view["effective_status"] = "stale" if run.get("status") == "ready" and not base_tradeable else run.get("status")
    view["effective_stale_symbols"] = stale_symbols
    build_eligibility_view(view, now, now_et)
    return view


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RunMetadata:
    run_id: str
    generated_at: str
    published_at: Optional[str]
    env: str  # REAL | SIMULATE
    account_id: str  # opaque, non-sensitive identity
    preset_key: str
    preset_version: int
    market_state: str  # open | closed | unknown
    status: str  # ready | partial | planning | stale
    errors: tuple[str, ...] = ()
    partial_symbols: tuple[str, ...] = ()
    stale_symbols: tuple[str, ...] = ()
    quote_fetched_at: dict[str, str] = field(default_factory=dict)
    max_tradeable_age_sec: int = 300
    coverage_scanned: int = 0
    coverage_total: int = 0
    schema_version: int = 2

    @property
    def coverage_complete(self) -> bool:
        return self.coverage_total > 0 and self.coverage_scanned >= self.coverage_total


@dataclass(frozen=True)
class WheelRunSnapshot:
    run: RunMetadata
    portfolio: Optional[dict]
    csp_picks: tuple[dict, ...]
    cc_decisions: tuple[dict, ...]
    roll_decisions: tuple[dict, ...]
    rejected: tuple[dict, ...]
    preset: dict
    watchlist_origins: dict
    signals: tuple[dict, ...] = ()
    # ACTIVE WATCHLIST foot payload: the scan universe actually evaluated (the
    # signed-in OpenD session's Moomoo watchlist group), its group status +
    # explanation, per-ticker scan status, last successful sync, unsupported
    # symbols, and the holdings checked for covered calls. Never contains
    # archived config/app additions. Default {} keeps old snapshots loadable.
    active_watchlist: dict = field(default_factory=dict)

    @property
    def tradeable(self) -> bool:
        """Only a ready, complete-coverage, fresh-quote run is actionable."""
        if self.run.status not in ACTIONABLE_STATES or self.run.errors:
            return False
        if not self.run.coverage_complete:
            return False
        return self._quotes_fresh()

    def _quotes_fresh(self) -> bool:
        fresh, _ = _fresh_quote_map(
            self.run.quote_fetched_at,
            self.run.max_tradeable_age_sec,
            datetime.now(timezone.utc),
        )
        return fresh

    def to_dict(self) -> dict:
        return {
            "run": {
                "run_id": self.run.run_id,
                "generated_at": self.run.generated_at,
                "published_at": self.run.published_at,
                "env": self.run.env,
                "account_id": self.run.account_id,
                "preset_key": self.run.preset_key,
                "preset_version": self.run.preset_version,
                "market_state": self.run.market_state,
                "status": self.run.status,
                "errors": list(self.run.errors),
                "partial_symbols": list(self.run.partial_symbols),
                "stale_symbols": list(self.run.stale_symbols),
                "quote_fetched_at": self.run.quote_fetched_at,
                "max_tradeable_age_sec": self.run.max_tradeable_age_sec,
                "coverage_scanned": self.run.coverage_scanned,
                "coverage_total": self.run.coverage_total,
                "coverage_complete": self.run.coverage_complete,
                "schema_version": self.run.schema_version,
            },
            "tradeable": self.tradeable,
            "portfolio": self.portfolio,
            "csp_picks": list(self.csp_picks),
            "cc_decisions": list(self.cc_decisions),
            "roll_decisions": list(self.roll_decisions),
            "rejected": list(self.rejected),
            "preset": self.preset,
            "watchlist_origins": self.watchlist_origins,
            "signals": list(self.signals),
            "active_watchlist": dict(self.active_watchlist or {}),
        }


@dataclass(frozen=True)
class RefreshAttempt:
    attempt_id: str
    run_id: Optional[str]  # set only when a NEW run is published
    state: str  # queued | refreshing | succeeded | failed
    stage: str = "idle"  # account | positions | watchlist | csp | cc | roll | publish
    progress: float = 0.0  # 0..1
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    latest_error: Optional[str] = None
    latest_failure_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "state": self.state,
            "stage": self.stage,
            "progress": round(self.progress, 3),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "latest_error": self.latest_error,
            "latest_failure_at": self.latest_failure_at,
        }
