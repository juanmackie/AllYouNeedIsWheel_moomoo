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


def deployment_plan(candidates: list[dict] | None, cash_available: float, max_per_underlying: int = 1) -> list[dict]:
    """Allocate remaining CSP cash across already-ranked candidates.

    Candidates are expected to be in backend rank order. This helper never
    creates a signal: it only sizes existing candidates using their broker-
    derived ``cash_required`` and ``recommended_contracts`` fields. Each
    underlying contributes at most one candidate by default, while that
    candidate may use multiple affordable contracts.
    """
    try:
        remaining = max(float(cash_available or 0), 0.0)
    except (TypeError, ValueError):
        remaining = 0.0
    try:
        underlying_cap = max(int(max_per_underlying), 0)
    except (TypeError, ValueError):
        underlying_cap = 1
    selected: list[dict] = []
    counts: dict[str, int] = {}

    for candidate in candidates or []:
        if not isinstance(candidate, dict) or str(candidate.get("option_type", "")).upper() != "PUT":
            continue
        try:
            cash_required = float(candidate.get("cash_required", 0) or 0)
            requested = int(candidate.get("recommended_contracts", 0) or 0)
        except (TypeError, ValueError):
            continue
        if cash_required <= 0 or requested <= 0 or remaining < cash_required:
            continue

        underlying = earnings_underlying_ticker(str(candidate.get("ticker", "") or "")).upper()
        if not underlying or counts.get(underlying, 0) >= underlying_cap:
            continue
        contracts = min(requested, int(remaining // cash_required))
        if contracts <= 0:
            continue

        planned = dict(candidate)
        planned["deployment_contracts"] = contracts
        planned["deployment_cash_required"] = round(contracts * cash_required, 2)
        planned["deployment_income"] = round(
            contracts * float(candidate.get("bid_premium_per_contract", candidate.get("premium_per_contract", 0)) or 0),
            2,
        )
        remaining = round(remaining - planned["deployment_cash_required"], 2)
        planned["deployment_cash_remaining"] = remaining
        selected.append(planned)
        counts[underlying] = counts.get(underlying, 0) + 1

    return selected


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
