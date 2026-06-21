"""
Underlying Quality Gate — free-data quality assessment for wheel candidates.

Uses only yfinance (free). Computes:
- Trend regime: price vs 200-day EMA
- Relative strength slope vs SPY (63-day)
- ADX trend strength
- ATR percent (volatility)
- Volume ratio vs 20-day average

Returns a flat dict with grade and warnings. No classes, no abstraction layers.
"""

import logging
from datetime import datetime

from core.ttl_cache import make_ttl_cache

logger = logging.getLogger('api.services.underlying_quality')

CACHE_TTL = 3600  # 1 hour

_cache = make_ttl_cache(maxsize=512, ttl=CACHE_TTL)

_EMPTY = {
    'grade': 'unknown',
    'score': 0,
    'regime': 'unknown',
    'trend_strength': 'unknown',
    'adx': 0.0,
    'rs_slope_63d': 0.0,
    'atr_pct': 0.0,
    'volume_ratio': 0.0,
    'price_vs_ema200': 0.0,
    'warnings': ['insufficient_data'],
    'source': 'yfinance',
    'updated_at': None,
}


def get_underlying_quality(ticker: str) -> dict:
    """Return quality assessment for a single ticker. Cached 1h."""
    cached = _cache.get(ticker)
    if cached:
        return cached

    result = _compute(ticker)
    _cache[ticker] = result
    return result


def _compute(ticker: str) -> dict:
    try:
        from api.services.utils import get_yfinance_history, validate_ticker
        import pandas as pd
        import numpy as np
    except ImportError:
        return {**_EMPTY, 'warnings': ['dependencies_missing']}

    if not validate_ticker(ticker):
        return {**_EMPTY, 'warnings': ['invalid_ticker']}

    try:
        hist = get_yfinance_history(ticker, period='1y')
        if hist is None or hist.empty or len(hist) < 50:
            return {**_EMPTY, 'warnings': ['insufficient_price_data'], 'updated_at': datetime.now().isoformat()}

        close = hist['Close']
        high = hist['High']
        low = hist['Low']
        volume = hist['Volume']
        current_price = float(close.iloc[-1])

        # --- EMA200 regime ---
        ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1]) if len(close) >= 200 else float(close.ewm(span=min(len(close), 200), adjust=False).mean().iloc[-1])
        price_vs_ema200 = ((current_price - ema200) / ema200 * 100) if ema200 > 0 else 0.0
        if current_price > ema200 * 1.02:
            regime = 'bullish'
        elif current_price < ema200 * 0.98:
            regime = 'bearish'
        else:
            regime = 'neutral'

        # --- ADX (14-period) ---
        adx = _compute_adx(high, low, close, period=14)

        # --- Relative strength slope vs SPY (63-day) ---
        rs_slope = 0.0
        try:
            spy_hist = get_yfinance_history('SPY', period='1y')
            if spy_hist is not None and not spy_hist.empty and len(spy_hist) >= 63:
                spy_close = spy_hist['Close']
                # Align on common dates
                common_idx = close.index.intersection(spy_close.index)
                if len(common_idx) >= 63:
                    stock_aligned = close.reindex(common_idx)
                    spy_aligned = spy_close.reindex(common_idx)
                    rs = (stock_aligned / spy_aligned).values[-63:]
                    x = np.arange(len(rs))
                    if np.std(x) > 0 and np.mean(rs) != 0:
                        slope = np.polyfit(x, rs, 1)[0]
                        rs_slope = slope / np.mean(rs) * 100  # normalize to % per day
        except Exception:
            pass  # RS is optional — degrade gracefully

        # --- ATR percent ---
        atr_pct = 0.0
        if len(close) >= 14:
            tr = pd.concat([
                high - low,
                (high - close.shift(1)).abs(),
                (low - close.shift(1)).abs(),
            ], axis=1).max(axis=1)
            atr14 = float(tr.rolling(14).mean().iloc[-1])
            atr_pct = (atr14 / current_price * 100) if current_price > 0 else 0.0

        # --- Volume ratio (current vs 20-day avg) ---
        vol_ratio = 1.0
        if len(volume) >= 21:
            avg_vol = float(volume.iloc[-21:-1].mean())
            current_vol = float(volume.iloc[-1])
            vol_ratio = (current_vol / avg_vol) if avg_vol > 0 else 1.0

        # --- Grade ---
        warnings = []
        score = 50  # neutral starting point

        if regime == 'bullish':
            score += 20
        elif regime == 'bearish':
            score -= 20
            warnings.append('below_200_sma')

        if adx > 25:
            score += 10
        elif adx < 15:
            score -= 5
            warnings.append('weak_trend')

        if rs_slope > 0.05:
            score += 15
        elif rs_slope < -0.05:
            score -= 15
            warnings.append('underperforming_spy')

        if atr_pct > 5.0:
            score -= 10
            warnings.append('high_volatility')
        elif atr_pct > 3.5:
            warnings.append('elevated_volatility')

        if vol_ratio < 0.5:
            warnings.append('low_volume')

        score = max(0, min(100, score))

        if score >= 70:
            grade = 'strong'
        elif score >= 50:
            grade = 'mixed'
        else:
            grade = 'weak'
            warnings.append('weak_underlying')

        if not warnings:
            warnings.append('none')

        return {
            'grade': grade,
            'score': round(score, 1),
            'regime': regime,
            'trend_strength': 'trending' if adx > 25 else 'ranging',
            'adx': round(adx, 2),
            'rs_slope_63d': round(rs_slope, 4),
            'atr_pct': round(atr_pct, 2),
            'volume_ratio': round(vol_ratio, 2),
            'price_vs_ema200': round(price_vs_ema200, 2),
            'warnings': warnings,
            'source': 'yfinance',
            'updated_at': datetime.now().isoformat(),
        }

    except Exception as e:
        logger.warning(f"Underlying quality failed for {ticker}: {e}")
        return {**_EMPTY, 'warnings': ['fetch_error'], 'updated_at': datetime.now().isoformat()}


def _compute_adx(high, low, close, period=14) -> float:
    """Minimal ADX computation."""
    try:
        import pandas as pd
        import numpy as np

        if len(close) < period + 10:
            return 25.0

        h = high.values
        l = low.values
        c = close.values

        tr_list = np.zeros(len(h))
        plus_dm = np.zeros(len(h))
        minus_dm = np.zeros(len(h))

        for i in range(1, len(h)):
            tr_list[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
            up = h[i] - h[i - 1]
            down = l[i - 1] - l[i]
            plus_dm[i] = up if (up > down and up > 0) else 0
            minus_dm[i] = down if (down > up and down > 0) else 0

        tr_s = pd.Series(tr_list).rolling(period).sum()
        plus_s = pd.Series(plus_dm).rolling(period).sum()
        minus_s = pd.Series(minus_dm).rolling(period).sum()

        plus_di = 100 * plus_s / tr_s
        minus_di = 100 * minus_s / tr_s
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = float(dx.rolling(period).mean().iloc[-1])

        return adx if not np.isnan(adx) else 25.0
    except Exception:
        return 25.0
