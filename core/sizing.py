"""Account-aware exposure helpers.

Pure, broker-free functions used by the recommendation pipeline to make picks
portfolio-aware. All inputs come from the portfolio context dict built by
``api/services/portfolio_context``.

Note: per-pick income-at-size / cash-remaining arithmetic is intentionally
done client-side in ``top-recommendations.js`` from broker fields already in
the signal payload — a server-side duplicate would be waste.
"""

from __future__ import annotations

from math import isfinite

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


def remaining_underlying_capital(
    ticker: str,
    portfolio_context: dict | None,
    sizing_profile: dict | None,
) -> float | None:
    """Return remaining account-capital room for one underlying, or None if unknown/unconfigured.

    ``underlying_capital_exposure`` is produced alongside the broker portfolio
    snapshot and counts current stock market value plus short-put collateral.
    A configured cap fails closed when the account value or snapshot exposure
    is missing or malformed.
    """
    profile = sizing_profile or {}
    if "max_account_exposure_pct_per_underlying" not in profile:
        return None
    try:
        cap_pct = float(profile["max_account_exposure_pct_per_underlying"])
        account_value = float((portfolio_context or {}).get("account_value"))
        underlying = earnings_underlying_ticker(str(ticker or "")).upper()
        exposures = (portfolio_context or {}).get("underlying_capital_exposure")
        if not underlying or not isinstance(exposures, dict) or not isfinite(cap_pct) or not 0 <= cap_pct <= 100:
            return None
        if not isfinite(account_value) or account_value <= 0:
            return None
        exposure = float(exposures.get(underlying, 0.0))
    except (TypeError, ValueError):
        return None
    if not isfinite(exposure) or exposure < 0:
        return None
    return max(account_value * cap_pct / 100.0 - exposure, 0.0)


def annotate_pick_funding(
    candidates: list[dict] | None,
    portfolio_context: dict | None,
    sizing_profile: dict | None,
) -> list[dict]:
    """Annotate ranked picks with sequential cash and per-underlying funding.

    Candidate membership and order are preserved. A missing cash or exposure
    input stays unknown instead of being rendered as zero funded contracts.
    """
    context = portfolio_context if isinstance(portfolio_context, dict) else {}
    profile = sizing_profile or {}
    try:
        remaining_cash = float(context.get("cash_available_for_csp"))
        if not isfinite(remaining_cash) or remaining_cash < 0:
            remaining_cash = None
    except (TypeError, ValueError):
        remaining_cash = None

    allocated_by_underlying: dict[str, float] = {}
    annotated: list[dict] = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        item = dict(candidate)
        option_type = str(item.get("option_type", "")).upper()
        try:
            requested = int(item.get("recommended_contracts"))
            if requested < 0:
                raise ValueError
        except (TypeError, ValueError):
            item["funded_contracts"] = None
            item["cash_conflict"] = None
            annotated.append(item)
            continue

        if option_type != "PUT":
            item["funded_contracts"] = requested
            item["cash_conflict"] = False
            annotated.append(item)
            continue

        try:
            cash_per_contract = float(item.get("cash_required"))
            if not isfinite(cash_per_contract) or cash_per_contract <= 0:
                raise ValueError
        except (TypeError, ValueError):
            item["funded_contracts"] = None
            item["cash_conflict"] = None
            annotated.append(item)
            continue

        ticker = str(item.get("ticker", "") or "")
        underlying = earnings_underlying_ticker(ticker).upper()
        room = remaining_underlying_capital(ticker, context, profile) if ticker else None
        if "max_account_exposure_pct_per_underlying" not in profile:
            room = float("inf")
        if remaining_cash is None or room is None or not underlying:
            item["funded_contracts"] = None
            item["cash_conflict"] = None
            annotated.append(item)
            continue

        remaining_room = max(room - allocated_by_underlying.get(underlying, 0.0), 0.0)
        cash_capacity = int(remaining_cash // cash_per_contract)
        exposure_capacity = int(remaining_room // cash_per_contract) if isfinite(remaining_room) else requested
        funded = min(requested, cash_capacity, exposure_capacity)
        item["funded_contracts"] = funded
        item["cash_conflict"] = funded < requested
        remaining_cash = max(remaining_cash - funded * cash_per_contract, 0.0)
        allocated_by_underlying[underlying] = allocated_by_underlying.get(underlying, 0.0) + funded * cash_per_contract
        annotated.append(item)
    return annotated


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
