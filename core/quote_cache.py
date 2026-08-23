"""Quote-cache and pending-request coordination for MoomooConnection.

Owns the option-chain / expiration TTL caches and the single-flight
pending-request map, so the connection class only does broker I/O.
Thread-safety policy lives here (F-S1 decomposition).
"""

from __future__ import annotations

import logging
import threading
import time

from core.utils import is_market_open

logger = logging.getLogger("core.quote_cache")


class OptionChainCache:
    """TTL caches for option chains and expiration lists.

    Freshness rule: while the US market is open entries expire after their
    TTL; outside market hours cached data is reused (broker is closed, so a
    re-fetch cannot produce newer quotes) unless ``broker_cache_after_hours``
    is disabled.
    """

    def __init__(self, chain_ttl: int = 180, expiration_ttl: int = 300, broker_cache_after_hours: bool = True):
        self._chain_cache: dict = {}
        self._expiration_cache: dict = {}
        self._lock = threading.Lock()
        self._chain_ttl = int(chain_ttl)
        self._expiration_ttl = int(expiration_ttl)
        self.broker_cache_after_hours = bool(broker_cache_after_hours)

    def get_option_chain(self, symbol, expiration, right):
        cache_key = f"{symbol}_{expiration}_{right}"
        with self._lock:
            if cache_key in self._chain_cache:
                cached_data, timestamp = self._chain_cache[cache_key]
                cache_age = time.time() - timestamp
                if is_market_open():
                    if cache_age < self._chain_ttl:
                        logger.debug(f"Using cached option chain for {cache_key}")
                        return cached_data
                elif self.broker_cache_after_hours:
                    logger.debug(f"Using cached option chain for {cache_key} (after-hours, age={cache_age:.0f}s)")
                    return cached_data
                del self._chain_cache[cache_key]
        return None

    def cache_option_chain(self, symbol, expiration, right, data):
        cache_key = f"{symbol}_{expiration}_{right}"
        with self._lock:
            self._chain_cache[cache_key] = (data, time.time())
            logger.debug(f"Cached option chain for {cache_key}")

    def get_option_expirations(self, symbol):
        with self._lock:
            if symbol in self._expiration_cache:
                cached_data, timestamp = self._expiration_cache[symbol]
                cache_age = time.time() - timestamp
                if is_market_open():
                    if cache_age < self._expiration_ttl:
                        return cached_data
                    del self._expiration_cache[symbol]
                elif self.broker_cache_after_hours:
                    return cached_data
        return None

    def cache_option_expirations(self, symbol, data):
        with self._lock:
            self._expiration_cache[symbol] = (data, time.time())


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
