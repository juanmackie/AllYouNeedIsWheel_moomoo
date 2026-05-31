"""
Market Regime module - handles VIX regime detection
Extracted from the monolithic options_service.py for maintainability.
"""

import logging
from datetime import datetime

from api.services.utils import get_yfinance_ticker

logger = logging.getLogger('api.services.market_regime')


class MarketRegime:
    """
    Handles VIX regime detection and market regime analysis.
    """
    
    def __init__(self, config_provider, openbb_service_provider=None):
        self._config_provider = config_provider
        self.config = config_provider.config if hasattr(config_provider, 'config') else config_provider
        self._openbb_service_provider = openbb_service_provider
        self._vix_cache = None
        
    def _get_openbb_service(self):
        if not self.config.get('openbb_enabled', False):
            return None
        if self._openbb_service_provider:
            return self._openbb_service_provider._get_openbb_service()
        return None
        
    def get_vix_regime(self):
        """
        Get current VIX market regime for adaptive delta targeting.
        Uses yfinance as the default free source, with optional enrichment
        only when explicitly enabled in config.
        
        Returns:
            dict: {
                'vix': float,
                'regime': str ('complacency', 'normal', 'fear'),
                'delta_adjustment': float,
                'exposure_multiplier': float,
                'description': str
            }
        """
        cache_key = '_vix_regime_cache'
        if hasattr(self, cache_key):
            cache_entry = getattr(self, cache_key)
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < 300:  # 5 minute cache
                return cache_entry['data']
        
        vix_value = None
        
        if self.config.get('openbb_enabled', False):
            try:
                openbb = self._get_openbb_service()
                if openbb:
                    vix_data = openbb.get_vix()
                    if vix_data and 'vix' in vix_data:
                        vix_value = float(vix_data['vix'])
                        logger.debug(f"VIX from optional enrichment: {vix_value}")
            except Exception as e:
                logger.debug(f"Optional enrichment VIX fetch failed, trying yfinance: {e}")
        
        if vix_value is None:
            try:
                vix_ticker = get_yfinance_ticker('^VIX')
                hist = vix_ticker.history(period='1d')
                if not hist.empty:
                    vix_value = float(hist['Close'].iloc[-1])
                    logger.debug(f"VIX from yfinance: {vix_value}")
            except Exception as e:
                logger.debug(f"yfinance VIX fetch failed: {e}")
        
        if vix_value is None:
            logger.warning("Unable to fetch VIX, using default normal regime")
            result = {
                'vix': 20.0,
                'regime': 'normal',
                'delta_adjustment': 0.0,
                'exposure_multiplier': 1.0,
                'description': 'Normal volatility (VIX 15-30) - standard delta targets'
            }
            setattr(self, cache_key, {'data': result, 'timestamp': datetime.now()})
            return result
        
        if vix_value < 15:
            result = {
                'vix': round(vix_value, 2),
                'regime': 'complacency',
                'delta_adjustment': 0.10,
                'exposure_multiplier': 0.7,
                'description': 'Low volatility (VIX < 15) - higher delta targets, reduced exposure'
            }
        elif vix_value <= 30:
            result = {
                'vix': round(vix_value, 2),
                'regime': 'normal',
                'delta_adjustment': 0.0,
                'exposure_multiplier': 1.0,
                'description': 'Normal volatility (VIX 15-30) - standard delta targets'
            }
        else:
            result = {
                'vix': round(vix_value, 2),
                'regime': 'fear',
                'delta_adjustment': -0.05,
                'exposure_multiplier': 0.5,
                'description': 'High volatility (VIX > 30) - lower delta targets, conservative exposure'
            }
        
        setattr(self, cache_key, {'data': result, 'timestamp': datetime.now()})
        return result
