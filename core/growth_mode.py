"""
Growth-aware metrics — stress loss, risk budget, confidence, intent labels.

Always-on helpers used by wheel_decision.py to compute growth-oriented
risk metrics regardless of mode.  No longer a toggleable "mode" — these
are always computed for every recommendation.
"""



# ---------------------------------------------------------------------------
# Growth-related helpers
# ---------------------------------------------------------------------------

def estimate_target_gap(
    account_value: float,
    target_multiple: float,
    current_premium_income: float,
    projected_months: int = 12
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
