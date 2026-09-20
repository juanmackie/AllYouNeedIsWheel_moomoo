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
* how many positions and cash-flow rows are exposed.

Durable findings and their implications: ``docs/broker-history-capability.md``.

Run:  .venv/Scripts/python tools/probe_broker_history.py
Query-only: no order, unlock, cancel, or modify member is reachable.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402
from core.connection_manager import MoomooConnection  # noqa: E402

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
        return report
    report["env"] = env
    report["account_resolved"] = bool(opaque_account)

    start = (datetime.now() - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
    report["history_window_days_requested"] = days
    deals = conn.get_history_deals(start=start, end="")
    report["deals"] = None if deals is None else len(deals)
    if not deals:
        report["deals_note"] = "no fills returned — that IS the finding, never a fabricated row"
        return report

    report["deal_columns"] = sorted(str(key) for key in deals[0].keys())
    stamps = sorted(str(row.get("create_time", "")) for row in deals if row.get("create_time"))
    if stamps:
        report["deal_window"] = {"oldest": stamps[0][:19], "newest": stamps[-1][:19]}

    order_ids = sorted({str(row.get("order_id", "")) for row in deals if row.get("order_id")})
    sampled = order_ids[:FEE_SAMPLE_LIMIT]
    report["orders_in_window"] = len(order_ids)
    report["orders_sampled_for_fees"] = len(sampled)
    fees = conn.get_order_fees(sampled) if sampled else None
    report["orders_with_fees"] = None if fees is None else sum(1 for row in fees if row.get("fee_amount") is not None)

    portfolio = conn.get_portfolio()
    report["positions"] = len(portfolio.get("positions") or {}) if isinstance(portfolio, dict) else None

    flows = conn.get_cash_flow(datetime.now().strftime("%Y-%m-%d"))
    report["cash_flow_rows_today"] = None if flows is None else len(flows)
    return report


def main() -> int:
    report = probe()
    print(json.dumps(report, indent=2, default=str))
    print("\nFindings + implications: docs/broker-history-capability.md")
    return 0 if report.get("opend_reachable") else 1


if __name__ == "__main__":
    raise SystemExit(main())
