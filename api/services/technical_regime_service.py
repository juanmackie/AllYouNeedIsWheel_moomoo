"""
Technical Market Regime Service
Detects market regime using 200-day EMA and ADX (trend strength).
Provides a simple color-coded signal: bullish (green), bearish (red), neutral (grey).
"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional

from api.services.utils import clean_yfinance_ticker, get_yfinance_ticker, validate_ticker
from core.ttl_cache import make_ttl_cache

logger = logging.getLogger('api.services.technical_regime')

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    logger.warning("yfinance not available — TechnicalRegimeService will return neutral")
    YFINANCE_AVAILABLE = False

try:
    import pandas as pd
    import numpy as np
    PANDAS_AVAILABLE = True
except ImportError:
    logger.warning("pandas/numpy not available — TechnicalRegimeService will return neutral")
    PANDAS_AVAILABLE = False


class TechnicalRegimeService:
    """
    Service for detecting technical market regime.

    200-day EMA Regime:
        - bullish:  price > EMA200 * 1.02
        - bearish:  price < EMA200 * 0.98
        - neutral:  price within 2% envelope around EMA200

    ADX Trend Strength (14-period):
        - trending:   ADX > 25
        - ranging:    ADX <= 25

    Combined regime: 'bullish' | 'bearish' | 'neutral'
    """

    CACHE_TTL_SECONDS = 3600  # 1 hour

    def __init__(self):
        self._cache = make_ttl_cache(maxsize=256, ttl=self.CACHE_TTL_SECONDS)

    def _get_cached(self, ticker: str) -> Optional[Dict[str, Any]]:
        entry = self._cache.get(ticker)
        if entry:
            logger.debug(f"Technical regime cache hit for {ticker}")
            return entry
        return None

    def _set_cached(self, ticker: str, data: Dict[str, Any]) -> None:
        self._cache[ticker] = data

    def get_200_ema_regime(self, ticker: str) -> Dict[str, Any]:
        """
        Get 200-day EMA regime for a ticker.

        Returns:
            dict: {
                'regime': 'bullish' | 'bearish' | 'neutral',
                'price': float,
                'ema200': float,
                'distance_pct': float,  # How far price is from EMA200 (%)
            }
        """
        result = {
            'regime': 'neutral',
            'price': 0.0,
            'ema200': 0.0,
            'distance_pct': 0.0,
        }

        if not YFINANCE_AVAILABLE or not PANDAS_AVAILABLE:
            return result

        try:
            # Fetch ~300 days to ensure we have enough data for 200-day EMA
            clean_ticker = clean_yfinance_ticker(ticker)
            stock = get_yfinance_ticker(clean_ticker)
            hist = stock.history(period='1y', interval='1d')

            if hist.empty or len(hist) < 200:
                logger.warning(f"Insufficient data for {ticker} EMA200 (got {len(hist)} days)")
                return result

            # Calculate 200-day EMA
            ema200_series = hist['Close'].ewm(span=200, adjust=False).mean()
            ema200 = float(ema200_series.iloc[-1])
            price = float(hist['Close'].iloc[-1])

            result['price'] = round(price, 2)
            result['ema200'] = round(ema200, 2)
            result['distance_pct'] = round(((price - ema200) / ema200) * 100, 2)

            # Classify regime with 2% envelope
            if price > ema200 * 1.02:
                result['regime'] = 'bullish'
            elif price < ema200 * 0.98:
                result['regime'] = 'bearish'
            else:
                result['regime'] = 'neutral'

            logger.debug(f"{ticker} EMA200 regime: {result['regime']} (price={price:.2f}, ema200={ema200:.2f})")

        except Exception as e:
            logger.error(f"Error computing EMA200 regime for {ticker}: {e}")

        return result

    def get_adx(self, ticker: str, period: int = 14) -> Dict[str, Any]:
        """
        Get ADX (Average Directional Index) for trend strength.

        Returns:
            dict: {
                'adx': float,          # ADX value (0-100)
                'trend_strength': 'trending' | 'ranging',
                'plus_di': float,
                'minus_di': float,
            }
        """
        result = {
            'adx': 25.0,  # Neutral default
            'trend_strength': 'ranging',
            'plus_di': 0.0,
            'minus_di': 0.0,
        }

        if not YFINANCE_AVAILABLE or not PANDAS_AVAILABLE:
            return result

        try:
            clean_ticker = clean_yfinance_ticker(ticker)
            stock = get_yfinance_ticker(clean_ticker)
            hist = stock.history(period='6mo', interval='1d')

            if hist.empty or len(hist) < period + 10:
                logger.warning(f"Insufficient data for {ticker} ADX (got {len(hist)} days)")
                return result

            # Calculate +DI and -DI
            high = hist['High'].values
            low = hist['Low'].values
            close = hist['Close'].values

            # True Range
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

            tr_series = pd.Series(tr_list, index=hist.index)

            # +DM and -DM
            plus_dm_list = []
            minus_dm_list = []
            for i in range(len(hist)):
                if i == 0:
                    plus_dm_list.append(0)
                    minus_dm_list.append(0)
                else:
                    up_move = high[i] - high[i-1]
                    down_move = low[i-1] - low[i]
                    
                    if up_move > down_move and up_move > 0:
                        plus_dm_list.append(up_move)
                    else:
                        plus_dm_list.append(0)
                    
                    if down_move > up_move and down_move > 0:
                        minus_dm_list.append(down_move)
                    else:
                        minus_dm_list.append(0)

            plus_dm_series = pd.Series(plus_dm_list, index=hist.index)
            minus_dm_series = pd.Series(minus_dm_list, index=hist.index)

            # Smoothed averages
            tr_smoothed = tr_series.rolling(window=period).sum()
            plus_di_smoothed = plus_dm_series.rolling(window=period).sum()
            minus_di_smoothed = minus_dm_series.rolling(window=period).sum()

            # +DI and -DI
            plus_di = 100 * (plus_di_smoothed / tr_smoothed)
            minus_di = 100 * (minus_di_smoothed / tr_smoothed)

            # DX
            dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)

            # ADX
            adx_series = dx.rolling(window=period).mean()
            adx = float(adx_series.iloc[-1])

            result['adx'] = round(adx, 2)
            result['plus_di'] = round(float(plus_di.iloc[-1]), 2)
            result['minus_di'] = round(float(minus_di.iloc[-1]), 2)
            result['trend_strength'] = 'trending' if adx > 25 else 'ranging'

            logger.debug(f"{ticker} ADX: {adx:.2f} ({result['trend_strength']})")

        except Exception as e:
            logger.error(f"Error computing ADX for {ticker}: {e}")

        return result

    def get_combined_regime(self, ticker: str) -> Dict[str, Any]:
        """
        Get combined technical regime for a ticker.
        Merges EMA200 regime and ADX trend strength.

        Returns:
            dict: {
                'ticker': str,
                'regime': 'bullish' | 'bearish' | 'neutral',
                'trend_strength': 'trending' | 'ranging',
                'adx': float,
                'ema200': float,
                'price': float,
                'distance_pct': float,
                'summary': str,  # Human-readable summary
                'updated_at': str,  # ISO timestamp
            }
        """
        # ── Defense in depth: reject invalid tickers before any yfinance call ──
        if not validate_ticker(ticker):
            logger.debug(f"Technical regime: Skipping invalid ticker '{ticker}'")
            return {
                'ticker': ticker,
                'regime': 'neutral',
                'trend_strength': 'ranging',
                'adx': 25.0,
                'ema200': 0.0,
                'price': 0.0,
                'distance_pct': 0.0,
                'plus_di': 0.0,
                'minus_di': 0.0,
                'summary': f'⚪ {ticker}: Invalid ticker',
                'updated_at': datetime.now().isoformat(),
            }

        # Check cache first
        cached = self._get_cached(ticker)
        if cached:
            return cached

        ema_result = self.get_200_ema_regime(ticker)
        adx_result = self.get_adx(ticker)

        combined = {
            'ticker': ticker,
            'regime': ema_result['regime'],
            'trend_strength': adx_result['trend_strength'],
            'adx': adx_result['adx'],
            'ema200': ema_result['ema200'],
            'price': ema_result['price'],
            'distance_pct': ema_result['distance_pct'],
            'plus_di': adx_result['plus_di'],
            'minus_di': adx_result['minus_di'],
            'updated_at': datetime.now().isoformat(),
        }

        # Generate human-readable summary
        regime_emoji = {'bullish': '🟢', 'bearish': '🔴', 'neutral': '⚪'}.get(combined['regime'], '⚪')
        trend_emoji = '📈' if combined['trend_strength'] == 'trending' else '📊'
        
        combined['summary'] = (
            f"{regime_emoji} {combined['regime'].title()} | "
            f"{trend_emoji} {combined['trend_strength'].title()} (ADX: {combined['adx']:.1f}) | "
            f"Price: ${combined['price']:.2f} | EMA200: ${combined['ema200']:.2f} "
            f"({combined['distance_pct']:+.1f}%)"
        )

        # Cache the result
        self._set_cached(ticker, combined)

        return combined

    def get_batch_regimes(self, tickers: list) -> Dict[str, Dict[str, Any]]:
        """
        Get combined regime for multiple tickers.

        Args:
            tickers: List of ticker symbols

        Returns:
            dict: {ticker: regime_dict}
        """
        results = {}
        for ticker in tickers:
            try:
                results[ticker] = self.get_combined_regime(ticker)
            except Exception as e:
                logger.error(f"Error getting regime for {ticker}: {e}")
                results[ticker] = {
                    'ticker': ticker,
                    'regime': 'neutral',
                    'trend_strength': 'ranging',
                    'adx': 25.0,
                    'ema200': 0.0,
                    'price': 0.0,
                    'distance_pct': 0.0,
                    'summary': f'⚪ {ticker}: Error fetching data',
                    'updated_at': datetime.now().isoformat(),
                }
        return results

    def clear_cache(self, ticker: Optional[str] = None) -> None:
        """Clear cache for a ticker or all tickers."""
        if ticker:
            self._cache.pop(ticker, None)
            logger.info(f"Cleared technical regime cache for {ticker}")
        else:
            self._cache.clear()
            logger.info("Cleared all technical regime cache")


# ------------------------------------------------------------------ #
#  Singleton                                                           #
# ------------------------------------------------------------------ #

_tech_regime_service = None
_service_lock = None


def get_technical_regime_service() -> TechnicalRegimeService:
    """Get or create the technical regime service singleton."""
    global _tech_regime_service
    if _tech_regime_service is None:
        _tech_regime_service = TechnicalRegimeService()
    return _tech_regime_service
