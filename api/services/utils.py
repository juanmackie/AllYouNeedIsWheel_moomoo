"""
Shared utilities for API services.

Currently provides:
- clean_yfinance_ticker(): Strip moomoo/exchange prefixes for yfinance compatibility.
"""

import re
import logging

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
