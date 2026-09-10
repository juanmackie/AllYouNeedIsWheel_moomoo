import csv
import io
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger("api.services.alpha_vantage")

ALPHA_VANTAGE_BASE = "https://www.alphavantage.co/query"
_UNSET = object()

# Free-tier documented allowance (25 requests/day, ~5 calls/minute). The exact
# numeric allowance is operational guidance for the dashboard, not a hard gate;
# the provider still enforces its own 12s minimum spacing and a failure cooldown.
ALPHA_VANTAGE_FREE_DAILY_ALLOWANCE = 25

# Cooldowns after a failed bulk fetch so a broken/quota-exhausted provider is
# not hammered by every ticker lookup within one refresh. Quota ("Information" /
# "Note" / 429) means retrying is useless for the rest of the cache window;
# transient errors (network/timeout/5xx/invalid key) get a shorter cooldown.
_QUOTA_COOLDOWN_SECONDS = 6 * 3600
_ERROR_COOLDOWN_SECONDS = 15 * 60

_cache: Optional[Dict] = None
_cache_timestamp: Optional[datetime] = None
_cache_ttl_hours = 6
_last_request_time: float = 0
_min_request_interval = 12.0
_cache_lock = threading.Lock()
# wall-clock timestamp until which refresh attempts are skipped (failure backoff)
_cooldown_until: float = 0.0

# Sentinel for a provider that has never attempted a request yet.
_STATUS_IDLE = "idle"
_STATUS_MISSING_KEY = "missing_key"
_STATUS_OK = "ok"
_STATUS_QUOTA = "quota"
_STATUS_ERROR = "error"


class AlphaVantageEarningsProvider:
    def __init__(self, api_key: Optional[str] = _UNSET):
        if api_key is _UNSET:
            self.api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
        else:
            self.api_key = api_key
        self.status: str = _STATUS_IDLE
        self.last_error: Optional[str] = None
        self.last_attempt_at: Optional[datetime] = None
        self.last_success_at: Optional[datetime] = None
        # Per-day request accounting (best-effort; resets on date change).
        self._requests_date: str = ""
        self._requests_today: int = 0
        if not self.api_key:
            self.status = _STATUS_MISSING_KEY
            self.last_error = "ALPHA_VANTAGE_API_KEY is not set; Alpha Vantage provider is unavailable"
            logger.warning(self.last_error)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _record_request(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._requests_date:
            self._requests_date = today
            self._requests_today = 0
        self._requests_today += 1

    def _rate_limit(self):
        global _last_request_time
        elapsed = time.time() - _last_request_time
        if elapsed < _min_request_interval:
            time.sleep(_min_request_interval - elapsed)
        _last_request_time = time.time()

    def _set_status(self, status: str, error: Optional[str] = None) -> None:
        self.status = status
        if error is not None:
            self.last_error = error

    def _classify_response(self, resp) -> str:
        """Classify an Alpha Vantage HTTP response into ok / quota / error.

        Alpha Vantage returns HTTP 200 with a JSON body for most failure modes:
        - ``Error Message``  -> invalid/missing apikey, malformed request (error)
        - ``Note``           -> per-minute frequency limit (quota)
        - ``Information``    -> daily allowance exhausted (quota)
        A plain HTTP 429 also means rate limited.
        """
        status_code = int(getattr(resp, "status_code", 0) or 0)
        text = str(getattr(resp, "text", "") or "").strip()

        if status_code == 429:
            self._set_status(_STATUS_QUOTA, f"HTTP {status_code}: Alpha Vantage rate limited")
            return _STATUS_QUOTA
        if status_code >= 500:
            self._set_status(_STATUS_ERROR, f"HTTP {status_code}: Alpha Vantage server error")
            return _STATUS_ERROR
        if status_code != 200:
            self._set_status(_STATUS_ERROR, f"HTTP {status_code}: {text[:200]}")
            return _STATUS_ERROR

        if text.startswith("{"):
            try:
                payload = json.loads(text)
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                if payload.get("Error Message"):
                    self._set_status(_STATUS_ERROR, str(payload["Error Message"]))
                    return _STATUS_ERROR
                note = payload.get("Note") or payload.get("Information")
                if note:
                    self._set_status(_STATUS_QUOTA, str(note))
                    return _STATUS_QUOTA

        self._set_status(_STATUS_OK, None)
        return _STATUS_OK

    def _fetch_csv(self) -> Optional[str]:
        if not self.available:
            self._set_status(_STATUS_MISSING_KEY, "ALPHA_VANTAGE_API_KEY is not set")
            return None

        import requests

        params = {
            "function": "EARNINGS_CALENDAR",
            "horizon": "3month",
            "datatype": "csv",
            "apikey": self.api_key,
        }
        self._rate_limit()
        self._record_request()
        self.last_attempt_at = datetime.now()
        try:
            resp = requests.get(ALPHA_VANTAGE_BASE, params=params, timeout=30)
        except requests.Timeout:
            self._set_status(_STATUS_ERROR, "Alpha Vantage request timed out")
            logger.warning("%s", self.last_error)
            return None
        except Exception as e:
            self._set_status(_STATUS_ERROR, f"Alpha Vantage request failed: {e}")
            logger.warning("%s", self.last_error)
            return None

        if self._classify_response(resp) != _STATUS_OK:
            logger.warning("Alpha Vantage fetch classified %s: %s", self.status, self.last_error)
            return None

        text = resp.text or ""
        if len(text) <= 50:
            self._set_status(_STATUS_ERROR, f"Alpha Vantage returned short/empty CSV ({len(text)} chars)")
            logger.warning("%s", self.last_error)
            return None

        self.last_success_at = datetime.now()
        return text

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
        global _cache, _cache_timestamp, _cooldown_until
        if time.time() < _cooldown_until:
            logger.debug("Alpha Vantage cache refresh skipped — in failure cooldown (status=%s)", self.status)
            return
        if _cache_lock.acquire(blocking=False):
            try:
                csv_text = self._fetch_csv()
                if csv_text:
                    _cache = self._parse_csv(csv_text)
                    _cache_timestamp = datetime.now()
                    _cooldown_until = 0.0
                    self._set_status(_STATUS_OK, None)
                    logger.info("Alpha Vantage earnings cache refreshed: %d tickers", len(_cache))
                else:
                    # Never cache an empty/error calendar as valid 6h data.
                    if self.status == _STATUS_QUOTA:
                        _cooldown_until = time.time() + _QUOTA_COOLDOWN_SECONDS
                    elif self.status == _STATUS_ERROR:
                        _cooldown_until = time.time() + _ERROR_COOLDOWN_SECONDS
                    logger.warning(
                        "Failed to refresh Alpha Vantage earnings cache (status=%s); keeping previous cache",
                        self.status,
                    )
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

    def get_status(self) -> dict:
        """Return a UI-facing status block for the Alpha Vantage provider."""
        cache_age_hours: Optional[float] = None
        if _cache_timestamp:
            cache_age_hours = round((datetime.now() - _cache_timestamp).total_seconds() / 3600, 2)
        if not self.available:
            return {
                "available": False,
                "status": _STATUS_MISSING_KEY,
                "error": "ALPHA_VANTAGE_API_KEY is not set",
                "last_attempt_at": None,
                "last_success_at": None,
                "requests_today": self._requests_today,
                "daily_allowance": ALPHA_VANTAGE_FREE_DAILY_ALLOWANCE,
                "cache_entries": 0,
                "cache_age_hours": None,
            }
        return {
            "available": True,
            "status": self.status or _STATUS_IDLE,
            "error": self.last_error,
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "requests_today": self._requests_today,
            "daily_allowance": ALPHA_VANTAGE_FREE_DAILY_ALLOWANCE,
            "cache_entries": len(_cache or {}),
            "cache_age_hours": cache_age_hours,
        }
