"""Deterministic candidate ranking for the backend shortlist.

Ordering contract (SCORING.md): quality tier → event tier → descending
executable-bid premium velocity → canonical ticker/expiration/strike.
The composite score is intentionally absent from this key: it may
qualify/explain a candidate, never break a velocity tie.

Pure helpers extracted from RecommendationEngine (F-S1).
"""

from __future__ import annotations

from core.scoring_factors import premium_velocity_per_day

QUALITY_ORDER = {"qualified": 0, "marginal": 1}
EVENT_ORDER = {
    "event_safe": 0,
    "event_not_applicable": 0,
    "earnings_before_expiry": 1,
    "event_unknown": 2,
}


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
        bid = candidate.get("bid", 0) or 0
        bid_premium = float(bid) * 100
    return premium_velocity_per_day(bid_premium, candidate.get("dte", 0))


def rank_key(candidate: dict):
    quality = str(candidate_field(candidate, "quality_tier") or "qualified").lower()
    event = str(candidate_field(candidate, "event_tier") or "").lower()
    if not event:
        # Compatibility for old mocked payloads only; real decisions always
        # serialize an explicit event tier.
        event = (
            "event_safe"
            if (candidate.get("earnings_date") or candidate.get("days_to_earnings") is not None)
            else "event_unknown"
        )
    return (
        QUALITY_ORDER.get(quality, 1),
        EVENT_ORDER.get(event, EVENT_ORDER["event_unknown"]),
        -rank_velocity(candidate),
        str(candidate.get("ticker", "")).upper(),
        str(candidate.get("expiration", "")),
        float(candidate.get("strike", 0) or 0),
    )


def rank_candidates(candidates: list) -> list:
    """Sort candidates by the deterministic shortlist ordering."""
    return sorted(candidates, key=rank_key)


def option_source_value(option: dict, wheel_decision: dict | None, key: str, fallback: str = "broker") -> str:
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
    opt_type = option.get("option_type", "")
    is_long = option.get("profile_type", "") in ("long_call", "long_put")
    is_csp = opt_type == "PUT" and not option.get("held_position", False)
    is_cc = opt_type == "CALL" and option.get("max_contracts", 0) > 0
    is_covered_call = is_cc
    is_csp_signal = is_csp
    # Research-only: long calls, long puts, earnings-vol calendars
    research_only = bool(
        option.get("research_only") or is_long or option.get("profile_type", "") == "earnings_calendar"
    )
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
        "score": option.get("score"),
        "annualized_return": option.get("annualized_return"),
        "iv_adjusted_return": option.get("iv_adjusted_return"),
        "otm_pct": option.get("otm_pct"),
        "delta": option.get("delta"),
        "iv_rank": option.get("iv_rank"),
        "iv_status": option.get("iv_status"),
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
        "score_details": option.get("score_details", {}),
        "size_fit": option.get("size_fit", 0),
        "expected_move_buffer": option.get("expected_move_buffer", 0),
        "wheel_decision": option.get("wheel_decision", {}),
        "from_watchlist": option.get("from_watchlist", False),
        "held_position": option.get("held_position", False),
        # CSP-specific fields
        "cash_required": option.get("cash_required"),
        "breakeven": option.get("breakeven"),
        "breakeven_buffer_pct": option.get("breakeven_buffer_pct"),
        # Growth-aware fields (always-on)
        "score_rationale": wd.get("score_rationale", ""),
        "remaining_gap_to_target": wd.get("remaining_gap_to_target", 0),
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
    }
