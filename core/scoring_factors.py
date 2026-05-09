"""
Pure scoring factor functions and shared sub-score computations.

Extracted from wheel_decision.py to reduce god-file size.
All functions are stateless helpers with no service dependencies.
"""

import logging

logger = logging.getLogger(__name__)


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _score_proximity(value: float, target: float, tolerance: float) -> float:
    if tolerance <= 0:
        return 0.0
    return _clamp(1 - (abs(value - target) / tolerance))


def _score_positive_metric(value: float, ideal_value: float) -> float:
    if ideal_value <= 0:
        return 0.0
    return _clamp(value / ideal_value)


def _calculate_mid_price(bid: float, ask: float, last: float = 0.0) -> float:
    bid = float(bid or 0)
    ask = float(ask or 0)
    last = float(last or 0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    if bid > 0:
        return bid
    if ask > 0:
        return ask
    if last > 0:
        return last
    return 0.0


def _compute_shared_subscores(decision, profile: dict) -> None:
    """
    Compute all sub-scores that are shared between CALL and PUT.
    Operates in-place on the WheelDecision.
    """
    decision.oi_score = _score_positive_metric(decision.open_interest, profile['ideal_open_interest']) * 100
    decision.volume_score = _score_positive_metric(decision.volume, profile['ideal_volume']) * 100
    decision.spread_score = _clamp(1 - (decision.spread_pct / max(profile['ideal_spread_pct'], 1)), 0, 1) * 100

    liquidity_raw = (
        decision.oi_score * 0.45 +
        decision.volume_score * 0.2 +
        decision.spread_score * 0.35
    ) / 100
    liq_mult = profile.get('liquidity_weight_multiplier', 1.0)
    decision.liquidity_score = _clamp(liquidity_raw * liq_mult) * 100

    if decision.iv_adjusted_return > 0:
        decision.iv_adjusted_score = _score_positive_metric(
            decision.iv_adjusted_return, profile.get('target_iv_adjusted', 50)
        ) * 100

    if decision.stock_price > 0 and abs(decision.delta) > 0:
        decision._theta_delta_ratio = abs(decision.theta) / (abs(decision.delta) * decision.stock_price)
    decision.tdr_score = _score_positive_metric(
        decision._theta_delta_ratio, profile.get('target_theta_delta_ratio', 0.005)
    ) * 100

    if decision.premium_per_contract > 0:
        decision.ev_score = _clamp(decision.expected_value / max(decision.premium_per_contract, 0.01)) * 100

    decision.delta_score = _score_proximity(
        abs(decision.delta), profile['target_delta'], profile['delta_tolerance']
    ) * 100
    decision.dte_score = _score_proximity(
        decision.dte, profile['preferred_dte'], max(profile['preferred_dte'], 10)
    ) * 100

    desired_otm = 10
    decision.otm_score = _score_proximity(
        decision.otm_pct, desired_otm, max(desired_otm * 0.75, 6)
    ) * 100


def _compute_roll_pressure(decision) -> float:
    """
    Compute roll_pressure (0-100) for an open position.

    Combines DTE remaining, % distance to strike, extrinsic value remaining.
    """
    dte_component = _clamp(1 - (decision.dte / 45)) * 100 if decision.dte >= 0 else 100

    if decision.option_type == 'CALL':
        if decision.strike > 0 and decision.stock_price > 0:
            distance_pct = ((decision.strike - decision.stock_price) / decision.strike) * 100
        else:
            distance_pct = 50
    else:
        if decision.strike > 0 and decision.stock_price > 0:
            distance_pct = ((decision.stock_price - decision.strike) / decision.strike) * 100
        else:
            distance_pct = 50

    if distance_pct <= 0:
        distance_component = 100
    elif distance_pct < 10:
        distance_component = _clamp(1 - (distance_pct / 10)) * 100
    else:
        distance_component = 0

    extrinsic = decision.extrinsic_remaining
    if extrinsic <= 0:
        extrinsic_component = 100
    elif extrinsic < 0.10:
        extrinsic_component = _clamp(1 - (extrinsic / 0.10)) * 100
    else:
        extrinsic_component = 0

    pressure = (
        dte_component * 0.35 +
        distance_component * 0.40 +
        extrinsic_component * 0.25
    )
    return round(_clamp(pressure / 100) * 100, 1)


def _compute_profit_target_progress(decision, target_pct: float = 50.0) -> float:
    """
    Compute how close the position is to a profit target (0-100).
    Uses DTE decay as proxy when entry data is unavailable.
    """
    if decision.premium_per_contract <= 0:
        return 0.0
    if decision.dte <= 0:
        return 100.0

    entry_dte_estimate = 30
    progress = _clamp(1 - (decision.dte / entry_dte_estimate)) * 100
    return round(progress, 1)


def _compute_size_fit(decision, portfolio_context: dict) -> float:
    """
    Compute size_fit (0-100): how well the contract fits the portfolio.

    For CALLs: based on shares owned vs contracts needed.
    For PUTs: based on cash available vs cash required.
    """
    if decision.option_type == 'CALL':
        shares_owned = float(portfolio_context.get('positions', {}).get(decision.ticker, {}).get('position', 0) or 0)
        if shares_owned <= 0:
            return 0.0
        needed = decision.max_contracts * 100
        if needed <= 0:
            return 0.0
        fit = _clamp(shares_owned / needed) * 100
    else:
        cash_balance = float(portfolio_context.get('cash_balance', 0) or 0)
        if decision.cash_required <= 0:
            return 50.0
        if cash_balance <= 0:
            return 0.0
        fit = _clamp(cash_balance / decision.cash_required) * 100

    return round(fit, 1)


def _compute_expected_move_buffer(decision) -> float:
    """
    Compute expected move buffer (%).

    Uses IV and DTE to estimate the 1-standard-deviation expected move,
    then compares it to the current OTM distance.
    """
    if decision.stock_price <= 0 or decision.implied_volatility <= 0 or decision.dte <= 0:
        return 0.0

    expected_move = decision.stock_price * decision.implied_volatility * ((decision.dte / 365) ** 0.5)
    expected_move_pct = (expected_move / decision.stock_price) * 100
    buffer = decision.otm_pct - expected_move_pct
    return round(buffer, 1)
