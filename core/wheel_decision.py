"""
Unified Wheel Decision Engine

One canonical decision model for both candidate contracts and open positions.
All surfaces (recommendations, rollover, dashboard) read from this single source.
Orchestrates scoring by composing pure factor functions from scoring_factors.py.
"""

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from core.scoring_factors import (
    _clamp,
    _score_proximity,
    _score_positive_metric,
    _calculate_mid_price,
    _compute_shared_subscores,
    _compute_roll_pressure,
    _compute_profit_target_progress,
    _compute_size_fit,
    _compute_expected_move_buffer,
    _compute_recommended_contracts,
    CALL_W_IV_ADJ, CALL_W_TDR, CALL_W_LIQ, CALL_W_EV, CALL_W_UPSIDE, CALL_W_OTM, CALL_W_EARNINGS, CALL_W_CE,
    PUT_W_IV_ADJ, PUT_W_TDR, PUT_W_EV, PUT_W_LIQ, PUT_W_BUF, PUT_W_CE, PUT_W_EARNINGS, PUT_W_DELTA,
    CALL_MAX_LOSS_ESTIMATE_PCT, PUT_MAX_LOSS_ESTIMATE_PCT,
    STALE_QUOTE_THRESHOLD, ACCOUNT_VALUE_MIN,
)
from core.connection_constants import _normalize_iv
from core.growth_mode import (
    compute_stress_loss,
    compute_risk_budget_used,
    compute_confidence_score,
    classify_covered_call_intent,
    estimate_target_gap,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class WheelDecision:
    """
    The single source of truth for any wheel-related decision:
    - candidate contracts (before entry)
    - open positions (hold / roll / close)

    Fields are grouped by the decision they inform.
    """

    # -- Identity -----------------------------------------------------------
    ticker: str = ""
    option_type: str = ""          # CALL | PUT
    strike: float = 0.0
    expiration: str = ""           # YYYYMMDD
    dte: int = 0

    # -- Price / premium ----------------------------------------------------
    stock_price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    mid_price: float = 0.0
    premium_per_contract: float = 0.0
    annualized_return: float = 0.0
    iv_adjusted_return: float = 0.0

    # -- Greeks -------------------------------------------------------------
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    implied_volatility: float = 0.0

    # -- Liquidity ----------------------------------------------------------
    open_interest: int = 0
    volume: int = 0
    spread_pct: float = 0.0

    # -- Sub-scores (0-100) -------------------------------------------------
    delta_score: float = 0.0
    dte_score: float = 0.0
    oi_score: float = 0.0
    volume_score: float = 0.0
    spread_score: float = 0.0
    liquidity_score: float = 0.0
    iv_adjusted_score: float = 0.0
    tdr_score: float = 0.0            # theta/delta ratio score
    ev_score: float = 0.0              # expected value score
    iv_env_score: float = 0.0          # IV environment score
    otm_score: float = 0.0
    upside_score: float = 0.0          # CALL only
    buffer_score: float = 0.0          # PUT only
    cost_basis_score: float = 0.0      # CALL only
    capital_fit: float = 0.0           # PUT only
    ce_score: float = 0.0              # PUT: capital efficiency

    # -- Composite ----------------------------------------------------------
    contract_score: float = 0.0        # Final composite score (0-~200+)
    wheel_rank: int = 0                # Rank among peer candidates

    # -- Position-specific (for open positions) -----------------------------
    otm_pct: float = 0.0               # % OTM distance
    breakeven: float = 0.0             # PUT only
    breakeven_buffer_pct: float = 0.0  # PUT only
    cash_required: float = 0.0         # PUT only
    if_called_return: float = 0.0      # CALL only

    # -- Size / portfolio fit -----------------------------------------------
    size_fit: float = 0.0              # 0-100: how well the contract fits the portfolio
    max_contracts: int = 0             # Max contracts allowed by shares/cash
    recommended_contracts: int = 0     # Recommended contract quantity from sizing logic
    expected_move_buffer: float = 0.0  # Expected move range vs strike distance (%)

    # -- Roll / hold / close signals (open positions) -----------------------
    roll_pressure: float = 0.0         # 0-100: urgency to roll
    extrinsic_remaining: float = 0.0   # Remaining extrinsic value ($)
    profit_target_progress: float = 0.0  # 0-100: how close to profit target

    # -- Environment --------------------------------------------------------
    iv_rank: float = 0.0               # 0-100
    iv_status: str = "unknown"         # extreme_low | low | normal | high | extreme_high
    iv_env_adjustment: float = 0.0     # -20 to +20
    earnings_adjustment: float = 0.0   # % score adjustment
    earnings_date: Optional[str] = None
    days_to_earnings: Optional[int] = None
    vix_regime: str = "normal"
    vix_level: float = 20.0
    macro_multiplier: float = 1.0
    macro_regime: str = "unknown"
    macro_credit_stress: str = "unknown"
    macro_summary: str = ""
    macro_advice: str = ""
    profile_type: str = "monthly"

    # -- Quote quality ------------------------------------------------------
    quote_quality: str = ""              # tradable | no_bid | no_ask | zero_mark | wide_spread | low_liquidity
    blocked_reason_codes: list[str] = field(default_factory=list)

    # -- Decision flags -----------------------------------------------------
    hard_blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)

    # -- Data provenance (TODO 2.1) --------------------------------------
    price_source: str = ''           # Moomoo, portfolio fallback, yfinance
    chain_source: str = ''           # Moomoo, yfinance
    greeks_source: str = ''          # broker, Black-Scholes computed, missing
    iv_source: str = ''             # broker, yfinance, historical cache
    earnings_source: str = ''         # provider/cache/manual
    macro_source: str = ''           # FRED/cache/disabled
    quote_timestamp: Optional[str] = None
    generated_at: Optional[str] = None

    # -- Score breakdown (for display) --------------------------------------
    score_details: dict = field(default_factory=dict)

    # -- Expected value -----------------------------------------------------
    expected_value: float = 0.0
    pop: float = 0.0                   # Probability of profit

    # -- Capital efficiency -------------------------------------------------
    capital_efficiency: float = 0.0

    # -- Return breakdown (TODO 1.2) ------------------------------------
    return_on_underlying: Optional[float] = None  # CALL: premium / (stock_price * 100)
    return_on_secured_cash: Optional[float] = None  # PUT: premium / (strike * 100)

    # -- Growth-aware metrics (always-on) -----------------------------------
    remaining_gap_to_target: float = 0.0
    risk_budget_used_pct: float = 0.0
    stress_loss: float = 0.0
    confidence_score: float = 0.0
    covered_call_intent: str = ""   # income | profit-taking | upside-capping risk
    score_rationale: str = ""

    # -- Internal tracking --------------------------------------------------
    _theta_delta_ratio: float = 0.0    # Raw theta/delta ratio

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    def has_blocker(self) -> bool:
        return bool(self.hard_blockers)

    def has_warnings(self) -> bool:
        return bool(self.warnings)


def _create_failed_decision(ticker: str, option_type: str, strike: float, expiration: str, reason: str, blocked_reason_codes: list[str] | None = None) -> WheelDecision:
    """
    Create a WheelDecision that failed hard filters.
    Populates hard_blockers with the reason and blocked_reason_codes with machine-readable codes.
    """
    logger.info("Hard blocker for %s %s at %.2f exp %s: %s", ticker, option_type, strike, expiration, reason)
    return WheelDecision(
        ticker=ticker,
        option_type=option_type,
        strike=strike,
        expiration=expiration,
        hard_blockers=[reason],
        blocked_reason_codes=blocked_reason_codes or [],
    )


def _normalize_expiration(expiration: str) -> str:
    """Return YYYYMMDD for common broker/yfinance expiration formats."""
    value = str(expiration or '').strip()
    if not value:
        return ''

    if len(value) >= 10 and value[4] == '-' and value[7] == '-':
        return value[:10].replace('-', '')

    return value.replace('-', '')


def _coerce_optional_float(value):
    """Return a float when possible, otherwise None."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_source_value(value: object, fallback: str) -> str:
    """Return a normalized source label with a fallback when the value is missing."""
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return fallback


# ---------------------------------------------------------------------------
# Re-exports for backward compatibility
# ---------------------------------------------------------------------------

__all__ = [
    'WheelDecision',
    '_clamp',
    '_score_proximity',
    '_score_positive_metric',
    '_calculate_mid_price',
    '_compute_shared_subscores',
    '_compute_roll_pressure',
    '_compute_profit_target_progress',
    '_compute_size_fit',
    '_compute_expected_move_buffer',
    'score_contract',
    'score_existing_position',
]


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def score_contract(
    ticker: str,
    option: dict,
    stock_price: float,
    profile: dict,
    portfolio_context: dict,
    iv_env_adjustment: float = 0.0,
    iv_rank: float = 0.0,
    iv_status_str: str = "normal",
    earnings_adjustment: float = 0.0,
    earnings_info: dict | None = None,
    macro_regime: dict | None = None,
    growth_profile: dict | None = None,
    research_only_mode: bool = False,
) -> WheelDecision:
    """
    Score a single option contract and return a WheelDecision.

    This is the unified scorer used for both portfolio candidates and
    watchlist candidates. Missing data is handled gracefully.

    Returns a WheelDecision with hard_blockers populated if the contract fails hard filters.
    """
    earnings_info = earnings_info or {}
    macro_regime = macro_regime or {}

    # -- Parse inputs -------------------------------------------------------
    strike = float(option.get('strike', 0) or 0)
    expiration = _normalize_expiration(option.get('expiration', '') or '')
    option_type = str(option.get('option_type', '') or '').upper()
    
    if strike <= 0 or not expiration:
        return _create_failed_decision(ticker, option_type, strike, expiration, "Invalid strike or expiration")
    
    from datetime import datetime
    try:
        expiry_date = datetime.strptime(expiration, '%Y%m%d').date()
    except ValueError:
        return _create_failed_decision(ticker, option_type, strike, expiration, "Invalid expiration format")
    
    dte = (expiry_date - datetime.now().date()).days
    if dte <= 0:
        return _create_failed_decision(ticker, option_type, strike, expiration, "Expired or no time value")

    if option_type == 'PUT':
        min_dte = _coerce_optional_float(profile.get('min_dte'))
        max_dte = _coerce_optional_float(profile.get('max_dte'))
        if min_dte is not None and dte < min_dte:
            return _create_failed_decision(
                ticker,
                'PUT',
                strike,
                expiration,
                f"CSP DTE below target range: {dte} < {min_dte:.0f}",
                ['outside_csp_dte_range'],
            )
        if max_dte is not None and dte > max_dte:
            return _create_failed_decision(
                ticker,
                'PUT',
                strike,
                expiration,
                f"CSP DTE above target range: {dte} > {max_dte:.0f}",
                ['outside_csp_dte_range'],
            )

    bid = float(option.get('bid', 0) or 0)
    ask = float(option.get('ask', 0) or 0)
    last = float(option.get('last', 0) or 0)

    # -- Strict quote quality gates for sell signals -------------------------
    # Require a two-sided market with executable bid and ask.
    # Ask-only, last-only, zero-mark, or zero-bid quotes are hard blockers.
    blocked_reason_codes = []
    if bid <= 0 and ask <= 0:
        return _create_failed_decision(ticker, option_type, strike, expiration, "No two-sided market - no bid or ask available", ['no_market'])
    if bid <= 0:
        return _create_failed_decision(ticker, option_type, strike, expiration, "No executable bid - ask-only quote", ['no_bid'])
    if ask <= 0:
        return _create_failed_decision(ticker, option_type, strike, expiration, "No ask price available", ['no_ask'])

    mid_price = _calculate_mid_price(bid, ask, last)
    if mid_price <= 0:
        return _create_failed_decision(ticker, option_type, strike, expiration, "Zero computed mid price", ['zero_mark'])
    if mid_price < profile.get('min_mid_price', 0.05):
        return _create_failed_decision(ticker, option_type, strike, expiration, f"Mid price too low: {mid_price}")

    spread_pct = ((ask - bid) / mid_price) * 100

    if spread_pct > profile.get('max_spread_pct', 60):
        return _create_failed_decision(ticker, option_type, strike, expiration, f"Spread too wide: {spread_pct:.1f}%", ['wide_spread'])

    quote_quality = 'tradable'
    delta = float(option.get('delta', 0) or 0)
    implied_volatility = _normalize_iv(option.get('implied_volatility', 0))
    open_interest = int(option.get('open_interest', 0) or 0)
    volume = int(option.get('volume', 0) or 0)
    premium_per_contract = mid_price * 100

    if premium_per_contract < profile.get('min_premium_per_contract', 10):
        return _create_failed_decision(ticker, option_type, strike, expiration, f"Premium too low: {premium_per_contract}")
    
    if open_interest < profile.get('min_open_interest', 10) and volume < profile.get('min_volume', 1):
        return _create_failed_decision(ticker, option_type, strike, expiration, "Open interest and volume too low", ['low_liquidity'])

    # -- Enrich Greeks if missing (TODO 0.3) -----------------------
    if implied_volatility > 0 and abs(delta) < 0.001:
        from core.greeks import enrich_option_with_greeks
        enrich_option_with_greeks(option, stock_price)
        # Re-read delta after enrichment
        delta = float(option.get('delta', 0) or 0)

    if implied_volatility <= 0:
        return _create_failed_decision(
            ticker,
            option_type,
            strike,
            expiration,
            "Missing implied volatility after enrichment",
            ['missing_iv'],
        )
    if abs(delta) < 0.001:
        return _create_failed_decision(
            ticker,
            option_type,
            strike,
            expiration,
            "Missing delta/Greeks after enrichment",
            ['missing_greeks'],
        )

    # -- Portfolio context ---------------------------------------------------
    position = portfolio_context.get('positions', {}).get(ticker, {})
    shares_owned = float(position.get('position', 0) or 0)
    avg_cost = float(position.get('avg_cost', 0) or 0)
    cash_balance = float(portfolio_context.get('cash_balance', 0) or 0)
    available_cash = float(portfolio_context.get('available_cash', cash_balance) or 0)
    cash_available_for_csp = float(portfolio_context.get('cash_available_for_csp', available_cash) or 0)
    cash_reserved_for_csp = float(portfolio_context.get('cash_reserved_for_csp', 0) or 0)
    broker_buying_power = float(portfolio_context.get('broker_buying_power', cash_available_for_csp) or cash_available_for_csp)
    account_value = portfolio_context.get('account_value', cash_balance)
    vix_regime = portfolio_context.get('vix_regime')

    # -- Check for yfinance fallback data (TODO 2.2) -----------------------
    price_source = _normalize_source_value(option.get('price_source'), '')
    chain_source = _normalize_source_value(option.get('chain_source'), '')
    iv_source = _normalize_source_value(option.get('iv_source'), '')
    from_yfinance = bool(
        option.get('from_yfinance', False)
        or price_source.lower() == 'yfinance'
        or chain_source.lower() == 'yfinance'
        or iv_source.lower() == 'yfinance'
    )
    price_source = _normalize_source_value(price_source, 'yfinance' if from_yfinance else 'broker')
    chain_source = _normalize_source_value(chain_source, 'yfinance' if from_yfinance else 'broker')
    iv_source = _normalize_source_value(iv_source, 'yfinance' if from_yfinance else 'broker')

    # -- Build decision -----------------------------------------------------
    decision = WheelDecision(
        ticker=ticker,
        option_type=option_type,
        strike=strike,
        expiration=expiration,
        dte=dte,
        stock_price=stock_price,
        bid=bid,
        ask=ask,
        quote_quality=quote_quality,
        blocked_reason_codes=blocked_reason_codes,
        last=last,
        mid_price=round(mid_price, 4),
        premium_per_contract=round(premium_per_contract, 2),
        delta=round(delta, 5),
        gamma=round(float(option.get('gamma', 0) or 0), 5),
        theta=round(float(option.get('theta', 0) or 0), 5),
        vega=round(float(option.get('vega', 0) or 0), 5),
        implied_volatility=round(implied_volatility, 2),
        open_interest=open_interest,
        volume=volume,
        spread_pct=round(spread_pct, 2),
        iv_rank=round(iv_rank * 100, 1),
        iv_status=iv_status_str,
        iv_env_adjustment=iv_env_adjustment,
        earnings_adjustment=earnings_adjustment,
        earnings_date=earnings_info.get('earnings_date'),
        days_to_earnings=earnings_info.get('days_to_earnings'),
        vix_regime=vix_regime.get('regime', 'normal') if vix_regime else 'normal',
        vix_level=vix_regime.get('vix', 20.0) if vix_regime else 20.0,
        macro_multiplier=macro_regime.get('macro_multiplier', 1.0),
        macro_regime=macro_regime.get('rate_regime', 'unknown'),
        macro_credit_stress=macro_regime.get('credit_stress', 'unknown'),
        macro_summary=macro_regime.get('summary', ''),
        macro_advice=macro_regime.get('advice', ''),
        profile_type=profile.get('profile_type', 'monthly'),
        # Data provenance (TODO 2.1)
        price_source=price_source,
        chain_source=chain_source,
        greeks_source=(option.get('greeks_source') or ('broker' if abs(delta) > 0.001 else 'Black-Scholes computed')),
        iv_source=iv_source,
        macro_source=macro_regime.get('source', 'FRED/cache/disabled'),
        quote_timestamp=datetime.now().isoformat(),
        generated_at=datetime.now().isoformat(),
    )

    # Add yfinance warning if applicable (TODO 2.2)
    if from_yfinance:
        decision.warnings.append('Data from yfinance (not Moomoo) - verify before trading')

    # -- IV-adjusted return -------------------------------------------------
    # PUT return denominator uses strike * 100 (secured cash).
    # CALL return denominator uses stock_price * 100 (underlying value).
    if option_type == 'CALL':
        capital_at_risk = stock_price * 100
        decision.return_on_underlying = round(
            (premium_per_contract / capital_at_risk) * (365 / dte) * 100 if capital_at_risk > 0 and dte > 0 else 0, 2
        )
        decision.return_on_secured_cash = None
    else:
        capital_at_risk = strike * 100  # strike * 100 = cash required for PUT
        decision.return_on_secured_cash = round(
            (premium_per_contract / capital_at_risk) * (365 / dte) * 100 if capital_at_risk > 0 and dte > 0 else 0, 2
        )
        decision.return_on_underlying = None

    annualized_return_raw = (
        (premium_per_contract / capital_at_risk) * (365 / dte) * 100
        if capital_at_risk > 0 and dte > 0 else 0
    )
    iv_adjusted_return = annualized_return_raw / max(implied_volatility, 0.05)
    decision.annualized_return = round(annualized_return_raw, 2)
    decision.iv_adjusted_return = round(iv_adjusted_return, 2)
    decision.iv_adjusted_score = _score_positive_metric(
        iv_adjusted_return, profile.get('target_iv_adjusted', 50)
    ) * 100

    # -- Expected value -----------------------------------------------------
    abs_delta = abs(delta)
    pop = 1 - abs_delta
    if option_type == 'CALL':
        max_loss_estimate = 0
    else:
        max_loss_estimate = strike * 100 * PUT_MAX_LOSS_ESTIMATE_PCT
    if option_type == 'CALL':
        expected_value = premium_per_contract
    else:
        expected_value = (pop * premium_per_contract) - ((1 - pop) * max_loss_estimate)
    decision.expected_value = round(expected_value, 2)
    decision.pop = round(pop, 4)
    decision.ev_score = _clamp(expected_value / max(premium_per_contract, 0.01)) * 100

    # -- Shared sub-scores --------------------------------------------------
    _compute_shared_subscores(decision, profile)

    # -- Hard blocker checks ------------------------------------------------
    macro_multiplier = macro_regime.get('macro_multiplier', 1.0)

    profile_type = str(profile.get('profile_type', '') or '').lower()
    is_long_research = research_only_mode and profile_type in {'long_call', 'long_put'}

    if option_type == 'CALL':
        if stock_price <= 0 or strike <= stock_price:
            return _create_failed_decision(ticker, 'CALL', strike, expiration, f"Strike {strike} not above stock price {stock_price}")
        max_contracts = max(int(shares_owned // 100), 0)
        if max_contracts < 1 and not is_long_research:
            return _create_failed_decision(ticker, 'CALL', strike, expiration, "No covered shares available")
        decision.max_contracts = max_contracts if max_contracts > 0 else 1

        otm_pct = ((strike - stock_price) / stock_price) * 100
        if_called_return = (((strike - stock_price) + mid_price) / stock_price) * 100 if stock_price > 0 else 0
        cost_basis_score = (
            1.0 if avg_cost <= 0 or strike >= avg_cost
            else _clamp(1 - ((avg_cost - strike) / avg_cost) * 4)
        )

        decision.otm_pct = round(otm_pct, 2)
        decision.if_called_return = round(if_called_return, 2)
        decision.cost_basis_score = round(cost_basis_score * 100, 1)
        decision.otm_score = _score_proximity(
            otm_pct, profile.get('default_otm_pct', 10), max(profile.get('default_otm_pct', 10) * 0.75, 6)
        ) * 100
        decision.upside_score = _score_positive_metric(if_called_return, 12) * 100

        # Capital efficiency for covered calls — how much premium per dollar of account equity
        capital_efficiency = 0.0
        ce_score_val = 0.0
        if account_value > 0:
            call_capital = shares_owned * stock_price
            if call_capital > 0:
                capital_efficiency = annualized_return_raw / (call_capital / account_value)
                ce_score_val = _score_positive_metric(
                    capital_efficiency, profile.get('target_capital_efficiency', 100)
                ) * 100
        decision.capital_efficiency = round(capital_efficiency, 1)
        decision.ce_score = round(ce_score_val, 1)

        # Earnings risk score from earnings_adjustment (e.g., -30 → 70, 0 → 100)
        earnings_risk_score = _clamp((100 + earnings_adjustment) / 100) * 100

        # CALL base score — growth-oriented weights (premium, capital efficiency, liquidity, theta decay)
        w_iv_adj = CALL_W_IV_ADJ
        w_tdr = CALL_W_TDR
        w_liq = CALL_W_LIQ
        w_ev = CALL_W_EV
        w_upside = CALL_W_UPSIDE
        w_otm = CALL_W_OTM
        w_earnings = CALL_W_EARNINGS
        w_ce = CALL_W_CE
        total_w = w_iv_adj + w_tdr + w_liq + w_ev + w_upside + w_otm + w_earnings + w_ce
        if total_w > 0:
            w_iv_adj /= total_w
            w_tdr /= total_w
            w_liq /= total_w
            w_ev /= total_w
            w_upside /= total_w
            w_otm /= total_w
            w_earnings /= total_w
            w_ce /= total_w

        base_score = (
            decision.iv_adjusted_score * w_iv_adj +
            decision.tdr_score * w_tdr +
            decision.liquidity_score * w_liq +
            decision.ev_score * w_ev +
            decision.upside_score * w_upside +
            decision.otm_score * w_otm +
            earnings_risk_score * w_earnings +
            decision.ce_score * w_ce
        )

        iv_adjusted_score_final = base_score * (1 + iv_env_adjustment / 100)
        score = iv_adjusted_score_final
        score *= (0.65 + (0.35 * cost_basis_score))
        score *= macro_multiplier

        decision.contract_score = round(_clamp(score / 100) * 100, 2)
        decision.size_fit = _compute_size_fit(decision, portfolio_context)

        # Warnings
        if spread_pct > profile.get('ideal_spread_pct', 12):
            decision.warnings.append(f'Wide bid/ask spread ({spread_pct:.0f}%)')
        if open_interest < profile.get('ideal_open_interest', 500):
            decision.warnings.append('Below ideal open interest')
        if avg_cost > 0 and strike < avg_cost:
            decision.warnings.append('Strike below stock cost basis')
        if iv_status_str == 'extreme_low':
            decision.warnings.append(f'IV extremely low ({decision.iv_rank:.0f}%) - poor risk/reward')
        elif iv_status_str == 'low':
            decision.warnings.append(f'IV below average ({decision.iv_rank:.0f}%)')
        elif iv_status_str == 'extreme_high':
            decision.warnings.append(f'IV extremely high ({decision.iv_rank:.0f}%) - excellent premium')
        if earnings_info.get('warning_level') == 'today':
            decision.warnings.append('EARNINGS TODAY - extreme risk')
        elif earnings_info.get('warning_level') == 'very_soon':
            decision.warnings.append(f'Earnings in {earnings_info.get("days_to_earnings")}d - high assignment risk')
        elif earnings_info.get('warning_level') == 'soon':
            decision.warnings.append(f'Earnings in {earnings_info.get("days_to_earnings")} days')
        if vix_regime:
            if vix_regime.get('regime') == 'complacency':
                decision.warnings.append(f'Low VIX ({vix_regime["vix"]}) - premiums compressed')
            elif vix_regime.get('regime') == 'fear':
                decision.warnings.append(f'High VIX ({vix_regime["vix"]}) - elevated risk, wider stops')
        if macro_multiplier < 1.0:
            decision.warnings.append(f"{macro_regime.get('summary', 'Macro headwinds')}")

        # Rationale
        decision.rationale = [
            f"{decision.annualized_return:.1f}% ann. yield (IV-adj: {iv_adjusted_return:.1f}, rank: {iv_rank*100:.0f}%)",
            f"Theta/Delta: {decision._theta_delta_ratio:.4f} | EV: ${expected_value:.2f} | Profile: {profile.get('profile_type', 'monthly')}",
            f"{otm_pct:.1f}% OTM, {abs_delta:.2f} delta | {open_interest} OI / {volume} vol"
        ]

        decision.score_details = {
            'annualized': round(_score_positive_metric(annualized_return_raw, 24) * 100, 1),
            'upside': decision.upside_score,
            'liquidity': decision.liquidity_score,
            'delta_fit': decision.delta_score,
            'otm_fit': decision.otm_score,
            'cost_basis_fit': decision.cost_basis_score,
            'iv_adjusted': decision.iv_adjusted_score,
            'theta_delta': decision.tdr_score,
            'expected_value': decision.ev_score,
            'iv_environment': _clamp((iv_env_adjustment + 20) / 40) * 100,
        }

        decision.expected_move_buffer = _compute_expected_move_buffer(decision)
        decision.recommended_contracts = _compute_recommended_contracts(decision, portfolio_context)
        if is_long_research:
            decision.warnings.append('Research-only long call signal - user executes manually')

    elif option_type == 'PUT':
        if stock_price <= 0 or strike >= stock_price:
            return _create_failed_decision(ticker, 'PUT', strike, expiration, f"Strike {strike} not below stock price {stock_price}")

        otm_pct = ((stock_price - strike) / stock_price) * 100
        min_otm_pct = _coerce_optional_float(profile.get('min_otm_pct'))
        max_otm_pct = _coerce_optional_float(profile.get('max_otm_pct'))
        if min_otm_pct is not None and otm_pct < min_otm_pct:
            return _create_failed_decision(
                ticker,
                'PUT',
                strike,
                expiration,
                f"CSP OTM below target range: {otm_pct:.1f}% < {min_otm_pct:.0f}%",
                ['outside_csp_otm_range'],
            )
        if max_otm_pct is not None and otm_pct > max_otm_pct:
            return _create_failed_decision(
                ticker,
                'PUT',
                strike,
                expiration,
                f"CSP OTM above target range: {otm_pct:.1f}% > {max_otm_pct:.0f}%",
                ['outside_csp_otm_range'],
            )
        cash_required = strike * 100

        # Cash-secured puts require true CSP cash, not margin buying power.
        if cash_required > cash_available_for_csp:
            if not research_only_mode:
                return _create_failed_decision(ticker, 'PUT', strike, expiration, f"Insufficient cash: requires ${cash_required}, CSP cash available ${cash_available_for_csp:.0f} (open short-put collateral ${cash_reserved_for_csp:.0f}; broker buying power ${broker_buying_power:.0f})")
            decision.warnings.append(
                f'Research-only: requires ${cash_required:.0f} cash, CSP cash available ${cash_available_for_csp:.0f}'
            )

        breakeven = strike - mid_price
        breakeven_buffer_pct = ((stock_price - breakeven) / stock_price) * 100 if stock_price > 0 else 0
        capital_fit = 0.0 if cash_available_for_csp <= 0 else _clamp(cash_available_for_csp / cash_required)

        capital_efficiency = 0.0
        ce_score = 0.0
        account_value = max(account_value, 1)
        if account_value > 0 and cash_required > 0:
            capital_efficiency = annualized_return_raw / (cash_required / account_value)
            ce_score = _score_positive_metric(
                capital_efficiency, profile.get('target_capital_efficiency', 100)
            ) * 100

        decision.otm_pct = round(otm_pct, 2)
        decision.breakeven = round(breakeven, 2)
        decision.breakeven_buffer_pct = round(breakeven_buffer_pct, 2)
        decision.cash_required = round(cash_required, 2)
        decision.max_contracts = max(int(cash_available_for_csp // cash_required), 0)
        decision.capital_fit = round(capital_fit * 100, 1)
        decision.ce_score = round(ce_score, 1)
        decision.capital_efficiency = round(capital_efficiency, 1)
        decision.otm_score = _score_proximity(
            otm_pct, profile.get('default_otm_pct', 10), max(profile.get('default_otm_pct', 10) * 0.75, 6)
        ) * 100
        decision.buffer_score = _score_positive_metric(
            breakeven_buffer_pct, max(10, 8)
        ) * 100

        # Earnings risk score from earnings_adjustment (e.g., -30 → 70, 0 → 100)
        earnings_risk_score = _clamp((100 + earnings_adjustment) / 100) * 100

        # PUT base score — growth-oriented weights (premium, theta decay, EV, liquidity, buffer, capital efficiency)
        w_iv_adj = PUT_W_IV_ADJ
        w_tdr = PUT_W_TDR
        w_ev = PUT_W_EV
        w_liq = PUT_W_LIQ
        w_buf = PUT_W_BUF
        w_ce = PUT_W_CE
        w_earnings = PUT_W_EARNINGS
        w_delta = PUT_W_DELTA
        total_w = w_iv_adj + w_tdr + w_ev + w_liq + w_buf + w_ce + w_earnings + w_delta
        if total_w > 0:
            w_iv_adj /= total_w
            w_tdr /= total_w
            w_ev /= total_w
            w_liq /= total_w
            w_buf /= total_w
            w_ce /= total_w
            w_earnings /= total_w
            w_delta /= total_w

        base_score = (
            decision.iv_adjusted_score * w_iv_adj +
            decision.tdr_score * w_tdr +
            decision.ev_score * w_ev +
            decision.liquidity_score * w_liq +
            decision.buffer_score * w_buf +
            decision.ce_score * w_ce +
            earnings_risk_score * w_earnings +
            decision.delta_score * w_delta
        )

        iv_adjusted_score_final = base_score * (1 + iv_env_adjustment / 100)
        score = iv_adjusted_score_final
        score *= (0.75 + (0.25 * capital_fit))
        score *= macro_multiplier

        decision.contract_score = round(_clamp(score / 100) * 100, 2)
        decision.size_fit = _compute_size_fit(decision, portfolio_context)

        # Warnings
        if spread_pct > profile.get('ideal_spread_pct', 12):
            decision.warnings.append(f'Wide bid/ask spread ({spread_pct:.0f}%)')
        if open_interest < profile.get('ideal_open_interest', 500):
            decision.warnings.append('Below ideal open interest')
        if cash_available_for_csp > 0 and cash_required > cash_available_for_csp:
            decision.warnings.append(f'Cash required (${cash_required:.0f}) exceeds CSP cash available (${cash_available_for_csp:.0f})')
        if iv_status_str == 'extreme_low':
            decision.warnings.append(f'IV extremely low ({decision.iv_rank:.0f}%) - poor risk/reward')
        elif iv_status_str == 'low':
            decision.warnings.append(f'IV below average ({decision.iv_rank:.0f}%)')
        elif iv_status_str == 'extreme_high':
            decision.warnings.append(f'IV extremely high ({decision.iv_rank:.0f}%) - excellent premium')
        if earnings_info.get('warning_level') == 'today':
            decision.warnings.append('EARNINGS TODAY - extreme risk')
        elif earnings_info.get('warning_level') == 'very_soon':
            decision.warnings.append(f'Earnings in {earnings_info.get("days_to_earnings")}d - high assignment risk')
        elif earnings_info.get('warning_level') == 'soon':
            decision.warnings.append(f'Earnings in {earnings_info.get("days_to_earnings")} days')
        if macro_multiplier < 1.0:
            decision.warnings.append(f"{macro_regime.get('summary', 'Macro headwinds')}")

        # Rationale
        decision.rationale = [
            f"{decision.annualized_return:.1f}% ann. yield (IV-adj: {iv_adjusted_return:.1f}, rank: {iv_rank*100:.0f}%)",
            f"Theta/Delta: {decision._theta_delta_ratio:.4f} | EV: ${expected_value:.2f} | CapEff: {capital_efficiency:.1f}",
            f"{otm_pct:.1f}% OTM, {breakeven_buffer_pct:.1f}% buffer | Profile: {profile.get('profile_type', 'monthly')}"
        ]

        decision.score_details = {
            'annualized': round(_score_positive_metric(annualized_return_raw, 18) * 100, 1),
            'buffer': decision.buffer_score,
            'liquidity': decision.liquidity_score,
            'delta_fit': decision.delta_score,
            'otm_fit': decision.otm_score,
            'capital_fit': decision.capital_fit,
            'iv_adjusted': decision.iv_adjusted_score,
            'theta_delta': decision.tdr_score,
            'expected_value': decision.ev_score,
            'capital_efficiency': decision.ce_score,
            'iv_environment': _clamp((iv_env_adjustment + 20) / 40) * 100,
        }

        decision.expected_move_buffer = _compute_expected_move_buffer(decision)
        decision.recommended_contracts = _compute_recommended_contracts(decision, portfolio_context)
        if is_long_research:
            decision.warnings.append('Research-only long put signal - user executes manually')

    else:
        return _create_failed_decision(ticker, option_type, strike, expiration, "Unknown option type")

    # -- Growth-aware scoring (always-on) ----------------------------------
    _apply_growth_scoring(
        decision=decision,
        portfolio_context=portfolio_context,
        option=option,
        premium_per_contract=premium_per_contract,
        delta=delta,
        stock_price=stock_price,
        strike=strike,
        option_type=option_type,
        annualized_return_raw=annualized_return_raw,
        from_yfinance=from_yfinance,
        growth_profile=growth_profile,
        ticker=ticker,
    )

    # Risk-budget gate: penalize trade scores that consume too much drawdown budget.
    # Trades using >25% of the budget are progressively penalized so ranking
    # reflects contribution-to-2x within the account's risk envelope.
    _rbp = decision.risk_budget_used_pct
    if _rbp > 25:
        _penalty = max(0.0, 1.0 - ((_rbp - 25) / 150))
        decision.contract_score = round(decision.contract_score * _penalty, 2)

    if logger.isEnabledFor(logging.INFO):
        logger.info(
            "score_contract ticker=%s type=%s strike=%.2f exp=%s dte=%d score=%.1f "
            "premium=%.2f delta=%.3f iv=%.2f blockers=%s",
            ticker, option_type, strike, expiration, dte,
            decision.contract_score, premium_per_contract, delta, implied_volatility,
            decision.hard_blockers or [],
        )
    return decision


def score_existing_position(
    ticker: str,
    position_data: dict,
    current_stock_price: float,
    portfolio_context: dict,
    iv_env_adjustment: float = 0.0,
    iv_rank: float = 0.0,
    iv_status_str: str = "normal",
    earnings_adjustment: float = 0.0,
    earnings_info: dict | None = None,
    macro_regime: dict | None = None,
) -> WheelDecision:
    """
    Score an existing open option position for roll/hold/close decisions.

    Unlike score_contract(), this works with position data (entry details,
    current market price, etc.) rather than candidate contracts.
    """
    earnings_info = earnings_info or {}
    macro_regime = macro_regime or {}

    option_type = str(position_data.get('option_type', '') or '').upper()
    strike = float(position_data.get('strike', 0) or 0)
    expiration = str(position_data.get('expiration', '') or '')
    dte = int(position_data.get('dte', 0) or 0)

    # Current market data
    bid = float(position_data.get('bid', 0) or 0)
    ask = float(position_data.get('ask', 0) or 0)
    last = float(position_data.get('last', 0) or 0)
    mid_price = _calculate_mid_price(bid, ask, last)
    premium_per_contract = mid_price * 100

    # Greeks
    delta = float(position_data.get('delta', 0) or 0)
    theta = float(position_data.get('theta', 0) or 0)
    iv = _normalize_iv(position_data.get('implied_volatility', 0))

    # Extrinsic value approximation: option price - intrinsic
    if option_type == 'CALL':
        intrinsic = max(current_stock_price - strike, 0)
    else:
        intrinsic = max(strike - current_stock_price, 0)
    extrinsic = max(mid_price - intrinsic, 0)

    # OTM %
    if current_stock_price > 0:
        if option_type == 'CALL':
            otm_pct = ((strike - current_stock_price) / current_stock_price) * 100
        else:
            otm_pct = ((current_stock_price - strike) / current_stock_price) * 100
    else:
        otm_pct = 0.0

    decision = WheelDecision(
        ticker=ticker,
        option_type=option_type,
        strike=strike,
        expiration=expiration,
        dte=dte,
        stock_price=current_stock_price,
        bid=bid,
        ask=ask,
        mid_price=round(mid_price, 4),
        premium_per_contract=round(premium_per_contract, 2),
        delta=round(delta, 5),
        theta=round(theta, 5),
        implied_volatility=round(iv, 2),
        extrinsic_remaining=round(extrinsic, 2),
        otm_pct=round(otm_pct, 2),
        iv_rank=round(iv_rank * 100, 1),
        iv_status=iv_status_str,
        iv_env_adjustment=iv_env_adjustment,
        earnings_adjustment=earnings_adjustment,
        vix_regime=portfolio_context.get('vix_regime', {}).get('regime', 'normal'),
        vix_level=portfolio_context.get('vix_regime', {}).get('vix', 20.0),
    )

    # Compute roll pressure
    decision.roll_pressure = _compute_roll_pressure(decision)

    # Compute profit target progress
    decision.profit_target_progress = _compute_profit_target_progress(decision)

    # Size fit
    decision.size_fit = _compute_size_fit(decision, portfolio_context)

    # Expected move buffer
    decision.expected_move_buffer = _compute_expected_move_buffer(decision)

    # Simple warnings
    if decision.dte <= 7:
        decision.warnings.append(f'Only {decision.dte} DTE remaining')
    if decision.roll_pressure >= 70:
        decision.warnings.append(f'High roll pressure ({decision.roll_pressure:.0f}%)')
    if otm_pct < 5 and otm_pct >= 0:
        decision.warnings.append(f'Approaching strike ({otm_pct:.1f}% OTM)')
    elif otm_pct < 0:
        decision.warnings.append(f'Strike crossed ({abs(otm_pct):.1f}% ITM)')

    logger.info(
        "score_existing_position ticker=%s type=%s strike=%.2f exp=%s dte=%d "
        "roll_pressure=%.1f profit_progress=%.1f extrinsic=%.2f",
        ticker, option_type, strike, expiration, dte,
        decision.roll_pressure, decision.profit_target_progress, extrinsic,
    )
    return decision


# ---------------------------------------------------------------------------
# Extracted helpers to reduce god-function size (Punchlist #14)
# ---------------------------------------------------------------------------

def _is_quote_stale(option: dict, from_yfinance: bool) -> bool:
    """Determine if a quote is stale based on timestamp and data source."""
    quote_ts = option.get('quote_timestamp')
    if not quote_ts:
        return False
    try:
        ts = datetime.fromisoformat(quote_ts) if isinstance(quote_ts, str) else quote_ts
        return (datetime.now() - ts).total_seconds() > STALE_QUOTE_THRESHOLD
    except (ValueError, TypeError):
        return False


def _apply_growth_scoring(
    decision: WheelDecision,
    portfolio_context: dict,
    option: dict,
    premium_per_contract: float,
    delta: float,
    stock_price: float,
    strike: float,
    option_type: str,
    annualized_return_raw: float,
    from_yfinance: bool,
    growth_profile: dict | None = None,
    ticker: str = '',
) -> None:
    """
    Apply growth-aware scoring to a decision: stress loss, risk budget,
    confidence score, covered call intent, and score rationale.
    Always computed regardless of option type or profile.
    Mutates decision in-place.
    """
    account_value = float(portfolio_context.get('account_value', 0) or 0)
    cash_balance = float(portfolio_context.get('cash_balance', 0) or 0)
    growth_obj = growth_profile or {}
    max_drawdown_pct = float(growth_obj.get('max_drawdown_pct', 0.40))

    stress_loss = compute_stress_loss(
        premium_per_contract=premium_per_contract,
        abs_delta=abs(delta),
        stock_price=stock_price,
        strike=strike,
        option_type=option_type,
        num_contracts=max(decision.recommended_contracts or decision.max_contracts, 1),
    )
    decision.stress_loss = stress_loss

    risk_budget = compute_risk_budget_used(
        stress_loss=stress_loss,
        account_value=max(account_value, cash_balance, ACCOUNT_VALUE_MIN),
        max_drawdown_pct=max_drawdown_pct,
    )
    decision.risk_budget_used_pct = risk_budget

    is_stale = _is_quote_stale(option, from_yfinance)

    decision.confidence_score = compute_confidence_score(
        data_source=decision.price_source,
        has_yfinance_fallback=from_yfinance,
        is_stale=is_stale,
        spread_pct=decision.spread_pct,
        open_interest=option.get('open_interest', 0),
        has_iv=decision.implied_volatility > 0,
        has_greeks=abs(decision.delta) > 0.001,
    )

    if option_type == 'CALL':
        shares_owned = float(portfolio_context.get('positions', {}).get(ticker, {}).get('position', 0) or 0)
        avg_cost = float(portfolio_context.get('positions', {}).get(ticker, {}).get('avg_cost', 0) or 0)
        decision.covered_call_intent = classify_covered_call_intent(
            strike=strike,
            stock_price=stock_price,
            premium_per_contract=premium_per_contract,
            annualized_return=annualized_return_raw,
            shares_owned=int(shares_owned),
            avg_cost=avg_cost,
        )

        if decision.covered_call_intent == "upside-capping risk" and annualized_return_raw < 12:
            decision.warnings.append('Low-premium CC caps upside without meaningful 2x acceleration')
        elif decision.covered_call_intent == "income" and annualized_return_raw < 6:
            decision.warnings.append('Low premium relative to growth target')

    income_per_month = premium_per_contract * 4
    target_multiple = float(growth_obj.get('target_account_multiple', 2.0))
    decision.remaining_gap_to_target = estimate_target_gap(
        account_value=max(account_value, cash_balance, ACCOUNT_VALUE_MIN),
        target_multiple=target_multiple,
        current_premium_income=income_per_month,
        projected_months=1,
    )

    rationale_parts = []
    if decision.contract_score >= 70:
        rationale_parts.append("Strong growth candidate")
    elif decision.contract_score >= 50:
        rationale_parts.append("Moderate growth contribution")
    else:
        rationale_parts.append("Limited growth impact")

    if decision.risk_budget_used_pct > 0:
        rationale_parts.append(f"Uses {decision.risk_budget_used_pct:.0f}% of drawdown budget")
    if decision.covered_call_intent:
        rationale_parts.append(f"CC intent: {decision.covered_call_intent}")
    if decision.confidence_score < 60:
        rationale_parts.append("Low confidence — verify data")

    decision.score_rationale = " | ".join(rationale_parts) if rationale_parts else ""
