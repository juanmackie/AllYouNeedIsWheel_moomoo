"""Query-only, current-position and executable-price checks for exit tickets."""

from datetime import timezone
from math import isfinite

from core.run_model import _max_tradeable_age_sec, resolve_coverage_truth, resolve_session_context
from core.scoring_factors import parse_broker_timestamp
from core.ticker_utils import canonical_underlying, earnings_underlying_ticker
from core.utils import safe_float


def call_quote_blocker(target, quote, basis, preset):
    """Reapply the owner's repair gates to executable copy-time evidence."""
    if str(target.get("option_type") or "").upper() != "CALL":
        return None
    if basis is None or not isfinite(basis) or basis <= 0:
        return "current covered-call basis unavailable"
    if safe_float(target.get("strike")) - basis + safe_float(quote.get("bid")) >= 0:
        return None
    delta = safe_float(quote.get("delta"), None)
    if delta is None or not isfinite(delta) or abs(delta) > safe_float(preset.get("repair_call_max_delta"), 0.2):
        return "repair call no longer passes the assignment-delta limit"
    if str(target.get("security_type") or "").lower() in {"etf", "index"}:
        return None
    # The scoring-time tier must be event_safe; a date alone is not enough.
    tier = target.get("event_tier") or (target.get("wheel_decision") or {}).get("event_tier")
    if tier != "event_safe":
        return "repair call earnings date is unknown, stale, or before expiry"
    earnings_date = str(target.get("earnings_date") or "").replace("-", "")[:8]
    expiration = str(target.get("expiration") or "").replace("-", "")[:8]
    if len(earnings_date) != 8 or not earnings_date.isdigit() or earnings_date <= expiration:
        return "repair call earnings date is unknown, stale, or before expiry"
    return None


def repair_open_blocker(target, quote, quantity, preset, fetch_positions):
    """Repair openings need current free shares and freshly qualified quotes."""
    intent = target.get("covered_call_intent") or (target.get("wheel_decision") or {}).get("covered_call_intent")
    if intent != "repair":
        return None
    if fetch_positions is None:
        return "current broker shares and basis must be rechecked for a repair call"
    try:
        context = fetch_positions()
        positions = context.get("positions") if isinstance(context, dict) else context
        positions = list(positions.values()) if isinstance(positions, dict) else list(positions or [])
        ticker = earnings_underlying_ticker(str(target.get("ticker") or "")).upper()
        rows = [p for p in positions if earnings_underlying_ticker(str(p.get("symbol") or "")).upper() == ticker]
        stock = next((p for p in rows if str(p.get("security_type") or "").upper() == "STK"), {})
        covered = sum(
            abs(safe_float(p.get("position")))
            for p in rows
            if str(p.get("option_type") or "").upper() in {"CALL", "C"} and safe_float(p.get("position")) < 0
        )
        if int(safe_float(stock.get("position")) // 100) - covered < quantity:
            return "current broker shares no longer cover the requested repair calls"
        basis = safe_float(stock.get("adjusted_basis", stock.get("avg_cost")), None)
        return call_quote_blocker(target, quote, basis, preset)
    except Exception:
        return "current broker shares and basis unavailable"


def validate_exit_ticket(view, requested, *, now_et, now_utc, fetch_quote, fetch_positions, find_contract):
    run = view.get("run") or {}
    payload = {
        "ok": True,
        "matched_run": requested.get("run_id") == run.get("run_id") and bool(run.get("run_id")),
        "matched_contract": False,
        "mode": "review_only",
        "reasons": [],
        "run_id": run.get("run_id", ""),
        "verified_at": now_utc.isoformat(),
        "contract": None,
        "kind": requested.get("kind"),
    }

    def blocked(reason):
        payload["reasons"] = [reason]
        return payload

    if not payload["matched_run"]:
        return blocked("run changed — refresh the action before copying")
    session, _ = resolve_session_context(run, now_et)
    coverage, _ = resolve_coverage_truth(run, session)
    if session == "unknown" or coverage != "complete":
        return blocked("complete coverage and broker session evidence are required")
    if fetch_positions is None or fetch_quote is None:
        return blocked("current broker positions and quotes must be rechecked")
    kind = requested.get("kind")
    ticker = canonical_underlying(str(requested.get("ticker") or "")).upper()
    right = str(requested.get("option_type") or "").upper()
    expiration = str(
        requested.get("close_expiration") if kind == "roll" else requested.get("expiration") or ""
    ).replace("-", "")
    strike = safe_float(requested.get("close_strike") if kind == "roll" else requested.get("strike"), None)
    quantity = safe_float(requested.get("quantity"), None)
    if quantity is None or not isfinite(quantity) or quantity <= 0 or quantity != int(quantity):
        return blocked("a positive integer held quantity is required")
    try:
        context = fetch_positions()
        positions = context.get("positions") if isinstance(context, dict) else context
        if isinstance(positions, dict):
            positions = list(positions.values())
        held = next(
            (
                p
                for p in positions or []
                if earnings_underlying_ticker(str(p.get("symbol") or p.get("ticker") or "")).upper() == ticker
                and str(p.get("option_type") or "").upper() in {right, right[:1]}
                and str(p.get("expiration") or "").replace("-", "") == expiration
                and safe_float(p.get("strike"), -1) == strike
                and safe_float(p.get("position")) < 0
            ),
            None,
        )
    except Exception:
        return blocked("current broker position unavailable")
    if held is None or abs(safe_float(held.get("position"))) != quantity:
        return blocked("short position changed or quantity no longer matches the broker")
    payload["matched_contract"] = True

    def read_quote(expiry, price_strike):
        evidence = fetch_quote(ticker=ticker, expiration=expiry, right=right[:1], strike=price_strike)
        if not evidence or evidence.get("source") != "broker" or not isinstance(evidence.get("option"), dict):
            raise ValueError("fresh direct Moomoo quote unavailable")
        quote = dict(evidence["option"])
        stamp = parse_broker_timestamp(quote.get("update_time") or quote.get("quote_update_time"))
        if stamp is None:
            raise ValueError("broker quote timestamp unavailable")
        age = (now_utc - stamp.astimezone(timezone.utc)).total_seconds()
        if age < 0 or (session == "open" and age > _max_tradeable_age_sec(run)):
            raise ValueError("broker quote is stale during the open session")
        if session != "open" and age < _max_tradeable_age_sec(run):
            raise ValueError("market appears open now — refresh for live copy")
        bid, ask = safe_float(quote.get("bid"), None), safe_float(quote.get("ask"), None)
        if bid is None or ask is None or not isfinite(bid) or not isfinite(ask) or bid < 0 or ask <= 0 or ask < bid:
            raise ValueError("executable broker bid/ask unavailable")
        return quote

    try:
        closing = read_quote(expiration, strike)
        closing.update(
            ticker=ticker, option_type=right, expiration=expiration, strike=strike, recommended_contracts=int(quantity)
        )
        payload["contract"] = closing
        if kind == "roll":
            target, lane = find_contract(view, ticker, right, requested.get("expiration"), requested.get("strike"))
            if target is None or not target.get("copy_eligible"):
                return blocked("roll target no longer qualifies in the current run")
            if str(target.get("expiration") or "") <= expiration or (
                right == "CALL" and safe_float(target.get("strike")) < strike
            ):
                return blocked("defend roll must extend expiry and keep or raise the call strike")
            opened = read_quote(str(target["expiration"]), safe_float(target["strike"]))
            if right == "CALL":
                stock = next(
                    (
                        p
                        for p in positions or []
                        if str(p.get("security_type") or "").upper() == "STK"
                        and earnings_underlying_ticker(str(p.get("symbol") or "")).upper() == ticker
                    ),
                    {},
                )
                if safe_float(stock.get("position")) < quantity * 100:
                    return blocked("current broker shares no longer cover the rolled calls")
                basis = safe_float(stock.get("adjusted_basis", stock.get("avg_cost")), None)
                policy_blocker = call_quote_blocker(target, opened, basis, view.get("preset") or {})
                if policy_blocker:
                    return blocked(policy_blocker)
            fees = sum(safe_float(row.get("expected_fee_per_contract")) for row in (held, target)) / 100
            net_credit = safe_float(opened.get("bid")) - safe_float(closing.get("ask")) - fees
            if net_credit < 0:
                return blocked("roll no longer pays a net credit")
            payload.update(
                contract={**target, **opened}, close_contract=closing, net_credit=round(net_credit, 6), signal_lane=lane
            )
        payload["mode"] = "live" if session == "open" else "staged"
        return payload
    except Exception as exc:
        return blocked(str(exc))
