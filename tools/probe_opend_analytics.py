#!/usr/bin/env python
"""Probe read-only OpenD analytics endpoints and report redacted results.

This is a one-off diagnostic tool, not application runtime code. It checks the
candidate quote endpoints from the 2026-10 roadmap and two trade-context query
endpoints. It never emits account identifiers, broker rows, raw SDK error
messages, or account values. Trade-context calls require an explicitly
configured account ID; this tool does not select an account implicitly.

Run: .venv/Scripts/python tools/probe_opend_analytics.py
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
from datetime import date, timedelta
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402


class ReadOnlyTradeProbe:
    """A deliberately small view over the SDK trade context."""

    def __init__(self, context: Any):
        self._context = context

    def get_acc_list(self, *args, **kwargs):
        return self._context.get_acc_list(*args, **kwargs)

    def order_list_query(self, *args, **kwargs):
        return self._context.order_list_query(*args, **kwargs)

    def acctradinginfo_query(self, *args, **kwargs):
        return self._context.acctradinginfo_query(*args, **kwargs)

    def position_list_query(self, *args, **kwargs):
        return self._context.position_list_query(*args, **kwargs)

    def close(self):
        return self._context.close()


def _tcp_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def _row_count(data: Any) -> int | None:
    """Return only an aggregate count; never serialize broker rows."""
    if data is None:
        return None
    if isinstance(data, tuple):
        # Dividend calendar returns (total_count, DataFrame).
        if len(data) == 2 and isinstance(data[0], (int, float)):
            return int(data[0])
        data = data[-1]
    try:
        return int(len(data))
    except (TypeError, ValueError):
        return None


def _result_status(result: Any, ret_ok: Any) -> dict[str, Any]:
    """Summarize SDK result without returning its payload or error text."""
    if not isinstance(result, tuple) or not result:
        return {"status": "unexpected_shape", "rows": None}
    ret = result[0]
    if ret == ret_ok:
        payload = result[1] if len(result) > 1 else None
        return {"status": "ok", "rows": _row_count(payload)}
    error_text = str(result[1]).lower() if len(result) > 1 else ""
    if any(marker in error_text for marker in ("permission", "entitlement", "subscribe", "not authorized")):
        category = "permission_or_subscription"
    elif any(
        marker in error_text
        for marker in (
            "not support",
            "unsupported",
            "invalid param",
            "invalid parameter",
            "unknown protocol id",
        )
    ):
        category = "unsupported_or_invalid_request"
    else:
        category = "provider_error_unclassified"
    return {"status": category, "rows": None}


def _call_quote(name: str, fn, ret_ok: Any, *args, **kwargs) -> dict[str, Any]:
    try:
        return _result_status(fn(*args, **kwargs), ret_ok)
    except Exception as exc:  # never emit exception text; it can contain private data
        return {"status": "exception", "error_type": type(exc).__name__, "rows": None}


def _position_capability(fn, ret_ok: Any, **kwargs) -> dict[str, Any]:
    """Report only whether diluted cost is exposed, never position values."""
    try:
        result = fn(**kwargs)
    except Exception as exc:
        return {"status": "exception", "error_type": type(exc).__name__}
    status = _result_status(result, ret_ok)
    if status["status"] != "ok":
        return status
    payload = result[1] if len(result) > 1 else None
    columns = set(getattr(payload, "columns", []))
    has_diluted_cost = "diluted_cost" in columns
    non_null_rows = int(payload["diluted_cost"].notna().sum()) if has_diluted_cost else 0
    return {
        "status": "ok",
        "position_rows": _row_count(payload),
        "diluted_cost_column_present": has_diluted_cost,
        "diluted_cost_non_null_rows": non_null_rows,
    }


def _account_match_capability(fn, ret_ok: Any, target_account_id: Any) -> dict[str, Any]:
    """Check only whether the supplied ID appears in the account list."""
    try:
        result = fn()
    except Exception as exc:
        return {"status": "exception", "error_type": type(exc).__name__}
    status = _result_status(result, ret_ok)
    if status["status"] != "ok":
        return status
    payload = result[1] if len(result) > 1 else None
    if not hasattr(payload, "columns") or "acc_id" not in payload.columns:
        return {"status": "unexpected_shape", "supplied_id_listed": None}

    def normalize_id(value: Any) -> str:
        try:
            return str(int(value))
        except (TypeError, ValueError, OverflowError):
            return str(value).strip()

    target = normalize_id(target_account_id)
    mask = payload["acc_id"].map(normalize_id) == target
    matches = payload.loc[mask]
    environments = set()
    if "trd_env" in matches.columns:
        for value in matches["trd_env"].dropna().tolist():
            normalized = str(value).upper()
            if "SIMULATE" in normalized:
                environments.add("SIMULATE")
            elif "REAL" in normalized:
                environments.add("REAL")
            else:
                environments.add("other")
    return {
        "status": "ok",
        "supplied_id_listed": not matches.empty,
        "matching_environments": sorted(environments),
    }


def _call_quota(fn, ret_ok: Any) -> dict[str, Any]:
    """Expose aggregate quota counts only; never emit per-code quota details."""
    try:
        result = fn()
    except Exception as exc:
        return {"status": "exception", "error_type": type(exc).__name__}
    status = _result_status(result, ret_ok)
    if status["status"] != "ok" or len(result) < 2:
        return status
    payload = result[1]
    if not isinstance(payload, tuple) or len(payload) < 3:
        return {"status": "unexpected_shape", "rows": None}
    try:
        return {
            "status": "ok",
            "used_quota": int(payload[0]),
            "remaining_quota": int(payload[1]),
            "detail_rows": int(len(payload[2])),
        }
    except (TypeError, ValueError):
        return {"status": "unexpected_shape", "rows": None}


def probe() -> dict[str, Any]:
    # The SDK logs connection handles. Suppress provider logging so the probe
    # output contains only the explicitly redacted JSON summary.
    logging.disable(logging.CRITICAL)
    cfg = Config()
    host = str(cfg.get("host", "127.0.0.1"))
    port = int(cfg.get("port", 11111) or 11111)
    report: dict[str, Any] = {
        "sdk_version": "10.9.6908",
        "opend_reachable": _tcp_reachable(host, port),
        "quote_endpoints": {},
        "trade_endpoints": {},
    }
    if not report["opend_reachable"]:
        report["note"] = "OpenD TCP endpoint unavailable; live endpoint and entitlement checks were not run."
        report["trade_endpoints"] = {
            "order_list_query": {"status": "not_run_opend_unavailable"},
            "acctradinginfo_query": {"status": "not_run_opend_unavailable"},
        }
        return report

    from moomoo import (  # noqa: PLC0415
        KL_FIELD,
        RET_OK,
        KLType,
        OpenQuoteContext,
        OpenSecTradeContext,
        OrderType,
        SecurityFirm,
        TrdEnv,
        TrdMarket,
    )

    quote_ctx = None
    trade_probe = None
    today = date.today()
    start = (today - timedelta(days=7)).isoformat()
    end = today.isoformat()
    history_start = (today - timedelta(days=4)).isoformat()
    try:
        quote_ctx = OpenQuoteContext(host=host, port=port)
        symbols = ["US.AAPL"]
        calls = {
            "get_option_underlying_overview": (quote_ctx.get_option_underlying_overview, (symbols,), {}),
            "get_option_underlying_his_volatility": (
                quote_ctx.get_option_underlying_his_volatility,
                (symbols[0],),
                {"begin_time": start, "end_time": end},
            ),
            "get_earnings_calendar": (
                quote_ctx.get_earnings_calendar,
                (),
                {"market": "US", "begin_date": start, "end_date": end},
            ),
            "get_dividend_calendar": (
                quote_ctx.get_dividend_calendar,
                (),
                {"market": "US", "date": end, "data_from": 0, "count": 5},
            ),
            "get_rehab": (quote_ctx.get_rehab, (symbols[0],), {}),
            "request_trading_days": (
                quote_ctx.request_trading_days,
                (),
                {"market": "US", "start": start, "end": end},
            ),
            "get_market_state": (quote_ctx.get_market_state, (symbols,), {}),
            "request_history_kline": (
                quote_ctx.request_history_kline,
                (),
                {
                    "code": symbols[0],
                    "start": history_start,
                    "end": end,
                    "ktype": KLType.K_DAY,
                    "fields": [KL_FIELD.CLOSE],
                    "max_count": 5,
                },
            ),
            "get_history_kl_quota": (quote_ctx.get_history_kl_quota, (), {}),
            "query_subscription": (quote_ctx.query_subscription, (), {"is_all_conn": True}),
        }
        for name, (method, args, kwargs) in calls.items():
            if name == "get_history_kl_quota":
                report["quote_endpoints"][name] = _call_quota(method, RET_OK)
            else:
                report["quote_endpoints"][name] = _call_quote(name, method, RET_OK, *args, **kwargs)

        account_id = cfg.get("account_id")
        portfolio_env = str(cfg.get("portfolio_env", "SIMULATE")).strip().upper()
        if not account_id:
            report["trade_endpoints"] = {
                "order_list_query": {"status": "skipped_no_explicit_account_id"},
                "acctradinginfo_query": {"status": "skipped_no_explicit_account_id"},
                "position_list_query": {"status": "skipped_no_explicit_account_id"},
            }
        else:
            env_enum = getattr(TrdEnv, portfolio_env, None)
            firm_name = str(cfg.get("security_firm", "FUTUSECURITIES")).strip().upper()
            firm_enum = getattr(SecurityFirm, firm_name, SecurityFirm.FUTUSECURITIES)
            if env_enum is None:
                report["trade_endpoints"] = {
                    "order_list_query": {"status": "skipped_unknown_configured_environment"},
                    "acctradinginfo_query": {"status": "skipped_unknown_configured_environment"},
                    "position_list_query": {"status": "skipped_unknown_configured_environment"},
                }
            else:
                raw_trade_ctx = OpenSecTradeContext(
                    host=host,
                    port=port,
                    filter_trdmarket=TrdMarket.NONE,
                    security_firm=firm_enum,
                )
                trade_probe = ReadOnlyTradeProbe(raw_trade_ctx)
                report["trade_endpoints"]["account_match"] = _account_match_capability(
                    trade_probe.get_acc_list,
                    RET_OK,
                    account_id,
                )
                try:
                    explicit_acc_id = int(str(account_id).strip())
                except (TypeError, ValueError):
                    explicit_acc_id = account_id
                trade_args = {"trd_env": env_enum, "acc_id": explicit_acc_id}
                report["trade_endpoints"]["order_list_query"] = _call_quote(
                    "order_list_query",
                    trade_probe.order_list_query,
                    RET_OK,
                    code="US.AAPL",
                    start=end,
                    end=end,
                    **trade_args,
                )
                report["trade_endpoints"]["acctradinginfo_query"] = _call_quote(
                    "acctradinginfo_query",
                    trade_probe.acctradinginfo_query,
                    RET_OK,
                    order_type=OrderType.NORMAL,
                    code="US.AAPL",
                    price=1.0,
                    **trade_args,
                )
                report["trade_endpoints"]["position_list_query"] = _position_capability(
                    trade_probe.position_list_query,
                    RET_OK,
                    **trade_args,
                )
    except Exception as exc:
        report["probe_error_type"] = type(exc).__name__
    finally:
        if trade_probe is not None:
            try:
                trade_probe.close()
            except Exception:
                pass
        if quote_ctx is not None:
            try:
                quote_ctx.close()
            except Exception:
                pass
    report["entitlement_interpretation"] = (
        "status=ok means OpenD returned RET_OK. Other statuses are redacted classifications of the returned "
        "error text; unclassified non-OK results do not distinguish entitlement, unsupported requests, or "
        "provider failures. Raw provider error text is not retained."
    )
    return report


def main() -> int:
    report = probe()
    print(json.dumps(report, indent=2, sort_keys=True))
    print("\nDetailed signatures and interpretation: docs/opend-analytics-capability.md")
    return 0 if report.get("opend_reachable") else 1


if __name__ == "__main__":
    raise SystemExit(main())
