"""
VIX Regime Service - detects VIX market regime for adaptive delta targeting.
Extracted from the monolithic MarketRegime class for focused responsibility.
"""

import logging
from datetime import datetime

from api.services.utils import get_yfinance_ticker

logger = logging.getLogger("api.services.vix_regime")


class VixRegimeService:
    def __init__(self, config_provider):
        self._config_provider = config_provider
        self.config = config_provider.config if hasattr(config_provider, "config") else config_provider

    def get_vix_regime(self):
        cache_key = "_vix_regime_cache"
        stale_cache = None
        if hasattr(self, cache_key):
            cache_entry = getattr(self, cache_key)
            age = (datetime.now() - cache_entry["timestamp"]).total_seconds()
            if age < 300:
                return cache_entry["data"]
            stale_cache = cache_entry["data"]

        vix_value = None

        try:
            vix_ticker = get_yfinance_ticker("^VIX")
            hist = vix_ticker.history(period="1d")
            if not hist.empty:
                vix_value = float(hist["Close"].iloc[-1])
                logger.debug(f"VIX from yfinance: {vix_value}")
        except Exception as e:
            logger.debug(f"yfinance VIX fetch failed: {e}")

        if vix_value is None:
            if stale_cache:
                logger.warning("Unable to fetch VIX, using stale cached regime")
                return stale_cache
            logger.warning("Unable to fetch VIX, using default normal regime")
            result = {
                "vix": 20.0,
                "regime": "normal",
                "delta_adjustment": 0.0,
                "exposure_multiplier": 1.0,
                "description": "Normal volatility (VIX 15-30) - standard delta targets",
            }
            setattr(self, cache_key, {"data": result, "timestamp": datetime.now()})
            return result

        if vix_value < 15:
            result = {
                "vix": round(vix_value, 2),
                "regime": "complacency",
                "delta_adjustment": 0.10,
                "exposure_multiplier": 0.7,
                "description": "Low volatility (VIX < 15) - higher delta targets, reduced exposure",
            }
        elif vix_value <= 30:
            result = {
                "vix": round(vix_value, 2),
                "regime": "normal",
                "delta_adjustment": 0.0,
                "exposure_multiplier": 1.0,
                "description": "Normal volatility (VIX 15-30) - standard delta targets",
            }
        else:
            result = {
                "vix": round(vix_value, 2),
                "regime": "fear",
                "delta_adjustment": -0.05,
                "exposure_multiplier": 0.5,
                "description": "High volatility (VIX > 30) - lower delta targets, conservative exposure",
            }

        setattr(self, cache_key, {"data": result, "timestamp": datetime.now()})
        return result
