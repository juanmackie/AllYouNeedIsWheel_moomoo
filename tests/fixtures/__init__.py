"""
Test fixtures for score regression testing.
"""

from .scenarios import (
    get_cc_below_cost_basis,
    # Covered call scenarios
    get_cc_healthy,
    # Cash-secured put scenarios
    get_csp_healthy,
    get_csp_low_cash,
    get_earnings_today_scenario,
    get_high_iv_scenario,
    # Environment scenarios
    get_low_iv_scenario,
    get_missing_greeks_scenario,
    # Edge case scenarios
    get_wide_spread_scenario,
    get_yfinance_fallback_scenario,
)

__all__ = [
    "get_cc_healthy",
    "get_cc_below_cost_basis",
    "get_csp_healthy",
    "get_csp_low_cash",
    "get_low_iv_scenario",
    "get_high_iv_scenario",
    "get_wide_spread_scenario",
    "get_earnings_today_scenario",
    "get_missing_greeks_scenario",
    "get_yfinance_fallback_scenario",
]
