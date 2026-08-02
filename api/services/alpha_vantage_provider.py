import csv
import io
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger("api.services.alpha_vantage")

ALPHA_VANTAGE_BASE = "https://www.alphavantage.co/query"
_UNSET = object()

_cache: Optional[Dict] = None
_cache_timestamp: Optional[datetime] = None
_cache_ttl_hours = 6
_last_request_time: float = 0
_min_request_interval = 12.0
_cache_lock = threading.Lock()


class AlphaVantageEarningsProvider:
    def __init__(self, api_key: Optional[str] = _UNSET):
        if api_key is _UNSET:
            self.api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
        else:
            self.api_key = api_key
        if not self.api_key:
            logger.warning("ALPHA_VANTAGE_API_KEY is not set; Alpha Vantage provider will be unavailable")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _rate_limit(self):
        global _last_request_time
        elapsed = time.time() - _last_request_time
        if elapsed < _min_request_interval:
            time.sleep(_min_request_interval - elapsed)
        _last_request_time = time.time()

    def _fetch_csv(self) -> Optional[str]:
        import requests

        params = {
            "function": "EARNINGS_CALENDAR",
            "horizon": "3month",
            "datatype": "csv",
            "apikey": self.api_key,
        }
        self._rate_limit()
        try:
            resp = requests.get(ALPHA_VANTAGE_BASE, params=params, timeout=30)
            if resp.status_code == 200:
                text = resp.text
                if text and len(text) > 50:
                    return text
                logger.warning("Alpha Vantage returned unexpectedly short CSV (%d chars)", len(text or ""))
                return None
            logger.warning("Alpha Vantage HTTP %d: %s", resp.status_code, resp.text[:200])
            return None
        except Exception as e:
            logger.warning("Alpha Vantage request failed: %s", e)
            return None

    def _parse_csv(self, text: str) -> Dict[str, dict]:
        result = {}
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            symbol = (row.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            report_date = (row.get("reportDate") or "").strip()
            if not report_date:
                continue
            report_date = report_date[:10]
            existing = result.get(symbol)
            if existing and existing.get("reportDate", "")[:10] >= report_date:
                continue
            result[symbol] = {
                "symbol": symbol,
                "name": (row.get("name") or "").strip(),
                "reportDate": report_date,
                "fiscalDateEnding": (row.get("fiscalDateEnding") or "").strip()[:10],
                "estimate": self._parse_float(row.get("estimate")),
                "currency": (row.get("currency") or "").strip().upper(),
                "timeOfDay": self._normalize_time_of_day(row.get("timeOfTheDay") or row.get("timeOfDay")),
            }
        return result

    @staticmethod
    def _parse_float(val) -> Optional[float]:
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _normalize_time_of_day(raw: Optional[str]) -> str:
        if not raw:
            return ""
        val = raw.strip().lower()
        if val in ("post-market", "post market", "after-market", "after market", "after_close", "afterclose"):
            return "post-market"
        if val in ("pre-market", "pre market", "before-market", "before market", "before_open", "beforeopen"):
            return "pre-market"
        if val in ("during-market", "during market", "market-hours", "market hours"):
            return "during-market"
        return val

    def _is_cache_valid(self) -> bool:
        global _cache, _cache_timestamp
        if not _cache or not _cache_timestamp:
            return False
        return datetime.now() - _cache_timestamp < timedelta(hours=_cache_ttl_hours)

    def _refresh_cache(self):
        global _cache, _cache_timestamp
        if _cache_lock.acquire(blocking=False):
            try:
                csv_text = self._fetch_csv()
                if csv_text:
                    parsed = self._parse_csv(csv_text)
                    _cache = parsed
                    _cache_timestamp = datetime.now()
                    logger.info("Alpha Vantage earnings cache refreshed: %d tickers", len(_cache))
                else:
                    logger.warning("Failed to refresh Alpha Vantage earnings cache")
            finally:
                _cache_lock.release()
        else:
            logger.debug("Alpha Vantage cache refresh skipped — another thread is already refreshing")

    def get_earnings(self, ticker: str) -> Optional[dict]:
        if not self.available:
            return None
        clean_ticker = ticker.upper().strip()
        if not self._is_cache_valid():
            self._refresh_cache()
        global _cache
        if not _cache:
            return None
        return _cache.get(clean_ticker)

    def get_all_earnings(self) -> Dict[str, dict]:
        if not self.available:
            return {}
        if not self._is_cache_valid():
            self._refresh_cache()
        global _cache
        return _cache or {}
