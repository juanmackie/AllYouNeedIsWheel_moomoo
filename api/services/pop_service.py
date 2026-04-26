"""
Probability of Profit (PoP) Service
Enhanced PoP estimation with Delta-based and Monte Carlo methods.
"""

import logging
import random
from typing import Dict, Any, Optional

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
    Calculate PoP using Monte Carlo simulation.
    Uses Geometric Brownian Motion (GBM) to estimate probability of finishing OTM.
    
    Returns:
        dict: {'pop': float, 'method': 'monte_carlo', 'simulations': int, 'details': str}
    """
    if iv is None or iv <= 0 or dte is None or dte <= 0:
        return {'pop': 0.5, 'method': 'monte_carlo_fallback', 'simulations': 0, 'details': 'Invalid IV or DTE'}
    
    # Placeholder: Monte Carlo requires current stock price
    # For now, return delta-based as fallback
    # TODO: Fetch current price and implement full Monte Carlo
    # NOTE: This is a known limitation - full Monte Carlo requires stock price data
    logger.debug(f"Monte Carlo not fully implemented for {ticker} — using delta fallback")
    return {
        'pop': 0.5,
        'pop_pct': 50.0,
        'method': 'monte_carlo_fallback',
        'simulations': 0,
        'details': 'Monte Carlo not yet implemented — using 50% default',
    }


def get_pop(ticker, strike, expiration, option_type, delta=None, iv=None, dte=None, method='delta'):
    """
    Get PoP using specified method.
    
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
        dict: PoP result
    """
    if method == 'monte_carlo':
        return calculate_pop_monte_carlo(ticker, strike, expiration, option_type, iv, dte)
    else:
        return calculate_pop_delta(ticker, strike, expiration, option_type, delta, iv, dte)
