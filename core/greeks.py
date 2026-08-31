"""
Black-Scholes Greeks calculator for options.

Computes delta, gamma, theta, vega, and implied volatility using scipy.
Falls back to yfinance for implied volatility when other sources return 0.
"""

import logging
import math
from datetime import date, datetime

from scipy.stats import norm

from core.connection_constants import _normalize_iv

logger = logging.getLogger(__name__)

RISK_FREE_RATE = 0.05  # Approximate current risk-free rate


def compute_bs_greeks(
    stock_price: float,
    strike: float,
    time_to_expiry_years: float,
    iv: float,
    option_type: str,
    risk_free_rate: float = RISK_FREE_RATE,
) -> tuple[float, float, float, float]:
    """
    Compute Black-Scholes delta, gamma, theta (per day), vega (per 1% IV change).

    Args:
        stock_price: Current underlying price (S)
        strike: Option strike price (K)
        time_to_expiry_years: Time to expiration in years (T)
        iv: Implied volatility (sigma, as decimal e.g. 0.30 for 30%)
        option_type: 'CALL' or 'PUT'
        risk_free_rate: Risk-free interest rate (r), default 5%

    Returns:
        Tuple of (delta, gamma, theta_per_day, vega_per_1pct)
    """
    option_type = option_type.upper()
    sigma = _normalize_iv(iv)
    S, K, T, r = stock_price, strike, max(time_to_expiry_years, 1 / 365), risk_free_rate

    if sigma <= 0 or S <= 0 or K <= 0:
        return 0.0, 0.0, 0.0, 0.0

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    # Common components
    nd1 = norm.pdf(d1)  # Standard normal PDF at d1
    Nd1 = norm.cdf(d1)  # Standard normal CDF at d1

    # Delta
    if option_type == "CALL":
        delta = Nd1
    else:
        delta = Nd1 - 1.0

    # Gamma (same for calls and puts)
    gamma = nd1 / (S * sigma * sqrt_T) if S > 0 and sigma > 0 and sqrt_T > 0 else 0.0

    # Vega (per 1 percentage point change in IV, i.e. divide by 100)
    vega = S * nd1 * sqrt_T / 100.0

    # Theta (per calendar day, i.e. divide by 365)
    if option_type == "CALL":
        theta_per_year = -(S * nd1 * sigma) / (2.0 * sqrt_T) - r * K * math.exp(-r * T) * norm.cdf(d2)
    else:
        theta_per_year = -(S * nd1 * sigma) / (2.0 * sqrt_T) + r * K * math.exp(-r * T) * norm.cdf(-d2)
    theta_per_day = theta_per_year / 365.0

    return delta, gamma, theta_per_day, vega


def enrich_option_with_greeks(
    option: dict,
    stock_price: float,
) -> dict:
    """
    Fill in missing Greeks (delta, gamma, theta, vega) for an option dict
    using Black-Scholes when implied_volatility is available but Greeks are 0.

    Mutates the option dict in-place AND returns it for convenience.
    """
    # Get current values (normalize IV as safety net)
    iv = _normalize_iv(option.get("implied_volatility", 0))
    delta = float(option.get("delta", 0) or 0)
    float(option.get("gamma", 0) or 0)
    theta = float(option.get("theta", 0) or 0)
    float(option.get("vega", 0) or 0)

    # If we already have non-zero delta, assume other Greeks are also valid
    if abs(delta) > 0.001 and abs(theta) > 0.0001:
        return option

    # Need IV to compute Greeks
    if iv <= 0:
        return option

    strike = float(option.get("strike", 0) or 0)
    expiration = str(option.get("expiration", "") or "")
    option_type = str(option.get("option_type", "") or "").upper()

    if not all([strike > 0, expiration, stock_price > 0]):
        return option

    # Calculate time to expiry in years
    try:
        expiry_date = datetime.strptime(expiration, "%Y%m%d").date()
        today = date.today()
        dte = max((expiry_date - today).days, 1)
        T = dte / 365.0
    except (ValueError, TypeError):
        return option

    try:
        new_delta, new_gamma, new_theta, new_vega = compute_bs_greeks(
            stock_price=stock_price,
            strike=strike,
            time_to_expiry_years=T,
            iv=iv,
            option_type=option_type,
        )

        # Only overwrite if the computed value is meaningful
        if abs(new_delta) > 0.001:
            option["delta"] = round(new_delta, 5)
        if abs(new_gamma) > 0.0001:
            option["gamma"] = round(new_gamma, 5)
        if abs(new_theta) > 0.0001:
            option["theta"] = round(new_theta, 5)
        if abs(new_vega) > 0.0001:
            option["vega"] = round(new_vega, 5)
        option["greeks_source"] = "Black-Scholes computed"
        logger.info(
            "Computed BS Greeks for strike=%.2f exp=%s type=%s S=%.2f IV=%.2f "
            "delta=%.4f gamma=%.4f theta=%.4f vega=%.4f",
            strike,
            expiration,
            option_type,
            stock_price,
            iv,
            new_delta,
            new_gamma,
            new_theta,
            new_vega,
        )
    except Exception as e:
        logger.debug(f"BS Greeks computation failed: {e}")

    return option
