"""
OpenBB Service Layer
Lightweight SDK wrapper for OpenBB financial data enrichment.
Provides 4 methods used by the Wheel Strategy scoring pipeline:
- get_unusual_options(ticker) - unusual options activity (sweeps/blocks)
- get_technical_snapshot(ticker) - RSI, SMA, ATR
- get_quality_check(ticker) - fundamentals + insider trading pass/fail
- get_vix() - market volatility context
"""

import logging
import threading
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger('api.services.openbb')


class OpenBBService:
    """
    Service for fetching enriched financial data from OpenBB.
    Lazy-initializes the SDK, caches results with per-type TTLs,
    and degrades gracefully if OpenBB is unavailable.
    """

    def __init__(self):
        self._obb = None
        self._initialized = False
        self._init_lock = threading.Lock()
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        self._ttl = {
            'unusual_options': 300,
            'technical': 600,
            'quality': 3600,
            'vix': 300,
        }

    def _ensure_initialized(self) -> bool:
        if self._initialized:
            return self._obb is not None
        with self._init_lock:
            if self._initialized:
                return self._obb is not None
            try:
                from openbb import obb
                self._obb = obb
                self._initialized = True
                logger.info("OpenBB SDK initialized")
                return True
            except ImportError:
                logger.warning("OpenBB SDK not installed. Run: pip install openbb")
                return False
            except Exception as e:
                logger.error(f"Failed to initialize OpenBB SDK: {e}")
                return False

    def _get_cache(self, key: str, ttl: int) -> Optional[Dict]:
        with self._cache_lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            if (datetime.now() - entry['timestamp']).total_seconds() > ttl:
                del self._cache[key]
                return None
            return entry['data']

    def _set_cache(self, key: str, data: Dict):
        with self._cache_lock:
            self._cache[key] = {'data': data, 'timestamp': datetime.now()}

    def _safe_fetch(self, key: str, ttl: int, func):
        cached = self._get_cache(key, ttl)
        if cached is not None:
            return cached
        try:
            result = func()
            if result is not None:
                self._set_cache(key, result)
            return result
        except Exception as e:
            logger.error(f"OpenBB {key} failed: {e}")
            return None

    # ------------------------------------------------------------------ #
    #  1. Unusual Options Activity                                         #
    # ------------------------------------------------------------------ #

    def get_unusual_options(self, ticker: str) -> Optional[Dict]:
        """
        Fetch unusual options activity for a ticker.
        Returns sweeps and block trades that indicate smart money positioning.
        """
        if not self._ensure_initialized():
            return None

        cache_key = f"unusual_options:{ticker}"

        def _fetch():
            try:
                result = self._obb.derivatives.options.unusual(symbol=ticker)
                df = result.to_df()
                if df is None or df.empty:
                    return {'sweeps': [], 'block_trades': [], 'count': 0}

                sweeps = []
                blocks = []
                for _, row in df.iterrows():
                    entry = {
                        'contract': str(row.get('contract', '')),
                        'option_type': str(row.get('option_type', '')),
                        'strike': float(row.get('strike', 0) or 0),
                        'expiration': str(row.get('expiration', '')),
                        'volume': int(row.get('volume', 0) or 0),
                        'open_interest': int(row.get('open_interest', 0) or 0),
                        'premium': float(row.get('premium', 0) or 0),
                        'sentiment': str(row.get('sentiment', '')),
                    }
                    trade_type = str(row.get('trade_type', '') or '').lower()
                    if 'sweep' in trade_type:
                        sweeps.append(entry)
                    else:
                        blocks.append(entry)

                return {
                    'sweeps': sweeps[:20],
                    'block_trades': blocks[:20],
                    'count': len(df),
                }
            except Exception as e:
                logger.warning(f"Unusual options fetch failed for {ticker}: {e}")
                return {'sweeps': [], 'block_trades': [], 'count': 0}

        return self._safe_fetch(cache_key, self._ttl['unusual_options'], _fetch)

    # ------------------------------------------------------------------ #
    #  2. Technical Snapshot                                               #
    # ------------------------------------------------------------------ #

    def get_technical_snapshot(self, ticker: str) -> Optional[Dict]:
        """
        Fetch RSI, price vs SMA 200, and ATR % for a ticker.
        Used as a scoring adjustment in _build_candidate().
        """
        if not self._ensure_initialized():
            return None

        cache_key = f"technical:{ticker}"

        def _fetch():
            try:
                hist = self._obb.equity.price.historical(ticker, interval="1d")
                df = hist.to_df()
                if df is None or df.empty or 'close' not in df.columns:
                    return None

                result = {}
                last_close = float(df['close'].iloc[-1])

                # RSI (14-period)
                try:
                    rsi_result = self._obb.technical.rsi(data=df, target_column="close")
                    rsi_df = rsi_result.to_df()
                    if rsi_df is not None and not rsi_df.empty:
                        rsi_col = [c for c in rsi_df.columns if 'rsi' in c.lower()]
                        if rsi_col:
                            result['rsi'] = round(float(rsi_df[rsi_col[0]].iloc[-1]), 2)
                except Exception:
                    pass

                # SMA 200
                try:
                    sma200 = df['close'].rolling(window=200).mean().iloc[-1]
                    import math
                    if not math.isnan(sma200) and sma200 > 0:
                        result['sma_200'] = round(float(sma200), 2)
                        result['price_vs_sma_200'] = round((last_close / sma200 - 1) * 100, 1)
                except Exception:
                    pass

                # ATR
                try:
                    atr_result = self._obb.technical.atr(data=df, target_column="close")
                    atr_df = atr_result.to_df()
                    if atr_df is not None and not atr_df.empty:
                        atr_col = [c for c in atr_df.columns if 'atr' in c.lower()]
                        if atr_col:
                            atr = float(atr_df[atr_col[0]].iloc[-1])
                            result['atr'] = round(atr, 2)
                            result['atr_pct'] = round(atr / last_close * 100, 2) if last_close > 0 else 0
                except Exception:
                    pass

                if result:
                    result['last_close'] = round(last_close, 2)
                    return result
                return None
            except Exception as e:
                logger.warning(f"Technical snapshot failed for {ticker}: {e}")
                return None

        return self._safe_fetch(cache_key, self._ttl['technical'], _fetch)

    # ------------------------------------------------------------------ #
    #  3. Quality Check (Fundamentals + Insider)                           #
    # ------------------------------------------------------------------ #

    def get_quality_check(self, ticker: str) -> Optional[Dict]:
        """
        Fetch fundamental quality metrics and insider trading activity.
        Returns a pass/fail gate for candidate screening.
        """
        if not self._ensure_initialized():
            return None

        cache_key = f"quality:{ticker}"

        def _fetch():
            result = {}

            # Key metrics
            try:
                metrics = self._obb.equity.key_metrics(ticker)
                df = metrics.to_df()
                if df is not None and not df.empty:
                    row = df.iloc[-1]
                    for col in ['pe_ratio', 'debt_to_equity', 'roe', 'current_ratio']:
                        if col in df.columns:
                            val = row.get(col)
                            if val is not None:
                                try:
                                    result[col] = round(float(val), 4)
                                except (TypeError, ValueError):
                                    pass
            except Exception:
                pass

            # Company profile for beta
            try:
                profile = self._obb.equity.profile(ticker)
                df = profile.to_df()
                if df is not None and not df.empty:
                    row = df.iloc[0] if hasattr(df, 'iloc') else df
                    if 'beta' in df.columns and row.get('beta') is not None:
                        try:
                            result['beta'] = round(float(row['beta']), 2)
                        except (TypeError, ValueError):
                            pass
            except Exception:
                pass

            # Insider trading
            try:
                insider = self._obb.equity.ownership.insider(ticker)
                df = insider.to_df()
                if df is not None and not df.empty:
                    net_shares = 0
                    shares_col = None
                    for c in ['shares', 'num_shares', 'transaction_shares']:
                        if c in df.columns:
                            shares_col = c
                            break
                    if shares_col:
                        for _, row in df.head(20).iterrows():
                            try:
                                net_shares += float(row.get(shares_col, 0) or 0)
                            except (TypeError, ValueError):
                                pass
                    result['insider_net_shares'] = round(net_shares, 0)
            except Exception:
                pass

            # Pass/fail gate
            passes = True
            reasons = []
            dte = result.get('debt_to_equity')
            if dte is not None and dte > 2.0:
                passes = False
                reasons.append(f"High debt-to-equity ({dte:.1f})")
            roe = result.get('roe')
            if roe is not None and roe < 0:
                passes = False
                reasons.append(f"Negative ROE ({roe:.1f}%)")
            insider = result.get('insider_net_shares')
            if insider is not None and insider < -10000:
                reasons.append(f"Heavy insider selling ({insider:,.0f} shares)")

            result['passes'] = passes
            result['reasons'] = reasons
            return result if result else None

        return self._safe_fetch(cache_key, self._ttl['quality'], _fetch)

    # ------------------------------------------------------------------ #
    #  4. VIX Level                                                        #
    # ------------------------------------------------------------------ #

    def get_vix(self) -> Optional[Dict]:
        """
        Fetch current VIX level for market volatility context.
        """
        if not self._ensure_initialized():
            return None

        cache_key = "vix"

        def _fetch():
            try:
                # Try OpenBB first
                hist = self._obb.equity.price.historical("VIX", interval="1d")
                df = hist.to_df()
                if df is not None and not df.empty and 'close' in df.columns:
                    current = float(df['close'].iloc[-1])
                    return {
                        'vix': round(current, 2),
                        'status': 'low' if current < 15 else ('normal' if current < 25 else ('elevated' if current < 35 else 'high')),
                    }
            except Exception as e:
                logger.debug(f"OpenBB VIX fetch failed: {e}")
            
            # Fallback to yfinance
            try:
                import yfinance as yf
                import time
                time.sleep(0.5)
                vix_ticker = yf.Ticker("^VIX")
                info = vix_ticker.info
                if info and 'previousClose' in info:
                    current = float(info['previousClose'])
                    return {
                        'vix': round(current, 2),
                        'status': 'low' if current < 15 else ('normal' if current < 25 else ('elevated' if current < 35 else 'high')),
                    }
            except Exception as e:
                logger.debug(f"yfinance VIX fallback failed: {e}")
            
            return None

        return self._safe_fetch(cache_key, self._ttl['vix'], _fetch)

    # ------------------------------------------------------------------ #
    #  Scoring Helpers                                                     #
    # ------------------------------------------------------------------ #

    def get_technical_score_adjustment(self, ticker: str) -> float:
        """Technical score adjustment: -10 to +10."""
        t = self.get_technical_snapshot(ticker)
        if not t:
            return 0.0
        adj = 0.0
        rsi = t.get('rsi')
        if rsi is not None:
            if rsi < 30:
                adj += 3.0
            elif rsi < 40:
                adj += 1.5
            elif rsi > 70:
                adj -= 3.0
            elif rsi > 60:
                adj -= 1.5
        pvs = t.get('price_vs_sma_200')
        if pvs is not None:
            if pvs > 10:
                adj += 2.0
            elif pvs > 0:
                adj += 1.0
            elif pvs > -10:
                adj -= 1.0
            else:
                adj -= 2.0
        atr_pct = t.get('atr_pct')
        if atr_pct is not None:
            if atr_pct > 3:
                adj += 2.0
            elif atr_pct > 2:
                adj += 1.0
            elif atr_pct < 1:
                adj -= 1.0
        return round(max(-10.0, min(10.0, adj)), 1)

    def get_quality_score_adjustment(self, ticker: str) -> float:
        """Quality score adjustment: -10 to +10."""
        q = self.get_quality_check(ticker)
        if not q:
            return 0.0
        adj = 0.0
        roe = q.get('roe')
        if roe is not None:
            if roe > 20:
                adj += 3.0
            elif roe > 15:
                adj += 2.0
            elif roe > 10:
                adj += 1.0
            elif roe < 0:
                adj -= 3.0
        dte = q.get('debt_to_equity')
        if dte is not None:
            if dte < 0.3:
                adj += 3.0
            elif dte < 0.5:
                adj += 2.0
            elif dte < 1.0:
                adj += 1.0
            elif dte > 2.0:
                adj -= 2.0
        cr = q.get('current_ratio')
        if cr is not None:
            if cr > 2.0:
                adj += 2.0
            elif cr > 1.5:
                adj += 1.0
            elif cr < 1.0:
                adj -= 2.0
        insider = q.get('insider_net_shares')
        if insider is not None:
            if insider > 0:
                adj += 2.0
            elif insider < -10000:
                adj -= 2.0
        return round(max(-10.0, min(10.0, adj)), 1)

    # ------------------------------------------------------------------ #
    #  Cache Management                                                    #
    # ------------------------------------------------------------------ #

    def clear_cache(self, pattern: Optional[str] = None):
        with self._cache_lock:
            if pattern is None:
                self._cache.clear()
            else:
                for k in [k for k in self._cache if pattern in k]:
                    del self._cache[k]

    def get_cache_stats(self) -> Dict:
        with self._cache_lock:
            return {'total_entries': len(self._cache)}


_openbb_service = None
_service_lock = threading.Lock()


def get_openbb_service() -> OpenBBService:
    global _openbb_service
    if _openbb_service is None:
        with _service_lock:
            if _openbb_service is None:
                _openbb_service = OpenBBService()
    return _openbb_service
