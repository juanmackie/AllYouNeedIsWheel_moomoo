"""
Signal overlay service.

Builds a normalized, read-only multi-dimensional signal layer from moomoo
market data. This is an evidence service, not a scoring engine.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from moomoo import RET_OK

from api.services.utils import clean_yfinance_ticker, validate_ticker
from core.ticker_utils import canonical_underlying

logger = logging.getLogger("api.services.signal_overlay")


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _as_float(value, default: float = 0.0) -> float:
    try:
        if value in (None, "", "N/A"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int = 0) -> int:
    try:
        if value in (None, "", "N/A"):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _df_to_records(data) -> list[dict]:
    if data is None:
        return []
    if isinstance(data, pd.DataFrame):
        return data.to_dict("records")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _safe_column_sum(data, columns: tuple[str, ...]) -> float:
    total = 0.0
    if not isinstance(data, pd.DataFrame) or data.empty:
        return total
    for column in columns:
        if column in data.columns:
            total += float(pd.to_numeric(data[column], errors="coerce").fillna(0).sum())
    return total


@dataclass
class _DimensionResult:
    bias: str = "unknown"
    score: float = 0.0
    confidence: float = 0.0
    summary: str = ""
    signals: list[str] = field(default_factory=list)
    raw: dict | None = None

    def to_dict(self) -> dict:
        return {
            "bias": self.bias,
            "score": round(float(self.score), 1),
            "confidence": round(float(self.confidence), 1),
            "summary": self.summary,
            "signals": list(self.signals or []),
            "raw": self.raw or {},
        }


class SignalOverlayService:
    _shared_cache: dict[str, dict] = {}
    _shared_cache_ttl = 300

    def __init__(self, config_provider=None):
        self._config_provider = config_provider
        self.connection = None

    @property
    def config(self):
        if hasattr(self._config_provider, "config"):
            return self._config_provider.config
        return self._config_provider or {}

    def _ensure_connection(self):
        if self.connection is not None and self.connection.is_connected():
            return self.connection

        from core.connection import MoomooConnection

        host = str(self.config.get("host", "127.0.0.1"))
        port = int(self.config.get("port", 11111))
        self.connection = MoomooConnection(
            host=host,
            port=port,
            readonly=bool(self.config.get("readonly", True)),
            account_id=self.config.get("account_id"),
            portfolio_env=self.config.get("portfolio_env"),
            security_firm=self.config.get("security_firm"),
            broker_cache_after_hours=self.config.get("broker_cache_after_hours", True),
        )
        if self.connection.connect():
            return self.connection
        logger.debug("SignalOverlayService: failed to connect to Moomoo")
        return None

    def _normalize_symbol(self, ticker: str) -> str | None:
        clean = canonical_underlying(clean_yfinance_ticker(ticker or ""))
        clean = str(clean or "").strip().upper()
        if not clean or not validate_ticker(clean):
            return None
        return clean

    def _cache_key(self, ticker: str) -> str:
        return self._normalize_symbol(ticker) or str(ticker).strip().upper()

    def _cached_overlay(self, ticker: str, refresh: bool = False):
        key = self._cache_key(ticker)
        if refresh:
            return None
        cached = self._shared_cache.get(key)
        if not cached:
            return None
        if time.time() - cached.get("ts", 0) >= self._shared_cache_ttl:
            return None
        return cached.get("payload")

    def _store_cache(self, ticker: str, payload: dict) -> None:
        self._shared_cache[self._cache_key(ticker)] = {"ts": time.time(), "payload": payload}

    def get_overlays(self, tickers: list[str], refresh: bool = False) -> dict:
        start = time.time()
        normalized = []
        seen = set()
        invalid = []
        for ticker in tickers or []:
            symbol = self._normalize_symbol(ticker)
            if not symbol:
                invalid.append(str(ticker).strip())
                continue
            if symbol in seen:
                continue
            seen.add(symbol)
            normalized.append(symbol)

        if not normalized:
            return {
                "success": True,
                "generated_at": datetime.now().isoformat(),
                "count": 0,
                "source_available": False,
                "overlays": {},
                "errors": [{"ticker": ticker, "error": "Invalid ticker"} for ticker in invalid],
                "invalid_tickers": invalid,
                "elapsed_seconds": round(time.time() - start, 3),
            }

        conn = self._ensure_connection()
        source_available = conn is not None

        overlays = {}
        errors = []
        for ticker in normalized:
            try:
                cached = self._cached_overlay(ticker, refresh=refresh)
                if cached is not None:
                    overlays[ticker] = cached
                    continue

                payload = self._build_overlay(conn, ticker) if conn else self._unknown_overlay(ticker, "Moomoo connection unavailable")
                self._store_cache(ticker, payload)
                overlays[ticker] = payload
            except Exception as exc:
                logger.debug("Signal overlay failed for %s: %s", ticker, exc)
                errors.append({"ticker": ticker, "error": str(exc)})
                payload = self._unknown_overlay(ticker, str(exc))
                self._store_cache(ticker, payload)
                overlays[ticker] = payload

        for ticker in invalid:
            errors.append({"ticker": ticker, "error": "Invalid ticker"})

        return {
            "success": True,
            "generated_at": datetime.now().isoformat(),
            "count": len(overlays),
            "source_available": source_available,
            "overlays": overlays,
            "errors": errors,
            "invalid_tickers": invalid,
            "elapsed_seconds": round(time.time() - start, 3),
        }

    def get_overlay(self, ticker: str, refresh: bool = False) -> dict:
        symbol = self._normalize_symbol(ticker)
        if not symbol:
            return self._unknown_overlay(str(ticker).strip().upper() or "UNKNOWN", "Invalid ticker")

        result = self.get_overlays([symbol], refresh=refresh)
        return result["overlays"].get(symbol, self._unknown_overlay(symbol, "Unavailable"))

    def get_overlay_map(self, tickers: list[str], refresh: bool = False) -> dict[str, dict]:
        result = self.get_overlays(tickers, refresh=refresh)
        return result.get("overlays", {}) if isinstance(result, dict) else {}

    def _unknown_overlay(self, ticker: str, reason: str) -> dict:
        return {
            "ticker": ticker,
            "generated_at": datetime.now().isoformat(),
            "source": "moomoo",
            "source_available": False,
            "verdict": "unknown",
            "bias": "unknown",
            "score": 0.0,
            "summary": reason,
            "capital": _DimensionResult(summary=reason, signals=[reason]).to_dict(),
            "technical": _DimensionResult(summary=reason, signals=[reason]).to_dict(),
            "derivatives": _DimensionResult(summary=reason, signals=[reason]).to_dict(),
            "overall": {
                "bias": "unknown",
                "score": 0.0,
                "confidence": 0.0,
            },
            "signals": [reason],
            "warnings": [reason],
            "fit": "unknown",
            "fit_reason": reason,
        }

    def _build_overlay(self, conn, ticker: str) -> dict:
        capital = self._build_capital_dimension(conn, ticker)
        technical = self._build_technical_dimension(conn, ticker)
        derivatives = self._build_derivatives_dimension(conn, ticker)

        dimensions = [capital, technical, derivatives]
        known = [d for d in dimensions if d.bias != "unknown"]
        bullish = sum(d.confidence for d in known if d.bias == "bullish")
        bearish = sum(d.confidence for d in known if d.bias == "bearish")
        neutral = sum(d.confidence for d in known if d.bias == "neutral")

        if not known:
            verdict = "unknown"
            bias = "unknown"
            score = 0.0
        else:
            bias = max(
                (("bullish", bullish), ("bearish", bearish), ("neutral", neutral)),
                key=lambda item: item[1],
            )[0]
            score = _clamp(50.0 + (bullish - bearish) * 0.35)
            if bullish and bearish:
                verdict = "conflict" if abs(bullish - bearish) < 20 else "caution"
            elif bias == "neutral":
                verdict = "caution"
            else:
                verdict = "confirming"

        signals = []
        for dim_name, dim in (("capital", capital), ("technical", technical), ("derivatives", derivatives)):
            if dim.summary:
                signals.append(f"{dim_name}: {dim.summary}")
        if not signals:
            signals = ["No structured overlay signals"]

        summary = " | ".join(
            part for part in [
                capital.summary,
                technical.summary,
                derivatives.summary,
            ] if part
        ) or "No structured overlay signals"

        raw = {
            "capital": capital.raw or {},
            "technical": technical.raw or {},
            "derivatives": derivatives.raw or {},
        }

        return {
            "ticker": ticker,
            "generated_at": datetime.now().isoformat(),
            "source": "moomoo",
            "source_available": True,
            "verdict": verdict,
            "bias": bias,
            "score": round(score, 1),
            "summary": summary,
            "capital": capital.to_dict(),
            "technical": technical.to_dict(),
            "derivatives": derivatives.to_dict(),
            "overall": {
                "bias": bias,
                "score": round(score, 1),
                "confidence": round(max(bullish, bearish, neutral, 0.0), 1),
            },
            "signals": signals,
            "warnings": self._collect_warnings(capital, technical, derivatives),
            "raw": raw,
        }

    def _collect_warnings(self, *dimensions: _DimensionResult) -> list[str]:
        warnings = []
        for dim in dimensions:
            for signal in dim.signals or []:
                if any(token in signal.lower() for token in ("overbought", "death cross", "bearish", "breakdown", "short pressure", "put skew")):
                    warnings.append(signal)
        return warnings[:5]

    def _build_capital_dimension(self, conn, ticker: str) -> _DimensionResult:
        dist_ret, dist_data = conn.get_capital_distribution(ticker)
        flow_ret, flow_data = conn.get_capital_flow(ticker)
        snap_ret, snap_data = conn.get_market_snapshot([ticker])
        dist_row = {}
        if dist_ret == RET_OK and dist_data is not None:
            if isinstance(dist_data, pd.DataFrame) and not dist_data.empty:
                dist_row = dist_data.iloc[0].to_dict()
            elif isinstance(dist_data, dict):
                dist_row = dist_data

        flow_rows = _df_to_records(flow_data)
        snap_row = {}
        if snap_ret == RET_OK and isinstance(snap_data, pd.DataFrame) and not snap_data.empty:
            snap_row = snap_data.iloc[0].to_dict()

        in_super = _as_float(dist_row.get("capital_in_super"))
        in_big = _as_float(dist_row.get("capital_in_big"))
        in_mid = _as_float(dist_row.get("capital_in_mid"))
        in_small = _as_float(dist_row.get("capital_in_small"))
        out_super = _as_float(dist_row.get("capital_out_super"))
        out_big = _as_float(dist_row.get("capital_out_big"))
        out_mid = _as_float(dist_row.get("capital_out_mid"))
        out_small = _as_float(dist_row.get("capital_out_small"))
        gross = sum(abs(v) for v in [in_super, in_big, in_mid, in_small, out_super, out_big, out_mid, out_small]) or 1.0
        net = (in_super + in_big + in_mid + in_small) - (out_super + out_big + out_mid + out_small)
        net_ratio = net / gross

        recent_flow = 0.0
        recent_main_flow = 0.0
        if flow_rows:
            recent_rows = flow_rows[-5:]
            recent_flow = sum(_as_float(row.get("in_flow")) for row in recent_rows)
            recent_main_flow = sum(_as_float(row.get("main_in_flow")) for row in recent_rows if row.get("main_in_flow") not in (None, "N/A"))

        short_enabled = bool(snap_row.get("enable_short_sell"))
        short_sell_rate = _as_float(snap_row.get("short_sell_rate"))
        short_available = _as_int(snap_row.get("short_available_volume"))
        short_pressure = short_enabled and short_sell_rate >= 2.0 and short_available >= 10000

        signals = []
        if net_ratio > 0.15:
            signals.append(f"capital inflow skew +{net_ratio:.0%}")
        elif net_ratio < -0.15:
            signals.append(f"capital outflow skew {net_ratio:.0%}")
        else:
            signals.append(f"balanced capital flow {net_ratio:.0%}")

        if flow_rows:
            signals.append(f"recent flow {recent_flow:,.0f}")
            if recent_main_flow:
                signals.append(f"main flow {recent_main_flow:,.0f}")

        if short_enabled:
            signals.append(f"short sell rate {short_sell_rate:.2f}")
            if short_pressure:
                signals.append("short pressure elevated")
            elif short_available < 5000:
                signals.append("shortable supply thin")

        if net_ratio > 0.15 and recent_flow >= 0:
            bias = "bullish"
        elif net_ratio < -0.15 or (short_pressure and net_ratio <= 0):
            bias = "bearish"
        else:
            bias = "neutral"

        score = _clamp(50.0 + net_ratio * 60.0 + (recent_main_flow / gross) * 20.0)
        confidence = _clamp(abs(net_ratio) * 100.0 + (12.0 if flow_rows else 0.0) + (10.0 if short_enabled else 0.0))
        summary = signals[0] if signals else "capital data unavailable"

        return _DimensionResult(
            bias=bias,
            score=score,
            confidence=confidence,
            summary=summary,
            signals=signals,
            raw={
                "net": round(net, 2),
                "net_ratio": round(net_ratio, 4),
                "recent_flow": round(recent_flow, 2),
                "recent_main_flow": round(recent_main_flow, 2),
                "short_sell_rate": round(short_sell_rate, 2),
                "short_available_volume": short_available,
                "short_enabled": short_enabled,
                "update_time": dist_row.get("update_time") or (flow_rows[-1].get("capital_flow_item_time") if flow_rows else None),
            },
        )

    def _build_technical_dimension(self, conn, ticker: str) -> _DimensionResult:
        ret, data, _ = conn.get_history_kline(ticker, max_count=120)
        if ret != RET_OK or data is None or not isinstance(data, pd.DataFrame) or data.empty:
            return _DimensionResult(summary="price history unavailable", signals=["price history unavailable"])

        frame = data.copy()
        close_col = "close" if "close" in frame.columns else "close_price" if "close_price" in frame.columns else None
        high_col = "high" if "high" in frame.columns else "high_price" if "high_price" in frame.columns else None
        low_col = "low" if "low" in frame.columns else "low_price" if "low_price" in frame.columns else None
        vol_col = "volume" if "volume" in frame.columns else None

        if not close_col or not high_col or not low_col:
            return _DimensionResult(summary="price history missing core fields", signals=["price history missing core fields"])

        closes = pd.to_numeric(frame[close_col], errors="coerce").dropna()
        highs = pd.to_numeric(frame[high_col], errors="coerce").dropna()
        lows = pd.to_numeric(frame[low_col], errors="coerce").dropna()
        if len(closes) < 30 or len(highs) < 30 or len(lows) < 30:
            return _DimensionResult(summary="insufficient kline history", signals=["insufficient kline history"])

        closes = closes.reset_index(drop=True)
        highs = highs.reset_index(drop=True)
        lows = lows.reset_index(drop=True)
        volumes = pd.to_numeric(frame[vol_col], errors="coerce").fillna(0).reset_index(drop=True) if vol_col else pd.Series([0] * len(closes))

        ema20 = closes.ewm(span=20, adjust=False).mean()
        ema50 = closes.ewm(span=50, adjust=False).mean()
        sma20 = closes.rolling(20).mean()
        sma50 = closes.rolling(50).mean()

        delta = closes.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean().replace(0, pd.NA)
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        ema12 = closes.ewm(span=12, adjust=False).mean()
        ema26 = closes.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        macd_signal = macd.ewm(span=9, adjust=False).mean()
        macd_hist = macd - macd_signal

        ma20 = float(sma20.iloc[-1]) if not pd.isna(sma20.iloc[-1]) else float(ema20.iloc[-1])
        ma50 = float(sma50.iloc[-1]) if not pd.isna(sma50.iloc[-1]) else float(ema50.iloc[-1])
        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2])
        rsi_last = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
        macd_hist_last = float(macd_hist.iloc[-1]) if not pd.isna(macd_hist.iloc[-1]) else 0.0
        vol_last = float(volumes.iloc[-1]) if len(volumes) else 0.0
        vol_avg20 = float(volumes.rolling(20).mean().iloc[-1]) if len(volumes) >= 20 and not pd.isna(volumes.rolling(20).mean().iloc[-1]) else 0.0
        high20 = float(highs.tail(20).max())
        low20 = float(lows.tail(20).min())
        upper_band = float((closes.rolling(20).mean() + closes.rolling(20).std() * 2).iloc[-1]) if len(closes) >= 20 else last
        lower_band = float((closes.rolling(20).mean() - closes.rolling(20).std() * 2).iloc[-1]) if len(closes) >= 20 else last

        bullish = 0
        bearish = 0
        signals = []

        if last > ma20:
            bullish += 1
            signals.append("price above 20d mean")
        else:
            bearish += 1
            signals.append("price below 20d mean")

        if ma20 > ma50:
            bullish += 1
            signals.append("trend stack bullish")
        else:
            bearish += 1
            signals.append("trend stack bearish")

        if macd_hist_last > 0:
            bullish += 1
            signals.append("MACD momentum positive")
        elif macd_hist_last < 0:
            bearish += 1
            signals.append("MACD momentum negative")

        if last >= high20:
            bullish += 1
            signals.append("20d breakout")
        elif last <= low20:
            bearish += 1
            signals.append("20d breakdown")

        if last > upper_band:
            bullish += 1
            signals.append("upper band breakout")
        elif last < lower_band:
            bearish += 1
            signals.append("lower band breakdown")

        if vol_avg20 > 0 and vol_last >= vol_avg20 * 1.5:
            signals.append(f"volume surge {vol_last / vol_avg20:.1f}x")

        if rsi_last >= 70:
            signals.append(f"RSI overbought {rsi_last:.0f}")
            bearish += 1
        elif rsi_last <= 30:
            signals.append(f"RSI oversold {rsi_last:.0f}")
            bullish += 1
        else:
            signals.append(f"RSI {rsi_last:.0f}")

        if bullish - bearish >= 2:
            bias = "bullish"
        elif bearish - bullish >= 2:
            bias = "bearish"
        else:
            bias = "neutral"

        score = _clamp(50.0 + (bullish - bearish) * 12.0 + (1.0 if last > prev else -1.0) * 5.0)
        confidence = _clamp((bullish + bearish) * 12.0)
        summary = signals[0] if signals else "technical data unavailable"

        return _DimensionResult(
            bias=bias,
            score=score,
            confidence=confidence,
            summary=summary,
            signals=signals,
            raw={
                "last": round(last, 4),
                "ma20": round(ma20, 4),
                "ma50": round(ma50, 4),
                "rsi": round(rsi_last, 2),
                "macd_hist": round(macd_hist_last, 4),
                "high20": round(high20, 4),
                "low20": round(low20, 4),
                "upper_band": round(upper_band, 4),
                "lower_band": round(lower_band, 4),
                "volume": round(vol_last, 0),
                "volume_avg20": round(vol_avg20, 0),
            },
        )

    def _build_derivatives_dimension(self, conn, ticker: str) -> _DimensionResult:
        exp_ret, exp_data = conn.get_option_expiration_dates(ticker)
        expirations = []
        if exp_ret == RET_OK and exp_data is not None:
            if isinstance(exp_data, pd.DataFrame) and not exp_data.empty:
                col = "expiration_date" if "expiration_date" in exp_data.columns else "strike_time" if "strike_time" in exp_data.columns else None
                if col:
                    for raw in exp_data[col].tolist():
                        exp = str(raw).replace("-", "")[:8]
                        if len(exp) == 8:
                            expirations.append(exp)
            elif isinstance(exp_data, list):
                for raw in exp_data:
                    exp = str(raw).replace("-", "")[:8]
                    if len(exp) == 8:
                        expirations.append(exp)

        expirations = expirations[:2]
        if not expirations:
            return _DimensionResult(summary="option expirations unavailable", signals=["option expirations unavailable"])

        call_oi = 0
        put_oi = 0
        call_vol = 0
        put_vol = 0
        call_notional = 0.0
        put_notional = 0.0
        call_count = 0
        put_count = 0
        avg_iv_values = []
        unusual_hits = []

        for exp in expirations:
            for right in ("C", "P"):
                ret, chain = conn.get_option_chain(ticker, exp, right)
                if ret != RET_OK or not chain or not isinstance(chain, dict):
                    continue
                options = chain.get("options") or []
                for opt in options:
                    strike = _as_float(opt.get("strike"))
                    bid = _as_float(opt.get("bid"))
                    ask = _as_float(opt.get("ask"))
                    last = _as_float(opt.get("last"))
                    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else (bid or ask or last)
                    volume = _as_int(opt.get("volume"))
                    oi = _as_int(opt.get("open_interest"))
                    iv = _as_float(opt.get("implied_volatility"))
                    if iv:
                        avg_iv_values.append(iv)
                    notional = volume * max(mid, 0) * 100
                    if right == "C":
                        call_oi += oi
                        call_vol += volume
                        call_notional += notional
                        call_count += 1
                    else:
                        put_oi += oi
                        put_vol += volume
                        put_notional += notional
                        put_count += 1
                    if oi > 0 and volume / max(oi, 1) >= 2.0 and notional >= 50_000:
                        unusual_hits.append(f"{right} strike {strike:.2f} {volume / max(oi, 1):.1f}x fresh volume")

        if call_oi == 0 and put_oi == 0 and call_vol == 0 and put_vol == 0:
            return _DimensionResult(summary="no option chain data", signals=["no option chain data"])

        pcr_oi = put_oi / max(call_oi, 1)
        pcr_vol = put_vol / max(call_vol, 1)
        skew = call_notional - put_notional
        avg_iv = sum(avg_iv_values) / len(avg_iv_values) if avg_iv_values else 0.0

        signals = [f"PCR OI {pcr_oi:.2f}", f"PCR vol {pcr_vol:.2f}"]
        if unusual_hits:
            signals.extend(unusual_hits[:2])
        if avg_iv:
            signals.append(f"avg IV {avg_iv:.2f}")

        if pcr_oi < 0.85 and pcr_vol < 0.85:
            bias = "bullish"
        elif pcr_oi > 1.15 and pcr_vol > 1.15:
            bias = "bearish"
        else:
            bias = "neutral"

        if bias == "bullish":
            summary = f"call skew {pcr_oi:.2f} PCR"
        elif bias == "bearish":
            summary = f"put skew {pcr_oi:.2f} PCR"
        else:
            summary = f"balanced options flow {pcr_oi:.2f} PCR"

        score = _clamp(50.0 + (1 - min(pcr_oi, 2.0)) * 20.0 - max(pcr_oi - 1.0, 0) * 15.0 + (5.0 if unusual_hits else 0.0))
        confidence = _clamp((call_count + put_count) * 10.0 + (10.0 if avg_iv else 0.0))

        return _DimensionResult(
            bias=bias,
            score=score,
            confidence=confidence,
            summary=summary,
            signals=signals,
            raw={
                "call_oi": call_oi,
                "put_oi": put_oi,
                "call_volume": call_vol,
                "put_volume": put_vol,
                "call_notional": round(call_notional, 2),
                "put_notional": round(put_notional, 2),
                "pcr_oi": round(pcr_oi, 4),
                "pcr_vol": round(pcr_vol, 4),
                "skew_notional": round(skew, 2),
                "avg_iv": round(avg_iv, 4),
                "expirations": expirations,
            },
        )


def _overlay_fit_annotation(signal: dict, overlay: dict | None) -> tuple[str, list[str]]:
    if not overlay or overlay.get("verdict") == "unknown":
        return "unknown", []

    signal_type = str(signal.get("signal_type") or "").lower()
    option_type = str(signal.get("option_type") or "").upper()
    bias = str(overlay.get("bias") or "unknown").lower()
    verdict = str(overlay.get("verdict") or "unknown").lower()
    warnings: list[str] = []

    if signal_type in {"csp", "put"} or option_type == "PUT":
        if bias == "bullish":
            fit = "supporting"
        elif bias == "bearish":
            fit = "caution"
            warnings.append("Overlay is bearish for a CSP")
        else:
            fit = "neutral"
    elif signal_type in {"covered_call", "call"} or option_type == "CALL":
        if bias == "bearish":
            fit = "supporting"
        elif bias == "bullish":
            fit = "caution"
            warnings.append("Overlay is bullish and can cap covered-call upside")
        else:
            fit = "neutral"
    else:
        fit = verdict if verdict in {"confirming", "caution", "conflict"} else "neutral"

    if verdict == "conflict":
        fit = "conflict"
        warnings.append("Overlay shows conflicting capital, technical, and derivatives signals")
    elif verdict == "caution" and fit == "neutral":
        fit = "caution"

    return fit, warnings[:2]


def apply_signal_overlay(signal: dict, overlay: dict | None) -> dict:
    enriched = dict(signal or {})
    overlay_payload = overlay or {}
    enriched["signal_overlay"] = overlay_payload

    fit, warnings = _overlay_fit_annotation(enriched, overlay_payload)
    enriched["signal_overlay_fit"] = fit
    enriched["signal_overlay_warnings"] = warnings

    summary = overlay_payload.get("summary") or overlay_payload.get("verdict") or "unknown"
    enriched["signal_overlay_summary"] = summary

    if warnings:
        existing = list(enriched.get("warnings") or [])
        for warning in warnings:
            if warning not in existing:
                existing.append(warning)
        enriched["warnings"] = existing

    if fit in {"caution", "conflict"}:
        rationale = list(enriched.get("rationale") or [])
        overlay_note = f"Overlay: {summary}"
        if overlay_note not in rationale:
            rationale.append(overlay_note)
        enriched["rationale"] = rationale

    return enriched


def fetch_signal_overlay_map(tickers: list[str], overlay_service: SignalOverlayService | None = None, refresh: bool = False) -> dict[str, dict]:
    service = overlay_service or get_signal_overlay_service()
    if service is None:
        return {}
    return service.get_overlay_map(tickers, refresh=refresh)


def enrich_signals_with_overlay(signals: list[dict], overlay_map: dict[str, dict] | None = None, overlay_service: SignalOverlayService | None = None, refresh: bool = False) -> list[dict]:
    if not signals:
        return []
    if overlay_map is None:
        tickers = []
        seen = set()
        for signal in signals:
            ticker = canonical_underlying(signal.get("ticker", ""))
            if ticker and ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)
        overlay_map = fetch_signal_overlay_map(tickers, overlay_service=overlay_service, refresh=refresh)

    enriched = []
    for signal in signals:
        ticker = canonical_underlying(signal.get("ticker", ""))
        overlay = overlay_map.get(ticker) if overlay_map else None
        enriched.append(apply_signal_overlay(signal, overlay))
    return enriched

def get_signal_overlay_service():
    from api.services.config import get_config

    return SignalOverlayService(config_provider=get_config())
