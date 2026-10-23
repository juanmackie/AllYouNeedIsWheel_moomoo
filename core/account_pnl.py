"""Broker-truth account P&L read model (pure, no I/O).

Net P&L per underlying uses the cash-plus-mark method:

    net = option cash + share cash + value of held positions - known fees

Option cash is premium sold minus premium bought (x100). Share cash is the
same flow for stock fills. Held positions are valued at the broker mark.
The method needs no option lifecycle inference: OpenD records an expiry or
an assignment as a zero-price closing fill (measured 2026-10-23: expired
short puts and calls appear as BUY at 0, expired long options as SELL at 0,
assignments as BUY/SELL at 0 plus a share fill at the strike).

Fill rows and broker positions are reconciled per underlying and per
contract with strict quantity equality. A contract that is open by fills but
absent at the broker means a missing fill (usually history older than the
backfill), so the row is unreconciled. Its P&L is None (unknown), never zero,
and the notes name the mismatch. Shares held before the fill history
(partial history) are valued at the broker cost basis, which covers the old
and new lots. This is exact only while no share was sold, so a window share
sale on partial history makes the row unreconciled.

Interest and dividend cash flows are reported as income only when the flow
type names interest or dividends. Other flow types are listed as unclassified
and are not counted. They are kept separate from the trading P&L. The NAV
change is measured only after the last capital movement (deposit or
withdrawal). It is null when that movement has no date. It is marked verified
only when the cash-flow backfill covers the window.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from core.outcome_attribution import CONTRACT_MULTIPLIER, is_capital_movement_cash_flow
from core.utils import safe_float

_OPTION_UNDERLYING_RE = re.compile(r"^([A-Z]+)\d{6}[CP]\d+$")
_SIDE_CASH_SIGN = {"SELL": 1, "BUY": -1}
_TOLERANCE = 1e-9
_VERIFY_LAG_DAYS = 4
OLD_SHARES_SOLD_NOTE = "shares older than the fill history were partly sold; realized share P&L is unknown"
_INCOME_FLOW_WORDS = ("interest", "dividend")


def _money(value: float) -> float:
    """Round to cents and normalize negative zero."""
    return round(value, 2) + 0.0


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= _TOLERANCE


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _parse_iso_date(value) -> date | None:
    """Parse the date part of an ISO timestamp or date string."""
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _option_key(option_type, strike, expiration) -> tuple:
    """Canonical contract identity: (right, strike rounded to 4 dp, expiration digits)."""
    strike_value = safe_float(strike, None)
    return (
        str(option_type or "").upper(),
        None if strike_value is None else round(strike_value, 4),
        _digits(expiration),
    )


def _option_underlying(position: dict) -> str:
    underlying = str(position.get("underlying") or "").strip().upper()
    if underlying:
        return underlying
    match = _OPTION_UNDERLYING_RE.match(str(position.get("symbol") or "").strip().upper())
    return match.group(1) if match else ""


def _new_bucket() -> dict:
    return {
        "option_cash": 0.0,
        "share_cash": 0.0,
        "option_mark": 0.0,
        "share_mark": 0.0,
        "avg_cost_total": 0.0,
        "fees": 0.0,
        "fees_unknown": 0,
        "bad_fills": 0,
        "fill_share_qty": 0.0,
        "share_sells": 0,
        "broker_share_qty": 0.0,
        "fill_contracts": {},
        "broker_contracts": {},
        "problems": [],
        "first_fill_at": None,
        "last_fill_at": None,
    }


def _track_fill_time(bucket: dict, captured_at) -> None:
    text = str(captured_at or "")
    if not text:
        return
    if bucket["first_fill_at"] is None or text < bucket["first_fill_at"]:
        bucket["first_fill_at"] = text
    if bucket["last_fill_at"] is None or text > bucket["last_fill_at"]:
        bucket["last_fill_at"] = text


def _add_fill(bucket: dict, fill: dict) -> None:
    """Accumulate one fill row. Option quantities are per contract."""
    security_type = str(fill.get("security_type") or "").upper()
    cash_sign = _SIDE_CASH_SIGN.get(str(fill.get("side") or "").upper(), 0)
    qty = safe_float(fill.get("qty"), None)
    price = safe_float(fill.get("price"), None)
    if security_type not in ("OPT", "STK") or cash_sign == 0 or qty is None or price is None:
        bucket["bad_fills"] += 1
        return

    qty = abs(qty)
    holding_delta = -cash_sign * qty
    cash = cash_sign * qty * price
    _track_fill_time(bucket, fill.get("captured_at"))
    fee = safe_float(fill.get("fees"), None)
    if fee is None:
        bucket["fees_unknown"] += 1
    else:
        bucket["fees"] += fee

    if security_type == "OPT":
        bucket["option_cash"] += cash * CONTRACT_MULTIPLIER
        key = _option_key(fill.get("option_type"), fill.get("strike"), fill.get("expiration"))
        contracts = bucket["fill_contracts"]
        contracts[key] = contracts.get(key, 0.0) + holding_delta
    else:
        bucket["share_cash"] += cash
        bucket["fill_share_qty"] += holding_delta
        if cash_sign > 0:
            bucket["share_sells"] += 1


def _add_position(bucket: dict, position: dict) -> None:
    """Accumulate one broker position at its mark."""
    security_type = str(position.get("security_type") or "").upper()
    qty = safe_float(position.get("qty"), None)
    if qty is None:
        bucket["problems"].append("broker position has no quantity")
        return
    if qty == 0:
        return

    mark = safe_float(position.get("market_price"), None)
    if security_type == "STK":
        avg_cost = safe_float(position.get("avg_cost"), None)
        bucket["broker_share_qty"] += qty
        if mark is None or avg_cost is None:
            bucket["problems"].append("broker shares lack a market price or average cost")
        else:
            bucket["share_mark"] += qty * mark
            bucket["avg_cost_total"] += qty * avg_cost
    elif security_type == "OPT":
        key = _option_key(position.get("option_type"), position.get("strike"), position.get("expiration"))
        contracts = bucket["broker_contracts"]
        contracts[key] = contracts.get(key, 0.0) + qty
        if mark is None:
            bucket["problems"].append("broker option lacks a market price")
        else:
            bucket["option_mark"] += qty * mark * CONTRACT_MULTIPLIER
    else:
        bucket["problems"].append("broker position has an unsupported security type")


def _contract_mismatches(bucket: dict) -> list[str]:
    """Compare fill-derived and broker contract quantities for each nonzero contract."""
    fill = {key: qty for key, qty in bucket["fill_contracts"].items() if not _same(qty, 0)}
    broker = {key: qty for key, qty in bucket["broker_contracts"].items() if not _same(qty, 0)}
    notes = []
    for key in sorted(set(fill) | set(broker), key=str):
        fill_qty = fill.get(key, 0.0)
        broker_qty = broker.get(key, 0.0)
        if _same(fill_qty, broker_qty):
            continue
        right, strike, expiration = key
        notes.append(f"fills show {fill_qty:g} {right} {strike} {expiration}, broker holds {broker_qty:g}")
    return notes


def _reconcile(bucket: dict) -> tuple[str, list[str]]:
    """Return (status, notes). Status is reconciled, partial_history, or unreconciled."""
    mismatches = list(dict.fromkeys(bucket["problems"]))
    if bucket["bad_fills"]:
        mismatches.append(f"{bucket['bad_fills']} fill row(s) have an unreadable side, type, quantity or price")

    fill_qty = bucket["fill_share_qty"]
    broker_qty = bucket["broker_share_qty"]
    partial = False
    if _same(fill_qty, broker_qty):
        pass
    elif broker_qty > fill_qty >= 0:
        if bucket["share_sells"]:
            mismatches.append(OLD_SHARES_SOLD_NOTE)
        else:
            partial = True
    else:
        mismatches.append(f"fills show {fill_qty:g} shares, broker holds {broker_qty:g}")

    mismatches.extend(_contract_mismatches(bucket))
    if mismatches:
        return "unreconciled", mismatches
    if partial:
        unexplained = broker_qty - fill_qty
        return "partial_history", [
            f"{unexplained:g} shares were held before the fill history and use broker average cost"
        ]
    return "reconciled", []


def _share_pnl(bucket: dict, status: str) -> float | None:
    if status == "unreconciled":
        return None
    if status == "reconciled":
        return bucket["share_cash"] + bucket["share_mark"]
    # Partial history with no share sale: the broker cost covers every held lot,
    # so the window cash is already inside it.
    return bucket["share_mark"] - bucket["avg_cost_total"]


def _build_row(ticker: str, bucket: dict) -> dict:
    status, notes = _reconcile(bucket)
    known = status != "unreconciled"
    option_pnl = bucket["option_cash"] + bucket["option_mark"]
    share_pnl = _share_pnl(bucket, status)
    net_pnl = option_pnl + share_pnl - bucket["fees"] if known else None
    if bucket["fees_unknown"]:
        notes = [*notes, f"{bucket['fees_unknown']} fill fee(s) not yet known; net excludes them"]
    return {
        "ticker": ticker,
        "status": status,
        "net_pnl": _money(net_pnl) if known else None,
        "option_pnl": _money(option_pnl) if known else None,
        "share_pnl": _money(share_pnl) if known else None,
        "fees": _money(bucket["fees"]),
        "fees_unknown": bucket["fees_unknown"],
        "shares_held": bucket["broker_share_qty"],
        "open_contracts": sum(1 for qty in bucket["broker_contracts"].values() if not _same(qty, 0)),
        "first_fill_at": bucket["first_fill_at"],
        "last_fill_at": bucket["last_fill_at"],
        "notes": notes,
    }


def _group_by_underlying(fills, positions) -> dict[str, dict]:
    buckets: dict[str, dict] = {}
    for fill in fills:
        ticker = str(fill.get("ticker") or "").strip().upper()
        _add_fill(buckets.setdefault(ticker, _new_bucket()), fill)
    for position in positions:
        if not isinstance(position, dict):
            continue
        security_type = str(position.get("security_type") or "").upper()
        if security_type == "OPT":
            ticker = _option_underlying(position)
        else:
            ticker = str(position.get("symbol") or "").strip().upper()
        _add_position(buckets.setdefault(ticker, _new_bucket()), position)
    return buckets


def _cash_flow_summary(cash_flows) -> tuple[dict, dict]:
    """Split cash flows into income, unclassified flows, and capital movements.

    Only flow types naming interest or dividends are income. Other flow types are
    kept under ``unclassified`` and are not counted in any total.
    """
    income: dict[str, dict[str, float]] = {}
    unclassified: dict[str, dict[str, float]] = {}
    capital = {"count": 0, "by_currency": {}, "last_date": None}
    for row in cash_flows:
        flow_type = str(row.get("flow_type") or "")
        if "exchange" in flow_type.lower():
            continue
        currency = str(row.get("currency") or "").strip().upper()
        amount = safe_float(row.get("amount"), None)
        if is_capital_movement_cash_flow(flow_type, str(row.get("remark") or "")):
            capital["count"] += 1
            day = str(row.get("clearing_date") or "")[:10]
            if day and (capital["last_date"] is None or day > capital["last_date"]):
                capital["last_date"] = day
            if amount is not None:
                capital["by_currency"][currency] = capital["by_currency"].get(currency, 0.0) + amount
            continue
        if amount is None:
            continue
        lowered = flow_type.lower()
        bucket = income if any(word in lowered for word in _INCOME_FLOW_WORDS) else unclassified
        flows = bucket.setdefault(currency, {})
        flows[flow_type] = flows.get(flow_type, 0.0) + amount

    usd_total = sum(sum(flows.values()) for currency, flows in income.items() if currency == "USD")
    income_out = {
        "by_currency": {
            currency: {flow: _money(total) for flow, total in flows.items()} for currency, flows in income.items()
        },
        "usd_total": _money(usd_total),
        "unclassified": {
            currency: {flow: _money(total) for flow, total in flows.items()} for currency, flows in unclassified.items()
        },
    }
    capital_out = {
        "count": capital["count"],
        "by_currency": {currency: _money(total) for currency, total in capital["by_currency"].items()},
        "last_date": capital["last_date"],
    }
    return income_out, capital_out


def _coverage_verifies(coverage, start_day: str, end_day: str) -> bool:
    """True when the cash-flow backfill spans the NAV window (end minus 4-day lag)."""
    if not isinstance(coverage, dict):
        return False
    earliest = _parse_iso_date(coverage.get("earliest"))
    latest = _parse_iso_date(coverage.get("latest"))
    start = _parse_iso_date(start_day)
    end = _parse_iso_date(end_day)
    if None in (earliest, latest, start, end):
        return False
    return earliest <= start and latest >= end - timedelta(days=_VERIFY_LAG_DAYS)


def _nav_since_last_capital_movement(nav_history, last_date: str | None, coverage) -> dict | None:
    points = []
    for point in nav_history:
        nav = safe_float(point.get("net_liquidation"), None)
        if nav is not None and nav > 0:
            points.append((str(point.get("captured_at") or ""), nav))
    start_index = next(
        (i for i, (captured_at, _) in enumerate(points) if last_date is None or captured_at[:10] > last_date),
        None,
    )
    if start_index is None:
        return None
    end_index = len(points) - 1
    if start_index == end_index:
        return None

    start_at, start_nav = points[start_index]
    end_at, end_nav = points[end_index]
    change = end_nav - start_nav
    return {
        "start_at": start_at,
        "start_nav": _money(start_nav),
        "end_at": end_at,
        "end_nav": _money(end_nav),
        "change": _money(change),
        "change_pct": round(change / start_nav * 100, 2) + 0.0,
        "verified": _coverage_verifies(coverage, start_at[:10], end_at[:10]),
    }


def _history_from(fills) -> str | None:
    days = [str(fill.get("captured_at") or "")[:10] for fill in fills]
    days = [day for day in days if day]
    return min(days) if days else None


def _totals(rows: list[dict], income: dict) -> dict:
    known = [row for row in rows if row["net_pnl"] is not None]
    totals = {
        "net_pnl": _money(sum(row["net_pnl"] for row in known)),
        "option_pnl": _money(sum(row["option_pnl"] for row in known)),
        "share_pnl": _money(sum(row["share_pnl"] for row in known)),
        "fees": _money(sum(row["fees"] for row in known)),
        "unreconciled_count": len(rows) - len(known),
        "fees_unknown": sum(row["fees_unknown"] for row in rows),
        "underlying_count": len(rows),
    }
    totals["net_pnl_with_income"] = _money(totals["net_pnl"] + income["usd_total"])
    return totals


def _sort_key(row: dict) -> tuple:
    return (row["net_pnl"] is None, row["net_pnl"] if row["net_pnl"] is not None else 0.0, row["ticker"])


def build_account_pnl(
    fills,
    positions,
    cash_flows=(),
    nav_history=(),
    positions_as_of=None,
    cash_flow_coverage=None,
) -> dict:
    """Build the broker-truth account P&L read model from persisted rows.

    ``fills`` are option/share fill dicts. ``positions`` are latest snapshot
    position dicts. ``cash_flows`` are account cash-flow rows. ``nav_history``
    is oldest-first snapshot NAV. ``cash_flow_coverage`` is the cash-flow
    backfill span (``earliest``/``latest`` ISO dates).
    """
    fills = list(fills or ())
    positions = list(positions or ())

    buckets = _group_by_underlying(fills, positions)
    rows = [_build_row(ticker, bucket) for ticker, bucket in buckets.items()]
    rows.sort(key=_sort_key)

    income, capital = _cash_flow_summary(cash_flows or ())
    # A capital movement without a date cannot place the NAV window, so NAV is unknown.
    undated_capital = capital["count"] > 0 and capital["last_date"] is None
    nav = None
    if not undated_capital:
        nav = _nav_since_last_capital_movement(list(nav_history or ()), capital["last_date"], cash_flow_coverage)
    return {
        "as_of": positions_as_of,
        "history_from": _history_from(fills),
        "totals": _totals(rows, income),
        "income": income,
        "capital_movements": capital,
        "nav_since_last_capital_movement": nav,
        "underlyings": rows,
    }
