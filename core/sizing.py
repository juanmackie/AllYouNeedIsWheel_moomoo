"""Account-aware exposure helpers.

Pure, broker-free functions used by the recommendation pipeline to make picks
portfolio-aware. All inputs come from the portfolio context dict built by
``api/services/portfolio_context``.

Note: per-pick income-at-size / cash-remaining arithmetic is intentionally
done client-side in ``top-recommendations.js`` from broker fields already in
the signal payload — a server-side duplicate would be waste.
"""

from __future__ import annotations

from core.ticker_utils import earnings_underlying_ticker


def existing_short_exposure_by_underlying(portfolio_context: dict | None) -> dict[str, int]:
    """Count open short-option contracts per canonical underlying.

    Combines ``short_calls`` and ``short_puts`` maps from the portfolio
    context. Keys may be bare tickers or OCC-style contract codes, so
    ``earnings_underlying_ticker`` normalizes them before grouping.

    Returns {} for empty/missing input. Used by the scan pipeline so the
    per-underlying diversity cap accounts for what you already have on.
    """
    result: dict[str, int] = {}
    if not isinstance(portfolio_context, dict):
        return result
    for field in ("short_puts", "short_calls"):
        contracts_map = portfolio_context.get(field, {}) or {}
        if not isinstance(contracts_map, dict):
            continue
        for symbol, contracts in contracts_map.items():
            underlying = earnings_underlying_ticker(str(symbol or "")).upper()
            if not underlying:
                continue
            try:
                result[underlying] = result.get(underlying, 0) + max(int(contracts or 0), 0)
            except (TypeError, ValueError):
                continue
    return result
