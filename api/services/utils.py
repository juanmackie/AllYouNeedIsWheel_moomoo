"""
Shared utilities for API services.

Currently provides:
- clean_yfinance_ticker(): Strip moomoo/exchange prefixes for yfinance compatibility.
- get_yfinance_ticker(): Convenience wrapper that lets yfinance manage its own session.
- shared yfinance request helpers with conservative rate limiting and TTL caches.
"""

import logging
from threading import Lock

from core.rate_limiter import RateLimiter
from core.ttl_cache import make_ttl_cache

logger = logging.getLogger('api.services.utils')

# Known market/exchange prefixes to strip from ticker symbols
KNOWN_PREFIXES = {'US', 'HK', 'SZ', 'SH', 'SS', 'SG', 'JP', 'UK', 'DE', 'FR', 'IT', 'CA', 'AU', 'NZ'}


def clean_yfinance_ticker(ticker: str) -> str:
    """
    Sanitize a ticker symbol for use with yfinance.

    Handles:
    - Moomoo prefixes: ``US.UBER`` → ``UBER``, ``HK.0700`` → ``0700``
    - Exchange prefixes: ``NASDAQ:AAPL`` → ``AAPL``, ``NYSE:BRK.B`` → ``BRK-B``
    - Dots in tickers: ``BRK.B`` → ``BRK-B`` (yfinance uses hyphens for class shares)
    - Moomoo-style with exchange prefix + dot: ``US.BRK.B`` → ``BRK-B``

    Args:
        ticker: Raw ticker symbol (e.g. ``US.UBER``, ``NASDAQ:AAPL``, ``BRK.B``)

    Returns:
        Cleaned ticker safe for yfinance download.
    """
    if not ticker or not isinstance(ticker, str):
        return ticker or ''

    original = ticker

    # --- Step 1: Strip exchange prefix via colon (e.g. "NASDAQ:AAPL") ---
    if ':' in ticker:
        ticker = ticker.split(':', 1)[-1]

    # --- Step 2: Strip moomoo-style market prefix (e.g. "US.UBER", "HK.0700") ---
    if '.' in ticker:
        parts = ticker.split('.', 1)
        if len(parts) == 2 and parts[0] in KNOWN_PREFIXES:
            ticker = parts[1]

    # --- Step 3: Replace dots with hyphens for yfinance compatibility (e.g. "BRK.B" → "BRK-B") ---
    ticker = ticker.replace('.', '-')

    # --- Step 4: Strip remaining invalid characters ---
    # Slashes ("/") cause path traversal in yfinance HTTP requests (e.g. "ORCL/PD" → 500)
    # Dollars ("$") are reserved URL characters
    ticker = ticker.replace('/', '-').replace('$', '-')

    if ticker != original:
        logger.debug(f"Cleaned ticker: '{original}' → '{ticker}'")

    return ticker


def validate_ticker(ticker: str) -> bool:
    """
    Return True if *ticker* can be processed by yfinance or moomoo.

    A ticker is invalid if:
    - It is empty/None
    - After normalisation it is empty
    - It contains characters that break yfinance URL parsing: ``/``, ``$``, spaces

    This is the **single validation gate** at system entry boundaries.
    Downstream services do not need to re-validate — they call
    :func:`clean_yfinance_ticker` and get a safe value.

    Args:
        ticker: Raw ticker to validate.

    Returns:
        ``True`` if the ticker is usable, ``False`` otherwise.
    """
    if not ticker or not isinstance(ticker, str):
        return False

    clean = clean_yfinance_ticker(ticker)
    if not clean:
        return False

    # Slashes ("/") break yfinance URL construction (e.g. "ORCL/PD" → path traversal → 500 error)
    # Dollars ("$") are yfinance special characters
    # Spaces break HTTP requests
    if '/' in ticker or '$' in ticker or ' ' in ticker:
        return False

    return True


def get_yfinance_ticker(ticker: str):
    """Create a yfinance Ticker. yfinance manages its own session (curl_cffi for modern versions)."""
    import yfinance as yf

    try:
        from curl_cffi import requests as curl_requests

        return yf.Ticker(ticker, session=curl_requests.Session())
    except Exception:
        return yf.Ticker(ticker)


def get_yfinance_history(ticker: str, period: str = "1d", ticker_factory=None):
    """Return cached yfinance history for a ticker."""
    return _SharedYFinanceBudget.get_history(ticker, period=period, ticker_factory=ticker_factory)


def get_yfinance_options(ticker: str, ticker_factory=None):
    """Return cached yfinance expiration dates for a ticker."""
    return _SharedYFinanceBudget.get_options(ticker, ticker_factory=ticker_factory)


def get_yfinance_option_chain(ticker: str, expiration: str, ticker_factory=None):
    """Return cached yfinance option-chain data for a ticker/expiration pair."""
    return _SharedYFinanceBudget.get_option_chain(ticker, expiration, ticker_factory=ticker_factory)


class _SharedYFinanceBudget:
    """Shared yfinance budget and caches for bursty dashboard scans."""

    _lock = Lock()
    _limiter = RateLimiter(
        max_requests_per_window=30,
        rate_limit_window=60,
        burst_threshold=5,
        burst_window=10,
    )
    _limiter._min_request_spacing = 1.0
    _history_cache = make_ttl_cache(maxsize=256, ttl=60)
    _options_cache = make_ttl_cache(maxsize=256, ttl=300)
    _chain_cache = make_ttl_cache(maxsize=512, ttl=300)

    @classmethod
    def _cache_key(cls, ticker: str, *parts) -> tuple:
        return (clean_yfinance_ticker(ticker),) + tuple(parts)

    @classmethod
    def _get_cached(cls, cache, key):
        with cls._lock:
            if key in cache:
                return cache[key]
        return None

    @classmethod
    def _set_cached(cls, cache, key, value):
        with cls._lock:
            cache[key] = value

    @classmethod
    def _fetch_with_budget(cls, cache, key, fetcher):
        cached = cls._get_cached(cache, key)
        if cached is not None or key in cache:
            return cached

        cls._limiter.check_rate_limit()
        value = fetcher()
        cls._set_cached(cache, key, value)
        return value

    @classmethod
    def get_history(cls, ticker: str, period: str = "1d", ticker_factory=None):
        clean = clean_yfinance_ticker(ticker)
        factory = ticker_factory or get_yfinance_ticker
        factory_key = "default" if ticker_factory is None else id(factory)
        key = cls._cache_key(clean, "history", period, factory_key)

        def _fetch():
            return factory(clean).history(period=period)

        return cls._fetch_with_budget(cls._history_cache, key, _fetch)

    @classmethod
    def get_options(cls, ticker: str, ticker_factory=None):
        clean = clean_yfinance_ticker(ticker)
        factory = ticker_factory or get_yfinance_ticker
        factory_key = "default" if ticker_factory is None else id(factory)
        key = cls._cache_key(clean, "options", factory_key)

        def _fetch():
            options = factory(clean).options or []
            return tuple(options)

        return cls._fetch_with_budget(cls._options_cache, key, _fetch)

    @classmethod
    def get_option_chain(cls, ticker: str, expiration: str, ticker_factory=None):
        clean = clean_yfinance_ticker(ticker)
        normalized = str(expiration or "").strip().replace("/", "-")
        if len(normalized) == 8 and normalized.isdigit():
            normalized = f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:8]}"
        factory = ticker_factory or get_yfinance_ticker
        factory_key = "default" if ticker_factory is None else id(factory)
        key = cls._cache_key(clean, "option_chain", normalized, factory_key)

        def _fetch():
            chain = factory(clean).option_chain(normalized)
            calls = chain.calls.copy(deep=True) if getattr(chain, "calls", None) is not None else None
            puts = chain.puts.copy(deep=True) if getattr(chain, "puts", None) is not None else None
            return {
                "source": "yfinance",
                "ticker": clean,
                "expiration": normalized,
                "calls": calls,
                "puts": puts,
            }

        return cls._fetch_with_budget(cls._chain_cache, key, _fetch)
