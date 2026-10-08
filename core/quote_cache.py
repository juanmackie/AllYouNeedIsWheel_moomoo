"""Quote-cache and pending-request coordination for MoomooConnection.

Owns the option-chain / expiration TTL caches and the single-flight
pending-request map, so the connection class only does broker I/O.
Thread-safety policy lives here (F-S1 decomposition).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from core.session_clock import MARKET_TIMEZONE, SessionCalendar

logger = logging.getLogger("core.quote_cache")


class OptionChainCache:
    """TTL caches for option chains and expiration lists.

    Freshness rule: while the US market is open entries expire after their
    TTL; outside market hours cached data is reused (broker is closed, so a
    re-fetch cannot produce newer quotes) unless ``broker_cache_after_hours``
    is disabled.
    """

    def __init__(
        self,
        chain_ttl: int = 180,
        expiration_ttl: int = 300,
        broker_cache_after_hours: bool = True,
        session_calendar: SessionCalendar | None = None,
        clock=None,
        timestamp_clock=None,
    ):
        self._chain_cache: dict = {}
        self._expiration_cache: dict = {}
        self._range_chain_cache: dict = {}
        self._lock = threading.Lock()
        self._chain_ttl = int(chain_ttl)
        self._expiration_ttl = int(expiration_ttl)
        self.broker_cache_after_hours = bool(broker_cache_after_hours)
        self.session_calendar = session_calendar or SessionCalendar.weekday_fallback()
        self._clock = clock or (lambda: datetime.now(MARKET_TIMEZONE))
        self._timestamp_clock = timestamp_clock or time.time

    def get_option_chain(self, symbol, expiration, right):
        cache_key = f"{symbol}_{expiration}_{right}"
        with self._lock:
            if cache_key in self._chain_cache:
                cached_data, timestamp = self._chain_cache[cache_key]
                cache_age = self._timestamp_clock() - timestamp
                if self.session_calendar.is_open(self._clock()):
                    if 0 <= cache_age < self._chain_ttl:
                        logger.debug(f"Using cached option chain for {cache_key}")
                        return cached_data
                elif self.broker_cache_after_hours:
                    cached_at = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(MARKET_TIMEZONE)
                    last_close = self.session_calendar.last_regular_close(self._clock())
                    if last_close is not None and cached_at >= last_close:
                        logger.debug(f"Using after-close option chain for {cache_key} (age={cache_age:.0f}s)")
                        return cached_data
                del self._chain_cache[cache_key]
        return None

    def get_option_chain_range(self, symbol, right, start, end, band):
        key = (
            str(symbol),
            str(right),
            str(start),
            str(end),
            round(float(band["min_strike"]), 8),
            round(float(band["max_strike"]), 8),
        )
        with self._lock:
            cached = self._range_chain_cache.get(key)
            if cached is None:
                return None
            cached_data, timestamp = cached
            age = self._timestamp_clock() - timestamp
            if self.session_calendar.is_open(self._clock()):
                if 0 <= age < self._chain_ttl:
                    return cached_data
                del self._range_chain_cache[key]
                return None
            if self.broker_cache_after_hours:
                cached_at = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(MARKET_TIMEZONE)
                last_close = self.session_calendar.last_regular_close(self._clock())
                if last_close is not None and cached_at >= last_close:
                    return cached_data
            del self._range_chain_cache[key]
        return None

    def cache_option_chain_range(self, symbol, right, start, end, band, data):
        key = (
            str(symbol),
            str(right),
            str(start),
            str(end),
            round(float(band["min_strike"]), 8),
            round(float(band["max_strike"]), 8),
        )
        with self._lock:
            self._range_chain_cache[key] = (data, self._timestamp_clock())

    def cache_option_chain(self, symbol, expiration, right, data):
        cache_key = f"{symbol}_{expiration}_{right}"
        with self._lock:
            self._chain_cache[cache_key] = (data, self._timestamp_clock())
            logger.debug(f"Cached option chain for {cache_key}")

    def get_option_expirations(self, symbol):
        with self._lock:
            if symbol in self._expiration_cache:
                cached_data, timestamp = self._expiration_cache[symbol]
                cache_age = self._timestamp_clock() - timestamp
                if self.session_calendar.is_open(self._clock()):
                    if 0 <= cache_age < self._expiration_ttl:
                        return cached_data
                    del self._expiration_cache[symbol]
                elif self.broker_cache_after_hours:
                    cached_at = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(MARKET_TIMEZONE)
                    last_close = self.session_calendar.last_regular_close(self._clock())
                    if last_close is not None and cached_at >= last_close:
                        return cached_data
        return None

    def cache_option_expirations(self, symbol, data):
        with self._lock:
            self._expiration_cache[symbol] = (data, self._timestamp_clock())


class PendingRequestCoordinator:
    """Single-flight coordination: identical concurrent requests share one
    broker call. Results are remembered briefly so late waiters still see them."""

    def __init__(self, result_ttl_seconds: float = 1.0):
        self._requests: dict = {}
        self._lock = threading.Lock()
        self._result_ttl_seconds = float(result_ttl_seconds)

    def get_or_create(self, request_key):
        with self._lock:
            if request_key in self._requests:
                return self._requests[request_key], False
            entry = {
                "event": threading.Event(),
                "result": None,
                "completed_at": None,
            }
            self._requests[request_key] = entry
            return entry, True

    def cleanup(self, request_key):
        with self._lock:
            entry = self._requests.get(request_key)
            if not entry:
                return
            if not entry.get("event") or not entry["event"].is_set():
                return
            self._requests.pop(request_key, None)

    def complete(self, request_key, result):
        with self._lock:
            entry = self._requests.get(request_key)
            if entry is not None:
                entry["result"] = result
                entry["completed_at"] = time.time()
                entry["event"].set()
                timer = threading.Timer(
                    self._result_ttl_seconds,
                    self.cleanup,
                    args=(request_key,),
                )
                timer.daemon = True
                timer.start()

    def wait_for(self, request_key, timeout=90):
        entry, is_new = self.get_or_create(request_key)
        if not is_new:
            logger.debug(f"Waiting for pending request: {request_key}")
            entry["event"].wait(timeout=timeout)
            with self._lock:
                current = self._requests.get(request_key)
                if current is not None:
                    return current.get("result")
            logger.warning(f"Timeout waiting for pending request: {request_key}")
            return None
        return None
