"""
Technical Indicators Service
Computes Bollinger Bands, RSI, Supertrend, and Volume Profile.
Used by the Theta-Three indicator stack.
"""

import logging
from typing import Dict, Any, List, Optional, Tuple, TypedDict
from datetime import datetime

from api.services.utils import clean_yfinance_ticker, validate_ticker

logger = logging.getLogger('api.services.technical_indicators')


class BollingerBandsResult(TypedDict):
    upper: float
    middle: float
    lower: float
    position: str
    current_price: float


class RsiResult(TypedDict):
    rsi: float
    signal: str


class SupertrendResult(TypedDict):
    trend: str
    stop: float
    atr: float


class VolumeProfileResult(TypedDict):
    poc: float
    vah: float
    val: float
    gaps: list

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    PANDAS_AVAILABLE = True
except ImportError:
    logger.warning("yfinance/pandas/numpy not available — TechnicalIndicatorsService will use fallback")
    PANDAS_AVAILABLE = False


class TechnicalIndicatorsService:
    """
    Service for computing technical indicators.
    
    Indicators:
    - Bollinger Bands (BB)
    - Relative Strength Index (RSI)
    - Supertrend
    - Volume Profile
    """

    CACHE_TTL_SECONDS = 1800  # 30 minutes
    PRICE_CACHE_TTL_SECONDS = 60  # 1 minute (per-request reuse across indicators)

    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._price_data_cache: Dict[str, Dict[str, Any]] = {}

    def _is_cache_valid(self, entry: Dict[str, Any]) -> bool:
        if not entry:
            return False
        age = (datetime.now() - entry['timestamp']).total_seconds()
        return age < self.CACHE_TTL_SECONDS

    def _get_cached(self, key: str) -> Optional[Dict[str, Any]]:
        entry = self._cache.get(key)
        if entry and self._is_cache_valid(entry):
            logger.debug(f"Technical indicators cache hit for {key}")
            return entry['data']
        return None

    def _set_cached(self, key: str, data: Dict[str, Any]) -> None:
        self._cache[key] = {
            'data': data,
            'timestamp': datetime.now()
        }

    def _get_price_data(self, ticker: str, period: str = '2mo') -> Tuple[List[float], List[float], List[float], List[int]]:
        """
        Fetch OHLCV data for a ticker.
        
        Results are cached per-ticker with a short TTL so that all 4 indicator
        methods (BB, RSI, Supertrend, Volume Profile) reuse the same yfinance
        call instead of making 4 independent requests.
        
        Returns:
            (closes, highs, lows, volumes) as lists of floats/ints
        """
        if not PANDAS_AVAILABLE:
            return [], [], [], []
        
        # ── Defense in depth: reject invalid tickers before any yfinance call ──
        if not validate_ticker(ticker):
            logger.debug(f"Technical indicators: Skipping invalid ticker '{ticker}'")
            return [], [], [], []
        
        # ── Shared per-request price cache ──
        price_cache_key = f"{ticker}_{period}"
        now = datetime.now()
        cached_entry = self._price_data_cache.get(price_cache_key)
        if cached_entry:
            age = (now - cached_entry['timestamp']).total_seconds()
            if age < self.PRICE_CACHE_TTL_SECONDS:
                logger.debug(f"Technical indicators: Reusing cached price data for {price_cache_key}")
                return cached_entry['data']
        
        try:
            clean_ticker = clean_yfinance_ticker(ticker)
            stock = yf.Ticker(clean_ticker)
            hist = stock.history(period=period, interval='1d')
            
            if hist.empty:
                return [], [], [], []
            
            closes = hist['Close'].tolist()
            highs = hist['High'].tolist()
            lows = hist['Low'].tolist()
            volumes = hist['Volume'].tolist()
            
            data = (closes, highs, lows, volumes)
            
            # Cache for reuse by the next indicator
            self._price_data_cache[price_cache_key] = {
                'data': data,
                'timestamp': now,
            }
            
            return data
        except Exception as e:
            logger.debug(f"Error fetching price data for {ticker}: {e}")
            return [], [], [], []

    def compute_bollinger_bands(self, ticker: str, period: int = 20, std_dev: float = 2.0) -> BollingerBandsResult:
        """
        Compute Bollinger Bands for a ticker.
        
        Returns:
            dict: {
                'upper': float,    # Upper band
                'middle': float,   # Middle band (SMA)
                'lower': float,    # Lower band
                'position': str,   # 'above_upper', 'below_lower', 'within'
                'current_price': float,
            }
        """
        cache_key = f"{ticker}_bb_{period}_{std_dev}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        result = {
            'upper': 0.0,
            'middle': 0.0,
            'lower': 0.0,
            'position': 'unknown',
            'current_price': 0.0,
        }
        
        if not PANDAS_AVAILABLE:
            return result
        
        try:
            closes, _, _, _ = self._get_price_data(ticker)
            if not closes or len(closes) < period:
                return result
            
            prices = pd.Series(closes)
            sma = prices.rolling(window=period).mean()
            std = prices.rolling(window=period).std()
            
            upper = sma + (std * std_dev)
            lower = sma - (std * std_dev)
            
            current_price = closes[-1]
            
            result['upper'] = round(float(upper.iloc[-1]), 2)
            result['middle'] = round(float(sma.iloc[-1]), 2)
            result['lower'] = round(float(lower.iloc[-1]), 2)
            result['current_price'] = round(float(current_price), 2)
            
            # Determine position
            if current_price > result['upper']:
                result['position'] = 'above_upper'
            elif current_price < result['lower']:
                result['position'] = 'below_lower'
            else:
                result['position'] = 'within'
            
            self._set_cached(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Error computing Bollinger Bands for {ticker}: {e}")
            return result

    def compute_rsi(self, ticker: str, period: int = 14) -> RsiResult:
        """
        Compute RSI (Relative Strength Index) for a ticker.
        
        Returns:
            dict: {
                'rsi': float,       # RSI value (0-100)
                'signal': str,     # 'oversold' (<30), 'overbought' (>70), 'neutral'
            }
        """
        cache_key = f"{ticker}_rsi_{period}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        result = {
            'rsi': 50.0,
            'signal': 'neutral',
        }
        
        if not PANDAS_AVAILABLE:
            return result
        
        try:
            closes, _, _, _ = self._get_price_data(ticker)
            if not closes or len(closes) < period + 1:
                return result
            
            prices = pd.Series(closes)
            delta = prices.diff()
            
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            
            avg_gain = gain.rolling(window=period).mean()
            avg_loss = loss.rolling(window=period).mean()
            
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            current_rsi = float(rsi.iloc[-1])
            
            result['rsi'] = round(current_rsi, 2)
            
            if current_rsi < 30:
                result['signal'] = 'oversold'
            elif current_rsi > 70:
                result['signal'] = 'overbought'
            else:
                result['signal'] = 'neutral'
            
            self._set_cached(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Error computing RSI for {ticker}: {e}")
            return result

    def compute_supertrend(self, ticker: str, period: int = 10, multiplier: float = 3.0) -> SupertrendResult:
        """
        Compute Supertrend indicator.
        
        Returns:
            dict: {
                'trend': str,      # 'up' or 'down'
                'stop': float,      # Supertrend stop level
                'atr': float,       # ATR value used
            }
        """
        cache_key = f"{ticker}_supertrend_{period}_{multiplier}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        result = {
            'trend': 'up',
            'stop': 0.0,
            'atr': 0.0,
        }
        
        if not PANDAS_AVAILABLE:
            return result
        
        try:
            closes, highs, lows, _ = self._get_price_data(ticker)
            if not closes or len(closes) < period:
                return result
            
            # Calculate ATR
            high = pd.Series(highs)
            low = pd.Series(lows)
            close = pd.Series(closes)
            
            tr_list = []
            for i in range(len(closes)):
                if i == 0:
                    tr = highs[i] - lows[i]
                else:
                    tr = max(
                        highs[i] - lows[i],
                        abs(highs[i] - closes[i-1]),
                        abs(lows[i] - closes[i-1])
                    )
                tr_list.append(tr)
            
            tr_series = pd.Series(tr_list)
            atr = tr_series.rolling(window=period).mean().iloc[-1]
            
            # Supertrend calculation
            hl2 = ((high + low) / 2).iloc[-1]
            basic_upper = hl2 + (multiplier * atr)
            basic_lower = hl2 - (multiplier * atr)
            
            final_upper = basic_upper
            final_lower = basic_lower
            
            # Simplified: just use current values
            current_price = closes[-1]
            
            if current_price > basic_upper:
                trend = 'up'
                stop = basic_lower
            else:
                trend = 'down'
                stop = basic_upper
            
            result['trend'] = trend
            result['stop'] = round(float(stop), 2)
            result['atr'] = round(float(atr), 4)
            
            self._set_cached(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Error computing Supertrend for {ticker}: {e}")
            return result

    def compute_volume_profile(self, ticker: str, bins: int = 50) -> VolumeProfileResult:
        """
        Compute Volume Profile for a ticker.
        
        Returns:
            dict: {
                'poc': float,          # Point of Control (price with highest volume)
                'vah': float,          # Value Area High
                'val': float,          # Value Area Low
                'gaps': list,           # List of {start, end} dicts for volume gaps
            }
        """
        cache_key = f"{ticker}_vp_{bins}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        result = {
            'poc': 0.0,
            'vah': 0.0,
            'val': 0.0,
            'gaps': [],
        }
        
        if not PANDAS_AVAILABLE:
            return result
        
        try:
            closes, _, _, volumes = self._get_price_data(ticker, period='3mo')
            if not closes or len(closes) < bins:
                return result
            
            # Simple volume profile: group prices into bins
            price_min = min(closes)
            price_max = max(closes)
            
            if price_min == price_max:
                return result
            
            # Create bins
            bin_size = (price_max - price_min) / bins
            volume_per_bin = [0] * bins
            
            for i, price in enumerate(closes):
                if i >= len(volumes):
                    break
                bin_index = min(int((price - price_min) / bin_size), bins - 1)
                volume_per_bin[bin_index] += volumes[i]
            
            # Find POC (highest volume bin)
            poc_index = volume_per_bin.index(max(volume_per_bin))
            poc_price = price_min + (poc_index * bin_size) + (bin_size / 2)
            
            # Value Area (70% of volume)
            total_volume = sum(volume_per_bin)
            target_volume = total_volume * 0.70
            
            # Expand from POC until we hit 70%
            cumulative_volume = volume_per_bin[poc_index]
            val_index = poc_index
            vah_index = poc_index
            
            while cumulative_volume < target_volume:
                # Try to expand left and right
                if val_index > 0:
                    val_index -= 1
                    cumulative_volume += volume_per_bin[val_index]
                if vah_index < bins - 1:
                    vah_index += 1
                    cumulative_volume += volume_per_bin[vah_index]
                if val_index == 0 and vah_index == bins - 1:
                    break
            
            val_price = price_min + (val_index * bin_size)
            vah_price = price_min + (vah_index * bin_size) + bin_size
            
            result['poc'] = round(poc_price, 2)
            result['vah'] = round(vah_price, 2)
            result['val'] = round(val_price, 2)
            
            # Find volume gaps (optional)
            gaps = []
            for i in range(bins - 1):
                if volume_per_bin[i] < total_volume * 0.001 and volume_per_bin[i+1] < total_volume * 0.001:
                    # Low volume zone
                    gap_start = price_min + (i * bin_size)
                    gaps.append({'start': round(gap_start, 2), 'end': round(gap_start + bin_size * 2, 2)})
            
            result['gaps'] = gaps[:5]  # Limit to 5 gaps
            
            self._set_cached(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Error computing volume profile for {ticker}: {e}")
            return result

    def clear_cache(self, ticker: Optional[str] = None) -> None:
        """Clear cache for a ticker or all tickers."""
        if ticker:
            keys_to_remove = [k for k in self._cache.keys() if k.startswith(ticker)]
            for k in keys_to_remove:
                del self._cache[k]
            # Also clear price data cache for this ticker
            self._price_data_cache = {
                k: v for k, v in self._price_data_cache.items() if not k.startswith(ticker)
            }
            logger.info(f"Cleared technical indicators cache for {ticker}")
        else:
            self._cache.clear()
            self._price_data_cache.clear()
            logger.info("Cleared all technical indicators cache")


# ------------------------------------------------------------------ #
#  Singleton                                                           #
# ------------------------------------------------------------------ #

_tech_indicators_service = None


def get_technical_indicators_service() -> TechnicalIndicatorsService:
    """Get or create the technical indicators service singleton."""
    global _tech_indicators_service
    if _tech_indicators_service is None:
        _tech_indicators_service = TechnicalIndicatorsService()
    return _tech_indicators_service
