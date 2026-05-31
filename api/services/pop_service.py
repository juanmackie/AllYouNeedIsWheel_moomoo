"""
Probability of Profit (PoP) Service
Enhanced PoP estimation with Delta-based and Monte Carlo methods.
"""

import logging

logger = logging.getLogger('api.services.pop')

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    logger.warning("numpy not available — Monte Carlo will use naive fallback")
    NUMPY_AVAILABLE = False


def calculate_pop_delta(ticker, strike, expiration, option_type, delta, iv, dte):
    """
    Calculate PoP using delta (fast, reliable).
    PoP = 1 - |delta| for most option types.
    
    Returns:
        dict: {'pop': float, 'method': 'delta', 'details': str}
    """
    if delta is None:
        return {'pop': 0.5, 'method': 'delta', 'details': 'No delta available'}
    
    pop = 1.0 - abs(delta)
    return {
        'pop': round(pop, 4),
        'pop_pct': round(pop * 100, 1),
        'method': 'delta',
        'details': f'PoP = 1 - |{delta:.3f}| = {pop:.4f}',
    }


def calculate_pop_monte_carlo(ticker, strike, expiration, option_type, iv, dte, simulations=10000):
    """
    Monte Carlo PoP estimation — NOT PRODUCTION READY.

    Requires current stock price data which is not yet wired in.
    Returns a fallback (50% default) with clear warning.
    Use calculate_pop_delta() for all production PoP needs.

    Returns:
        dict: {'pop': float, 'method': 'monte_carlo_unavailable', 'simulations': int, 'details': str}
    """
    if iv is None or iv <= 0 or dte is None or dte <= 0:
        return {'pop': 0.5, 'method': 'monte_carlo_unavailable', 'simulations': 0, 'details': 'Invalid IV or DTE'}
    
    logger.warning(f"Monte Carlo not implemented for {ticker} — stock price data not wired in")
    return {
        'pop': 0.5,
        'pop_pct': 50.0,
        'method': 'monte_carlo_unavailable',
        'simulations': 0,
        'details': 'Monte Carlo method requires current stock price; use delta method for production PoP',
    }


def get_pop(ticker, strike, expiration, option_type, delta=None, iv=None, dte=None, method='delta'):
    """
    Get Probability of Profit (PoP) for an option contract.

    Two methods available:
    - 'delta' (DEFAULT, RECOMMENDED): Uses option delta: PoP = 1 - |delta|. 
      Production-ready. Requires only the delta value.
    - 'monte_carlo': Returns a fallback (50% default). Not production-ready.
      Requires current stock price which is not yet wired in.

    Args:
        ticker: Stock symbol
        strike: Option strike price
        expiration: Expiration date (YYYYMMDD)
        option_type: 'CALL' or 'PUT'
        delta: Option delta (for delta-based)
        iv: Implied volatility (for Monte Carlo)
        dte: Days to expiration
        method: 'delta' or 'monte_carlo'
        
    Returns:
        dict: PoP result with 'pop', 'pop_pct', 'method', 'details' keys
    """
    if method == 'monte_carlo':
        return calculate_pop_monte_carlo(ticker, strike, expiration, option_type, iv, dte)
    else:
        return calculate_pop_delta(ticker, strike, expiration, option_type, delta, iv, dte)
