"""Pure helpers for planning a cash-aware watchlist scan.

These functions describe what can be proven from a broker price snapshot and
known cash limits. They do not fetch broker data or turn missing evidence into
a cash-fit disposition.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any


def _preset_value(preset: Any, *names: str, default: float) -> float:
    """Read a threshold from either a WheelPreset or its screening profile."""
    profile = preset
    if hasattr(preset, "to_screener_profile"):
        profile = preset.to_screener_profile()

    if not isinstance(profile, Mapping):
        return default
    for name in names:
        value = _finite_number(profile.get(name))
        if value is not None:
            return value
    return default


def _finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _price_value(value: Any) -> float | None:
    if isinstance(value, Mapping):
        value = value.get("last_price", value.get("price"))
    return _finite_number(value)


def _ticker_value(value: Any, ticker: str) -> float | None:
    if isinstance(value, Mapping):
        value = value.get(ticker)
    return _finite_number(value)


def _otm_band(preset: Any) -> tuple[float, float]:
    min_otm = _preset_value(preset, "csp_min_otm_pct", "min_otm_pct", default=5.0)
    max_otm = _preset_value(preset, "csp_max_otm_pct", "max_otm_pct", default=15.0)
    return max(min_otm, 0.0), max(max_otm, 0.0)


def csp_affordability(price: float, cash: float, exposure_room: float, preset: Any) -> bool:
    """Return whether any strike in the preset's CSP OTM band can fit.

    A put is cash secured at ``strike * 100``. The maximum deployable amount is
    the smaller of true CSP cash and the remaining exposure room. As in the
    legacy 5–15% OTM gate, this is a continuous strike-range preflight; the
    selected option chain must still prove an actual strike fits.
    """
    stock_price = _finite_number(price)
    cash_available = _finite_number(cash)
    remaining_exposure = _finite_number(exposure_room)
    if (
        stock_price is None
        or stock_price <= 0
        or cash_available is None
        or cash_available <= 0
        or remaining_exposure is None
        or remaining_exposure <= 0
    ):
        return False

    min_otm, max_otm = _otm_band(preset)
    if max_otm < min_otm or max_otm >= 100:
        return False

    lowest_allowed_strike = stock_price * (1 - max_otm / 100)
    highest_allowed_strike = stock_price * (1 - min_otm / 100)
    max_affordable_strike = min(highest_allowed_strike, min(cash_available, remaining_exposure) / 100)
    return max_affordable_strike >= lowest_allowed_strike


def partition_universe(
    tickers: Iterable[str],
    prices: Mapping[str, Any],
    cash: float | Mapping[str, Any] | None,
    exposure_room: float | Mapping[str, Any] | None,
    preset: Any,
    fetched_at: str | None = None,
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, str]]]:
    """Split a scan universe into eligible tickers, proven cash skips, and gaps.

    Missing or invalid price/cash/exposure evidence is reported as a gap. Only
    complete evidence can produce a ``skipped_no_cash_fit`` record. Ticker
    order is preserved in all returned groups.
    """
    eligible: list[str] = []
    skipped: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = []
    cash_available = _finite_number(cash) if not isinstance(cash, Mapping) else None

    for ticker in tickers:
        price = _price_value(prices.get(ticker))
        ticker_cash = _ticker_value(cash, ticker) if isinstance(cash, Mapping) else cash_available
        remaining_exposure = _ticker_value(exposure_room, ticker)
        if not isinstance(exposure_room, Mapping):
            remaining_exposure = _finite_number(exposure_room)
        if price is None or price <= 0:
            gaps.append({"ticker": ticker, "reason": "missing_price"})
            continue
        if ticker_cash is None or ticker_cash < 0:
            gaps.append({"ticker": ticker, "reason": "missing_cash"})
            continue
        if remaining_exposure is None or remaining_exposure < 0:
            gaps.append({"ticker": ticker, "reason": "missing_exposure_room"})
            continue
        if csp_affordability(price, ticker_cash, remaining_exposure, preset):
            eligible.append(ticker)
            continue

        _, max_otm = _otm_band(preset)
        skipped.append(
            {
                "ticker": ticker,
                "price": price,
                "min_strike_needed": round(price * (1 - max_otm / 100), 8),
                "cash_at_scan": min(ticker_cash, remaining_exposure),
                "fetched_at": fetched_at,
            }
        )

    return eligible, skipped, gaps


def chain_budget(preset: Any) -> dict[str, int]:
    """Return the DTE span and 30-day range-query calls needed per symbol."""
    min_dte = int(_preset_value(preset, "csp_min_dte", "min_dte", default=0))
    max_dte = int(_preset_value(preset, "csp_max_dte", "max_dte", default=0))
    span_days = max(max_dte - min_dte + 1, 0)
    calls_per_symbol = math.ceil(span_days / 30) if span_days else 0
    return {"span_days": span_days, "calls_per_symbol": calls_per_symbol}


def select_chain_rows(
    rows: Iterable[Mapping[str, Any]],
    price: float,
    cash: float,
    exposure_room: float,
    preset: Any,
) -> list[Mapping[str, Any]]:
    """Keep affordable CSP rows inside the preset OTM and DTE bands.

    All qualifying rows are retained, grouped by deterministic DTE/expiration
    order. Callers may then select the nearest, preferred, and farthest expiry
    samples without relying on broker row order or truncating the front of a
    multi-expiry response.
    """
    stock_price = _finite_number(price)
    cash_available = _finite_number(cash)
    remaining_exposure = _finite_number(exposure_room)
    if (
        stock_price is None
        or stock_price <= 0
        or cash_available is None
        or cash_available < 0
        or remaining_exposure is None
        or remaining_exposure < 0
    ):
        return []

    min_otm, max_otm = _otm_band(preset)
    min_dte = _preset_value(preset, "csp_min_dte", "min_dte", default=0)
    max_dte = _preset_value(preset, "csp_max_dte", "max_dte", default=float("inf"))
    deployable_cash = min(cash_available, remaining_exposure)
    selected: list[tuple[float, str, float, int, Mapping[str, Any]]] = []

    for row in rows:
        option_type = str(row.get("option_type", row.get("right", "")) or "").upper()
        if option_type not in {"PUT", "P"}:
            continue

        strike = _finite_number(row.get("strike"))
        dte = _finite_number(row.get("dte"))
        if strike is None or strike <= 0 or strike >= stock_price or dte is None:
            continue
        otm_pct = ((stock_price - strike) / stock_price) * 100
        if not min_otm <= otm_pct <= max_otm or not min_dte <= dte <= max_dte:
            continue
        if strike * 100 > deployable_cash:
            continue

        expiration = str(row.get("expiration", row.get("exp_str", "")) or "")
        selected.append((dte, expiration, -strike, len(selected), row))

    selected.sort(key=lambda item: item[:4])
    return [item[4] for item in selected]
