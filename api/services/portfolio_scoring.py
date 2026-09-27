"""
Shared position-scoring logic for portfolio route endpoints.

Extracted from api/routes/portfolio.py to avoid duplication between
the roll-pressure and alerts endpoints.
"""

from datetime import datetime, timezone
from math import isfinite

from core.logging_config import get_logger
from core.ticker_utils import canonical_underlying, earnings_underlying_ticker, parse_moomoo_symbol
from core.utils import market_now, parse_position_qty, safe_float

logger = get_logger("api.services.portfolio_scoring", "api")


def build_portfolio_context(option_positions, portfolio_service):
    try:
        summary = portfolio_service.get_portfolio_summary()
        cash_balance = float(summary.get("available_cash", 0) or 0)
        account_value = float(summary.get("account_value", 0) or 0)
    except Exception:
        cash_balance = 0
        account_value = 0

    positions_map = {}
    for pos in option_positions:
        ticker = parse_moomoo_symbol(pos.get("symbol", ""))
        positions_map[ticker] = pos

    portfolio_context = {
        "positions": positions_map,
        "cash_balance": cash_balance,
        "account_value": account_value,
        "short_calls": {},
        "short_puts": {},
    }

    for pos in option_positions:
        ticker = parse_moomoo_symbol(pos.get("symbol", ""))
        pos_qty = parse_position_qty(pos.get("position", 0))
        opt_type = str(pos.get("option_type", "") or "").upper()
        if pos_qty < 0:
            if opt_type == "CALL":
                portfolio_context["short_calls"][ticker] = abs(pos_qty)
            elif opt_type == "PUT":
                portfolio_context["short_puts"][ticker] = abs(pos_qty)

    return portfolio_context, cash_balance, account_value


def _normalized_option_type(value):
    value = str(value or "").strip().upper()
    return "CALL" if value in {"CALL", "C"} else "PUT" if value in {"PUT", "P"} else ""


def _select_fresh_roll_candidate(pos, ticker, option_type, fresh_candidates):
    """Choose the best fresh eligible same-side candidate with a later expiry."""
    held_expiration = str(pos.get("expiration") or "")
    underlying = canonical_underlying(str(ticker or "")).upper()
    if not underlying or not option_type or len(held_expiration) != 8 or not held_expiration.isdigit():
        return None
    now = datetime.now(timezone.utc)
    selected = None
    for candidate in fresh_candidates or []:
        if not isinstance(candidate, dict):
            continue
        eligible = candidate.get("copy_eligible")
        if not isinstance(eligible, bool) or not eligible:
            continue
        candidate_ticker = canonical_underlying(str(candidate.get("ticker") or "")).upper()
        if candidate_ticker != underlying or _normalized_option_type(candidate.get("option_type")) != option_type:
            continue
        expiration = str(candidate.get("expiration") or "")
        if len(expiration) != 8 or not expiration.isdigit() or expiration <= held_expiration:
            continue
        eligibility = candidate.get("eligibility")
        mode = eligibility.get("mode") if isinstance(eligibility, dict) else None
        if mode and mode not in {"live", "staged"}:
            continue
        age = candidate.get("quote_age_sec")
        if age is None:
            fetched_at = candidate.get("quote_fetched_at_utc") or (candidate.get("wheel_decision") or {}).get(
                "quote_fetched_at_utc"
            )
            if not fetched_at:
                continue
            try:
                fetched = datetime.fromisoformat(str(fetched_at))
                if fetched.tzinfo is None:
                    fetched = fetched.replace(tzinfo=timezone.utc)
                age = (now - fetched.astimezone(timezone.utc)).total_seconds()
            except (TypeError, ValueError):
                continue
        age = safe_float(age, None)
        strike_value = safe_float(candidate.get("strike"), None)
        velocity = safe_float(candidate.get("capital_velocity_per_day"), 0.0)
        if age is None or not isfinite(age) or age < 0 or age > 300:
            continue
        if strike_value is None or not isfinite(strike_value) or strike_value <= 0:
            continue
        key = (velocity if isfinite(velocity) else 0.0, expiration, -strike_value)
        if selected is None or key > selected[0]:
            selected = (key, candidate)
    return selected[1] if selected else None


def score_position(pos, conn, portfolio_context, iv_earnings_service, fresh_candidates=None):
    from core.position_scorer import score_existing_position

    ticker = parse_moomoo_symbol(pos.get("symbol", ""))
    underlying = earnings_underlying_ticker(ticker)
    option_type = str(pos.get("option_type", "") or "").upper()
    strike = safe_float(pos.get("strike"))
    expiration = str(pos.get("expiration", "") or "")
    position_qty = parse_position_qty(pos.get("position", 0))

    if position_qty >= 0:
        return None

    try:
        current_price = conn.get_stock_price(underlying)
        if current_price is None or current_price <= 0:
            return None
    except Exception:
        return None

    try:
        exp_date = datetime.strptime(expiration, "%Y%m%d").date()
        # Position DTE must use the US market clock, not the Windows host date
        # (Brisbane can be a calendar day ahead of the US session). (C13)
        dte = (exp_date - market_now().date()).days
    except (ValueError, TypeError):
        dte = 0

    bid = safe_float(pos.get("bid"))
    ask = safe_float(pos.get("ask"))
    last = safe_float(pos.get("last"))
    mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else last if last > 0 else safe_float(pos.get("market_price"))

    roll_candidate = _select_fresh_roll_candidate(
        pos, underlying, _normalized_option_type(option_type), fresh_candidates
    )
    roll_target = None
    rotation_comparison = None
    if roll_candidate:
        roll_target = {
            key: roll_candidate.get(key)
            for key in ("ticker", "expiration", "strike", "bid", "ask", "dte", "capital_velocity_per_day")
        }
        roll_target["option_type"] = _normalized_option_type(roll_candidate.get("option_type"))
        roll_target["quote_fetched_at_utc"] = roll_candidate.get("quote_fetched_at_utc") or (
            roll_candidate.get("wheel_decision") or {}
        ).get("quote_fetched_at_utc")
        held_capital = current_price * 100 if option_type in {"CALL", "C"} else strike * 100
        fresh_capital = (
            safe_float(roll_candidate.get("stock_price")) * 100
            if option_type in {"CALL", "C"}
            else safe_float(roll_candidate.get("cash_required"), safe_float(roll_candidate.get("strike")) * 100)
        )
        rotation_comparison = {
            "held_mark": mid_price,
            "held_capital": held_capital,
            "held_dte": dte,
            "close_ask": ask,
            "close_fee": pos.get("expected_fee_per_contract"),
            "fresh_bid": safe_float(roll_candidate.get("bid")),
            "fresh_capital": fresh_capital,
            "fresh_dte": safe_float(roll_candidate.get("dte")),
            "open_fee": roll_candidate.get("expected_fee_per_contract"),
            "target": roll_target,
        }

    delta = safe_float(pos.get("delta"), None)
    pos_data = {
        "option_type": option_type,
        "roll_target": roll_target,
        "rotation_comparison": rotation_comparison,
        "strike": strike,
        "expiration": expiration,
        "dte": dte,
        "bid": bid,
        "ask": ask,
        "last": mid_price,
        "delta": delta,
        "greeks_source": "broker" if delta is not None else "missing",
        "theta": safe_float(pos.get("theta")),
        "implied_volatility": safe_float(pos.get("implied_volatility")),
        # Entry credit per contract (average cost) -- required by the exit
        # playbook to decide profit-taking / roll / close (C02).
        "avg_cost": safe_float(pos.get("avg_cost")),
    }

    iv = safe_float(pos.get("implied_volatility"))
    if iv > 0:
        iv_earnings_service.record_iv_data(ticker, iv, current_price, option_type, expiration, dte, strike=strike)
    iv_env_adj, iv_rank, iv_status = iv_earnings_service.get_iv_environment_score(ticker, iv if iv > 0 else 0.20)
    earnings_adj, _ = iv_earnings_service.get_earnings_score_impact(ticker)

    decision = score_existing_position(
        ticker=ticker,
        position_data=pos_data,
        current_stock_price=current_price,
        portfolio_context=portfolio_context,
        iv_env_adjustment=iv_env_adj,
        iv_rank=iv_rank,
        iv_status_str=iv_status,
        iv_percentile=iv_earnings_service.get_iv_percentile(ticker),
        earnings_adjustment=earnings_adj,
        earnings_info=iv_earnings_service.get_earnings_info(ticker),
    )

    return decision
