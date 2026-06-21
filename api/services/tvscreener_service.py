"""
Tvscreener Service Layer - Simplified
Provides wheel strategy candidate screening via TradingView.
"""

import logging
import threading
from typing import Optional, List

from core.ttl_cache import make_ttl_cache

logger = logging.getLogger('api.services.tvscreener')


class TvscreenerService:
    """Service for wheel strategy candidate screening."""

    def __init__(self):
        self._tvscreener = None
        self._initialized = False
        self._init_lock = threading.Lock()
        self._cache = make_ttl_cache(maxsize=256, ttl=300)
        self._cache_lock = threading.Lock()

    def _ensure_initialized(self) -> bool:
        """Lazy initialization of tvscreener."""
        if self._initialized:
            return self._tvscreener is not None
        with self._init_lock:
            if self._initialized:
                return self._tvscreener is not None
            try:
                import tvscreener
                self._tvscreener = tvscreener
                self._initialized = True
                logger.info("tvscreener SDK initialized")
                return True
            except ImportError:
                logger.warning("tvscreener not installed")
                self._initialized = True  # Mark as initialized to avoid retry
                return False
            except Exception as e:
                logger.error(f"Failed to initialize tvscreener: {e}")
                self._initialized = True  # Mark as initialized to avoid retry
                return False

    def get_wheel_candidates(self, min_volatility_pct: float = 3.0,
                            min_volume: int = 1000000,
                            limit: int = 50,
                            max_price: float = None) -> Optional[List[str]]:
        """
        Fetch stocks suitable for wheel strategy.

        Args:
            min_volatility_pct: Minimum TradingView daily volatility percent
            min_volume: Minimum average daily volume
            limit: Maximum number of stocks to return
            max_price: Optional maximum stock price filter

        Returns:
            List of ticker symbols or None if unavailable
        """
        if not self._ensure_initialized():
            return None

        cache_key = f"wheel_candidates:{min_volatility_pct}:{min_volume}:{limit}:{max_price}"

        # Check cache
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        # Fetch from API
        try:
            from tvscreener import StockScreener, StockField

            screener = StockScreener()
            # TradingView exposes daily volatility percent, not IV rank. The
            # renamed knob is intentionally a volatility floor, not an IV-rank claim.
            volatility_floor = max(0.5, min(float(min_volatility_pct), 8.0))
            screener.where(StockField.VOLATILITY >= volatility_floor)
            screener.where(StockField.AVERAGE_VOLUME >= min_volume)
            if max_price is not None and max_price > 0:
                screener.where(StockField.PRICE <= max_price)
            try:
                screener.limit(limit)
            except AttributeError:
                pass  # Some versions don't have .limit() method

            df = screener.get()
            if df is None or df.empty:
                symbols = []
            else:
                symbols = df['symbol'].tolist() if 'symbol' in df.columns else []

            # Cache result
            with self._cache_lock:
                self._cache[cache_key] = symbols

            logger.info(f"Found {len(symbols)} wheel strategy candidates")
            return symbols
        except Exception as e:
            logger.warning(f"Wheel candidate screening failed: {e}")
            return None


def create_tvscreener_service():
    """Factory function for lazy initialization."""
    return TvscreenerService()
