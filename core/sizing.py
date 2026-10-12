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


def deployment_plan(
    candidates: list[dict] | None,
    cash_available: float,
    max_per_underlying: int = 1,
    *,
    reserve_pct: float = 10.0,
    per_underlying_cap_pct: float = 35.0,
    group_cap_pct: float = 35.0,
    leveraged_tickers: set[str] | list[str] | tuple[str, ...] = (),
    portfolio_context: dict | None = None,
) -> dict:
    """Allocate CSP cash across already-ranked candidates.

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
    try:
        reserve_pct = min(max(float(reserve_pct), 0.0), 100.0)
        per_underlying_cap_pct = min(max(float(per_underlying_cap_pct), 0.0), 100.0)
        group_cap_pct = min(max(float(group_cap_pct), 0.0), 100.0)
    except (TypeError, ValueError):
        reserve_pct, per_underlying_cap_pct, group_cap_pct = 10.0, 35.0, 35.0
    budget = round(remaining * (1.0 - reserve_pct / 100.0), 2)
    ticker_cap = remaining * per_underlying_cap_pct / 100.0
    leveraged_cap = remaining * group_cap_pct / 100.0
    room = budget
    selected: list[dict] = []
    skipped: list[dict] = []
    counts: dict[str, int] = {}
    ticker_cash: dict[str, float] = {}
    leveraged_cash = 0.0
    leveraged = {earnings_underlying_ticker(str(t or "")).upper() for t in leveraged_tickers}

    def skip(candidate: dict, reason_code: str, reason: str) -> None:
        skipped.append(
            {"ticker": candidate.get("ticker", ""), "contract": candidate, "reason_code": reason_code, "reason": reason}
        )

    for candidate in candidates or []:
        if not isinstance(candidate, dict) or str(candidate.get("option_type", "")).upper() != "PUT":
            continue
        if candidate.get("research_only") or candidate.get("copy_eligible") is False:
            skip(candidate, "review_only", "Candidate is not copy eligible")
            continue
        try:
            cash_required = float(candidate.get("cash_required", 0) or 0)
            requested = int(candidate.get("recommended_contracts", 0) or 0)
        except (TypeError, ValueError):
            skip(candidate, "invalid_sizing", "Candidate has invalid cash or quantity inputs")
            continue
        if cash_required <= 0 or requested <= 0:
            skip(candidate, "invalid_sizing", "Candidate has no positive cash requirement or quantity")
            continue

        underlying = earnings_underlying_ticker(str(candidate.get("ticker", "") or "")).upper()
        if not underlying:
            skip(candidate, "invalid_ticker", "Candidate has no canonical ticker")
            continue
        if counts.get(underlying, 0) >= underlying_cap:
            skip(candidate, "duplicate_ticker", "A line for this ticker is already in the cash plan")
            continue
        position = ((portfolio_context or {}).get("positions", {}) or {}).get(underlying, {})
        try:
            shares = float(position.get("position", 0) or 0)
            adjusted_basis = float(position.get("adjusted_basis", position.get("avg_cost", 0)) or 0)
            market_price = float(position.get("market_price", 0) or 0)
            if market_price <= 0 and shares > 0:
                market_price = float(position.get("market_val", position.get("market_value", 0)) or 0) / shares
        except (AttributeError, TypeError, ValueError):
            shares = adjusted_basis = market_price = 0.0
        if shares > 0 and adjusted_basis > 0 and market_price > 0 and market_price < adjusted_basis:
            skip(candidate, "held_below_adjusted_basis", "Shares are held below adjusted basis")
            continue
        if shares > 0 and (adjusted_basis <= 0 or market_price <= 0):
            skip(candidate, "held_basis_or_price_unavailable", "Held-share basis or market price is unavailable")
            continue
        line_room = min(room, max(ticker_cap - ticker_cash.get(underlying, 0.0), 0.0))
        if underlying in leveraged:
            line_room = min(line_room, max(leveraged_cap - leveraged_cash, 0.0))
        contracts = min(requested, int(line_room // cash_required))
        if contracts <= 0:
            code = (
                "leveraged_group_cap"
                if underlying in leveraged and leveraged_cap - leveraged_cash < cash_required
                else ("ticker_cap" if ticker_cap - ticker_cash.get(underlying, 0.0) < cash_required else "cash_budget")
            )
            skip(candidate, code, "No cash remains within the configured allocation caps")
            continue

        planned = dict(candidate)
        planned["deployment_contracts"] = contracts
        planned["deployment_cash_required"] = round(contracts * cash_required, 2)
        planned["deployment_income"] = round(
            contracts * float(candidate.get("bid_premium_per_contract", candidate.get("premium_per_contract", 0)) or 0),
            2,
        )
        spent = planned["deployment_cash_required"]
        room = round(room - spent, 2)
        ticker_cash[underlying] = ticker_cash.get(underlying, 0.0) + spent
        if underlying in leveraged:
            leveraged_cash += spent
        planned["deployment_cash_remaining"] = room
        selected.append(planned)
        counts[underlying] = counts.get(underlying, 0) + 1

    total_cash = round(sum(item["deployment_cash_required"] for item in selected), 2)
    total_credit = round(sum(item["deployment_income"] for item in selected), 2)
    weighted_return = (
        sum(item["deployment_cash_required"] * float(item.get("capital_velocity_per_day", 0) or 0) for item in selected)
        / total_cash
        if total_cash
        else 0.0
    )
    return {
        "lines": selected,
        "skipped": skipped,
        "total_cash": total_cash,
        "total_credit": total_credit,
        "reserve_cash": round(remaining * reserve_pct / 100.0, 2),
        "cash_left": round(remaining - total_cash, 2),
        "cash_budget": round(budget, 2),
        "capital_weighted_return_per_day": round(weighted_return, 8),
    }


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
