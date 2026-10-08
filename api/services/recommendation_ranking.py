"""Deterministic candidate ranking for the backend shortlist.

Ordering contract (SCORING.md): descending capital return per dollar of
capital per day (`rank_capital_velocity`) is the ONLY ranking priority after
hard gates; executable-bid premium velocity (`rank_velocity`) is the sole
tie-break; remaining ties resolve on canonical ticker (upper), expiration,
strike, then option type (upper). Quality/event tiers are display-only risk
information and never influence ordering. The composite score and midpoint
are intentionally absent from this key: they may qualify/explain a candidate,
never break a velocity tie.

Pure helpers extracted from RecommendationEngine (F-S1).
"""

from __future__ import annotations

from core.connection_constants import _parse_option_code_metadata
from core.scoring_factors import capital_velocity_per_day, premium_velocity_per_day
from core.utils import safe_float


def candidate_field(candidate: dict, field: str, default=None):
    """Read a field from the candidate or its embedded wheel_decision."""
    decision = candidate.get("wheel_decision") or {}
    value = candidate.get(field)
    return decision.get(field, default) if value is None else value


def rank_velocity(candidate: dict) -> float:
    """Executable-bid premium velocity (bid × 100 ÷ DTE)."""
    explicit = candidate_field(candidate, "premium_velocity_per_day")
    if explicit is not None:
        try:
            return float(explicit or 0)
        except (TypeError, ValueError):
            pass
    bid_premium = candidate_field(candidate, "bid_premium_per_contract")
    if bid_premium is None:
        bid = candidate_field(candidate, "bid", 0) or 0
        bid_premium = float(bid) * 100
    dte = safe_float(candidate_field(candidate, "dte", 0), default=0.0) or 0.0
    return premium_velocity_per_day(safe_float(bid_premium, default=0.0) or 0.0, dte)


def rank_capital_velocity(candidate: dict) -> float:
    """Return executable premium per secured dollar per day for ranking."""
    explicit = candidate_field(candidate, "capital_velocity_per_day")
    if explicit is not None:
        try:
            return float(explicit or 0)
        except (TypeError, ValueError):
            pass

    annualized = candidate_field(candidate, "annualized_return")
    if annualized is not None:
        try:
            # annualized_return is stored as a percentage, while this helper
            # returns a decimal daily rate.
            return float(annualized or 0) / (365 * 100)
        except (TypeError, ValueError):
            pass

    bid_premium = candidate_field(candidate, "bid_premium_per_contract")
    if bid_premium is None:
        bid = candidate_field(candidate, "bid", 0) or 0
        bid_premium = float(bid) * 100
    option_type = str(candidate_field(candidate, "option_type", "")).upper()
    capital_base = safe_float(candidate_field(candidate, "strike", 0), default=0.0) * 100
    if option_type == "CALL":
        capital_base = safe_float(candidate_field(candidate, "stock_price", 0), default=0.0) * 100
    dte = safe_float(candidate_field(candidate, "dte", 0), default=0.0) or 0.0
    return capital_velocity_per_day(safe_float(bid_premium, default=0.0) or 0.0, capital_base, dte)


def rank_key(candidate: dict):
    return (
        -rank_capital_velocity(candidate),
        -rank_velocity(candidate),
        str(candidate.get("ticker", "")).upper(),
        str(candidate.get("expiration", "")),
        float(candidate.get("strike", 0) or 0),
        str(candidate_field(candidate, "option_type", "")).upper(),
    )


def rank_candidates(candidates: list) -> list:
    """Sort candidates by the deterministic shortlist ordering."""
    return sorted(candidates, key=rank_key)


def option_code_for_candidate(candidate: dict) -> str | None:
    """Build the SDK option code used only for bounded read-only diagnostics."""
    ticker = str(candidate.get("ticker", "")).strip().upper().split(".")[-1]
    expiration = str(candidate.get("expiration", "")).replace("-", "")
    option_type = str(candidate.get("option_type", "")).upper()
    strike = safe_float(candidate.get("strike"), default=None)
    if (
        not ticker
        or not expiration.isdigit()
        or len(expiration) != 8
        or option_type not in {"CALL", "PUT"}
        or strike is None
        or strike <= 0
    ):
        return None
    right = "C" if option_type == "CALL" else "P"
    return f"US.{ticker}{expiration[2:]}{right}{int(round(strike * 1000))}"


def matching_working_orders(candidate: dict, orders: list[dict] | None) -> list[dict] | None:
    """Return working orders matching one exact option, or None if state is unknown."""
    if orders is None:
        return None
    target = (
        str(candidate.get("ticker", "")).upper().split(".")[-1],
        str(candidate.get("expiration", "")).replace("-", ""),
        str(candidate.get("option_type", "")).upper(),
        safe_float(candidate.get("strike"), default=None),
    )
    matches = []
    for order in orders:
        metadata = _parse_option_code_metadata(order.get("code"))
        if not metadata:
            continue
        key = (
            metadata["underlying"].upper(),
            metadata["expiration"],
            metadata["option_type"],
            safe_float(metadata["strike"], default=None),
        )
        if key[:3] == target[:3] and key[3] is not None and target[3] is not None and abs(key[3] - target[3]) < 1e-6:
            matches.append(order)
    return matches


def attach_readonly_trade_diagnostics(candidates: list[dict], connection) -> list[dict]:
    """Attach exact-order and max-sell evidence to at most three selected cards.

    Evidence is display-only: it does not change rank, candidate membership,
    copy eligibility, or recommended quantity.
    """
    selected = list(candidates or [])[:3]
    open_orders = None
    order_query = getattr(connection, "get_open_orders", None)
    if callable(order_query):
        try:
            open_orders = order_query()
        except Exception:
            open_orders = None

    capacity_query = getattr(connection, "get_max_sellable_info", None)
    for candidate in selected:
        candidate["trade_diagnostics_checked"] = True
        matches = matching_working_orders(candidate, open_orders)
        candidate["working_order_check"] = "unavailable" if matches is None else "ok"
        candidate["has_working_order"] = None if matches is None else bool(matches)
        candidate["working_order_count"] = None if matches is None else len(matches)
        candidate["working_order_statuses"] = sorted({str(row.get("status", "")) for row in matches or []})

        code = option_code_for_candidate(candidate)
        bid = safe_float(candidate.get("bid"), default=None)
        capacity = None
        if callable(capacity_query) and code and bid is not None and bid > 0:
            try:
                capacity = capacity_query(code, bid)
            except Exception:
                capacity = None
        candidate["broker_capacity_check"] = "ok" if capacity is not None else "unavailable"
        for field in ("max_position_sell", "max_sell_short", "max_buy_back"):
            candidate[f"broker_{field}"] = safe_float((capacity or {}).get(field), default=None)
    return candidates


def option_source_value(option: dict, wheel_decision: dict | None, key: str, fallback: str = "unknown") -> str:
    option_value = option.get(key)
    if isinstance(option_value, str) and option_value.strip():
        return option_value.strip()
    if wheel_decision:
        wd_value = wheel_decision.get(key)
        if isinstance(wd_value, str) and wd_value.strip():
            return wd_value.strip()
    if key == "data_source":
        data_source = option.get("data_source")
        if isinstance(data_source, str) and data_source.strip():
            return data_source.strip()
    return fallback


def option_uses_yfinance(option: dict, wheel_decision: dict | None = None) -> bool:
    source_values = [
        option_source_value(option, wheel_decision, "price_source", ""),
        option_source_value(option, wheel_decision, "chain_source", ""),
        option_source_value(option, wheel_decision, "iv_source", ""),
        option_source_value(option, wheel_decision, "data_source", ""),
    ]
    return bool(
        option.get("from_yfinance")
        or (wheel_decision and wheel_decision.get("from_yfinance"))
        or any(value.lower() == "yfinance" for value in source_values if isinstance(value, str))
    )


def format_recommendation(option: dict, rank: int = 0) -> dict:
    """Format a raw option dict into a standardized recommendation dict (signal-only)."""
    wd = option.get("wheel_decision", {})
    if not isinstance(wd, dict):
        wd = {}
    wd = {key: value for key, value in wd.items() if key not in {"iv_adjusted_return", "remaining_gap_to_target"}}
    opt_type = option.get("option_type", "")
    is_csp = opt_type == "PUT" and not option.get("held_position", False)
    is_cc = opt_type == "CALL" and option.get("max_contracts", 0) > 0
    is_covered_call = is_cc
    is_csp_signal = is_csp
    # Research-only is set by the engine when a candidate cannot be acted on
    # (e.g. cash-short mode); it is broker-agnostic display context only.
    research_only = bool(option.get("research_only"))
    return {
        "rank": rank,
        "ticker": option["ticker"],
        "option_type": opt_type,
        "strike": option.get("strike"),
        "expiration": option.get("expiration"),
        "dte": option.get("dte"),
        "mid_price": option.get("mid_price"),
        "premium_per_contract": option.get("premium_per_contract"),
        "bid_premium_per_contract": option.get("bid_premium_per_contract", wd.get("bid_premium_per_contract")),
        "limit_target_per_contract": option.get("limit_target_per_contract", wd.get("limit_target_per_contract")),
        "premium_velocity_per_day": option.get("premium_velocity_per_day", wd.get("premium_velocity_per_day")),
        "capital_velocity_per_day": option.get("capital_velocity_per_day", wd.get("capital_velocity_per_day")),
        "annualized_return": option.get("annualized_return"),
        "otm_pct": option.get("otm_pct"),
        "delta": option.get("delta"),
        "iv_rank": option.get("iv_rank"),
        "iv_status": option.get("iv_status"),
        "iv_percentile": option.get("iv_percentile", wd.get("iv_percentile")),
        "days_to_earnings": option.get("days_to_earnings"),
        "earnings_date": option.get("earnings_date"),
        "warnings": option.get("warnings", []),
        "rationale": option.get("rationale", []),
        "max_contracts": option.get("max_contracts"),
        "recommended_contracts": option.get("recommended_contracts", wd.get("recommended_contracts", 0)),
        "existing_position": option.get("existing_position", 0),
        "profile_type": option.get("profile_type"),
        "stock_price": option.get("stock_price"),
        "avg_cost": option.get("avg_cost"),
        "bid": option.get("bid"),
        "ask": option.get("ask"),
        "open_interest": option.get("open_interest"),
        "volume": option.get("volume"),
        "implied_volatility": option.get("implied_volatility"),
        "size_fit": option.get("size_fit", 0),
        "expected_move_buffer": option.get("expected_move_buffer", 0),
        "wheel_decision": wd,
        "from_watchlist": option.get("from_watchlist", False),
        "held_position": option.get("held_position", False),
        # CSP-specific fields
        "cash_required": option.get("cash_required"),
        "breakeven": option.get("breakeven"),
        "breakeven_buffer_pct": option.get("breakeven_buffer_pct"),
        # Growth-aware fields (always-on)
        "score_rationale": wd.get("score_rationale", ""),
        "risk_budget_used_pct": wd.get("risk_budget_used_pct", 0),
        "stress_loss": wd.get("stress_loss", 0),
        "confidence_score": wd.get("confidence_score", 100),
        "covered_call_intent": wd.get("covered_call_intent", ""),
        # Signal-only fields
        "signal_type": "covered_call" if is_covered_call else ("csp" if is_csp_signal else opt_type.lower()),
        "strategy": "wheel",
        "broker_feasible": bool(option.get("broker_feasible", not research_only)),
        "capital_required": option.get("cash_required", 0),
        "risk_budget_used": wd.get("risk_budget_used_pct", 0),
        "data_source": wd.get("price_source", "moomoo"),
        "confidence": wd.get("confidence_score", 100),
        "price_source": option_source_value(option, wd, "price_source", wd.get("price_source", "moomoo")),
        "chain_source": option_source_value(option, wd, "chain_source", wd.get("chain_source", "broker")),
        "iv_source": option_source_value(option, wd, "iv_source", wd.get("iv_source", "broker")),
        "from_yfinance": option_uses_yfinance(option, wd),
        "quote_quality": wd.get("quote_quality", ""),
        "quality_tier": option.get("quality_tier", wd.get("quality_tier", "")),
        "event_tier": option.get("event_tier", wd.get("event_tier", "")),
        "security_type": option.get("security_type", wd.get("security_type", "stock")),
        "review_only": option.get("review_only", wd.get("review_only", True)),
        "copy_eligible": option.get("copy_eligible", wd.get("copy_eligible", False)),
        "quote_update_time": option.get("quote_update_time", wd.get("quote_update_time", "")),
        "quote_fetched_at_utc": option.get("quote_fetched_at_utc", wd.get("quote_fetched_at_utc", "")),
        "blocked_reason_codes": wd.get("blocked_reason_codes", []) or wd.get("hard_blockers", []),
        "research_only": research_only,
        # Query-only broker diagnostics are display-only and never alter
        # shortlist rank, copy eligibility, or contract quantity.
        "working_order_check": option.get("working_order_check", "unavailable"),
        "has_working_order": option.get("has_working_order"),
        "working_order_count": option.get("working_order_count"),
        "working_order_statuses": option.get("working_order_statuses", []),
        "trade_diagnostics_checked": bool(option.get("trade_diagnostics_checked", False)),
        "broker_capacity_check": option.get("broker_capacity_check", "unavailable"),
        "broker_max_position_sell": option.get("broker_max_position_sell"),
        "broker_max_sell_short": option.get("broker_max_sell_short"),
        "broker_max_buy_back": option.get("broker_max_buy_back"),
    }
