"""Pure position-diff trade-event inference.

Compares two consecutive portfolio snapshots (shapes produced by
``core.portfolio_snapshot.build_portfolio_snapshot``) and infers what the user
did between runs:

- ``entry``       — new short option appeared
- ``exit``        — short option disappeared or was partially bought back
- ``roll``        — short option moved strike/expiry (same underlying + type)
- ``assignment``  — short put vanished while shares of the underlying increased

Deterministic, broker-free, no I/O. Premium/PnL fields are left at zero —
inference knows positions, not fill prices; analytics treat zeros explicitly
as "unknown", never as fabricated profit.

Events match the ``trade_events`` table schema consumed by
``db.trade_events_repository.TradeEventsRepository.save_trade_event``.
"""

from __future__ import annotations


def _safe_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _option_map(positions: list[dict]) -> dict[str, dict]:
    """Keyed by contract_key for all SHORT option positions."""
    result: dict[str, dict] = {}
    for pos in positions or []:
        if not isinstance(pos, dict):
            continue
        if str(pos.get("security_type", "") or "").upper() != "OPT":
            continue
        qty = int(pos.get("qty", 0) or 0)
        if qty >= 0:
            continue  # only short options are wheel-relevant
        key = str(pos.get("contract_key", "") or "")
        if key:
            result[key] = pos
    return result


def _stock_qty_map(positions: list[dict]) -> dict[str, int]:
    result: dict[str, int] = {}
    for pos in positions or []:
        if isinstance(pos, dict) and str(pos.get("security_type", "") or "").upper() == "STK":
            symbol = str(pos.get("symbol", "") or "")
            if symbol:
                result[symbol] = int(pos.get("qty", 0) or 0)
    return result


def _base_event(event_type: str, pos: dict, timestamp: str) -> dict:
    return {
        "timestamp": timestamp,
        "event_type": event_type,
        "ticker": str(pos.get("symbol", "") or ""),
        "option_type": str(pos.get("option_type", "") or "").upper(),
        "strike": _safe_float(pos.get("strike")),
        "expiration": str(pos.get("expiration", "") or ""),
        "from_strike": 0.0,
        "from_expiration": "",
        "to_strike": 0.0,
        "to_expiration": "",
        "premium_in": 0.0,
        "premium_out": 0.0,
        "pnl": 0.0,
        "leakage": 0.0,
        "reason": "",
        "details": {"source": "position_diff"},
    }


def infer_trade_events(previous: dict | None, current: dict | None) -> list[dict]:
    """Diff consecutive snapshots and return inferred trade events.

    Either side may be None (first run seeds the baseline; no events).
    """
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return []

    timestamp = str(current.get("captured_at", "") or "")
    prev_opts = _option_map(previous.get("positions") or [])
    curr_opts = _option_map(current.get("positions") or [])
    prev_stocks = _stock_qty_map(previous.get("positions") or [])
    curr_stocks = _stock_qty_map(current.get("positions") or [])

    def _group_key(pos: dict) -> tuple[str, str]:
        return (str(pos.get("symbol", "") or ""), str(pos.get("option_type", "") or "").upper())

    disappeared = sorted(set(prev_opts) - set(curr_opts))
    appeared = sorted(set(curr_opts) - set(prev_opts))

    # Pair disappeared + appeared legs with the same underlying and type as rolls.
    disappeared_by_group: dict[tuple[str, str], list[str]] = {}
    for key in disappeared:
        disappeared_by_group.setdefault(_group_key(prev_opts[key]), []).append(key)
    appeared_by_group: dict[tuple[str, str], list[str]] = {}
    for key in appeared:
        appeared_by_group.setdefault(_group_key(curr_opts[key]), []).append(key)

    matched_disappeared: set[str] = set()
    matched_appeared: set[str] = set()
    events: list[dict] = []
    assigned_symbols: set[str] = set()

    for group in sorted(set(disappeared_by_group) & set(appeared_by_group)):
        for old_key, new_key in zip(disappeared_by_group[group], appeared_by_group[group]):
            old_pos, new_pos = prev_opts[old_key], curr_opts[new_key]
            event = _base_event("roll", new_pos, timestamp)
            event["from_strike"] = _safe_float(old_pos.get("strike"))
            event["from_expiration"] = str(old_pos.get("expiration", "") or "")
            event["strike"] = _safe_float(new_pos.get("strike"))
            event["expiration"] = str(new_pos.get("expiration", "") or "")
            event["reason"] = f"Rolled {event['option_type']} {group[0]} {old_key} -> {new_key}"
            events.append(event)
            matched_disappeared.add(old_key)
            matched_appeared.add(new_key)

    # Remaining disappeared legs: exits — unless a short put vanished while its
    # underlying share count grew, which is an assignment.
    for key in disappeared:
        if key in matched_disappeared:
            continue
        old_pos = prev_opts[key]
        symbol = str(old_pos.get("symbol", "") or "")
        is_short_put = str(old_pos.get("option_type", "") or "").upper() == "PUT"
        share_growth = curr_stocks.get(symbol, 0) > prev_stocks.get(symbol, 0)
        if is_short_put and share_growth:
            event = _base_event("assignment", old_pos, timestamp)
            event["reason"] = (
                f"Short PUT {key} vanished while {symbol} shares grew "
                f"{prev_stocks.get(symbol, 0)} -> {curr_stocks.get(symbol, 0)}"
            )
            assigned_symbols.add(symbol)
        else:
            event = _base_event("exit", old_pos, timestamp)
            event["reason"] = f"Short position closed: {key}"
        events.append(event)

    # Remaining appeared legs: new short entries.
    for key in appeared:
        if key in matched_appeared:
            continue
        new_pos = curr_opts[key]
        event = _base_event("entry", new_pos, timestamp)
        event["premium_in"] = abs(_safe_float(new_pos.get("market_price")))
        event["reason"] = f"New short position: {key}"
        events.append(event)

    # Partial size changes on legs present in both snapshots.
    # Short legs have negative qty, so buying back contracts moves qty toward
    # zero: delta > 0 means contracts were closed.
    for key in sorted(set(prev_opts) & set(curr_opts)):
        delta = curr_opts[key]["qty"] - prev_opts[key]["qty"]
        if delta <= 0:
            continue  # shorting more is an add-on; keep the signal surface lean
        pos = prev_opts[key]
        event = _base_event("exit", pos, timestamp)
        event["details"]["contracts_closed"] = abs(int(delta))
        event["details"]["remaining_contracts"] = int(curr_opts[key]["qty"])
        event["reason"] = f"Partial buyback of {abs(int(delta))} contract(s): {key}"
        events.append(event)

    # Share growth without any put disappearance (e.g., bought stock outright)
    # is out of scope for the wheel journal — no event.

    return events
