"""Pure portfolio-snapshot builder.

Derives a persistable portfolio snapshot dict from a portfolio context (the
dict produced by ``api/services/portfolio_context.PortfolioContext``) plus run
identity. Deterministic and broker-free so tests can exercise it without
connectivity.

The snapshot records, per completed wheel run: net liquidation, true available
cash, cash reserved for open short puts, CSP-available cash, margin buying
power, and the full position book (stocks + options). It feeds equity history,
growth-pace tracking, and position-diff trade-event inference.
"""

from __future__ import annotations

import re

from core.position_utils import parse_moomoo_symbol, parse_position_qty

# Option-code shape produced by Moomoo after stripping the "US." prefix, e.g.
# TSLA260904P00300000 -> underlying TSLA, expiry 260904, right P, strike 300.
_OPTION_CODE_RE = re.compile(r"^(?P<under>[A-Z]+)(?P<expiry>\d{6})(?P<right>[CP])(?P<strike>\d+)$")


def _option_underlying(code: str) -> str:
    """Extract the underlying ticker from a Moomoo/OCC-style option code."""
    match = _OPTION_CODE_RE.match(str(code or "").strip())
    return match.group("under") if match else ""


# Fields copied from each stock position into positions_json.
_STOCK_FIELDS = ("market_price", "avg_cost", "cost_price", "market_val")

# Fields copied from each option position into positions_json.
_OPTION_FIELDS = ("strike", "expiration", "option_type", "market_price", "avg_cost", "market_val")


def _safe_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _serialize_position(raw_symbol: str, pos: dict) -> dict | None:
    """Normalize one raw Moomoo position into the snapshot position shape."""
    if not isinstance(pos, dict):
        return None
    symbol = parse_moomoo_symbol(raw_symbol)
    security_type = str(pos.get("security_type", "") or "").upper()
    qty = parse_position_qty(pos.get("position", pos.get("shares", 0)))
    if not symbol or security_type not in ("STK", "OPT") or qty == 0:
        return None

    entry = {
        "symbol": symbol,
        "security_type": security_type,
        "qty": qty,
    }
    fields = _STOCK_FIELDS if security_type == "STK" else _OPTION_FIELDS
    for field in fields:
        value = pos.get(field)
        if field in ("strike", "expiration"):
            entry[field] = str(value or "")
        elif field == "option_type":
            entry[field] = str(value or "").upper()
        else:
            entry[field] = _safe_float(value)
    # Canonical contract key for options: SYMBOL yyyymmdd C/P strike.
    if security_type == "OPT":
        entry["contract_key"] = f"{symbol} {entry['expiration']} {entry['option_type'][:1] or '?'}{entry['strike']}"
        # Distinct underlying identity so position-diff inference can group a
        # roll across two option codes and detect assignment against the
        # underlying share book (C06). symbol preserves the full option code.
        entry["underlying"] = _option_underlying(symbol)
    return entry


def build_portfolio_snapshot(
    portfolio_context: dict | None,
    run_id: str,
    env: str,
    opaque_account: str,
    captured_at: str,
) -> dict:
    """Build a snapshot dict from a live portfolio context. Pure — no I/O."""
    ctx = portfolio_context if isinstance(portfolio_context, dict) else {}
    positions_raw = ctx.get("positions", {}) or {}

    positions = []
    seen_keys = set()
    for raw_symbol, pos in positions_raw.items():
        serialized = _serialize_position(str(raw_symbol or ""), pos)
        if serialized is None:
            continue
        key = serialized.get("contract_key") or f"{serialized['symbol']}:{serialized['security_type']}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        positions.append(serialized)

    # Stable order: by symbol then contract key.
    positions.sort(key=lambda p: (p["symbol"], p.get("contract_key", "")))

    return {
        "run_id": str(run_id or ""),
        "captured_at": str(captured_at or ""),
        "env": str(env or ""),
        "account_id": str(opaque_account or ""),
        "net_liquidation": _safe_float(ctx.get("account_value")),
        "cash_available": _safe_float(ctx.get("available_cash")),
        "cash_reserved_for_csp": _safe_float(ctx.get("cash_reserved_for_csp")),
        "cash_available_for_csp": _safe_float(ctx.get("cash_available_for_csp")),
        "broker_buying_power": _safe_float(ctx.get("broker_buying_power")),
        "positions": positions,
    }
