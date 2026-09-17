"""
Wheel run API — immutable snapshot + refresh attempt state.

GET  /api/run               -> latest attempt + latest completed snapshot
POST /api/run/refresh       -> start one background refresh (serialized)
GET  /api/run/copy-check    -> read-only copy revalidation before a clipboard write
"""

import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from api.routes.utils import get_db as _get_db
from core.run_model import (
    MODE_REVIEW_ONLY,
    MODE_STAGED,
    SESSION_UNKNOWN,
    _fresh_quote_map,
    _max_tradeable_age_sec,
    build_eligibility_view,
    recompute_effective_snapshot,
    resolve_coverage_truth,
    resolve_session_context,
)
from core.utils import market_now
from core.utils import normalize_expiration as _normalize_expiration
from core.wheel_runner import start_background_refresh

logger = logging.getLogger("api.routes.run")

bp = Blueprint("run", __name__, url_prefix="/api/run")


def _get_runner():
    import api

    return api.get_service("wheel_runner")


def _get_options_service():
    import api

    return api.get_service("options")


@bp.route("", methods=["GET"])
def get_run_state():
    """Return the latest refresh attempt and the latest completed snapshot."""
    db = _get_db()
    attempt = db.get_latest_attempt() if db is not None else None
    from api.services.config import get_current_identity

    identity_env, identity_account = get_current_identity()
    snapshot = db.get_latest_snapshot(env=identity_env, account_id=identity_account) if db is not None else None
    return jsonify({"attempt": attempt, "snapshot": recompute_effective_snapshot(snapshot)})


@bp.route("/refresh", methods=["POST"])
def refresh():
    """Start one bounded background refresh; returns 202 with attempt state."""
    runner = _get_runner()
    started = start_background_refresh(runner)
    db = _get_db()
    attempt = db.get_latest_attempt() if db is not None else None
    return jsonify({"started": started, "attempt": attempt}), (202 if started else 409)


def _contract_fingerprint(ticker: str, option_type: str, expiration: str, strike) -> str:
    try:
        return f"{str(ticker).strip().upper()}|{str(option_type).strip().upper()}|{_normalize_expiration(expiration)}|{float(strike):.2f}"
    except (TypeError, ValueError):
        return ""


def _find_contract(view: dict, ticker, option_type, expiration, strike):
    """Locate a contract in any copy-addressable lane.

    Returns ``(candidate, lane)`` or ``(None, None)``. Lanes mirror
    ``core.run_model.CANDIDATE_LANES`` so candidates outside the combined
    top-3 shortlist (csp_picks / cc_decisions) get the same copy revalidation.
    """
    target = _contract_fingerprint(ticker, option_type, expiration, strike)
    if not target:
        return None, None
    from core.run_model import CANDIDATE_LANES

    for lane in CANDIDATE_LANES:
        for candidate in view.get(lane) or []:
            if not isinstance(candidate, dict):
                continue
            if (
                _contract_fingerprint(
                    candidate.get("ticker", ""),
                    candidate.get("option_type", ""),
                    candidate.get("expiration", ""),
                    candidate.get("strike", 0),
                )
                == target
            ):
                return candidate, lane
    return None, None


def _fresh_option_age_sec(option: dict, now_utc: datetime) -> float | None:
    """Age (seconds) of a broker quote timestamp; None when not parseable."""
    ts = option.get("quote_fetched_at_utc") or option.get("quote_timestamp") or option.get("quote_update_time") or ""
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now_utc - parsed.astimezone(timezone.utc)).total_seconds()


def evaluate_copy_check(
    view: dict | None,
    requested: dict,
    now_utc: datetime | None = None,
    now_et: datetime | None = None,
    fetch_live_chain=None,
) -> dict:
    """Read-only copy revalidation against the CURRENT snapshot view.

    ``fetch_live_chain(ticker, expiration, right, strike)`` returns a dict:
    ``{"source": "broker"|"persisted-broker"|None, "option": {...}|None,
    "quote_fetched_at_utc": str}`` — resolution authority for staged mode.
    Pure when ``fetch_live_chain`` is None (live/review_only still correct).
    """
    now_et = now_et or market_now()
    now_utc = now_utc or now_et.astimezone(timezone.utc)
    view = view or {}
    run = view.get("run") or {}
    payload = {
        "ok": True,
        "matched_run": False,
        "matched_contract": False,
        "mode": MODE_REVIEW_ONLY,
        "reasons": [],
        "run_id": run.get("run_id", ""),
        "contract": None,
        "signal_lane": None,
        "verified_at": now_utc.isoformat(),
    }
    if not run:
        payload["reasons"] = ["no completed run available"]
        return payload

    payload["run_id"] = run.get("run_id", "")
    requested_run_id = str(requested.get("run_id") or "").strip()
    payload["matched_run"] = bool(requested_run_id) and requested_run_id == run.get("run_id", "")

    session_state, session_reasons = resolve_session_context(run, now_et)
    coverage_truth, coverage_reasons = resolve_coverage_truth(run, session_state)
    quotes_fresh, _ = _fresh_quote_map(
        run.get("quote_fetched_at") or {},
        _max_tradeable_age_sec(run),
        now_utc,
    )
    build_eligibility_view(view, now_utc, now_et)

    ticker = str(requested.get("ticker") or "").strip()
    option_type = str(requested.get("option_type") or "").upper()
    expiration = _normalize_expiration(requested.get("expiration") or "")
    try:
        strike = float(requested.get("strike", 0) or 0)
    except (TypeError, ValueError):
        strike = 0.0

    contract, signal_lane = _find_contract(view, ticker, option_type, expiration, strike)
    payload["matched_contract"] = contract is not None
    if contract is None:
        payload["reasons"] = ["contract no longer in the current signal shortlist"]
        return payload
    payload["signal_lane"] = signal_lane

    def _stage_decision():
        # staged requires complete coverage + a copy-eligible candidate
        if coverage_truth != "complete":
            return MODE_REVIEW_ONLY, list(coverage_reasons or ["coverage incomplete — cannot stage"]), None
        if not bool(contract.get("copy_eligible")) or int(contract.get("recommended_contracts", 0) or 0) <= 0:
            return MODE_REVIEW_ONLY, ["candidate is not copy eligible"], None
        # Authoritative last-session evidence from OpenD right now.
        if fetch_live_chain is None:
            return MODE_REVIEW_ONLY, ["staged evidence must be re-checked against OpenD at copy time"], None
        evidence = None
        try:
            evidence = fetch_live_chain(
                ticker=ticker,
                expiration=expiration,
                right="C" if option_type == "CALL" else "P",
                strike=strike,
            )
        except Exception as exc:  # pragma: no cover - defensive only
            logger.warning(
                "Copy-check OpenD evidence fetch failed for %s %s %s: %s", ticker, option_type, expiration, exc
            )
            evidence = None
        if not evidence or evidence.get("option") is None:
            return (
                MODE_REVIEW_ONLY,
                ["no fresh broker evidence at copy time (OpenD unavailable or persisted fallback)"],
                evidence,
            )
        if evidence.get("source") != "broker":
            return (
                MODE_REVIEW_ONLY,
                ["no fresh broker evidence at copy time (persisted snapshots are never staged)"],
                evidence,
            )
        # The broker IS serving current-session quotes => market is open; never stage.
        option = evidence.get("option") or {}
        age = _fresh_option_age_sec(option, now_utc)
        if age is not None and age < _max_tradeable_age_sec(run):
            return (
                MODE_REVIEW_ONLY,
                ["market appears open now (fresh broker quotes) — refresh for live copy; staging blocked"],
                evidence,
            )
        return MODE_STAGED, list(session_reasons or []), evidence

    if session_state == SESSION_UNKNOWN:
        payload["mode"] = MODE_REVIEW_ONLY
        payload["reasons"] = list(session_reasons or ["no broker session evidence"])
        payload["contract"] = contract
        return payload

    if session_state == "open":
        if coverage_truth != "complete":
            payload["mode"] = MODE_REVIEW_ONLY
            payload["reasons"] = list(coverage_reasons or ["coverage incomplete"])
            payload["contract"] = contract
            return payload
        if not quotes_fresh:
            payload["mode"] = MODE_REVIEW_ONLY
            payload["reasons"] = ["broker quote evidence is stale"]
            payload["contract"] = contract
            return payload
        if not bool(contract.get("copy_eligible")) or int(contract.get("recommended_contracts", 0) or 0) <= 0:
            payload["mode"] = MODE_REVIEW_ONLY
            payload["reasons"] = ["candidate is not copy eligible"]
            payload["contract"] = contract
            return payload
        payload["mode"] = "live"
        payload["contract"] = contract
        payload["reasons"] = []
        return payload

    # closed / holiday-shortened -> staged revalidation (authoritative OpenD check)
    mode, reasons, evidence = _stage_decision()
    payload["mode"] = mode
    payload["reasons"] = reasons
    if mode == MODE_STAGED:
        merged = dict(contract)
        for field in (
            "bid",
            "ask",
            "bid_premium_per_contract",
            "limit_target_per_contract",
            "premium_per_contract",
            "mid_price",
            "quote_fetched_at_utc",
            "quote_timestamp",
        ):
            if evidence and evidence.get("option", {}).get(field) is not None:
                merged[field] = evidence["option"].get(field)
        if evidence and evidence.get("quote_fetched_at_utc"):
            merged["quote_fetched_at_utc"] = evidence["quote_fetched_at_utc"]
        payload["contract"] = merged
    else:
        payload["contract"] = contract
    return payload


@bp.route("/copy-check", methods=["GET"])
def run_copy_check():
    """Read-only revalidation for a copy-to-ticket click.

    Validates against the CURRENT snapshot (run id + contract fingerprint),
    classifies mode, and for staged re-fetches the last-session option chain
    directly from OpenD through the existing cache/rate-limit path. Never
    writes, modifies, or cancels anything.
    """
    from api.routes.utils import enforce_route_rate_limit, error_response

    allowed, retry_after = enforce_route_rate_limit(
        "run_copy_check", request.remote_addr or "local", max_requests=30, window_seconds=60
    )
    if not allowed:
        return error_response("Rate limit exceeded", status_code=429, retry_after=retry_after)

    ticker = request.args.get("ticker", "").strip()
    option_type = str(request.args.get("option_type", "")).upper()
    expiration = _normalize_expiration(request.args.get("expiration", ""))
    try:
        strike = float(request.args.get("strike", 0) or 0)
    except (TypeError, ValueError):
        strike = 0.0
    run_id = request.args.get("run_id", "").strip()

    if not ticker or option_type not in ("CALL", "PUT") or not expiration or strike <= 0:
        return error_response("Invalid copy-check parameters", status_code=400)

    db = _get_db()
    from api.services.config import get_current_identity

    identity_env, identity_account = get_current_identity()
    snapshot = db.get_latest_snapshot(env=identity_env, account_id=identity_account) if db is not None else None
    view = recompute_effective_snapshot(snapshot)
    if not isinstance(view, dict) or not view.get("run"):
        return jsonify(
            {
                "ok": False,
                "matched_run": False,
                "matched_contract": False,
                "mode": MODE_REVIEW_ONLY,
                "reasons": ["no completed run available"],
                "run_id": run_id,
                "contract": None,
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    payload = evaluate_copy_check(
        view,
        {
            "run_id": run_id,
            "ticker": ticker,
            "option_type": option_type,
            "expiration": expiration,
            "strike": strike,
        },
        fetch_live_chain=_options_service_fetch_live_chain,
    )
    return jsonify(payload)


def _options_service_fetch_live_chain(*, ticker, expiration, right, strike):
    """Direct-from-OpenD chain fetch for staged revalidation (read-only)."""
    try:
        from api.services.options_data import fetch_option_chain_live_first

        service = _get_options_service()
        conn = service._ensure_connection() if hasattr(service, "_ensure_connection") else None
        if conn is None:
            return {"source": None, "option": None, "error": "OpenD unavailable"}
        config = service.config if hasattr(service, "config") else {}
        db = service.db if hasattr(service, "db") else None
        chain = fetch_option_chain_live_first(conn, db, config, ticker, expiration, right, target_strike=float(strike))
    except Exception as exc:  # pragma: no cover - defensive only
        logger.warning("copy-check live chain unavailable for %s %s %s: %s", ticker, expiration, right, exc)
        return {"source": None, "option": None, "error": str(exc)}
    if not chain or not chain.get("options"):
        return {"source": None, "option": None, "error": "no chain returned"}
    source = str(chain.get("chain_source") or "").strip().lower()
    match = None
    for opt in chain.get("options") or []:
        try:
            if abs(float(opt.get("strike", -1)) - float(strike)) < 0.005:
                match = opt
                break
        except (TypeError, ValueError):
            continue
    if match is None:
        return {
            "source": "broker" if source == "broker" else source or None,
            "option": None,
            "error": "contract not in chain",
        }
    quote_ts = (
        chain.get("quote_timestamp", "")
        or match.get("quote_fetched_at_utc", "")
        or match.get("quote_timestamp", "")
        or chain.get("quote_fetched_at_utc", "")
    )
    return {
        "source": "broker" if source == "broker" else "persisted-broker",
        "option": match,
        "quote_fetched_at_utc": quote_ts,
    }
