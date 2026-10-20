#!/usr/bin/env python
"""Read-only capability probe: which broker history does OpenD actually expose?

Slim replacement for the one-shot ~660-line SDK script of 2026-08. It reuses the
app's structurally read-only ``MoomooConnection`` — the query-only surface that
``core/broker_protocol.py`` and ``tests/test_no_execution_surface.py`` enforce
repo-wide — instead of maintaining a second SDK facade with its own allowlist.

What it answers (the questions outcome attribution depends on):

* is OpenD reachable and is the configured account resolvable;
* how many historical fills exist and over what window (the SDK caps a query at
  90 days, so older trades can never be attributed);
* do fills carry fees, or does each fee need a separate order-level query;
* whether zero-price option movements pair with stock assignments; and
* how long deal/fee queries take against the application's configured trade limiter.

Durable findings and their implications: ``docs/broker-history-capability.md``.

Run:  .venv/Scripts/python tools/probe_broker_history.py
Query-only: no order, unlock, cancel, or modify member is reachable.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402
from core.connection_constants import infer_security_type_from_code, parse_option_code_metadata  # noqa: E402
from core.connection_manager import MoomooConnection  # noqa: E402
from core.ticker_utils import parse_moomoo_symbol  # noqa: E402

FEE_SAMPLE_LIMIT = 20


def probe(days: int = 90) -> dict:
    """Query the broker read-only and summarize what history it serves."""
    cfg = Config()
    conn = MoomooConnection(
        host=cfg.get("host", "127.0.0.1"),
        port=int(cfg.get("port", 11111) or 11111),
        readonly=True,
        account_id=cfg.get("account_id"),
        portfolio_env=cfg.get("portfolio_env"),
        security_firm=cfg.get("security_firm"),
    )

    report: dict = {"opend_reachable": bool(conn.connect())}
    if not report["opend_reachable"]:
        report["error"] = conn.last_error or "OpenD unreachable"
        return report

    try:
        env, opaque_account = conn.resolve_portfolio_identity()
    except Exception as exc:  # account resolution failure IS the finding
        report["error"] = f"Account resolution failed: {exc}"
        conn.disconnect()
        return report
    report["env"] = env
    report["account_resolved"] = bool(opaque_account)

    start = (datetime.now() - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    report["history_window_days_requested"] = days
    report["trade_api_limiter"] = {
        "configured_requests_per_window": conn._rate_limiter._configured_max_requests,
        "window_seconds": conn._rate_limiter.rate_limit_window,
        "burst_threshold": conn._rate_limiter.burst_threshold,
        "burst_window_seconds": conn._rate_limiter.burst_window,
    }
    started = time.perf_counter()
    deals = conn.get_history_deals(start=start, end="")
    report["history_query_elapsed_seconds"] = round(time.perf_counter() - started, 3)
    report["deals"] = None if deals is None else len(deals)
    if not deals:
        report["deals_note"] = "no fills returned — that IS the finding, never a fabricated row"
        conn.disconnect()
        return report

    report["deal_columns"] = sorted(str(key) for key in deals[0].keys())
    stamps = sorted(str(row.get("create_time", "")) for row in deals if row.get("create_time"))
    if stamps:
        report["deal_window"] = {"oldest": stamps[0][:19], "newest": stamps[-1][:19]}

    report["deal_shape"] = _summarize_deal_shapes(deals)
    order_ids = sorted({str(row.get("order_id", "")) for row in deals if row.get("order_id")})
    sampled = order_ids[:FEE_SAMPLE_LIMIT]
    report["orders_in_window"] = len(order_ids)
    report["orders_sampled_for_fees"] = len(sampled)
    started = time.perf_counter()
    fees = conn.get_order_fees(sampled) if sampled else None
    report["fee_query_elapsed_seconds"] = round(time.perf_counter() - started, 3) if sampled else 0.0
    report["orders_with_fees"] = None if fees is None else sum(1 for row in fees if row.get("fee_amount") is not None)
    report["fee_query_complete_for_sample"] = fees is not None and report["orders_with_fees"] == len(sampled)
    conn.disconnect()
    return report


def _summarize_deal_shapes(deals):
    """Report aggregate expiry/assignment clues without printing private rows."""
    now = datetime.now()
    grouped = {}
    stock_rows = []
    for row in deals:
        code = str(row.get("code", "") or "")
        security_type = infer_security_type_from_code(code)
        side = str(row.get("trd_side", "") or "").upper()
        try:
            qty = abs(float(row.get("qty", 0) or 0))
            price = float(row.get("price", 0) or 0)
        except (TypeError, ValueError):
            continue
        stamp = str(row.get("create_time", "") or "")
        if security_type == "STK":
            stock_rows.append((parse_moomoo_symbol(code), side, qty, price, stamp))
        elif security_type == "OPT":
            meta = parse_option_code_metadata(code)
            if not meta:
                continue
            key = (
                meta["underlying"],
                meta["expiration"],
                meta["option_type"],
                float(meta["strike"]),
            )
            item = grouped.setdefault(key, {"sold": 0.0, "bought": 0.0, "zero_price_buys": [], "buy_rows": []})
            if "SELL" in side:
                item["sold"] += qty
            elif "BUY" in side:
                item["bought"] += qty
                item["buy_rows"].append((qty, price, stamp))
                if price == 0:
                    item["zero_price_buys"].append((qty, stamp))

    expired_sold = expired_unclosed = zero_price_closes = possible_assignment_pairs = 0
    zero_price_without_pair = expired_close_before_expiry = 0
    expired_zero_close_after_expiry = expired_other_close_after_expiry = 0
    for (ticker, expiration, option_type, strike), item in grouped.items():
        try:
            normalized_expiration = expiration[:10]
            if len(normalized_expiration) == 8 and normalized_expiration.isdigit():
                expiry_date = datetime.strptime(normalized_expiration, "%Y%m%d")
            else:
                expiry_date = datetime.strptime(normalized_expiration, "%Y-%m-%d")
            expired = expiry_date.date() < now.date()
        except ValueError:
            expired = False
        if expired and item["sold"] > 0:
            expired_sold += 1
            if item["sold"] > item["bought"]:
                expired_unclosed += 1
            last_buy = max(item["buy_rows"], key=lambda row: row[2]) if item["buy_rows"] else None
            if last_buy:
                try:
                    last_buy_date = datetime.fromisoformat(last_buy[2][:19]).date()
                except ValueError:
                    last_buy_date = None
                if last_buy_date:
                    if last_buy_date < expiry_date.date():
                        expired_close_before_expiry += 1
                    elif last_buy[1] == 0:
                        expired_zero_close_after_expiry += 1
                    else:
                        expired_other_close_after_expiry += 1
        zero_price_closes += len(item["zero_price_buys"])
        expected_side = "BUY" if option_type.upper().startswith("PUT") else "SELL"
        for qty, option_stamp in item["zero_price_buys"]:
            try:
                option_time = datetime.fromisoformat(option_stamp[:19])
            except ValueError:
                continue
            if any(
                stock_ticker == ticker
                and stock_side == expected_side
                and abs(stock_qty - qty * 100) < 1e-6
                and abs((datetime.fromisoformat(stock_stamp[:19]) - option_time).total_seconds()) <= 2
                and abs(stock_price - strike) <= 0.01
                for stock_ticker, stock_side, stock_qty, stock_price, stock_stamp in stock_rows
                if stock_stamp
            ):
                possible_assignment_pairs += 1
            else:
                zero_price_without_pair += 1

    return {
        "option_contracts_in_window": len(grouped),
        "expired_contracts_with_sell_fill": expired_sold,
        "expired_contracts_still_net_short_in_window": expired_unclosed,
        "expired_contracts_closed_before_expiry": expired_close_before_expiry,
        "expired_contracts_with_zero_price_buy_on_or_after_expiry": expired_zero_close_after_expiry,
        "expired_contracts_with_other_buy_on_or_after_expiry": expired_other_close_after_expiry,
        "zero_price_option_buy_movements": zero_price_closes,
        "zero_price_option_buy_with_nearby_matching_stock_fill": possible_assignment_pairs,
        "zero_price_option_buy_without_matching_stock_fill": zero_price_without_pair,
        "interpretation": "aggregate clues only; owner confirmation is required before treating absence or pairing as definitive",
    }


def main() -> int:
    report = probe()
    print(json.dumps(report, indent=2, default=str))
    print("\nFindings + implications: docs/broker-history-capability.md")
    return 0 if report.get("opend_reachable") else 1


if __name__ == "__main__":
    raise SystemExit(main())
