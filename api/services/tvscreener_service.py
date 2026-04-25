"""
Tvscreener Service Layer - Simplified
Provides wheel strategy candidate screening via TradingView.
"""

import logging
import threading
from datetime import datetime
from typing import Optional, List

logger = logging.getLogger('api.services.tvscreener')


class TvscreenerService:
    """Service for wheel strategy candidate screening."""

    def __init__(self):
        self._tvscreener = None
        self._initialized = False
        self._init_lock = threading.Lock()
        self._cache = {}
        self._cache_lock = threading.Lock()
        self._cache_ttl = 300  # 5 minutes

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

    def get_wheel_candidates(self, min_iv_rank: int = 30,
                            min_volume: int = 1000000,
                            limit: int = 50) -> Optional[List[str]]:
        """
        Fetch stocks suitable for wheel strategy.

        Args:
            min_iv_rank: Minimum IV rank percentage (0-100)
            min_volume: Minimum average daily volume
            limit: Maximum number of stocks to return

        Returns:
            List of ticker symbols or None if unavailable
        """
        if not self._ensure_initialized():
            return None

        cache_key = f"wheel_candidates:{min_iv_rank}:{min_volume}:{limit}"

        # Check cache
        with self._cache_lock:
            entry = self._cache.get(cache_key)
            if entry and (datetime.now() - entry['timestamp']).total_seconds() <= self._cache_ttl:
                return entry['data']

        # Fetch from API
        try:
            from tvscreener import StockScreener, StockField

            screener = StockScreener()
            # Note: Symbol is automatically included by the API, no need to select it
            # Note: IV_PERCENTILE is not available in tvscreener, using volatility instead
            screener.where(StockField.VOLATILITY >= min_iv_rank / 100)  # Convert percentage to decimal
            screener.where(StockField.AVERAGE_VOLUME >= min_volume)
            screener.limit(limit)

            df = screener.get()
            if df is None or df.empty:
                symbols = []
            else:
                symbols = df['symbol'].tolist() if 'symbol' in df.columns else []

            # Cache result
            with self._cache_lock:
                self._cache[cache_key] = {
                    'data': symbols,
                    'timestamp': datetime.now()
                }

            logger.info(f"Found {len(symbols)} wheel strategy candidates")
            return symbols
        except Exception as e:
            logger.warning(f"Wheel candidate screening failed: {e}")
            return None


def create_tvscreener_service():
    """Factory function for lazy initialization."""
    return TvscreenerService()
