"""
Growth-aware metrics — stress loss, risk budget, confidence, intent labels,
and 5x-goal pace math.

Always-on helpers used by wheel_decision.py to compute growth-oriented
risk metrics regardless of mode.  No longer a toggleable "mode" — these
are always computed for every recommendation.
"""

from __future__ import annotations

import math
from datetime import datetime

# Minimum observation window (in days) before we annualize a growth ratio.
# Annualizing a 1-minute or even 1-day move blows up to nonsense (or overflows)
# even when the direction is real; below this we report "insufficient" rather
# than a fabricated annualized pace (C08).
MIN_OBSERVATION_DAYS = 1.0

# ---------------------------------------------------------------------------
# Growth-related helpers
# ---------------------------------------------------------------------------


def growth_pace(history: list[dict], target_multiple: float = 5.0) -> dict:
    """Compute path-to-target pace from persisted portfolio snapshots.

    No fixed deadline: the verdict is derived from realized pace, not a
    promised date. Inputs are snapshot dicts (oldest first) with at least
    ``captured_at`` and ``net_liquidation``.

    Returns a dict with:
        current_nav, baseline_nav, target_nav (baseline * multiple), reached,
        status ("no_data" | "insufficient" | "declining" | "progressing" |
        "reached"), first_captured_at, last_captured_at, elapsed_days,
        progress_pct (0-100 from baseline toward target), annualized_pace
        (decimal), eta_days, required_premium_per_day, on_track (bool|None).
    """
    snaps = [s for s in (history or []) if isinstance(s, dict)]
    if not snaps:
        return {
            "current_nav": 0.0,
            "baseline_nav": 0.0,
            "target_nav": 0.0,
            "reached": False,
            "status": "no_data",
            "elapsed_days": 0.0,
            "progress_pct": 0.0,
            "annualized_pace": None,
            "eta_days": None,
            "required_premium_per_day": None,
            "on_track": None,
        }

    # C07: the first snapshot in the account's history is the durable baseline.
    # Every goal metric derives from it, so progress, target, and pace agree
    # instead of the target moving as NAV grows.
    first, last = snaps[0], snaps[-1]
    first_nav = float(first.get("net_liquidation", 0) or 0)
    baseline_nav = first_nav
    current_nav = float(last.get("net_liquidation", 0) or 0)
    target_nav = baseline_nav * target_multiple
    reached = baseline_nav > 0 and current_nav >= target_nav

    elapsed_days = 0.0
    try:
        t0 = datetime.fromisoformat(str(first.get("captured_at", "")))
        t1 = datetime.fromisoformat(str(last.get("captured_at", "")))
        elapsed_days = max((t1 - t0).total_seconds() / 86_400.0, 0.0)
    except (TypeError, ValueError):
        elapsed_days = 0.0

    # Progress is the share of the baseline->target gap already realized.
    progress_pct = 0.0
    if target_nav > baseline_nav:
        progress_pct = (current_nav - baseline_nav) / (target_nav - baseline_nav) * 100.0
    progress_pct = max(0.0, min(progress_pct, 100.0))

    # C08: annualize only over an adequate observation window, with finite-safe
    # math, and reject nonfinite inputs/results. Distinguish "not enough data"
    # from an observed decline.
    annualized = None
    eta_days = None
    status = "insufficient"
    if reached:
        status = "reached"
        eta_days = 0.0
    elif baseline_nav > 0 and elapsed_days >= MIN_OBSERVATION_DAYS:
        ratio = current_nav / baseline_nav
        years = elapsed_days / 365.25
        if ratio > 0 and years > 0 and math.isfinite(ratio):
            try:
                annualized = math.exp(math.log(ratio) / years) - 1.0
            except (OverflowError, ValueError):
                annualized = None
            if annualized is not None and not math.isfinite(annualized):
                annualized = None
            if annualized is not None and annualized > 0:
                status = "progressing"
                growth = math.log1p(annualized)
                if growth > 0 and current_nav > 0 and target_nav > current_nav:
                    years_needed = math.log(target_nav / current_nav) / growth
                    if math.isfinite(years_needed) and years_needed > 0:
                        eta_days = years_needed * 365.25
            elif annualized is not None:
                # Flat (0) or declining (<0) — an observed shortfall, not a lack
                # of data.
                status = "declining"

    # Required pace: premium per day needed to compound the remaining gap over
    # the ETA implied by current pace (0 once reached; None when unknown).
    required_premium_per_day = None
    if reached:
        required_premium_per_day = 0.0
    elif eta_days and eta_days > 0 and target_nav > current_nav:
        required_premium_per_day = round((target_nav - current_nav) / eta_days, 2)

    on_track = None if annualized is None else annualized > 0
    return {
        "current_nav": round(current_nav, 2),
        "baseline_nav": round(baseline_nav, 2),
        "target_nav": round(target_nav, 2),
        "reached": reached,
        "status": status,
        "first_captured_at": first.get("captured_at", ""),
        "last_captured_at": last.get("captured_at", ""),
        "elapsed_days": round(elapsed_days, 2),
        "progress_pct": round(progress_pct, 2),
        "annualized_pace": None if annualized is None else round(annualized, 6),
        "eta_days": None if eta_days is None else round(eta_days, 1),
        "required_premium_per_day": required_premium_per_day,
        "on_track": on_track,
    }


def estimate_target_gap(
    account_value: float, target_multiple: float, current_premium_income: float, projected_months: int = 12
) -> float:
    """
    Estimate gap between projected income and the target account value.
    Returns the shortfall (positive = shortfall, 0 = on track).
    """
    target_value = account_value * target_multiple
    projected_growth = account_value + current_premium_income * projected_months
    return max(0.0, target_value - projected_growth)


def compute_stress_loss(
    premium_per_contract: float,
    abs_delta: float,
    stock_price: float,
    strike: float,
    option_type: str,
    num_contracts: int = 1,
    shock_pct: float = 0.20,
) -> float:
    """
    Estimate portfolio stress loss for a price shock.
    For puts: stress when stock drops shock_pct.
    For calls: stress when stock rises shock_pct (upside cap).
    Returns dollar loss estimate under stress.
    """
    if option_type == "PUT":
        shock_price = stock_price * (1 - shock_pct)
        loss_per_contract = max(0, strike - shock_price) * 100
    else:
        # Covered call: opportunity cost of upside cap
        shock_price = stock_price * (1 + shock_pct)
        loss_per_contract = max(0, (shock_price - strike) * 100) if strike < shock_price else 0

    return round(loss_per_contract * num_contracts, 2)


def compute_risk_budget_used(
    stress_loss: float,
    account_value: float,
    max_drawdown_pct: float,
) -> float:
    """
    Compute what % of the max drawdown budget this trade would consume.
    """
    drawdown_budget = account_value * max_drawdown_pct
    if drawdown_budget <= 0:
        return 0.0
    return round((stress_loss / drawdown_budget) * 100, 2)


def compute_confidence_score(
    data_source: str,
    has_yfinance_fallback: bool,
    is_stale: bool,
    spread_pct: float,
    open_interest: int,
    has_iv: bool = True,
    has_greeks: bool = True,
) -> float:
    """
    Score how much we trust this data (0-100).
    Deductions for: yfinance fallback, stale data, wide spreads, low OI.
    """
    score = 100.0
    if has_yfinance_fallback or data_source == "yfinance":
        score -= 30.0
    if is_stale:
        score -= 40.0
    if spread_pct > 30:
        score -= min((spread_pct - 30) * 0.5, 20.0)
    if open_interest < 50:
        score -= 15.0
    if not has_iv:
        score -= 30.0
    if not has_greeks:
        score -= 20.0
    return round(max(score, 0.0), 2)


def classify_covered_call_intent(
    strike: float,
    stock_price: float,
    premium_per_contract: float,
    annualized_return: float,
    shares_owned: int,
    avg_cost: float,
) -> str:
    """
    Label a covered call as 'income', 'profit-taking', or 'upside-capping risk'.
    """
    if strike <= stock_price:
        return "upside-capping risk"

    pct_above = ((strike - stock_price) / stock_price) * 100
    cost_basis_pct = ((strike - avg_cost) / avg_cost) * 100 if avg_cost > 0 else 999

    if annualized_return < 8 or premium_per_contract / (stock_price * 100) < 0.005:
        if cost_basis_pct < 3:
            return "upside-capping risk"
        return "income"

    if cost_basis_pct >= 5:
        return "profit-taking"

    if pct_above <= 3:
        return "upside-capping risk"

    return "income"


def should_block_for_data_quality(
    confidence_score: float,
    has_blockers: bool,
    is_from_yfinance: bool,
    price_source: str,
) -> tuple[bool, str]:
    """
    Determine if a recommendation should be blocked from execution.
    Returns (blocked, reason).
    """
    if has_blockers:
        return True, "Hard blockers present"
    if confidence_score < 35:
        return True, "Very low confidence data, insufficient for execution"
    if is_from_yfinance and confidence_score < 65:
        return True, "yfinance fallback data, insufficient confidence"
    return False, ""
