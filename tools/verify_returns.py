#!/usr/bin/env python
"""Independent check: do the app's account returns match raw broker records?

The broker side is recomputed here from raw OpenD rows with this file's own
small functions. It does not reuse the app's deal normalizer or the account
P&L math. The app side is read from a copy of the local database and the
app's own read model, and is the thing being checked.

Pure functions below are tested in ``tests/test_verify_returns.py``.
``main()`` makes query-only broker calls (connect, disconnect,
resolve_portfolio_identity, get_portfolio, get_history_deals,
get_order_fees, get_cash_flow). It writes only under ``_tmp/verify/``.

Run (Windows, OpenD running):
    set APPDATA=<repo>\\_tmp\\appdata
    set MOOMOO_LOG_PATH=<repo>\\_tmp\\moomoo_logs
    .venv\\Scripts\\python.exe tools\\verify_returns.py

Account identifiers and raw rows are never printed. Detail goes to the
private JSON and Markdown files only.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from core.outcome_attribution import is_capital_movement_cash_flow

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "_tmp" / "verify"
COPY_DB = OUT_DIR / "options-verify.db"
JSON_OUT = OUT_DIR / "returns_check.json"
MD_OUT = OUT_DIR / "returns_check.md"
PRICES_JSON = REPO_ROOT / "_tmp" / "replay" / "prices.json"

OPTION_MULTIPLIER = 100
MONEY_TOLERANCE = 0.05
FEE_TOLERANCE = 0.01
PRICE_TOLERANCE = 1e-6
QTY_TOLERANCE = 1e-9
HISTORY_WINDOW_DAYS = 89  # inclusive span of 90 days, the broker per-query limit
ORDER_FEE_BATCH = 50
CASH_RATE_ANNUAL = 0.04
MARKET_TZ = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

_OPTION_CODE_RE = re.compile(r"^([A-Z]+)\d{6}[CP]\d+$")
_STOCK_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]*$")
_OUT_DIRECTIONS = frozenset({"OUT", "WITHDRAW", "WITHDRAWAL", "DEBIT"})
_IN_DIRECTIONS = frozenset({"IN", "DEPOSIT", "CREDIT"})


# --- Broker code and row normalization (own implementation) ---------------


def normalize_symbol(text) -> str:
    """Upper-case a code or symbol and drop a leading ``US.`` prefix."""
    value = str(text or "").strip().upper()
    return value[3:] if value.startswith("US.") else value


def classify_code(code) -> tuple[str, str]:
    """Return (``OPT`` or ``STK``, underlying ticker) for a US broker code.

    ``US.SOXL261030P126000`` gives ``("OPT", "SOXL")``. ``US.AAPL`` gives
    ``("STK", "AAPL")``. Any other shape raises ``ValueError``.
    """
    text = str(code or "").strip().upper()
    if not text.startswith("US."):
        raise ValueError("non-US or empty broker code")
    symbol = normalize_symbol(text)
    match = _OPTION_CODE_RE.match(symbol)
    if match:
        return "OPT", match.group(1)
    if _STOCK_SYMBOL_RE.match(symbol):
        return "STK", symbol
    raise ValueError("unrecognized broker code shape")


def side_of(trd_side) -> str:
    """Map a broker trade side to SELL or BUY. Short sells count as SELL, buy-backs as BUY."""
    text = str(trd_side or "").upper()
    if "SELL" in text:
        return "SELL"
    if "BUY" in text:
        return "BUY"
    raise ValueError("unreadable trade side")


def normalize_broker_deal(raw: dict) -> dict:
    """Return one broker deal in this file's shape. Raises ``ValueError`` when unreadable."""
    deal_id = str(raw.get("deal_id") or "").strip()
    if not deal_id:
        raise ValueError("deal without deal_id")
    kind, underlying = classify_code(raw.get("code"))
    qty = float(raw.get("qty"))
    price = float(raw.get("price"))
    if qty <= 0 or price < 0:
        raise ValueError("non-positive quantity or negative price")
    return {
        "deal_id": deal_id,
        "order_id": str(raw.get("order_id") or "").strip(),
        "kind": kind,
        "underlying": underlying,
        "side": side_of(raw.get("trd_side")),
        "qty": qty,
        "price": price,
        "create_time": str(raw.get("create_time") or ""),
    }


def deal_cash_flow(side: str, qty: float, price: float, kind: str) -> float:
    """Cash for one deal: sale is positive, purchase is negative. Options are x100 per contract."""
    multiplier = OPTION_MULTIPLIER if kind == "OPT" else 1
    sign = 1.0 if side == "SELL" else -1.0
    return sign * qty * price * multiplier


def history_windows(start: date, end: date, span_days: int = HISTORY_WINDOW_DAYS) -> list[tuple[date, date]]:
    """Split [start, end] into windows of at most ``span_days`` + 1 days.

    Each window starts on the previous window's last day, so consecutive
    windows share one day.
    """
    if start > end:
        return []
    windows = []
    cursor = start
    while True:
        window_end = min(cursor + timedelta(days=span_days), end)
        windows.append((cursor, window_end))
        if window_end >= end:
            return windows
        cursor = window_end


# --- Fill and fee reconciliation ----------------------------------------


def compare_fills(db_fills: list[dict], broker_deals: list[dict]) -> dict:
    """Match stored fills to broker deals by deal id, and compare side, qty, price and order id."""
    db_by_id = {str(row["fill_id"]): row for row in db_fills}
    broker_by_id = {str(row["deal_id"]): row for row in broker_deals}
    field_mismatches = []
    matched = 0
    for deal_id in sorted(set(db_by_id) & set(broker_by_id)):
        db_row = db_by_id[deal_id]
        broker_row = broker_by_id[deal_id]
        found = False
        if str(db_row.get("order_id") or "") != broker_row["order_id"]:
            found = True
            field_mismatches.append(
                {
                    "deal_id": deal_id,
                    "field": "order_id",
                    "db": db_row.get("order_id"),
                    "broker": broker_row["order_id"],
                }
            )
        if str(db_row.get("side") or "").upper() != broker_row["side"]:
            found = True
            field_mismatches.append(
                {"deal_id": deal_id, "field": "side", "db": db_row.get("side"), "broker": broker_row["side"]}
            )
        if abs(float(db_row["qty"]) - broker_row["qty"]) > QTY_TOLERANCE:
            found = True
            field_mismatches.append(
                {"deal_id": deal_id, "field": "qty", "db": db_row["qty"], "broker": broker_row["qty"]}
            )
        if abs(float(db_row["price"]) - broker_row["price"]) > PRICE_TOLERANCE:
            found = True
            field_mismatches.append(
                {"deal_id": deal_id, "field": "price", "db": db_row["price"], "broker": broker_row["price"]}
            )
        if not found:
            matched += 1
    return {
        "db_count": len(db_by_id),
        "broker_count": len(broker_by_id),
        "matched": matched,
        "missing_in_db": sorted(set(broker_by_id) - set(db_by_id)),
        "missing_in_broker": sorted(set(db_by_id) - set(broker_by_id)),
        "field_mismatches": field_mismatches,
    }


def compare_order_fees(db_fills: list[dict], broker_fee_by_order: dict[str, float]) -> dict:
    """Compare the stored fee sum per order with the broker order fee.

    A NULL stored fee means unknown, not zero. Such an order is counted under
    ``unknown_db_fee_orders`` and is never compared as a match or a mismatch.
    """
    by_order: dict[str, list[dict]] = {}
    for row in db_fills:
        by_order.setdefault(str(row.get("order_id") or ""), []).append(row)
    result = {
        "orders_checked": 0,
        "matched": [],
        "mismatches": [],
        "unknown_db_fee_orders": [],
        "broker_fee_missing": [],
        "null_db_fee_fills": 0,
    }
    for order_id, fills in sorted(by_order.items()):
        if not order_id:
            continue
        result["orders_checked"] += 1
        null_count = sum(1 for row in fills if row.get("fees") is None)
        result["null_db_fee_fills"] += null_count
        if order_id not in broker_fee_by_order:
            result["broker_fee_missing"].append(order_id)
            continue
        if null_count:
            result["unknown_db_fee_orders"].append(order_id)
            continue
        db_total = sum(float(row["fees"]) for row in fills)
        broker_total = float(broker_fee_by_order[order_id])
        if abs(db_total - broker_total) > FEE_TOLERANCE:
            result["mismatches"].append(
                {
                    "order_id": order_id,
                    "db_fee": round(db_total, 4),
                    "broker_fee": round(broker_total, 4),
                    "diff": round(db_total - broker_total, 4),
                }
            )
        else:
            result["matched"].append(order_id)
    return result


# --- Independent per-underlying P&L (broker side) -----------------------


def _new_underlying_row() -> dict:
    return {
        "option_cash": 0.0,
        "share_cash": 0.0,
        "option_mark": 0.0,
        "share_mark": 0.0,
        "fees": 0.0,
        "deal_count": 0,
        "order_ids": set(),
        "fee_missing_orders": [],
        "unknown_mark": False,
        "problems": [],
    }


def independent_pnl(deals: list[dict], fee_by_order: dict[str, float], positions: list[dict]) -> dict:
    """Net P&L per underlying from raw broker rows: deal cash plus live marks minus order fees.

    ``positions`` items need ``kind``, ``underlying``, ``shares``,
    ``market_price`` and ``market_value``. A missing fee or mark makes that
    underlying's net None (unknown).
    """
    rows: dict[str, dict] = {}
    order_underlyings: dict[str, set[str]] = {}
    problems: list[str] = []

    def row_for(underlying: str) -> dict:
        return rows.setdefault(underlying, _new_underlying_row())

    for deal in deals:
        row = row_for(deal["underlying"])
        row["deal_count"] += 1
        cash = deal_cash_flow(deal["side"], deal["qty"], deal["price"], deal["kind"])
        row["option_cash" if deal["kind"] == "OPT" else "share_cash"] += cash
        row["order_ids"].add(deal["order_id"])
        order_underlyings.setdefault(deal["order_id"], set()).add(deal["underlying"])

    for order_id, underlyings in sorted(order_underlyings.items()):
        if len(underlyings) != 1:
            problems.append(f"order {order_id or '(none)'} spans more than one underlying; fee not assigned")
            continue
        (underlying,) = underlyings
        row = row_for(underlying)
        if order_id not in fee_by_order:
            row["fee_missing_orders"].append(order_id)
            continue
        row["fees"] += float(fee_by_order[order_id])

    for position in positions:
        row = row_for(position["underlying"])
        value = position.get("market_value")
        if value is None:
            row["unknown_mark"] = True
            continue
        if position["kind"] == "OPT":
            row["option_mark"] += float(value)
            price = position.get("market_price")
            if price is not None:
                expected = float(position["shares"]) * float(price) * OPTION_MULTIPLIER
                if abs(expected - float(value)) > max(MONEY_TOLERANCE, 0.001 * abs(float(value))):
                    row["problems"].append("option market value differs from quantity x price x 100")
        else:
            row["share_mark"] += float(value)

    result = {}
    for underlying, row in sorted(rows.items()):
        option_pnl = row["option_cash"] + row["option_mark"]
        share_pnl = row["share_cash"] + row["share_mark"]
        unknown = row["unknown_mark"] or bool(row["fee_missing_orders"])
        result[underlying] = {
            "option_cash": round(row["option_cash"], 2),
            "share_cash": round(row["share_cash"], 2),
            "option_mark": round(row["option_mark"], 2),
            "share_mark": round(row["share_mark"], 2),
            "option_pnl": round(option_pnl, 2),
            "share_pnl": round(share_pnl, 2),
            "fees": round(row["fees"], 2),
            "net": None if unknown else round(option_pnl + share_pnl - row["fees"], 2),
            "deal_count": row["deal_count"],
            "order_ids": sorted(row["order_ids"]),
            "fee_missing_orders": row["fee_missing_orders"],
            "problems": row["problems"],
        }
    return {"by_underlying": result, "problems": problems}


def same_scope_totals(app_rows: list, indep_rows: dict) -> dict:
    """Net P&L totals over the rows both sides can compute.

    Partial-history rows are excluded: their shares predate the deal history, so
    deals alone cannot rebuild their cost. Unreconciled app rows (net None) are
    excluded too. Each exclusion is listed by ticker.
    """
    scope, partial, unreconciled = [], [], []
    for row in app_rows:
        ticker = row.get("ticker")
        if row.get("net_pnl") is None:
            unreconciled.append(ticker)
        elif row.get("status") == "partial_history":
            partial.append(ticker)
        elif ticker in indep_rows and indep_rows[ticker].get("net") is not None:
            scope.append(row)
    app_sum = round(sum(row["net_pnl"] for row in scope), 2)
    indep_sum = round(sum(indep_rows[row["ticker"]]["net"] for row in scope), 2)
    return {
        "app_same_scope_net_sum": app_sum,
        "independent_same_scope_net_sum": indep_sum,
        "same_scope_diff": round(app_sum - indep_sum, 2),
        "same_scope_tickers": sorted(row["ticker"] for row in scope),
        "excluded_partial_history": partial,
        "excluded_unreconciled": unreconciled,
    }


def compare_pnl(
    app_rows: list[dict],
    indep_rows: dict[str, dict],
    null_fee_by_ticker: dict[str, float] | None = None,
    tolerance: float = MONEY_TOLERANCE,
) -> dict:
    """Compare the app's per-underlying net P&L with the broker-side figure.

    Only app rows marked reconciled count toward mismatches. Unreconciled rows
    (net None) are listed, not compared. Partial-history rows are listed, not
    compared, because the app values pre-history shares at broker cost.
    A difference that equals the broker fees of NULL-fee orders is listed as
    explained. Any other difference over ``tolerance`` is a mismatch.
    """
    null_fee_by_ticker = null_fee_by_ticker or {}
    out = {
        "mismatches": [],
        "matched": [],
        "fee_unknown_explained": [],
        "partial_history": [],
        "unreconciled_app": [],
        "independent_unknown": [],
        "independent_only": [],
    }
    app_by = {str(row["ticker"]): row for row in app_rows}
    for ticker, app in sorted(app_by.items()):
        if app.get("net_pnl") is None:
            out["unreconciled_app"].append(ticker)
            continue
        indep = indep_rows.get(ticker)
        if indep is None:
            out["mismatches"].append(
                {"ticker": ticker, "reason": "reconciled in app, absent from broker-side computation"}
            )
            continue
        if indep["net"] is None:
            out["independent_unknown"].append(ticker)
            continue
        diff = round(float(app["net_pnl"]) - indep["net"], 2)
        record = {
            "ticker": ticker,
            "app_status": app.get("status"),
            "app_net": app["net_pnl"],
            "independent_net": indep["net"],
            "diff": diff,
            "app": {"option_pnl": app.get("option_pnl"), "share_pnl": app.get("share_pnl"), "fees": app.get("fees")},
            "independent": {
                "option_pnl": indep["option_pnl"],
                "share_pnl": indep["share_pnl"],
                "fees": indep["fees"],
            },
            "app_fees_unknown": app.get("fees_unknown", 0),
            "app_notes": list(app.get("notes") or []),
            "independent_problems": list(indep.get("problems") or []),
        }
        if app.get("status") == "partial_history":
            out["partial_history"].append(record)
            continue
        null_fee_total = float(null_fee_by_ticker.get(ticker, 0.0))
        if app.get("fees_unknown") and abs(diff - null_fee_total) <= tolerance:
            record["explained_by_null_db_fees"] = round(null_fee_total, 2)
            out["fee_unknown_explained"].append(record)
            continue
        if abs(diff) > tolerance:
            out["mismatches"].append(record)
        else:
            out["matched"].append(ticker)
    for ticker in sorted(set(indep_rows) - set(app_by)):
        out["independent_only"].append(
            {
                "ticker": ticker,
                "independent_net": indep_rows[ticker]["net"],
                "deal_count": indep_rows[ticker]["deal_count"],
            }
        )
    return out


# --- Positions, NAV and cash flows --------------------------------------


def compare_positions(broker_positions: list[dict], snapshot_positions: list[dict]) -> dict:
    """Compare live broker positions with the latest stored snapshot by symbol."""
    broker = {normalize_symbol(row["code"]): row for row in broker_positions}
    snapshot = {normalize_symbol(row.get("symbol")): row for row in snapshot_positions}
    qty_mismatch = []
    price_differences = []
    matched = 0
    for symbol in sorted(set(broker) & set(snapshot)):
        b_qty = float(broker[symbol]["shares"])
        s_qty = float(snapshot[symbol]["qty"])
        if abs(b_qty - s_qty) > QTY_TOLERANCE:
            qty_mismatch.append({"symbol": symbol, "broker_qty": b_qty, "snapshot_qty": s_qty})
            continue
        matched += 1
        b_price = broker[symbol].get("market_price")
        s_price = snapshot[symbol].get("market_price")
        if b_price is not None and s_price is not None and abs(float(b_price) - float(s_price)) > 1e-9:
            price_differences.append(
                {
                    "symbol": symbol,
                    "broker_price": float(b_price),
                    "snapshot_price": float(s_price),
                    "abs_diff": round(abs(float(b_price) - float(s_price)), 4),
                }
            )
    return {
        "matched_qty": matched,
        "only_in_broker": sorted(set(broker) - set(snapshot)),
        "only_in_snapshot": sorted(set(snapshot) - set(broker)),
        "qty_mismatch": qty_mismatch,
        "price_differences": price_differences,
    }


def parse_stamp(text) -> datetime:
    """Parse an ISO timestamp. A naive value is taken as local time. Result is timezone-aware."""
    value = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    return value if value.tzinfo else value.astimezone()


def daily_last_nav(snapshots: list[dict], tz=MARKET_TZ) -> list[tuple[date, float, str]]:
    """One NAV per market-calendar day: the last positive snapshot of that day."""
    by_day: dict[date, tuple[datetime, float, str]] = {}
    for snap in snapshots:
        try:
            nav = float(snap.get("net_liquidation"))
        except (TypeError, ValueError):
            continue
        if nav <= 0:
            continue
        stamp = parse_stamp(snap.get("captured_at"))
        day = stamp.astimezone(tz).date()
        if day not in by_day or stamp > by_day[day][0]:
            by_day[day] = (stamp, nav, str(snap.get("captured_at")))
    return [(day, nav, text) for day, (_, nav, text) in sorted(by_day.items())]


def capital_flow_amount(direction, amount) -> float | None:
    """Signed capital movement from the broker direction. Amount sign is not trusted.

    The broker reports ``cashflow_amount`` as a magnitude with a direction.
    Unknown directions return None, so the flow is reported and not guessed.
    """
    text = str(direction or "").strip().upper()
    try:
        value = abs(float(amount))
    except (TypeError, ValueError):
        return None
    if text in _IN_DIRECTIONS:
        return value
    if text in _OUT_DIRECTIONS:
        return -value
    return None


def modified_dietz(
    start_value: float,
    end_value: float,
    flows: list[tuple[date, float]],
    start_day: date,
    end_day: date,
) -> dict | None:
    """Modified Dietz return over [start_day, end_day].

    A flow dated after start_day and on or before end_day is weighted by
    (end_day - flow_day) / days. A flow on start_day is taken to be in the
    start NAV already. Deposits are positive and withdrawals negative.
    """
    days = (end_day - start_day).days
    if days <= 0 or start_value <= 0:
        return None
    weighted = 0.0
    total = 0.0
    used = 0
    outside = 0
    for flow_day, amount in flows:
        if start_day < flow_day <= end_day:
            weighted += amount * (end_day - flow_day).days / days
            total += amount
            used += 1
        else:
            outside += 1
    denominator = start_value + weighted
    if denominator <= 0:
        return None
    gain = end_value - start_value - total
    return {
        "return": gain / denominator,
        "plain_change": end_value / start_value - 1.0,
        "gain": gain,
        "denominator": denominator,
        "net_flow_in_window": total,
        "flows_used": used,
        "flows_outside_window": outside,
        "days": days,
    }


def period_return_from_closes(rows: list[dict], start_day: date, end_day: date) -> dict | None:
    """Close-to-close return using the last close on or before each day."""
    usable = sorted(
        (date.fromisoformat(str(row["date"])[:10]), float(row["close"])) for row in rows if row.get("close") is not None
    )
    before_start = [point for point in usable if point[0] <= start_day]
    before_end = [point for point in usable if point[0] <= end_day]
    if not before_start or not before_end:
        return None
    (s_day, s_close), (e_day, e_close) = before_start[-1], before_end[-1]
    return {
        "start_date": s_day.isoformat(),
        "start_close": s_close,
        "end_date": e_day.isoformat(),
        "end_close": e_close,
        "return": e_close / s_close - 1.0,
        "start_gap_days": (start_day - s_day).days,
    }


def cash_return(days: int, annual_rate: float = CASH_RATE_ANNUAL) -> float:
    """Simple cash return: annual rate pro-rated by actual days over 365."""
    return annual_rate * days / 365.0


def us_sessions_between(start_at: datetime, end_at: datetime) -> list[str]:
    """Weekday regular sessions (09:30-16:00 ET) that overlap (start_at, end_at).

    US exchange holidays are not modelled, so a holiday weekday is listed as a session.
    """
    start = parse_stamp(start_at).astimezone(MARKET_TZ)
    end = parse_stamp(end_at).astimezone(MARKET_TZ)
    sessions = []
    day = start.date()
    while day <= end.date():
        if day.weekday() < 5:
            open_at = datetime.combine(day, MARKET_OPEN, tzinfo=MARKET_TZ)
            close_at = datetime.combine(day, MARKET_CLOSE, tzinfo=MARKET_TZ)
            if open_at < end and close_at > start:
                sessions.append(day.isoformat())
        day += timedelta(days=1)
    return sessions


# --- Private output helpers (no account identifiers) --------------------


def copy_database_read_only(source: Path, target: Path) -> None:
    """Copy the database with the SQLite backup API, opening the source read-only."""
    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        leftover = Path(str(target) + suffix)
        if leftover.exists():
            leftover.unlink()
    source_conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        source_conn.execute("PRAGMA temp_store=memory")
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source_conn.close()


def _read_rows(db_path: Path, sql: str, params: tuple) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def read_stored_fills(db_path: Path, env: str, opaque_account: str) -> list[dict]:
    return _read_rows(
        db_path,
        "SELECT fill_id, order_id, captured_at, security_type, ticker, side, qty, price, fees "
        "FROM option_fills WHERE env = ? AND account_id = ?",
        (env, opaque_account),
    )


def read_stored_snapshots(db_path: Path, env: str, opaque_account: str) -> list[dict]:
    rows = _read_rows(
        db_path,
        "SELECT run_id, captured_at, net_liquidation, positions_json FROM portfolio_snapshots "
        "WHERE env = ? AND account_id = ?",
        (env, opaque_account),
    )
    for row in rows:
        try:
            row["positions"] = json.loads(row.pop("positions_json") or "[]")
        except (TypeError, ValueError):
            row["positions"] = []
    return rows


# --- Broker reads (query-only) -------------------------------------------


def pull_deals(conn, start: date, end: date) -> dict:
    """Query deal history in windows. The last window is open-ended, as in the app."""
    windows = history_windows(start, end)
    raw_rows: list[dict] = []
    windows_report = []
    failed = []
    for index, (window_start, window_end) in enumerate(windows):
        query_end = "" if index == len(windows) - 1 else window_end.isoformat()
        rows = conn.get_history_deals(start=window_start.isoformat(), end=query_end)
        if rows is None:
            failed.append({"start": window_start.isoformat(), "end": window_end.isoformat()})
            windows_report.append({"start": window_start.isoformat(), "end": window_end.isoformat(), "rows": None})
            continue
        raw_rows.extend(rows)
        windows_report.append({"start": window_start.isoformat(), "end": window_end.isoformat(), "rows": len(rows)})
    return {"raw": raw_rows, "windows": windows_report, "failed_windows": failed}


def pull_order_fees(conn, order_ids: list[str]) -> dict:
    fee_by_order: dict[str, float] = {}
    failed_batches = 0
    for offset in range(0, len(order_ids), ORDER_FEE_BATCH):
        batch = order_ids[offset : offset + ORDER_FEE_BATCH]
        rows = conn.get_order_fees(batch)
        if rows is None:
            failed_batches += 1
            continue
        for row in rows:
            order_id = str(row.get("order_id") or "").strip()
            if order_id:
                fee_by_order[order_id] = float(row.get("fee_amount") or 0.0)
    return {"fee_by_order": fee_by_order, "failed_batches": failed_batches, "queried": len(order_ids)}


def pull_cash_flows(conn, first_day: date, last_day: date) -> dict:
    """Query every calendar date in [first_day, last_day]. Dates that fail are listed."""
    rows: list[dict] = []
    failed: list[str] = []
    queried = 0
    day = first_day
    while day <= last_day:
        queried += 1
        flows = conn.get_cash_flow(day.isoformat())
        if flows is None:
            failed.append(day.isoformat())
        else:
            for flow in flows:
                rows.append(
                    {
                        "clearing_date": str(flow.get("clearing_date") or day.isoformat()),
                        "currency": str(flow.get("currency") or ""),
                        "flow_type": str(flow.get("cashflow_type") or ""),
                        "direction": str(flow.get("cashflow_direction") or ""),
                        "amount": flow.get("cashflow_amount"),
                        "remark": str(flow.get("cashflow_remark") or ""),
                    }
                )
        day += timedelta(days=1)
    return {"rows": rows, "queried": queried, "failed_dates": failed}


# --- Orchestration ------------------------------------------------------


def _load_spy_rows() -> list[dict] | None:
    if not PRICES_JSON.exists():
        return None
    data = json.loads(PRICES_JSON.read_text(encoding="utf-8"))
    spy = data.get("SPY") if isinstance(data, dict) else None
    return spy.get("rows") if isinstance(spy, dict) else None


def build_report(
    *,
    env: str,
    app_payload: dict,
    stored_fills: list[dict],
    stored_snapshots: list[dict],
    deal_pull: dict,
    fee_pull: dict,
    portfolio: dict | None,
    cash_pull: dict,
    spy_rows: list[dict] | None,
) -> dict:
    """Assemble every check from raw inputs. Pure: all broker and DB reads happen in the caller."""
    normalized, deal_status_counts, unreadable = [], {}, []
    for raw in deal_pull["raw"]:
        status = str(raw.get("status") or "").upper() if isinstance(raw, dict) else "NOT_A_DICT"
        deal_status_counts[status] = deal_status_counts.get(status, 0) + 1
        if status != "OK":
            continue
        try:
            normalized.append(normalize_broker_deal(raw))
        except (TypeError, ValueError) as exc:
            unreadable.append(str(exc))
    unique = {}
    for deal in normalized:
        unique[deal["deal_id"]] = deal
    deals = list(unique.values())

    fills_check = compare_fills(stored_fills, deals)
    order_underlying = {}
    for deal in deals:
        order_underlying.setdefault(deal["order_id"], deal["underlying"])
    fee_check = compare_order_fees(stored_fills, fee_pull["fee_by_order"])
    null_fee_by_ticker: dict[str, float] = {}
    for order_id in fee_check["unknown_db_fee_orders"]:
        ticker = order_underlying.get(order_id)
        if ticker and order_id in fee_pull["fee_by_order"]:
            null_fee_by_ticker[ticker] = null_fee_by_ticker.get(ticker, 0.0) + fee_pull["fee_by_order"][order_id]

    report: dict = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "env": env,
        "coverage": {
            "deal_windows": deal_pull["windows"],
            "deal_failed_windows": deal_pull["failed_windows"],
            "deal_status_counts": deal_status_counts,
            "deals_ok_unique": len(deals),
            "deals_unreadable": len(unreadable),
            "order_fee_orders_queried": fee_pull["queried"],
            "order_fee_failed_batches": fee_pull["failed_batches"],
            "cash_flow_dates_queried": cash_pull["queried"],
            "cash_flow_failed_dates": cash_pull["failed_dates"],
            "cash_flow_rows": len(cash_pull["rows"]),
        },
        "fills": fills_check,
        "fees": fee_check,
    }

    # Positions and NAV use the latest stored snapshot.
    latest = max(stored_snapshots, key=lambda s: parse_stamp(s["captured_at"])) if stored_snapshots else None
    if latest is not None and portfolio is not None:
        broker_positions = []
        for code, pos in (portfolio.get("positions") or {}).items():
            try:
                kind, underlying = classify_code(code)
            except ValueError:
                report.setdefault("unclassified_broker_codes", []).append(normalize_symbol(code))
                continue
            broker_positions.append(
                {
                    "code": normalize_symbol(code),
                    "kind": kind,
                    "underlying": underlying,
                    "shares": float(pos.get("shares") or 0.0),
                    "market_price": pos.get("market_price"),
                    "market_value": pos.get("market_value"),
                }
            )
        report["positions"] = compare_positions(broker_positions, latest["positions"])
        report["positions"]["snapshot_captured_at"] = latest["captured_at"]
        report["positions"]["us_sessions_since_snapshot"] = us_sessions_between(
            parse_stamp(latest["captured_at"]), datetime.now().astimezone()
        )
        report["positions"]["snapshot_market_val_zero_count"] = sum(
            1 for row in latest["positions"] if float(row.get("market_val") or 0.0) == 0.0
        )
        report["positions"]["snapshot_position_count"] = len(latest["positions"])
    else:
        broker_positions = []
        report["positions"] = {"error": "no stored snapshot or broker portfolio read"}

    if portfolio is not None:
        indep = independent_pnl(deals, fee_pull["fee_by_order"], broker_positions)
        indep_rows = indep["by_underlying"]
        report["pnl"] = {"independent_problems": indep["problems"], "independent_by_underlying": indep_rows}
        app_rows = app_payload.get("underlyings") or []
        comparison = compare_pnl(app_rows, indep_rows, null_fee_by_ticker)
        app_known = [row for row in app_rows if row.get("net_pnl") is not None]
        report["pnl"].update(
            {
                "comparison": comparison,
                "null_fee_by_ticker": {k: round(v, 2) for k, v in null_fee_by_ticker.items()},
                "totals": {
                    "app_totals_net_pnl": app_payload["totals"]["net_pnl"],
                    "app_rows_net_sum": round(sum(row["net_pnl"] for row in app_known), 2),
                    **same_scope_totals(app_rows, indep_rows),
                    "independent_all_net_sum": round(
                        sum(row["net"] for row in indep_rows.values() if row["net"] is not None), 2
                    ),
                    "app_unreconciled_rows": app_payload["totals"]["unreconciled_count"],
                    "independent_unknown_rows": sum(1 for row in indep_rows.values() if row["net"] is None),
                },
            }
        )
        report["nav_live_vs_snapshot"] = {
            "live_account_value": portfolio.get("account_value"),
            "snapshot_net_liquidation": latest["net_liquidation"] if latest else None,
            "snapshot_captured_at": latest["captured_at"] if latest else None,
            "diff": (
                round(float(portfolio.get("account_value") or 0.0) - float(latest["net_liquidation"]), 2)
                if latest
                else None
            ),
        }

    report["app_read_model"] = {
        "totals": app_payload.get("totals"),
        "nav_since_last_capital_movement": app_payload.get("nav_since_last_capital_movement"),
        "capital_movements": app_payload.get("capital_movements"),
        "income": app_payload.get("income"),
        "cash_flow_coverage": app_payload.get("cash_flow_coverage"),
        "fill_coverage": app_payload.get("fill_coverage"),
    }

    # Cash flows: classify capital movements by type and remark, sign them by direction.
    flows_all = cash_pull["rows"]
    type_summary: dict[str, dict] = {}
    capital_rows = []
    for row in flows_all:
        is_capital = is_capital_movement_cash_flow(row["flow_type"], row["remark"])
        key = f"{row['direction']} | {row['flow_type']}"
        entry = type_summary.setdefault(key, {"count": 0, "capital_movement": is_capital})
        entry["count"] += 1
        if is_capital:
            signed = capital_flow_amount(row["direction"], row["amount"])
            capital_rows.append(
                {"clearing_date": row["clearing_date"], "signed_amount": signed, "direction": row["direction"]}
            )
    report["cash_flows"] = {
        "queried_from": cash_pull.get("from"),
        "queried_to": cash_pull.get("to"),
        "type_summary": type_summary,
        "capital_movement_rows": capital_rows,
        "capital_movements_unsigned": sum(1 for r in capital_rows if r["signed_amount"] is None),
    }

    report["nav_window"] = None
    nav_days = daily_last_nav(stored_snapshots)
    if len(nav_days) >= 2:
        start_day, start_nav, start_at = nav_days[0]
        end_day, end_nav, end_at = nav_days[-1]
        signed_in_window = [
            (date.fromisoformat(row["clearing_date"][:10]), row["signed_amount"])
            for row in capital_rows
            if row["signed_amount"] is not None and start_day < date.fromisoformat(row["clearing_date"][:10]) <= end_day
        ]
        dietz = modified_dietz(start_nav, end_nav, signed_in_window, start_day, end_day)
        days = (end_day - start_day).days
        spy = period_return_from_closes(spy_rows, start_day, end_day) if spy_rows else None
        report["nav_window"] = {
            "start_day": start_day.isoformat(),
            "start_nav": start_nav,
            "start_snapshot_at": start_at,
            "end_day": end_day.isoformat(),
            "end_nav": end_nav,
            "end_snapshot_at": end_at,
            "days": days,
            "nav_days": len(nav_days),
            "plain_change": round(end_nav / start_nav - 1.0, 6),
            "modified_dietz": dietz,
            "spy": spy,
            "cash_4pct_simple": round(cash_return(days), 6),
            "capital_movements_in_window": len(signed_in_window),
        }
        if dietz is not None:
            report["nav_window"]["dietz_minus_plain"] = round(dietz["return"] - dietz["plain_change"], 6)
    return report


def render_markdown(report: dict) -> str:
    """Short human summary of the JSON report (private output)."""
    cov = report["coverage"]
    lines = [
        "# Broker return check",
        "",
        f"Generated: {report['generated_at']}  env: {report.get('env', '')}",
        "",
        "## Coverage",
        f"- Deal windows: {len(cov['deal_windows'])}, failed: {len(cov['deal_failed_windows'])}",
        f"- Deal status counts: {cov['deal_status_counts']}",
        f"- OK unique deals: {cov['deals_ok_unique']}, unreadable: {cov['deals_unreadable']}",
        f"- Order fee orders queried: {cov['order_fee_orders_queried']}, failed batches: {cov['order_fee_failed_batches']}",
        f"- Cash-flow dates queried: {cov['cash_flow_dates_queried']}, failed: {len(cov['cash_flow_failed_dates'])}",
        "",
    ]
    fills = report["fills"]
    lines += [
        "## Fill reconciliation",
        f"- Matched: {fills['matched']} of DB {fills['db_count']} / broker {fills['broker_count']}",
        f"- Missing in DB: {len(fills['missing_in_db'])}; missing at broker: {len(fills['missing_in_broker'])}",
        f"- Field mismatches: {len(fills['field_mismatches'])}",
    ]
    for item in fills["field_mismatches"][:20]:
        lines.append(f"  - {item}")
    fees = report["fees"]
    lines += [
        "",
        "## Order fee reconciliation",
        f"- Orders checked: {fees['orders_checked']}; matched: {len(fees['matched'])}; "
        f"mismatches: {len(fees['mismatches'])}",
        f"- Unknown (NULL DB fee): {len(fees['unknown_db_fee_orders'])} orders, {fees['null_db_fee_fills']} fills",
        f"- Broker fee missing: {len(fees['broker_fee_missing'])}",
    ]
    for item in fees["mismatches"][:20]:
        lines.append(f"  - {item}")
    if "error" in report.get("positions", {}):
        lines += ["", "## Positions", f"- {report['positions']['error']}"]
    else:
        pos = report.get("positions", {})
        if pos:
            lines += [
                "",
                "## Positions vs latest snapshot",
                f"- Snapshot captured: {pos.get('snapshot_captured_at')}",
                f"- US regular sessions since snapshot (holidays not modelled): {len(pos.get('us_sessions_since_snapshot', []))}",
                f"- Matched qty: {pos.get('matched_qty')}; qty mismatch: {len(pos.get('qty_mismatch', []))}",
                f"- Only in broker: {len(pos.get('only_in_broker', []))}; only in snapshot: {len(pos.get('only_in_snapshot', []))}",
                f"- Price differences: {len(pos.get('price_differences', []))}",
                f"- Snapshot market_val zero: {pos.get('snapshot_market_val_zero_count')} of {pos.get('snapshot_position_count')}",
            ]
    if "nav_live_vs_snapshot" in report:
        nav = report["nav_live_vs_snapshot"]
        lines += [
            "",
            "## NAV",
            f"- Live account value vs latest snapshot: {nav['live_account_value']} vs {nav['snapshot_net_liquidation']} "
            f"(diff {nav['diff']})",
        ]
    if "pnl" in report:
        pnl = report["pnl"]
        comp = pnl["comparison"]
        tot = pnl["totals"]
        lines += [
            "",
            "## P&L per underlying",
            f"- Mismatches: {len(comp['mismatches'])}; matched: {len(comp['matched'])}; "
            f"partial history (not compared): {len(comp['partial_history'])}",
            f"- Fee-unknown explained: {len(comp['fee_unknown_explained'])}; "
            f"app unreconciled (not compared): {len(comp['unreconciled_app'])}; "
            f"independent unknown: {len(comp['independent_unknown'])}; independent only: {len(comp['independent_only'])}",
            f"- Totals: app {tot['app_totals_net_pnl']}, app row sum {tot['app_rows_net_sum']}, "
            f"independent all {tot['independent_all_net_sum']}",
            f"- Same scope ({len(tot['same_scope_tickers'])} underlyings): app {tot['app_same_scope_net_sum']} vs "
            f"independent {tot['independent_same_scope_net_sum']} (diff {tot['same_scope_diff']}); excluded partial "
            f"history {tot['excluded_partial_history']}, unreconciled {tot['excluded_unreconciled']}",
        ]
        for item in comp["mismatches"]:
            lines.append(f"  - MISMATCH {item}")
        for item in comp["partial_history"]:
            lines.append(
                f"  - partial history {item['ticker']}: app {item['app_net']} vs independent {item['independent_net']}"
            )
    if report.get("nav_window"):
        win = report["nav_window"]
        dz = win["modified_dietz"]
        lines += [
            "",
            "## Returns",
            f"- Window: {win['start_day']} to {win['end_day']} ({win['days']} days, {win['nav_days']} NAV days)",
            f"- Plain NAV change: {win['plain_change']:.4%}",
        ]
        if dz:
            lines.append(f"- Modified Dietz: {dz['return']:.4%} (flows used {dz['flows_used']})")
        if win.get("spy"):
            lines.append(f"- SPY close-to-close: {win['spy']['return']:.4%}")
        lines.append(f"- Cash at 4% simple: {win['cash_4pct_simple']:.4%}")
    return "\n".join(lines) + "\n"


def main() -> int:
    # Lazy imports keep the pure functions free of broker and SDK loading.
    from api.services.account_pnl_service import build_account_pnl_payload
    from config import Config
    from core.connection_manager import MoomooConnection
    from db.database import OptionsDatabase

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    source_db = REPO_ROOT / str(cfg.get("db_path", "options.db"))
    copy_database_read_only(source_db, COPY_DB)
    today = date.today()
    history_start = date.fromisoformat(str(cfg.get("broker_history_start", "2025-01-01"))[:10])

    conn = MoomooConnection(
        host=cfg.get("host", "127.0.0.1"),
        port=int(cfg.get("port", 11111) or 11111),
        readonly=True,
        account_id=cfg.get("account_id"),
        portfolio_env=cfg.get("portfolio_env"),
        security_firm=cfg.get("security_firm"),
    )
    if not conn.connect():
        message = {"error": conn.last_error or "OpenD unreachable"}
        JSON_OUT.write_text(json.dumps(message, indent=2, default=str), encoding="utf-8")
        print(f"OpenD not reachable: {message['error']}")
        return 1
    try:
        env, opaque_account = conn.resolve_portfolio_identity()
        stored_fills = read_stored_fills(COPY_DB, env, opaque_account)
        stored_snapshots = read_stored_snapshots(COPY_DB, env, opaque_account)

        db = OptionsDatabase(str(COPY_DB))
        try:
            app_payload = build_account_pnl_payload(db, env, opaque_account)
        finally:
            db.close()

        deal_pull = pull_deals(conn, history_start, today)
        order_ids = sorted(
            {
                str(raw.get("order_id") or "").strip()
                for raw in deal_pull["raw"]
                if isinstance(raw, dict) and str(raw.get("status") or "").upper() == "OK" and raw.get("order_id")
            }
        )
        fee_pull = pull_order_fees(conn, order_ids)
        portfolio = conn.get_portfolio()

        nav_days = daily_last_nav(stored_snapshots)
        first_day = nav_days[0][0] if nav_days else history_start
        cash_pull = pull_cash_flows(conn, first_day, today)
        cash_pull["from"] = first_day.isoformat()
        cash_pull["to"] = today.isoformat()

        report = build_report(
            env=env,
            app_payload=app_payload,
            stored_fills=stored_fills,
            stored_snapshots=stored_snapshots,
            deal_pull=deal_pull,
            fee_pull=fee_pull,
            portfolio=portfolio,
            cash_pull=cash_pull,
            spy_rows=_load_spy_rows(),
        )
    finally:
        conn.disconnect()

    JSON_OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    MD_OUT.write_text(render_markdown(report), encoding="utf-8")
    _print_summary(report)
    return 0


def _print_summary(report: dict) -> None:
    cov = report["coverage"]
    print(f"env={report.get('env')} deals_ok={cov['deals_ok_unique']} unreadable={cov['deals_unreadable']}")
    print(f"deal windows={len(cov['deal_windows'])} failed={len(cov['deal_failed_windows'])}")
    print(
        f"cash-flow dates queried={cov['cash_flow_dates_queried']} failed={len(cov['cash_flow_failed_dates'])} "
        f"rows={cov['cash_flow_rows']}"
    )
    fills = report["fills"]
    print(
        f"fills matched={fills['matched']} missing_in_db={len(fills['missing_in_db'])} "
        f"missing_at_broker={len(fills['missing_in_broker'])} field_mismatches={len(fills['field_mismatches'])}"
    )
    fees = report["fees"]
    print(
        f"fees matched={len(fees['matched'])} mismatches={len(fees['mismatches'])} "
        f"unknown_db_fee_orders={len(fees['unknown_db_fee_orders'])}"
    )
    if "pnl" in report:
        comp = report["pnl"]["comparison"]
        tot = report["pnl"]["totals"]
        print(
            f"pnl matched={len(comp['matched'])} mismatches={len(comp['mismatches'])} "
            f"partial={len(comp['partial_history'])} unreconciled={len(comp['unreconciled_app'])} "
            f"fee_unknown_explained={len(comp['fee_unknown_explained'])}"
        )
        print(
            f"total net: app={tot['app_totals_net_pnl']} app_rows={tot['app_rows_net_sum']} "
            f"same_scope app={tot['app_same_scope_net_sum']} independent={tot['independent_same_scope_net_sum']} "
            f"diff={tot['same_scope_diff']} independent_all={tot['independent_all_net_sum']}"
        )
    pos = report.get("positions", {})
    if "qty_mismatch" in pos:
        print(
            f"positions qty_mismatch={len(pos['qty_mismatch'])} price_diffs={len(pos['price_differences'])} "
            f"snapshot_at={pos.get('snapshot_captured_at')} "
            f"us_sessions_since_snapshot={len(pos.get('us_sessions_since_snapshot', []))}"
        )
    if report.get("nav_window"):
        win = report["nav_window"]
        dz = win["modified_dietz"]
        spy = win.get("spy") or {}
        print(
            f"nav window {win['start_day']}..{win['end_day']} ({win['days']}d): plain={win['plain_change']:.4%} "
            f"modified_dietz={(dz['return'] if dz else float('nan')):.4%} "
            f"spy={(spy.get('return', float('nan'))):.4%} cash4pct={win['cash_4pct_simple']:.4%}"
        )
    print(f"wrote {JSON_OUT} and {MD_OUT}")


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT))
    raise SystemExit(main())
