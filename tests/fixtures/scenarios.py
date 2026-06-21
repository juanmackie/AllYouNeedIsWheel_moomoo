"""
Score regression test scenarios.

Each function returns a tuple of (option_dict, profile_dict, portfolio_context_dict, expected).
"""

from datetime import datetime, timedelta


def _next_friday(dte=21):
    """Return a YYYYMMDD date string roughly dte days in the future."""
    target = datetime.now() + timedelta(days=dte)
    return target.strftime('%Y%m%d')


# ---------------------------------------------------------------------------
# Covered Call scenarios
# ---------------------------------------------------------------------------

def get_cc_healthy():
    """Covered call: 200 shares AAPL, strike $160, 21 DTE, clean liquidity."""
    future = _next_friday(21)
    option = {
        'strike': 160.0,
        'expiration': future,
        'option_type': 'CALL',
        'bid': 3.50,
        'ask': 3.70,
        'last': 3.60,
        'delta': 0.35,
        'gamma': 0.05,
        'theta': -0.08,
        'vega': 0.20,
        'implied_volatility': 0.30,
        'open_interest': 800,
        'volume': 200,
    }
    profile = _base_profile('monthly')
    portfolio = {
        'positions': {
            'AAPL': {'position': 200, 'avg_cost': 150.0, 'market_price': 155.0}
        },
        'cash_balance': 10000.0,
        'account_value': 50000.0,
        'short_calls': {},
        'short_puts': {},
    }
    expected = {
        'pass': True,
        'min_score': 50,
        'has_warnings': False,
        'option_type': 'CALL',
    }
    return option, profile, portfolio, expected


def get_cc_below_cost_basis():
    """Covered call: strike below cost basis."""
    future = _next_friday(21)
    option = {
        'strike': 160.0,  # Above stock_price (155), below avg_cost (170) -> triggers cost basis warning
        'expiration': future,
        'option_type': 'CALL',
        'bid': 3.50,
        'ask': 3.70,
        'last': 3.60,
        'delta': 0.35,  # Reasonable delta for strike above stock
        'gamma': 0.04,
        'theta': -0.08,
        'vega': 0.18,
        'implied_volatility': 0.30,
        'open_interest': 600,
        'volume': 150,
    }
    profile = _base_profile('monthly')
    portfolio = {
        'positions': {
            'AAPL': {'position': 200, 'avg_cost': 170.0, 'market_price': 155.0}
        },
        'cash_balance': 10000.0,
        'account_value': 50000.0,
        'short_calls': {},
        'short_puts': {},
    }
    expected = {
        'pass': True,
        'min_score': 50,
        'has_warnings': True,
        'warning_contains': 'cost basis',
        'option_type': 'CALL',
    }
    return option, profile, portfolio, expected


# ---------------------------------------------------------------------------
# Cash-Secured Put scenarios
# ---------------------------------------------------------------------------

def get_csp_healthy():
    """CSP: $10k cash, strike $95, 21 DTE, clean liquidity."""
    future = _next_friday(21)
    option = {
        'strike': 95.0,
        'expiration': future,
        'option_type': 'PUT',
        'bid': 2.0,
        'ask': 2.20,
        'last': 2.10,
        'delta': -0.25,
        'gamma': 0.04,
        'theta': -0.06,
        'vega': 0.15,
        'implied_volatility': 0.30,
        'open_interest': 600,
        'volume': 150,
    }
    profile = _base_profile('monthly')
    portfolio = {
        'positions': {},
        'cash_balance': 10000.0,
        'account_value': 50000.0,
        'short_calls': {},
        'short_puts': {},
    }
    expected = {
        'pass': True,
        'min_score': 60,
        'has_warnings': False,
        'option_type': 'PUT',
        'cash_required': 95.0 * 100,
    }
    return option, profile, portfolio, expected


def get_csp_low_cash():
    """CSP: not enough cash for strike * 100."""
    future = _next_friday(21)
    option = {
        'strike': 500.0,
        'expiration': future,
        'option_type': 'PUT',
        'bid': 10.0,
        'ask': 10.50,
        'last': 10.25,
        'delta': -0.30,
        'gamma': 0.03,
        'theta': -0.12,
        'vega': 0.20,
        'implied_volatility': 0.35,
        'open_interest': 300,
        'volume': 100,
    }
    profile = _base_profile('monthly')
    portfolio = {
        'positions': {},
        'cash_balance': 1000.0,  # Not enough for $500 * 100
        'account_value': 20000.0,
        'short_calls': {},
        'short_puts': {},
    }
    expected = {
        'pass': False,  # Should be filtered out
        'option_type': 'PUT',
    }
    return option, profile, portfolio, expected


# ---------------------------------------------------------------------------
# Environment scenarios
# ---------------------------------------------------------------------------

def get_low_iv_scenario():
    """Low IV environment (IV=0.15)."""
    future = _next_friday(21)
    option = {
        'strike': 95.0,
        'expiration': future,
        'option_type': 'PUT',
        'bid': 1.0,
        'ask': 1.20,
        'last': 1.10,
        'delta': -0.15,
        'gamma': 0.03,
        'theta': -0.03,
        'vega': 0.08,
        'implied_volatility': 0.15,
        'open_interest': 400,
        'volume': 100,
    }
    profile = _base_profile('monthly')
    portfolio = {
        'positions': {},
        'cash_balance': 10000.0,
        'account_value': 50000.0,
        'short_calls': {},
        'short_puts': {},
    }
    expected = {
        'pass': True,
        'min_score': 73,
        'has_warnings': True,
        'warning_contains': 'IV',
        'option_type': 'PUT',
    }
    return option, profile, portfolio, expected


def get_high_iv_scenario():
    """High IV environment (IV=0.60)."""
    future = _next_friday(21)
    option = {
        'strike': 90.0,
        'expiration': future,
        'option_type': 'PUT',
        'bid': 5.0,
        'ask': 5.30,
        'last': 5.15,
        'delta': -0.40,
        'gamma': 0.05,
        'theta': -0.15,
        'vega': 0.35,
        'implied_volatility': 0.60,
        'open_interest': 500,
        'volume': 200,
    }
    profile = _base_profile('monthly')
    portfolio = {
        'positions': {},
        'cash_balance': 10000.0,
        'account_value': 50000.0,
        'short_calls': {},
        'short_puts': {},
    }
    expected = {
        'pass': True,
        'min_score': 70,
        'has_warnings': True,
        'warning_contains': 'extremely high',
        'option_type': 'PUT',
    }
    return option, profile, portfolio, expected


# ---------------------------------------------------------------------------
# Edge case scenarios
# ---------------------------------------------------------------------------

def get_wide_spread_scenario():
    """Wide spread: bid=$1.00, ask=$3.00 (spread ~200%)."""
    future = _next_friday(21)
    option = {
        'strike': 95.0,
        'expiration': future,
        'option_type': 'PUT',
        'bid': 1.0,
        'ask': 3.0,
        'last': 2.0,
        'delta': -0.25,
        'gamma': 0.04,
        'theta': -0.06,
        'vega': 0.15,
        'implied_volatility': 0.30,
        'open_interest': 600,
        'volume': 150,
    }
    profile = _base_profile('monthly')
    portfolio = {
        'positions': {},
        'cash_balance': 10000.0,
        'account_value': 50000.0,
        'short_calls': {},
        'short_puts': {},
    }
    expected = {
        'pass': False,  # Spread > max_spread_pct (60%)
        'option_type': 'PUT',
    }
    return option, profile, portfolio, expected


def get_earnings_today_scenario():
    """Earnings today — extreme risk."""
    future = _next_friday(21)
    option = {
        'strike': 95.0,
        'expiration': future,
        'option_type': 'PUT',
        'bid': 2.0,
        'ask': 2.20,
        'last': 2.10,
        'delta': -0.25,
        'gamma': 0.04,
        'theta': -0.06,
        'vega': 0.15,
        'implied_volatility': 0.30,
        'open_interest': 600,
        'volume': 150,
    }
    profile = _base_profile('monthly')
    portfolio = {
        'positions': {},
        'cash_balance': 10000.0,
        'account_value': 50000.0,
        'short_calls': {},
        'short_puts': {},
    }
    expected = {
        'pass': True,
        'min_score': 50,
        'has_warnings': True,
        'warning_contains': 'EARNINGS TODAY',
        'option_type': 'PUT',
    }
    return option, profile, portfolio, expected


def get_missing_greeks_scenario():
    """Missing Greeks — computed via Black-Scholes fallback."""
    future = _next_friday(21)
    option = {
        'strike': 95.0,
        'expiration': future,
        'option_type': 'PUT',
        'bid': 2.0,
        'ask': 2.20,
        'last': 2.10,
        'delta': 0,  # Missing — will be computed
        'gamma': 0,
        'theta': 0,
        'vega': 0,
        'implied_volatility': 0.30,
        'open_interest': 600,
        'volume': 150,
    }
    profile = _base_profile('monthly')
    portfolio = {
        'positions': {},
        'cash_balance': 10000.0,
        'account_value': 50000.0,
        'short_calls': {},
        'short_puts': {},
    }
    expected = {
        'pass': True,
        'min_score': 60,
        'has_warnings': False,
        'option_type': 'PUT',
        'greeks_computed': True,
    }
    return option, profile, portfolio, expected


def get_yfinance_fallback_scenario():
    """yfinance fallback: all data from yfinance path."""
    future = _next_friday(21)
    option = {
        'strike': 95.0,
        'expiration': future,
        'option_type': 'PUT',
        'bid': 2.0,
        'ask': 2.20,
        'last': 2.10,
        'delta': -0.25,
        'gamma': 0.04,
        'theta': -0.06,
        'vega': 0.15,
        'implied_volatility': 0.30,
        'open_interest': 600,
        'volume': 150,
        'from_yfinance': True,
    }
    profile = _base_profile('monthly')
    portfolio = {
        'positions': {},
        'cash_balance': 10000.0,
        'account_value': 50000.0,
        'short_calls': {},
        'short_puts': {},
    }
    expected = {
        'pass': True,
        'min_score': 60,
        'has_warnings': True,
        'warning_contains': 'yfinance',
        'option_type': 'PUT',
    }
    return option, profile, portfolio, expected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_profile(profile_type='monthly'):
    """Return a base screening profile for testing."""
    return {
        'min_mid_price': 0.05,
        'max_spread_pct': 60,
        'min_premium_per_contract': 10,
        'min_open_interest': 10,
        'min_volume': 1,
        'target_iv_adjusted': 50,
        'target_theta_delta_ratio': 0.005,
        'preferred_dte': 21,
        'target_delta': 0.20,
        'delta_tolerance': 0.15,
        'ideal_open_interest': 500,
        'ideal_volume': 100,
        'ideal_spread_pct': 12,
        'liquidity_weight_multiplier': 1.0,
        'profile_type': profile_type,
        'min_dte': 7,
        'max_dte': 45,
        'target_iv_adjusted': 50,
    }
