#!/usr/bin/env python
"""Step-0 query-only gate for separating contract discovery from option quotes.

Run with OpenD logged in: .venv/Scripts/python tools/probe_option_chain_cost.py --group All
Reports aggregates only; does not read account balances or write to the database.
Exit 0 = measured go; 1 = measured no-go; 2 = blocked/unmeasured.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402

SNAPSHOT_FIELDS = {
    "bid": "bid_price",
    "ask": "ask_price",
    "delta": "option_delta",
    "iv": "option_implied_volatility",
    "update_time": "update_time",
}


def _populated(value, field):
    if field == "update_time":
        return isinstance(value, str) and bool(value.strip())
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(parsed) and (parsed != 0 if field == "delta" else parsed > 0)


def measure(conn, symbols, start, end):
    """Measure raw ranged discovery and exactly one 400-code snapshot request.

    Uses the existing connection's query context, chain gate, and adaptive limiter;
    never constructs a separate SDK context or bypasses the app's quota settings.
    """
    from core.connection_manager import RET_OK, _is_rate_limit_response

    report = {"decision": "no-go", "window_start": start, "window_end": end, "discovery": []}
    codes = []
    discovery_ok = True
    for symbol in symbols:
        code = conn._format_symbol(symbol)
        entry = {"symbol": code}
        conn._acquire_option_chain_gate(f"probe_contracts:{code}:{start}:{end}")
        try:
            conn._option_chain_rate_limiter.check_rate_limit()
            started = time.perf_counter()
            ret, data = conn.quote_ctx.get_option_chain(code=code, start=start, end=end, option_type="ALL")
            entry["elapsed_sec"] = round(time.perf_counter() - started, 3)
            if ret != RET_OK:
                if _is_rate_limit_response(data):
                    conn._option_chain_rate_limiter.record_rate_limit("contract discovery probe rate limited")
                entry["success"] = False
                discovery_ok = False
            else:
                records = data.to_dict("records")
                expiries = sorted({str(row.get("strike_time", "")) for row in records})
                rights = sorted({str(row.get("option_type", "")) for row in records})
                entry.update(success=True, rows=len(records), expiries=expiries, option_types=rights)
                discovery_ok = discovery_ok and len(expiries) >= 2 and {"CALL", "PUT"}.issubset(rights)
                codes.extend(row["code"] for row in records if row.get("code"))
        except Exception as exc:
            if _is_rate_limit_response(exc):
                conn._option_chain_rate_limiter.record_rate_limit("contract discovery probe rate limited")
            entry.update(success=False, error_type=type(exc).__name__)
            discovery_ok = False
        finally:
            conn._option_chain_gate.release()
        report["discovery"].append(entry)

    codes = list(dict.fromkeys(codes))[:400]
    if len(codes) < 400:
        report.update(
            decision="blocked" if discovery_ok else "no-go",
            reason="Fewer than 400 distinct contracts; snapshot capacity unmeasured.",
        )
        return report

    started = time.perf_counter()
    ret, data = conn.get_market_snapshot(codes)
    snapshot = {"requested": 400, "elapsed_sec": round(time.perf_counter() - started, 3), "success": ret == RET_OK}
    report["snapshot"] = snapshot
    if ret != RET_OK or data is None:
        report["reason"] = "The 400-code option snapshot failed."
        return report
    rows = data.to_dict("records")
    snapshot["returned"] = len({row.get("code") for row in rows} & set(codes))
    snapshot["populated"] = {
        field: sum(_populated(row.get(column), field) for row in rows) for field, column in SNAPSHOT_FIELDS.items()
    }
    snapshot["usable_rows"] = sum(
        all(_populated(row.get(column), field) for field, column in SNAPSHOT_FIELDS.items())
        and float(row["bid_price"]) <= float(row["ask_price"])
        for row in rows
    )
    stamps = sorted({row["update_time"] for row in rows if _populated(row.get("update_time"), "update_time")})
    snapshot["broker_time_range"] = [stamps[0], stamps[-1]] if stamps else []
    if discovery_ok and snapshot["returned"] == 400 and snapshot["usable_rows"] > 0:
        report["decision"] = "go"
    else:
        report["reason"] = "Ranged discovery or required option quote fields did not meet the gate."
    report["chain_limiter"] = conn._option_chain_rate_limiter.get_stats()
    return report


def probe(group="All", sample_size=3):
    """Load the app environment, fail fast without OpenD, then probe group members."""
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
    cfg = Config()
    try:
        with socket.create_connection((cfg.get("host", "127.0.0.1"), int(cfg.get("port", 11111))), timeout=3):
            pass
    except OSError as exc:
        return {"decision": "blocked", "opend_reachable": False, "error_type": type(exc).__name__}

    from core.connection_manager import RET_OK, MoomooConnection
    from core.ticker_utils import canonical_underlying, earnings_underlying_ticker
    from core.utils import is_market_open

    conn = MoomooConnection(
        host=cfg.get("host", "127.0.0.1"),
        port=int(cfg.get("port", 11111)),
        readonly=True,
        account_id=cfg.get("account_id"),
        portfolio_env=cfg.get("portfolio_env"),
        security_firm=cfg.get("security_firm"),
        chain_rate_limit_max_requests=cfg.get("chain_rate_limit_max_requests", 10),
        chain_rate_limit_window_sec=cfg.get("chain_rate_limit_window_sec", 30),
        chain_min_request_spacing_sec=cfg.get("chain_min_request_spacing_sec", 3.0),
    )
    try:
        if not conn.connect():
            return {"decision": "blocked", "opend_reachable": True, "reason": "SDK connection failed."}
        ret, data = conn.get_user_security(group)
        if ret != RET_OK or data is None:
            return {"decision": "blocked", "reason": "Could not read the requested Moomoo group."}
        symbols = []
        for raw in data["code"].tolist():
            code = str(raw).strip().upper()
            if "." in code and not code.startswith("US."):
                continue
            ticker = canonical_underlying(code)
            if ticker and earnings_underlying_ticker(code) == ticker and ticker not in symbols:
                symbols.append(ticker)
        if len(symbols) < 2:
            return {"decision": "blocked", "reason": "Group contains fewer than two US underlying symbols."}
        today = datetime.now(ZoneInfo("America/New_York")).date()
        report = measure(conn, symbols[:sample_size], str(today + timedelta(days=7)), str(today + timedelta(days=35)))
        report.update(
            group=group, market_open=is_market_open(), measured_at_utc=datetime.now(ZoneInfo("UTC")).isoformat()
        )
        if report["decision"] == "go" and report["market_open"]:
            report.update(
                decision="blocked", reason="Open-market measurements passed; closed-market fields remain unmeasured."
            )
        return report
    finally:
        conn.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", default="All")
    parser.add_argument("--sample-size", type=int, choices=(2, 3), default=3)
    args = parser.parse_args()
    report = probe(args.group, args.sample_size)
    print(json.dumps(report, indent=2))
    return {"go": 0, "no-go": 1, "blocked": 2}[report["decision"]]


if __name__ == "__main__":
    raise SystemExit(main())
