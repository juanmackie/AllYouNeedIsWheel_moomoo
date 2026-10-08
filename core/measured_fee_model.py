"""Pure per-contract fee estimates from the account's own US option fills.

Estimates are medians of complete, fee-resolved orders. They are historical
measurements, not a broker fee schedule; missing side evidence stays unknown.
"""

from __future__ import annotations

import json
from math import isfinite
from statistics import median


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def empty_measured_option_fee_model(lookback_days: int = 90) -> dict:
    return {
        "source": "unknown",
        "method": "median_per_order",
        "lookback_days": max(1, int(lookback_days)),
        "currency": "USD",
        "BUY": {"fee_per_contract": None, "sample_orders": 0, "source": "unknown"},
        "SELL": {"fee_per_contract": None, "sample_orders": 0, "source": "unknown"},
    }


def estimate_us_option_fees(fills, lookback_days: int = 90) -> dict:
    """Estimate BUY/SELL fees from complete fee-resolved US option orders.

    ``fills`` must already be scoped to one account/environment. Rows are
    grouped by order so partial executions count as one fee observation.
    Orders with a missing or invalid fee/quantity are excluded in full.
    """
    groups = {}
    for fill in fills or []:
        if not isinstance(fill, dict) or str(fill.get("security_type", "")).upper() != "OPT":
            continue
        side = str(fill.get("side", "")).upper()
        if side not in {"BUY", "SELL"}:
            continue
        order_id = str(fill.get("order_id", "") or "").strip()
        if not order_id:
            continue

        raw = fill.get("raw_json")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                raw = None
        code = str(raw.get("code", "") if isinstance(raw, dict) else "").upper()
        if not code.startswith("US."):
            continue

        group = groups.setdefault((side, order_id), {"qty": 0.0, "fees": 0.0, "complete": True})
        qty = _number(fill.get("qty"))
        fee = _number(fill.get("fees"))
        if qty is None or qty <= 0 or fee is None or fee < 0:
            group["complete"] = False
            continue
        group["qty"] += qty
        group["fees"] += fee

    model = empty_measured_option_fee_model(lookback_days)
    for side in ("BUY", "SELL"):
        samples = [
            group["fees"] / group["qty"]
            for (group_side, _), group in groups.items()
            if group_side == side and group["complete"] and group["qty"] > 0
        ]
        if samples:
            model[side] = {
                "fee_per_contract": float(median(samples)),
                "sample_orders": len(samples),
                "source": "measured_from_fills",
            }
    if any(model[side]["sample_orders"] for side in ("BUY", "SELL")):
        model["source"] = "measured_from_fills"
    return model


def measured_fee_note(model: dict) -> str:
    """Return a concise disclosure for a complete round-trip estimate."""
    model = model if isinstance(model, dict) else {}
    buy = model.get("BUY") if isinstance(model.get("BUY"), dict) else {}
    sell = model.get("SELL") if isinstance(model.get("SELL"), dict) else {}
    if buy.get("fee_per_contract") is None or sell.get("fee_per_contract") is None:
        return "Prospective fees unavailable; missing fee evidence is not treated as zero."
    days = max(1, int(model.get("lookback_days", 90) or 90))
    return (
        "Fees measured from your fills: US options, last "
        f"{days} days (BUY {int(buy.get('sample_orders', 0))} orders; "
        f"SELL {int(sell.get('sample_orders', 0))} orders)."
    )
