"""
Read-only earnings volatility signal scanner.

The scanner uses public market data to produce simple "traffic light" signals.
It does not create, stage, or execute orders.
"""

import logging
import math
import time
from datetime import datetime
from typing import Iterable

from api.services.utils import clean_yfinance_ticker
from api.services.utils import get_yfinance_ticker
from core.earnings_vol_decision import EarningsVolSignal, classify_earnings_vol_signal

logger = logging.getLogger("api.services.earnings_vol")


class EarningsVolSignalService:
    def __init__(self, config=None, iv_earnings_service=None):
        self.config = config or {}
        self.iv_earnings_service = iv_earnings_service
        self._cache = {}
        self._cache_ttl_seconds = 15 * 60

    def get_signals(self, tickers: Iterable[str] | None = None, limit: int = 8, refresh: bool = False) -> dict:
        source_tickers = list(tickers or self.config.get("watchlist", []))
        normalized = []
        for ticker in source_tickers:
            clean = clean_yfinance_ticker(str(ticker)).upper()
            if clean and clean not in normalized:
                normalized.append(clean)

        limit = max(1, min(int(limit or 8), 25))
        scan_tickers = normalized[: max(limit * 2, limit)]
        signals = []
        errors = []

        for ticker in scan_tickers:
            signal = self._get_signal(ticker, refresh=refresh)
            if signal:
                signals.append(signal.to_dict())
            else:
                errors.append(ticker)

        signal_rank = {"GREEN": 0, "YELLOW": 1, "WATCH": 2, "AVOID": 3}
        signals.sort(key=lambda item: (signal_rank.get(item.get("signal"), 9), -(item.get("score") or 0)))

        return {
            "success": True,
            "generated_at": datetime.now().isoformat(),
            "count": min(len(signals), limit),
            "signals": signals[:limit],
            "scanned": len(scan_tickers),
            "errors": errors,
            "read_only": True,
        }

    def _get_signal(self, ticker: str, refresh: bool = False) -> EarningsVolSignal | None:
        cached = self._cache.get(ticker)
        if not refresh and cached and time.time() - cached["timestamp"] < self._cache_ttl_seconds:
            return cached["signal"]

        try:
            signal = self._build_signal(ticker)
        except Exception as exc:
            logger.warning("Failed to build earnings vol signal for %s: %s", ticker, exc)
            signal = classify_earnings_vol_signal({
                "ticker": ticker,
                "earnings_date": None,
                "days_to_earnings": None,
            })
            signal.blockers.append("Market data unavailable")

        self._cache[ticker] = {"timestamp": time.time(), "signal": signal}
        return signal

    def _build_signal(self, ticker: str) -> EarningsVolSignal:
        yf_ticker = get_yfinance_ticker(ticker)
        earnings_info = self._get_earnings_info(ticker)
        earnings_date = earnings_info.get("earnings_date")
        days_to_earnings = earnings_info.get("days_to_earnings")
        time_of_day = earnings_info.get("time_of_day")
        earnings_source = earnings_info.get("earnings_source")

        hist = yf_ticker.history(period="45d")
        avg_volume_30d = None
        rv30 = None
        stock_price = None
        if hist is not None and not hist.empty:
            stock_price = float(hist["Close"].iloc[-1])
            if "Volume" in hist:
                avg_volume_30d = float(hist["Volume"].tail(30).mean())
            returns = hist["Close"].pct_change().dropna().tail(30)
            if len(returns) >= 10:
                rv30 = float(returns.std() * math.sqrt(252))

        options = list(getattr(yf_ticker, "options", []) or [])
        front_expiration, back_expiration = self._pick_expirations(options, earnings_date)

        metrics = {
            "ticker": ticker,
            "earnings_date": earnings_date,
            "days_to_earnings": days_to_earnings,
            "time_of_day": time_of_day,
            "earnings_source": earnings_source,
            "front_expiration": front_expiration,
            "back_expiration": back_expiration,
            "rv30": rv30,
            "avg_volume_30d": avg_volume_30d,
        }

        if stock_price and front_expiration and back_expiration:
            front = self._atm_option_metrics(yf_ticker, front_expiration, stock_price)
            back = self._atm_option_metrics(yf_ticker, back_expiration, stock_price)
            if front and back:
                front_iv = front["iv"]
                back_iv = back["iv"]
                debit = None
                if front.get("mid") is not None and back.get("mid") is not None:
                    debit = round(max(back["mid"] - front["mid"], 0) * 100, 2)

                metrics.update({
                    "front_iv": front_iv,
                    "back_iv": back_iv,
                    "atm_strike": front.get("strike"),
                    "estimated_calendar_debit": debit,
                    "max_risk_per_contract": debit,
                    "spread_pct": front.get("spread_pct"),
                    "open_interest": front.get("open_interest"),
                    "option_volume": front.get("volume"),
                })

        return classify_earnings_vol_signal(metrics)

    def _get_earnings_info(self, ticker: str) -> dict:
        if not self.iv_earnings_service:
            return {"earnings_date": None, "days_to_earnings": None}

        info = self.iv_earnings_service.get_earnings_info(ticker)
        if not info.get("earnings_date"):
            try:
                self.iv_earnings_service.update_earnings_data(ticker)
                info = self.iv_earnings_service.get_earnings_info(ticker)
            except Exception as exc:
                logger.debug("Earnings refresh failed for %s: %s", ticker, exc)
        return info

    def _pick_expirations(self, expirations: list[str], earnings_date: str | None) -> tuple[str | None, str | None]:
        if not expirations:
            return None, None

        today = datetime.now().date()
        parsed = []
        for exp in expirations:
            try:
                parsed.append((datetime.strptime(exp, "%Y-%m-%d").date(), exp))
            except ValueError:
                continue
        parsed.sort()

        anchor = None
        if earnings_date:
            try:
                anchor = datetime.strptime(earnings_date, "%Y-%m-%d").date()
            except ValueError:
                anchor = None

        if not anchor:
            anchor = today

        front = next((exp for exp_date, exp in parsed if exp_date >= anchor), None)
        if not front:
            return None, None

        front_date = datetime.strptime(front, "%Y-%m-%d").date()
        back = next((exp for exp_date, exp in parsed if 21 <= (exp_date - front_date).days <= 50), None)
        if not back:
            back = next((exp for exp_date, exp in parsed if exp_date > front_date), None)
        return front, back

    def _atm_option_metrics(self, yf_ticker, expiration: str, stock_price: float) -> dict | None:
        chain = yf_ticker.option_chain(expiration)
        candidates = []
        for frame in [getattr(chain, "calls", None), getattr(chain, "puts", None)]:
            if frame is None or frame.empty:
                continue
            frame = frame.copy()
            frame["distance"] = (frame["strike"] - stock_price).abs()
            row = frame.sort_values("distance").iloc[0]
            bid = float(row.get("bid") or 0)
            ask = float(row.get("ask") or 0)
            last = float(row.get("lastPrice") or 0)
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 else last if last > 0 else None
            spread_pct = ((ask - bid) / mid * 100) if bid > 0 and ask > 0 and mid else None
            candidates.append({
                "strike": float(row.get("strike") or 0),
                "iv": float(row.get("impliedVolatility") or 0),
                "mid": mid,
                "spread_pct": spread_pct,
                "open_interest": int(row.get("openInterest") or 0),
                "volume": int(row.get("volume") or 0),
            })

        if not candidates:
            return None

        iv_values = [item["iv"] for item in candidates if item["iv"] > 0]
        if not iv_values:
            return None

        best = max(candidates, key=lambda item: item["open_interest"] + item["volume"])
        best["iv"] = sum(iv_values) / len(iv_values)
        return best
