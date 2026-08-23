"""
Pure scoring factor functions and shared sub-score computations.

Extracted from wheel_decision.py to reduce god-file size.
All functions are stateless helpers with no service dependencies.
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# US equity options trade on exchanges quoting in America/New_York time.
NY_TZ = ZoneInfo("America/New_York")

# ── Named constants for score thresholds ─────────────────────────────────
# Only thresholds with live consumers live here; preset values come from
# core/presets.py and cache TTLs from their call sites.

# Score weights — tuned toward contribution-to-2x objective
CALL_W_IV_ADJ = 0.30
CALL_W_TDR = 0.15
CALL_W_LIQ = 0.15
CALL_W_EV = 0.10
CALL_W_UPSIDE = 0.10
CALL_W_OTM = 0.10
CALL_W_EARNINGS = 0.05
CALL_W_CE = 0.10

PUT_W_IV_ADJ = 0.30
PUT_W_TDR = 0.15
PUT_W_EV = 0.10
PUT_W_LIQ = 0.15
PUT_W_BUF = 0.10
PUT_W_CE = 0.20
PUT_W_EARNINGS = 0.05
PUT_W_DELTA = 0.05

# Max loss estimates
PUT_MAX_LOSS_ESTIMATE_PCT = 0.10

STALE_QUOTE_THRESHOLD = 300  # seconds

# Capital efficiency
ACCOUNT_VALUE_MIN = 1

# Roll pressure
ROLL_THETA_HIGH_THRESHOLD = 0.50  # daily theta decay ($) considered high


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


def parse_broker_timestamp(ts: object) -> "datetime | None":
    """Parse a Moomoo broker ``update_time`` into a tz-aware datetime (NY).

    Moomoo timestamps are quoted in the US exchange local time (Eastern).
    Naive strings are localized to ``America/New_York``; already-aware values
    are converted. Returns ``None`` when the value is missing or unparseable
    so callers can fail closed.
    """
    if ts is None:
        return None
    if isinstance(ts, datetime):
        dt = ts
    else:
        text = str(ts).strip()
        if not text:
            return None
        candidate_formats = (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y/%m/%d %H:%M:%S",
        )
        dt = None
        for fmt in candidate_formats:
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            # Last resort: let Python try ISO parsing.
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY_TZ)
    return dt.astimezone(NY_TZ)


def is_crossed_market(bid: float, ask: float) -> bool:
    """Return True when the ask is below the bid (invalid broker quote)."""
    try:
        return float(ask or 0) < float(bid or 0)
    except (TypeError, ValueError):
        return False


def quote_is_stale(
    update_time: object,
    as_of_utc: "datetime | None" = None,
    max_age_sec: "int | None" = None,
) -> bool:
    """Return True when the broker quote timestamp is missing/invalid/stale.

    ``as_of_utc`` should be the current UTC time; defaults to now. A quote with
    no parseable broker time, or older than ``max_age_sec``, is stale.
    """
    if max_age_sec is None:
        max_age_sec = STALE_QUOTE_THRESHOLD
    parsed = parse_broker_timestamp(update_time)
    if parsed is None:
        return True
    if as_of_utc is None:
        as_of_utc = datetime.now(timezone.utc)
    # Normalize the reference instant to NY for an apples-to-apples comparison.
    as_of_ny = as_of_utc.astimezone(NY_TZ)
    return (as_of_ny - parsed).total_seconds() > max_age_sec


def classify_event_tier(earnings_info: dict | None, dte: float, security_type: str = "stock") -> str:
    """Classify event evidence without creating an actionable external signal."""
    kind = str(security_type or "stock").strip().lower()
    if kind in {"etf", "index"}:
        return "event_not_applicable"
    if not isinstance(earnings_info, dict):
        return "event_unknown"
    if earnings_info.get("data_stale") or earnings_info.get("fetch_status") not in {"success"}:
        return "event_unknown"
    try:
        days = int(earnings_info.get("days_to_earnings"))
        days_to_expiry = int(dte)
    except (TypeError, ValueError):
        return "event_unknown"
    if days > days_to_expiry:
        return "event_safe"
    if 0 <= days <= days_to_expiry:
        return "earnings_before_expiry"
    return "event_unknown"


def premium_velocity_per_day(premium_per_contract: float, dte: float) -> float:
    """Return premium velocity as premium dollars per day to expiration."""
    try:
        premium = float(premium_per_contract or 0)
        days = float(dte or 0)
    except (TypeError, ValueError):
        return 0.0
    if premium <= 0 or days <= 0:
        return 0.0
    velocity = premium / days
    logger.debug("premium_velocity ticker=NA premium=%.2f dte=%.0f velocity=%.4f", premium, days, velocity)
    return velocity


def _compute_shared_subscores(decision, profile: dict, growth_mode_weights: dict | None = None) -> None:
    """
    Compute all sub-scores that are shared between CALL and PUT.
    Operates in-place on the WheelDecision.
    When growth_mode_weights is provided, sub-score computations tilt toward
    growth-oriented priorities (premium, capital efficiency, theta decay).
    """
    _growth_w = growth_mode_weights or {}
    _growth_active = bool(_growth_w.get("enabled", True))
    decision.oi_score = _score_positive_metric(decision.open_interest, profile["ideal_open_interest"]) * 100
    decision.volume_score = _score_positive_metric(decision.volume, profile["ideal_volume"]) * 100
    decision.spread_score = _clamp(1 - (decision.spread_pct / max(profile["ideal_spread_pct"], 1)), 0, 1) * 100

    if _growth_active:
        # Growth-tuned liquidity: volume matters more (ease of entry/exit)
        liquidity_raw = (decision.oi_score * 0.35 + decision.volume_score * 0.35 + decision.spread_score * 0.30) / 100
    else:
        liquidity_raw = (decision.oi_score * 0.45 + decision.volume_score * 0.2 + decision.spread_score * 0.35) / 100
    liq_mult = profile.get("liquidity_weight_multiplier", 1.0)
    decision.liquidity_score = _clamp(liquidity_raw * liq_mult) * 100

    if decision.iv_adjusted_return > 0:
        target_iv_adj = profile.get("target_iv_adjusted", 50)
        if _growth_active:
            target_iv_adj = max(target_iv_adj, 60)  # higher bar for IV-adjusted return in growth mode
        decision.iv_adjusted_score = _score_positive_metric(decision.iv_adjusted_return, target_iv_adj) * 100

    if decision.stock_price > 0 and abs(decision.delta) > 0:
        decision._theta_delta_ratio = abs(decision.theta) / (abs(decision.delta) * decision.stock_price)
    decision.tdr_score = (
        _score_positive_metric(decision._theta_delta_ratio, profile.get("target_theta_delta_ratio", 0.005)) * 100
    )

    if decision.premium_per_contract > 0:
        decision.ev_score = _clamp(decision.expected_value / max(decision.premium_per_contract, 0.01)) * 100

    logger.debug(
        "shared_subscores ticker=%s option_type=%s dte=%d liquidity=%.1f iv_adj=%.1f delta_score=%.1f dte_score=%.1f",
        getattr(decision, "ticker", "?"),
        getattr(decision, "option_type", "?"),
        decision.dte,
        decision.liquidity_score,
        decision.iv_adjusted_score,
        decision.delta_score,
        decision.dte_score,
    )
    decision.delta_score = (
        _score_proximity(abs(decision.delta), profile["target_delta"], profile["delta_tolerance"]) * 100
    )
    decision.dte_score = (
        _score_proximity(decision.dte, profile["preferred_dte"], max(profile["preferred_dte"], 10)) * 100
    )

    desired_otm = profile.get("default_otm_pct", 10)
    decision.otm_score = _score_proximity(decision.otm_pct, desired_otm, max(desired_otm * 0.75, 6)) * 100


def _compute_roll_pressure(decision) -> float:
    """
    Compute roll_pressure (0-100) for an open position.

    Combines DTE remaining, % distance to strike, extrinsic value remaining,
    and theta decay rate. Higher theta means higher time decay urgency.
    """
    dte_component = _clamp(1 - (decision.dte / 45)) * 100 if decision.dte >= 0 else 100

    if decision.option_type == "CALL":
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

    theta_abs = abs(decision.theta)
    if theta_abs >= 1.0:
        theta_component = 100
    elif theta_abs >= ROLL_THETA_HIGH_THRESHOLD:
        theta_component = 50 + _clamp((theta_abs - ROLL_THETA_HIGH_THRESHOLD) / 0.50) * 50
    elif theta_abs > 0:
        theta_component = _clamp(theta_abs / ROLL_THETA_HIGH_THRESHOLD) * 50
    else:
        theta_component = 0

    pressure = dte_component * 0.35 + distance_component * 0.40 + extrinsic_component * 0.10 + theta_component * 0.15
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
    if decision.option_type == "CALL":
        shares_owned = float(portfolio_context.get("positions", {}).get(decision.ticker, {}).get("position", 0) or 0)
        if shares_owned <= 0:
            return 0.0
        needed = decision.max_contracts * 100
        if needed <= 0:
            return 0.0
        fit = _clamp(shares_owned / needed) * 100
    else:
        cash_balance = float(portfolio_context.get("cash_balance", 0) or 0)
        available_cash = float(
            portfolio_context.get("cash_available_for_csp", portfolio_context.get("available_cash", cash_balance)) or 0
        )
        if decision.cash_required <= 0:
            return 50.0
        if available_cash <= 0:
            return 0.0
        fit = _clamp(available_cash / decision.cash_required) * 100

    return round(fit, 1)


def _compute_recommended_contracts(decision, portfolio_context: dict) -> int:
    """
    Compute recommended number of contracts based on account value,
    risk allocation, and contract-specific constraints.

    Aims for no more than 10% of account value per position for PUTs,
    and all available shares for CALLs (subject to diversification).
    """
    account_value = max(float(portfolio_context.get("account_value", 0) or 0), ACCOUNT_VALUE_MIN)
    max(len(portfolio_context.get("positions", {})), 1)
    max_contracts = max(int(decision.max_contracts or 0), 0)
    if max_contracts <= 0:
        return 0

    if decision.option_type == "CALL":
        return max_contracts

    cash_required = max(decision.cash_required, 1)
    position_target = account_value * 0.10
    by_value = max(int(position_target / cash_required), 1)
    by_available = max_contracts

    recommended = min(by_value, by_available)
    return max(recommended, 1)


def _compute_expected_move_buffer(decision) -> float:
    """
    Compute expected move buffer (%).

    Uses IV and DTE to estimate the 1-standard-deviation expected move,
    then compares it to the current OTM distance.
    """
    if decision.stock_price <= 0 or decision.implied_volatility <= 0 or decision.dte <= 0:
        return 0.0

    # Normalize IV to decimal form (safety net for Moomoo percentage format)
    iv = decision.implied_volatility
    if iv > 3.0:
        iv = iv / 100.0

    expected_move = decision.stock_price * iv * ((decision.dte / 365) ** 0.5)
    expected_move_pct = (expected_move / decision.stock_price) * 100
    buffer = decision.otm_pct - expected_move_pct
    return round(buffer, 1)
