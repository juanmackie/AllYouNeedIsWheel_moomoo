"""Deterministic candidate ranking for the backend shortlist.

Ordering contract (SCORING.md): descending capital return per dollar of
capital per day (`rank_capital_velocity`) is the ONLY ranking priority after
hard gates; executable-bid premium velocity (`rank_velocity`) is the sole
tie-break; remaining ties resolve on canonical ticker (upper), expiration,
strike, then option type (upper). Quality/event tiers are display-only risk
information and never influence ordering. The composite score and midpoint
are intentionally absent from this key: they may qualify/explain a candidate,
never break a velocity tie.

CSP tail-risk gates (owner decision 2026-10-23) are hard gates applied before
ranking: `csp_tail_risk_gate` marks a new put research-only, so it sorts after
actionable picks. `rank_key` is unchanged by these gates.

Pure helpers extracted from RecommendationEngine (F-S1).
"""

from __future__ import annotations

from core.scoring_factors import capital_velocity_per_day, premium_velocity_per_day
from core.ticker_utils import earnings_underlying_ticker
from core.utils import safe_float

LEVERAGED_ETF = "leveraged_etf"
EARNINGS_BEFORE_EXPIRY = "earnings_before_expiry"
EARNINGS_UNKNOWN = "earnings_unknown"


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


def mark_research_only_candidate(candidate: dict, reason: str | None = None) -> dict:
    """Flag a candidate as research-only and attach a visible warning."""
    candidate["research_only"] = True
    candidate.update(copy_eligible=False, review_only=True, max_contracts=0, recommended_contracts=0)
    decision = candidate.get("wheel_decision")
    if isinstance(decision, dict):
        decision.update(copy_eligible=False, review_only=True, max_contracts=0, recommended_contracts=0)
    warnings = list(candidate.get("warnings") or [])
    if reason and reason not in warnings:
        warnings.append(reason)
    candidate["warnings"] = warnings
    return candidate


def csp_tail_risk_gate(candidate: dict, leveraged_tickers=()) -> tuple[str, str] | None:
    """Return (reason_code, reason_text) when a new CSP must stay research-only, else None.

    Owner decision 2026-10-23. Checks run in order: leveraged or inverse ETF,
    then earnings inside expiry, then unknown earnings (missing or empty
    event_tier). event_safe and event_not_applicable (non-leveraged ETF or index)
    pass. Ranking order is unchanged. Covered calls and rolls of held positions
    are not gated.
    """
    underlying = earnings_underlying_ticker(str(candidate.get("ticker") or "")).upper()
    leveraged = {earnings_underlying_ticker(str(t or "")).upper() for t in leveraged_tickers}
    leveraged.discard("")
    if underlying and underlying in leveraged:
        return (
            LEVERAGED_ETF,
            "Research-only: leveraged/inverse ETF — daily-reset gap risk is larger than the premium shows",
        )
    event_tier = str(candidate.get("event_tier") or "")
    if event_tier == "earnings_before_expiry":
        earnings_date = candidate.get("earnings_date") or "a known date"
        return EARNINGS_BEFORE_EXPIRY, f"Research-only: earnings on {earnings_date} fall before expiry"
    if event_tier in {"", "event_unknown"}:
        return EARNINGS_UNKNOWN, "Research-only: earnings date unknown — cannot confirm it falls after expiry"
    return None


def is_roll_target_eligible(candidate: dict) -> bool:
    """Return True when a fresh candidate may be a roll target.

    A tail-risk gate blocks new entries only. A held position may roll into a
    gated contract, so copy_eligible False with a risk_gate still qualifies.
    The scan sets ``risk_gate`` only on picks that were copy-eligible before the
    gate, so a pick that is also cash-short or scorer-blocked never qualifies.
    """
    eligible = candidate.get("copy_eligible")
    if eligible is True:
        return True
    return eligible is False and bool(candidate.get("risk_gate"))


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
        "loss_if_called": wd.get("loss_if_called"),
        "if_called_pnl_vs_basis": wd.get("if_called_pnl_vs_basis"),
        "basis_source": wd.get("basis_source"),
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
        # CSP tail-risk gate code (leveraged_etf, earnings_before_expiry, earnings_unknown); None when ungated.
        "risk_gate": option.get("risk_gate"),
    }
