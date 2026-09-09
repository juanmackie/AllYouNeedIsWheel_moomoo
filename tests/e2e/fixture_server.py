"""E2E fixture server for browser-level dashboard tests.

Boots the REAL Flask application (`app.create_application` + `register_web_routes`)
against a temp SQLite database, then swaps the broker-backed services for
deterministic in-process stubs and patches the small set of bound names that
read the wall clock / probe OpenD, so every journey is deterministic without a
live Moomoo connection.

The server never touches production code paths used for live trading: the run
model, publish-once persistence, refresh attempts, route logic, and the
frontend are all REAL. Only broker/market-data I/O is stubbed at the service
boundary (portfolio, watchlist, options engine, earnings), exactly where the
real application would dial out to OpenD. Signals-only / broker-read-only is
preserved: nothing here can place, modify, or cancel orders.

Runtime contracts:
- `CONNECTION_CONFIG` is set to a JSON *file path* in a temp dir (this is what
  `app.create_application` and `api.services.config.get_config` both read). A
  missing file would silently fall back to the real `options.db`, so the file
  is always written.
- Clock: `FIXTURE["clock"]` drives the patched `core.utils.market_now` and
  `api.routes.run.market_now`. "closed" -> fixed weekend (fully deterministic),
  "default"/"open" -> real ET wall clock (used only by the gated live journey).
- Evidence: `FIXTURE["evidence"]` drives the patched `_options_service_fetch_live_chain`
  so staged copy revalidation is deterministic.
- Broker: `FIXTURE["broker"]` drives `probe_opend_status` and the stub
  connection, so the degraded/OpenD-unavailable banner is testable.
- Refresh: `FIXTURE["refresh"]` drives the stub recommendation engine speed /
  failure so refresh-completion and failure-after-success journeys are exact.

Run standalone:  `uv run python tests/e2e/fixture_server.py --port 8101`
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

ET = ZoneInfo("America/New_York")
WEEKEND_ET = datetime(2026, 9, 5, 12, 0, tzinfo=ET)  # Saturday, noon ET (in the past)

# ---------------------------------------------------------------------------
# Fixture state (mutated only through the /__e2e/* control endpoints, under lock)
# ---------------------------------------------------------------------------

DEFAULT_FIXTURE = {
    "clock": "closed",  # "closed" | "open" | "default"
    "broker": "up",  # "up" | "down"
    "refresh": {
        "mode": "fast",  # "fast" | "slow" | "fail"
        "duration_ms": 1200,
        "fail_message": "simulated engine failure (e2e)",
    },
    "evidence": {
        "source": "broker",  # "broker" | "persisted-broker" | None
        "quote_age_sec": 2 * 3600,  # age of the staged re-validation quote
        "option": None,  # explicit option override (None -> built from age)
    },
    "scan": {"scanned": 2, "total": 2, "complete": True},
}

FIXTURE = json.loads(json.dumps(DEFAULT_FIXTURE))
_FIXTURE_LOCK = threading.Lock()


def _mutate(delta: dict) -> dict:
    with _FIXTURE_LOCK:
        for key, value in delta.items():
            if isinstance(value, dict) and isinstance(FIXTURE.get(key), dict):
                FIXTURE[key].update(value)
            else:
                FIXTURE[key] = json.loads(json.dumps(value))
        return json.loads(json.dumps(FIXTURE))


def _snapshot_fixture() -> dict:
    with _FIXTURE_LOCK:
        return json.loads(json.dumps(FIXTURE))


# ---------------------------------------------------------------------------
# Clock helpers (shared by the patched bound names and the evidence builder so
# the staged revalidation age math is consistent)
# ---------------------------------------------------------------------------


def _fixture_now_et():
    clock = FIXTURE.get("clock", "default")
    if clock == "closed":
        return WEEKEND_ET
    return datetime.now(ET)


def _fixture_now_utc():
    return _fixture_now_et().astimezone(timezone.utc)


def _real_market_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return now.replace(tzinfo=None) < datetime(now.year, now.month, now.day, 16, 0).replace(
        tzinfo=None
    ) and now.replace(tzinfo=None) >= datetime(now.year, now.month, now.day, 9, 30).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Fake connection for the stub options service
# ---------------------------------------------------------------------------


class FakeConn:
    """Query-only connection stand-in: accounts only, no order surface."""

    def __init__(self):
        from moomoo import TrdEnv

        self._accounts = [{"acc_id": "90001", "trd_env": TrdEnv.SIMULATE}]

    def _get_available_accounts(self, refresh=False):
        return list(self._accounts)

    def get_connection_info(self):
        return {"platform": "fixture", "opend": "127.0.0.1:11111", "connected": True}


# ---------------------------------------------------------------------------
# Deterministic signal / engine builders
# ---------------------------------------------------------------------------


def _fresh_ts():
    return (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()


def _base_signal(ticker, option_type, expiration, strike, preset_key):
    """Realistic-enough signal dict for the dashboard cards (display + copy)."""
    is_call = option_type == "CALL"
    ts = _fresh_ts()
    cash_required = round(strike * 100.0, 2)
    premium = 1.85 if not is_call else 0.95
    limit_target = round(premium * 1.03 * 100, 2)  # cents (FE renders /100)
    bid_premium = round(premium * 0.97, 2)
    return {
        "ticker": ticker,
        "option_type": option_type,
        "expiration": expiration,
        "strike": float(strike),
        "dte": 33,
        "rank": 1,
        "annualized_return": 24.6 if is_call else 18.4,
        "score": 87.0 if is_call else 82.0,
        "contract_score": 87.0 if is_call else 82.0,
        "quality_tier": "high",
        "event_tier": "event_unknown" if ticker in ("NVDA",) else "no_event",
        "confidence": 0.81,
        "chain_source": "broker",
        "data_source": "broker",
        "copy_eligible": True,
        "recommended_contracts": 1,
        "max_contracts": 4,
        "bid_premium_per_contract": bid_premium,
        "limit_target_per_contract": limit_target,
        "premium_per_contract": premium,
        "premium_velocity_per_day": 0.56,
        "capital_velocity_per_day": 0.0056,
        "mid_price": premium,
        "open_interest": 2451,
        "volume": 632,
        "spread_pct": 0.11,
        "otm_pct": 7.2 if not is_call else 5.1,
        "delta": -0.21 if not is_call else 0.28,
        "iv_rank": 0.44,
        "implied_volatility": 0.27,
        "iv_status": "normal",
        "iv_source": "broker",
        "cash_required": cash_required,
        "capital_required": round(strike * 100.0, 2),
        "breakeven_buffer_pct": 8.3,
        "expected_move_buffer": 5.5,
        "risk_budget_used_pct": 4.1,
        "stress_loss": 420.0,
        "research_only": False,
        "warnings": [],
        "blocked_reason_codes": [],
        "hard_blockers": [],
        "existing_position": False,
        "signal_type": "covered_call" if is_call else "csp",
        "covered_call_intent": True if is_call else False,
        "score_details": {"rationale": f"e2e fixture {preset_key}"},
        "score_rationale": f"e2e fixture {preset_key}",
        "price_source": "broker",
        "quote_timestamp": ts,
        "quote_fetched_at_utc": ts,
        "generated_at": ts,
        "reset_value": False,
        "wheel_decision": {
            "score": 87.0 if is_call else 82.0,
            "quality_tier": "high",
            "spread_pct": 0.11,
            "hard_blockers": [],
            "blocked_reason_codes": [],
            "chain_source": "broker",
            "quote_fetched_at_utc": ts,
            "if_called_return": 5.4 if is_call else 0.0,
            "avg_cost": 0.0,
            "recommended_contracts": 1,
        },
    }


def _tickers_for_preset(preset_key):
    return {
        "conservative": ["AAPL", "KO"],
        "balanced": ["AAPL", "TSLA"],
        "aggressive": ["MSFT", "NVDA"],
    }.get(preset_key, ["AAPL", "TSLA"])


def _engine_result(preset_key, overrides=None):
    """Deterministic engine / run result dict consumed by WheelRunner._build_snapshot."""
    overrides = overrides or {}
    tickers = _tickers_for_preset(preset_key)
    signals = [
        _base_signal(tickers[0], "PUT", "2026-11-20", 190.0, preset_key),
        _base_signal(tickers[1], "CALL", "2026-12-18", 260.0, preset_key),
    ]
    ts = _fresh_ts()
    quote_fetched_at = {t: ts for t in tickers}
    return {
        "generated_at": ts,
        "state": overrides.get("state", "planning"),
        "errors": overrides.get("errors", []),
        "scan_coverage": {
            "scanned": overrides.get("scanned", 2),
            "total": overrides.get("total", 2),
            "complete": overrides.get("complete", True),
        },
        "quote_fetched_at": quote_fetched_at,
        "watchlist_origins": {t: {"origin": "watchlist"} for t in tickers},
        "signals": signals,
        "watchlist_csps": {"signals": [s for s in signals if s["option_type"] == "PUT"]},
        "covered_calls": {"signals": [s for s in signals if s["option_type"] == "CALL"]},
        "blocked_signals": [],
        "preset": _preset_dict(preset_key),
    }


def _preset_dict(preset_key):
    from core.presets import get_preset

    return get_preset(preset_key).to_dict()


# ---------------------------------------------------------------------------
# Stub services (registered in the app-scoped service store, mirroring the real
# factory contracts so route/service orchestration code stays unmodified)
# ---------------------------------------------------------------------------


class StubRecommendationEngine:
    def __init__(self, db):
        self._db = db

    def set_active_preset(self, key):
        if key:
            self._db.set_setting("wheel_preset", key)
        return True

    def get_top_recommendations(self, limit=3):
        fixture = _snapshot_fixture()
        refresh = fixture.get("refresh", {})
        mode = refresh.get("mode", "fast")
        duration_ms = max(0, int(refresh.get("duration_ms", 1200) or 1200))
        time.sleep(duration_ms / 1000.0)
        if mode == "fail":
            raise RuntimeError(refresh.get("fail_message", "simulated engine failure"))
        preset_key = self._db.get_setting("wheel_preset") or "balanced"
        if preset_key not in ("conservative", "balanced", "aggressive"):
            preset_key = "balanced"
        scan = fixture.get("scan", {})
        return _engine_result(
            preset_key,
            overrides={
                "scanned": int(scan.get("scanned", 2) or 2),
                "total": int(scan.get("total", 2) or 2),
                "complete": bool(scan.get("complete", True)),
            },
        )


class StubWatchlistManager:
    def __init__(self, db):
        self._db = db

    def get_effective_watchlist(self):
        return _tickers_for_preset(self._db.get_setting("wheel_preset") or "balanced")

    def get_watchlist_sources(self):
        return {"fixture": len(self.get_effective_watchlist())}

    def get_effective_watchlist_with_origins(self):
        return [{"symbol": t, "origin": "fixture"} for t in self.get_effective_watchlist()]


class StubPortfolioContextHelper:
    def __init__(self, db):
        self._db = db

    def get_cash_status(self, refresh=True):
        return {
            "account_value": 100000.0,
            "cash_balance": 78500.0,
            "excess_liquidity": 74000.0,
            "initial_margin": 21500.0,
            "is_frozen": False,
            "leverage_percentage": 6.7,
            "csp_capacity_available": 78500.0,
            "reserved_short_put_collateral": 0.0,
        }


class StubOptionsService:
    """Replacement for OptionsService satisfying the contracts the REAL route
    and WheelRunner code touch (connection, portfolio context, engine,
    watchlist, cash helper)."""

    def __init__(self, db, config):
        self.db = db
        self.config = config
        self.recommendation_engine = StubRecommendationEngine(db)
        self.watchlist_manager = StubWatchlistManager(db)
        self.portfolio_context_helper = StubPortfolioContextHelper(db)
        self.last_error = None
        self.connection = None

    def _ensure_connection(self):
        if _snapshot_fixture().get("broker") == "down":
            self.last_error = "Moomoo OpenD unavailable (e2e fixture)"
            return None
        if self.connection is None:
            self.connection = FakeConn()
        return self.connection

    def _get_portfolio_context(self, refresh=True):
        return {
            "account_value": 100000.0,
            "cash_balance": 78500.0,
            "excess_liquidity": 74000.0,
            "initial_margin": 21500.0,
            "is_frozen": False,
            "leverage_percentage": 6.7,
            "positions": [],
            "account_id": "90001",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }


class StubPortfolioService:
    def __init__(self):
        self.last_error = None

    def _ensure_connection(self):
        if _snapshot_fixture().get("broker") == "down":
            self.last_error = "Moomoo OpenD unavailable (e2e fixture)"
            return None
        return FakeConn()

    def get_portfolio_summary(self):
        return {
            "account_value": 100000.0,
            "cash_balance": 78500.0,
            "excess_liquidity": 74000.0,
            "initial_margin": 21500.0,
            "is_frozen": False,
            "leverage_percentage": 6.7,
            "positions": [],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_positions(self, position_type=None):
        return []

    def get_weekly_option_income(self):
        return {"income": [], "positions": [], "total_income": 0, "positions_count": 0, "error": None}


class StubIVEarningsService:
    def __init__(self, db):
        self.db = db

    def get_cache_stats(self):
        return {}

    def get_earnings_info(self, ticker):
        return {}

    def update_earnings_data(self, ticker):
        return {"success": False, "updated": 0}

    def batch_update_earnings(self, tickers):
        return {"successful": 0, "failed": 0}


def build_stub_registry(db, config):
    options = StubOptionsService(db, config)
    portfolio = StubPortfolioService()
    ivearnings = StubIVEarningsService(db)

    from core.wheel_runner import WheelRunner

    wheel_runner = WheelRunner(
        db=db,
        options_service=options,
        config=config,
        roll_diagnostics_provider=lambda pc, conn: [],
    )
    return {
        "options": options,
        "portfolio": portfolio,
        "ivearnings": ivearnings,
        "wheel_runner": wheel_runner,
    }


# ---------------------------------------------------------------------------
# Seed helpers (construct real WheelRunSnapshot / RefreshAttempt through the
# real persistence layer; nothing about orders is touched)
# ---------------------------------------------------------------------------


def _identity(db):
    from api.services.config import get_current_identity

    return get_current_identity()


def _wipe_runs(db):
    from db.database import pooled_connection

    with pooled_connection(db.db_path) as conn:
        conn.execute("DELETE FROM run_metadata")
        conn.execute("DELETE FROM refresh_attempts")
        conn.execute("DELETE FROM portfolio_snapshots")
        conn.commit()


def _seed_run(db, scene, preset_key="balanced"):
    """Persist a run snapshot + a matching succeeded attempt for a scene."""
    from core.presets import get_preset
    from core.run_model import RefreshAttempt, RunMetadata, WheelRunSnapshot

    env, opaque = _identity(db)
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(seconds=30)).isoformat()
    run_id = f"e2e-{uuid.uuid4().hex[:12]}"
    tickers = _tickers_for_preset(preset_key)

    result = _engine_result(preset_key)

    if scene == "complete_closed":
        status = "planning"
        coverage = {"scanned": 2, "total": 2, "complete": True}
        quote_fetched_at = {t: ts for t in tickers}
        chain_source = "broker"
        market_state = "closed"
    elif scene == "planning_partial":
        status = "planning"
        coverage = {"scanned": 1, "total": 2, "complete": False}
        quote_fetched_at = {t: ts for t in tickers}
        chain_source = "broker"
        market_state = "closed"
    elif scene == "persisted_fallback":
        status = "planning"
        coverage = {"scanned": 2, "total": 2, "complete": True}
        quote_fetched_at = {t: ts for t in tickers}
        chain_source = "persisted-broker"
        market_state = "closed"
    elif scene == "no_quote":
        status = "planning"
        coverage = {"scanned": 2, "total": 2, "complete": True}
        quote_fetched_at = {}
        chain_source = "broker"
        market_state = "closed"
    elif scene == "live_ready":
        status = "ready"
        coverage = {"scanned": 2, "total": 2, "complete": True}
        quote_fetched_at = {t: ts for t in tickers}
        chain_source = "broker"
        market_state = "open"
    else:
        raise ValueError(f"unknown scene {scene}")

    signals = []
    for i, sig in enumerate(result["signals"]):
        sig = dict(sig)
        sig["chain_source"] = chain_source
        sig["data_source"] = chain_source
        sig["wheel_decision"] = dict(sig["wheel_decision"])
        sig["wheel_decision"]["chain_source"] = chain_source
        if scene == "no_quote":
            sig["quote_timestamp"] = ""
            sig["quote_fetched_at_utc"] = ""
            sig["wheel_decision"]["quote_fetched_at_utc"] = ""
        signals.append(sig)

    published_at = now.isoformat()
    run = RunMetadata(
        run_id=run_id,
        generated_at=published_at,
        published_at=published_at,
        env=env,
        account_id=opaque,
        preset_key=preset_key,
        preset_version=get_preset(preset_key).version,
        market_state=market_state,
        status=status,
        errors=(),
        coverage_scanned=coverage["scanned"],
        coverage_total=coverage["total"],
        quote_fetched_at=quote_fetched_at,
        max_tradeable_age_sec=300,
    )
    snapshot = WheelRunSnapshot(
        run=run,
        portfolio=result
        if False
        else {
            "account_value": 100000.0,
            "cash_balance": 78500.0,
            "positions": [],
            "fetched_at": published_at,
        },
        csp_picks=tuple(s for s in signals if s["option_type"] == "PUT"),
        cc_decisions=tuple(s for s in signals if s["option_type"] == "CALL"),
        roll_decisions=(),
        rejected=(),
        preset=result["preset"],
        watchlist_origins=result["watchlist_origins"],
        signals=tuple(signals),
    )
    db.save_run_snapshot(snapshot)
    db.save_refresh_attempt(
        RefreshAttempt(
            # '!' sorts below any real hex attempt_id (ASCII 33 < '0'), so in a
            # same-second created_at tie the seed row can never shadow a real
            # refresh attempt under ORDER BY created_at DESC, attempt_id DESC.
            attempt_id=f"!_seed-{scene}-{uuid.uuid4().hex[:10]}",
            run_id=run_id,
            state="succeeded",
            stage="publish",
            progress=1.0,
            started_at=(now - timedelta(seconds=45)).isoformat(),
            finished_at=published_at,
        )
    )
    return {"run_id": run_id, "status": status, "scene": scene, "preset_key": preset_key}


# ---------------------------------------------------------------------------
# Moon-patches (bound names only; never the service source)
# ---------------------------------------------------------------------------


def apply_patches(app):
    import api.routes.run as run_module
    import api.routes.utils as utils_module
    import core.run_model as run_model_module

    def _patched_market_now():
        return _fixture_now_et()

    def _patched_probe(host=None, port=None, timeout=None, **kwargs):
        if _snapshot_fixture().get("broker") == "down":
            return {
                "status": "unavailable",
                "connected": False,
                "reachable": False,
                "host": host,
                "port": port,
                "message": "OpenD is not running on the configured host and port.",
                "details": {},
            }
        return {
            "status": "connected",
            "connected": True,
            "reachable": True,
            "host": host,
            "port": port,
            "message": "OpenD is reachable (TCP probe passed).",
            "details": {},
        }

    def _patched_fetch_live_chain(*, ticker, expiration, right, strike):
        evidence = _snapshot_fixture().get("evidence", {})
        source = evidence.get("source")
        now_utc = _fixture_now_utc()
        quote_age = max(0, int(evidence.get("quote_age_sec", 2 * 3600) or 0))
        quote_ts = (now_utc - timedelta(seconds=quote_age)).isoformat()
        if _snapshot_fixture().get("broker") == "down":
            return {"source": None, "option": None, "quote_fetched_at_utc": None, "error": "OpenD unavailable"}
        option = evidence.get("option")
        if option is None:
            option = {
                "ticker": ticker,
                "option_type": "CALL" if right == "C" else "PUT",
                "expiration": expiration,
                "strike": float(strike),
                "bid": 1.78,
                "ask": 1.92,
                "bid_premium_per_contract": 1.78,
                "limit_target_per_contract": 186,  # cents (FE renders /100)
                "premium_per_contract": 1.85,
                "mid_price": 1.85,
                "quote_timestamp": quote_ts,
                "quote_fetched_at_utc": quote_ts,
            }
        else:
            option = dict(option)
            option.setdefault("quote_timestamp", quote_ts)
            option.setdefault("quote_fetched_at_utc", quote_ts)
        return {
            "source": source or None,
            "option": option,
            "quote_fetched_at_utc": quote_ts,
        }

    # Patch bound names where clock/probe/evidence are read at runtime.
    run_module.market_now = _patched_market_now
    run_model_module.market_now = _patched_market_now
    run_module._options_service_fetch_live_chain = _patched_fetch_live_chain
    utils_module.probe_opend_status = _patched_probe
    import api

    api.probe_opend_status = _patched_probe


# ---------------------------------------------------------------------------
# Control endpoints (test-only; never registered on a production app)
# ---------------------------------------------------------------------------


def register_control_endpoints(app, db):
    from flask import jsonify, request

    @app.route("/__e2e/ping")
    def e2e_ping():
        return jsonify(
            {
                "ok": True,
                "real_market_open": _real_market_open(),
                "fixture": _snapshot_fixture(),
                "identity": list(_identity(db)),
            }
        )

    @app.route("/__e2e/reset", methods=["POST"])
    def e2e_reset():
        _mutate(json.loads(json.dumps(DEFAULT_FIXTURE)))
        _wipe_runs(db)
        db.set_setting("wheel_preset", "balanced")
        # Force the engine to reload the balanced default.
        _get_options_service_for(app).recommendation_engine.set_active_preset("balanced")
        return jsonify({"ok": True})

    @app.route("/__e2e/clock", methods=["POST"])
    def e2e_clock():
        payload = request.get_json(silent=True) or {}
        mode = str(payload.get("session", "") or "").strip().lower()
        if mode not in ("closed", "open", "default"):
            return jsonify({"ok": False, "error": "session must be closed|open|default"}), 400
        _mutate({"clock": mode})
        return jsonify({"ok": True, "fixture": _snapshot_fixture()})

    @app.route("/__e2e/broker", methods=["POST"])
    def e2e_broker():
        payload = request.get_json(silent=True) or {}
        state = str(payload.get("state", "") or "").strip().lower()
        if state not in ("up", "down"):
            return jsonify({"ok": False, "error": "state must be up|down"}), 400
        _mutate({"broker": state})
        return jsonify({"ok": True, "fixture": _snapshot_fixture()})

    @app.route("/__e2e/refresh", methods=["POST"])
    def e2e_refresh():
        payload = request.get_json(silent=True) or {}
        mode = str(payload.get("mode", "fast") or "fast").strip().lower()
        if mode not in ("fast", "slow", "fail"):
            return jsonify({"ok": False, "error": "mode must be fast|slow|fail"}), 400
        delta = {
            "refresh": {
                "mode": mode,
                "duration_ms": int(payload.get("duration_ms", 1200) or 1200),
                "fail_message": str(payload.get("fail_message", "simulated engine failure (e2e)") or ""),
            }
        }
        _mutate(delta)
        return jsonify({"ok": True, "fixture": _snapshot_fixture()})

    @app.route("/__e2e/evidence", methods=["POST"])
    def e2e_evidence():
        payload = request.get_json(silent=True) or {}
        source = payload.get("source")
        if source not in (None, "broker", "persisted-broker"):
            return jsonify({"ok": False, "error": "source must be broker|persisted-broker|null"}), 400
        _mutate(
            {
                "evidence": {
                    "source": source,
                    "quote_age_sec": int(payload.get("quote_age_sec", 2 * 3600) or 0),
                    "option": payload.get("option"),
                }
            }
        )
        return jsonify({"ok": True, "fixture": _snapshot_fixture()})

    @app.route("/__e2e/scan", methods=["POST"])
    def e2e_scan():
        payload = request.get_json(silent=True) or {}
        _mutate(
            {
                "scan": {
                    "scanned": int(payload.get("scanned", 2) or 0),
                    "total": int(payload.get("total", 2) or 0),
                    "complete": bool(payload.get("complete", True)),
                }
            }
        )
        return jsonify({"ok": True, "fixture": _snapshot_fixture()})

    @app.route("/__e2e/publish-run", methods=["POST"])
    def e2e_publish_run():
        payload = request.get_json(silent=True) or {}
        scene = str(payload.get("scene", "complete_closed") or "complete_closed").strip()
        preset_key = str(payload.get("preset", "balanced") or "balanced").strip()
        try:
            seeded = _seed_run(db, scene, preset_key=preset_key)
        except Exception as exc:  # pragma: no cover - defensive
            return jsonify({"ok": False, "error": str(exc), "traceback": traceback.format_exc()}), 500
        return jsonify({"ok": True, **seeded})

    @app.route("/__e2e/state")
    def e2e_state():
        attempt = db.get_latest_attempt()
        snapshot = db.get_latest_snapshot(env=_identity(db)[0], account_id=_identity(db)[1])
        run = (snapshot or {}).get("run") or {}
        return jsonify(
            {
                "ok": True,
                "real_market_open": _real_market_open(),
                "attempt_state": (attempt or {}).get("state"),
                "attempt_stage": (attempt or {}).get("stage"),
                "run_id": run.get("run_id"),
                "run_status": run.get("status"),
                "preset_key": run.get("preset_key"),
            }
        )

    @app.route("/__e2e/wait-refresh-idle", methods=["POST"])
    def e2e_wait_refresh_idle():
        deadline = time.monotonic() + float(request.args.get("timeout", 90))
        while time.monotonic() < deadline:
            attempt = db.get_latest_attempt()
            state = (attempt or {}).get("state")
            if state in ("succeeded", "failed", None):
                attempt = db.get_latest_attempt()
                snapshot = db.get_latest_snapshot(env=_identity(db)[0], account_id=_identity(db)[1])
                run = (snapshot or {}).get("run") or {}
                return jsonify(
                    {
                        "ok": True,
                        "attempt_state": (attempt or {}).get("state"),
                        "attempt_error": (attempt or {}).get("latest_error"),
                        "run_id": run.get("run_id"),
                        "run_status": run.get("status"),
                        "preset_key": run.get("preset_key"),
                    }
                )
            time.sleep(0.25)
        return jsonify({"ok": False, "error": "refresh still not idle"}), 504


def _get_options_service_for(app):
    return app.extensions["ayniwheel_service_instances"]["options"]


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(description="E2E fixture server")
    parser.add_argument("--port", type=int, default=8101)
    args = parser.parse_args(argv)

    tmpdir = tempfile.mkdtemp(prefix="ayniwheel_e2e_")
    atexit.register(lambda: shutil.rmtree(tmpdir, ignore_errors=True))

    conn_path = Path(tmpdir) / "connection.json"
    conn_config = {
        "host": "127.0.0.1",
        "port": 11111,
        "db_path": str(Path(tmpdir) / "options_e2e.db"),
        "portfolio_env": "SIMULATE",
        "account_id": "90001",
        "auto_launch_opend": False,
        "opend_path": "",
        "watchlist": ["AAPL", "TSLA"],
        "watchlist_mode": "static",
        "wheel_preset": "balanced",
        "client_id": 1,
    }
    conn_path.write_text(json.dumps(conn_config), encoding="utf-8")
    os.environ["CONNECTION_CONFIG"] = str(conn_path)

    from app import create_application, register_web_routes

    app = create_application()
    register_web_routes(app)
    db = app.config["database"]

    with app.app_context():
        from flask import current_app

        stubs = build_stub_registry(db, app.config["connection_config"])
        current_app.extensions["ayniwheel_service_instances"] = stubs
        from api.services.config import get_config as _get_cfg

        _get_cfg()  # warm Config singleton against the same file

    apply_patches(app)
    register_control_endpoints(app, db)

    port = args.port
    print(f"[fixture-server] listening on http://127.0.0.1:{port} db={db.db_path}", flush=True)
    try:
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
