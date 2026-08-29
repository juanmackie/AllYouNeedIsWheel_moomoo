"""
Ticker formatting and normalization utilities for moomoo.
Extracted from core/connection.py for maintainability.
"""

import re
import threading
import time

from core.logging_config import get_logger

logger = get_logger("ayniwheel.connection", "moomoo")

_OPTION_CODE_RE = re.compile(r"^(?P<underlying>[A-Z0-9-]+)(?P<expiry>\d{6})(?P<right>[CP])(?P<strike>\d+)$")


class TickerCache:
    """
    Stock price cache with TTL and failure tracking.
    Thread-safe for concurrent access.
    """

    def __init__(self, price_ttl=120, failed_ttl=300):
        self._stock_price_cache = {}
        self._stock_price_cache_lock = threading.Lock()
        self._stock_price_ttl = price_ttl

        self._failed_tickers = {}
        self._failed_tickers_lock = threading.Lock()
        self._failed_ticker_ttl = failed_ttl

    def get_cached_price(self, symbol):
        with self._stock_price_cache_lock:
            if symbol in self._stock_price_cache:
                price, timestamp = self._stock_price_cache[symbol]
                if time.time() - timestamp < self._stock_price_ttl:
                    logger.debug(f"Using cached stock price for {symbol}: {price}")
                    return price
                del self._stock_price_cache[symbol]
        return None

    def cache_price(self, symbol, price):
        with self._stock_price_cache_lock:
            self._stock_price_cache[symbol] = (price, time.time())
            logger.debug(f"Cached stock price for {symbol}: {price}")

    def is_ticker_failed(self, symbol):
        with self._failed_tickers_lock:
            if symbol in self._failed_tickers:
                failure_time = self._failed_tickers[symbol]
                if time.time() - failure_time < self._failed_ticker_ttl:
                    logger.debug(f"Skipping {symbol} - failed quote rights (cached)")
                    return True
                del self._failed_tickers[symbol]
        return False

    def mark_ticker_failed(self, symbol):
        with self._failed_tickers_lock:
            self._failed_tickers[symbol] = time.time()
            logger.info(f"Cached quote-rights failure for {symbol} (will skip for {self._failed_ticker_ttl}s)")

    def get_cache_stats(self):
        with self._stock_price_cache_lock:
            price_size = len(self._stock_price_cache)
        with self._failed_tickers_lock:
            failed_size = len(self._failed_tickers)
        return price_size, failed_size


def format_symbol(symbol):
    """Format symbol to moomoo format (e.g., US.AAPL)."""
    if "." not in symbol:
        return f"US.{symbol}"
    return symbol


def canonical_underlying(ticker):
    """
    Normalize a ticker to its canonical (bare) form for deduplication.
    Strips known exchange prefixes (US., HK., etc.) and returns the
    bare ticker used for grouping recommendations and display.

    Examples:
        'US.UBER' -> 'UBER'
        'UBER' -> 'UBER'
        'US.BRK.B' -> 'BRK-B'
        'HK.0700' -> '0700'

    This is a standalone implementation (no cross-package imports) so it
    can be safely used from core, api, or frontend JS equivalents.
    """
    if not ticker or not isinstance(ticker, str):
        return ticker or ""
    s = ticker
    if ":" in s:
        s = s.split(":", 1)[-1]
    if "." in s:
        parts = s.split(".", 1)
        known = {"US", "HK", "SZ", "SH", "SS", "SG", "JP", "UK", "DE", "FR", "IT", "CA", "AU", "NZ"}
        if len(parts) == 2 and parts[0] in known:
            s = parts[1]
    s = s.replace(".", "-")
    s = s.replace("/", "-").replace("$", "-")
    return s


def earnings_underlying_ticker(ticker):
    """
    Normalize any supported ticker-like input to the underlying stock symbol.

    This is stricter than canonical_underlying() because earnings lookups should
    never receive full option contract codes.
    """
    if not ticker or not isinstance(ticker, str):
        return ticker or ""

    raw = ticker.strip()
    if not raw:
        return ""

    s = canonical_underlying(raw).strip().upper()
    if not s:
        return ""

    match = _OPTION_CODE_RE.match(s)
    if match:
        return match.group("underlying")

    return s
