"""
Risk Sizing Service (Anti-UBER Tool)
Calculates position sizes based on ATR (Average True Range) to limit risk to 1% of account.

Formula:
    risk_amount = account_value * risk_pct (default 1% = 0.01)
    risk_per_contract = atr * 100  (100 shares per contract)
    max_contracts = floor(risk_amount / risk_per_contract)
"""

import logging
from typing import Dict, Any, List, Optional, TypedDict

from api.services.utils import clean_yfinance_ticker, get_yfinance_ticker, validate_ticker
from core.ttl_cache import make_ttl_cache

logger = logging.getLogger('api.services.risk_sizing')


class SizingResult(TypedDict):
    ticker: str
    atr: float
    atr_period: int
    account_value: float
    risk_pct: float
    risk_amount: float
    risk_per_contract: float
    max_contracts: int
    current_price: float
    warnings: List[str]

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    YFINANCE_AVAILABLE = True
except ImportError:
    logger.warning("yfinance/pandas/numpy not available — RiskSizingService will use fallback")
    YFINANCE_AVAILABLE = False


class RiskSizingService:
    """
    Service for calculating position sizes based on ATR risk.
    
    Ensures that a 1-ATR adverse move costs no more than
    a configured percentage of the account (default 1%).
    """
    
    CACHE_TTL_SECONDS = 900  # 15 minutes

    def __init__(self):
        self._cache = make_ttl_cache(maxsize=256, ttl=self.CACHE_TTL_SECONDS)
        # risk_pct: 0.01 = 1% of account
        # atr_period: 14 days is standard
        self.default_risk_pct = 0.01
        self.default_atr_period = 14

    def _get_cached(self, ticker: str) -> Optional[Dict[str, Any]]:
        entry = self._cache.get(ticker)
        if entry is not None:
            logger.debug(f"Risk sizing cache hit for {ticker}")
            return entry
        return None

    def _set_cached(self, ticker: str, data: Dict[str, Any]) -> None:
        self._cache[ticker] = data

    def calculate_atr(self, ticker: str, period: int = 14) -> float:
        """
        Calculate ATR (Average True Range) for a ticker.
        
        Args:
            ticker: Stock symbol
            period: ATR period (default 14)
            
        Returns:
            ATR value as float, or 0.0 if calculation fails
        """
        if not YFINANCE_AVAILABLE:
            return 0.0

        # ── Defense in depth: reject invalid tickers before any yfinance call ──
        if not validate_ticker(ticker):
            logger.debug(f"Risk sizing: Skipping invalid ticker '{ticker}'")
            return 0.0

        try:
            clean_ticker = clean_yfinance_ticker(ticker)
            stock = get_yfinance_ticker(clean_ticker)
            # Fetch 3x period to have enough data
            hist = stock.history(period='2mo', interval='1d')

            if hist.empty or len(hist) < period:
                logger.warning(f"Insufficient data for {ticker} ATR (got {len(hist)} days)")
                return 0.0

            # Calculate True Range
            high = hist['High'].values
            low = hist['Low'].values
            close = hist['Close'].values

            tr_list = []
            for i in range(len(hist)):
                if i == 0:
                    tr = high[i] - low[i]
                else:
                    tr = max(
                        high[i] - low[i],
                        abs(high[i] - close[i-1]),
                        abs(low[i] - close[i-1])
                    )
                tr_list.append(tr)

            # Calculate ATR as SMA of True Range
            tr_series = pd.Series(tr_list)
            atr = tr_series.rolling(window=period).mean().iloc[-1]

            return round(float(atr), 4)

        except Exception as e:
            logger.error(f"Error calculating ATR for {ticker}: {e}")
            return 0.0

    def calculate_position_size(
        self,
        ticker: str,
        account_value: float,
        risk_pct: float = 0.01,
        atr_period: int = 14
    ) -> SizingResult:
        """
        Calculate position size based on ATR risk.
        
        Args:
            ticker: Stock symbol
            account_value: Total account value
            risk_pct: Risk percentage (0.01 = 1%)
            atr_period: ATR period
            
        Returns:
            dict: {
                'ticker': str,
                'atr': float,
                'atr_period': int,
                'account_value': float,
                'risk_pct': float,
                'risk_amount': float,  # account_value * risk_pct
                'risk_per_contract': float,  # atr * 100
                'max_contracts': int,  # floor(risk_amount / risk_per_contract)
                'current_price': float,
                'warnings': list,
            }
        """
        # Check cache
        cache_key = f"{ticker}_{account_value}_{risk_pct}_{atr_period}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        result = {
            'ticker': ticker,
            'atr': 0.0,
            'atr_period': atr_period,
            'account_value': account_value,
            'risk_pct': risk_pct,
            'risk_amount': round(account_value * risk_pct, 2),
            'risk_per_contract': 0.0,
            'max_contracts': 1,
            'current_price': 0.0,
            'warnings': [],
        }

        if not YFINANCE_AVAILABLE:
            result['warnings'].append('yfinance not available — using default size')
            return result

        # ── Defense in depth: reject invalid tickers before any yfinance call ──
        if not validate_ticker(ticker):
            logger.debug(f"Risk sizing: Skipping invalid ticker '{ticker}'")
            result['warnings'].append(f'Invalid ticker: {ticker}')
            return result

        try:
            # Get current price
            clean_ticker = clean_yfinance_ticker(ticker)
            stock = get_yfinance_ticker(clean_ticker)
            hist = stock.history(period='5d', interval='1d')
            
            if hist.empty:
                result['warnings'].append('No price data available')
                return result

            current_price = float(hist['Close'].iloc[-1])
            result['current_price'] = current_price

            # Calculate ATR
            atr = self.calculate_atr(ticker, atr_period)
            if atr <= 0:
                result['warnings'].append('ATR calculation failed — using default size')
                return result

            result['atr'] = atr

            # Risk calculations
            risk_amount = account_value * risk_pct
            risk_per_contract = atr * 100  # 100 shares per contract
            max_contracts = max(1, int(risk_amount // risk_per_contract))

            result['risk_amount'] = round(risk_amount, 2)
            result['risk_per_contract'] = round(risk_per_contract, 2)
            result['max_contracts'] = max_contracts

            # Add warnings
            if max_contracts < 1:
                result['warnings'].append('ATR risk exceeds 1% — consider higher risk_pct')
            elif max_contracts > 10:
                result['warnings'].append('High contract count — verify available capital')

            # Cache the result
            self._set_cached(cache_key, result)

            return result

        except Exception as e:
            logger.error(f"Error calculating position size for {ticker}: {e}")
            result['warnings'].append(f'Error: {str(e)}')
            return result

    def clear_cache(self, ticker: Optional[str] = None) -> None:
        """Clear cache for a ticker or all tickers."""
        if ticker:
            prefix = f"{ticker}_"
            for key in [k for k in list(self._cache.keys()) if k == ticker or str(k).startswith(prefix)]:
                self._cache.pop(key, None)
            logger.info(f"Cleared risk sizing cache for {ticker}")
        else:
            self._cache.clear()
            logger.info("Cleared all risk sizing cache")


# ------------------------------------------------------------------ #
#  Singleton                                                           #
# ------------------------------------------------------------------ #

_risk_sizing_service = None


def get_risk_sizing_service() -> RiskSizingService:
    """Get or create the risk sizing service singleton."""
    global _risk_sizing_service
    if _risk_sizing_service is None:
        _risk_sizing_service = RiskSizingService()
    return _risk_sizing_service
