#!/usr/bin/env python
"""READ-ONLY capability probe of broker history via the installed moomoo SDK.

Purpose
-------
Establish what historical broker data is actually available for the configured
account: trade/fill history depth, order records with fees, cash movements
(deposits/withdrawals), position snapshots, and any assignment evidence.

Hard safety contract (mirrors core/broker_protocol.py)
------------------------------------------------------
* NO order, unlock, cancel, or modify calls occur anywhere in this script.
* All broker access is funneled through ``ReadOnlyBrokerFacade`` which raises
  ``RuntimeError`` if any forbidden SDK member (place/modify/cancel/unlock) is
  touched.  The facade only exposes the read-side query members.
* This script never mutates account or portfolio state.

If OpenD is not reachable or the account has no history, that IS the finding:
we capture the raw SDK error and do not fabricate data.

Run:  .venv/Scripts/python tools/probe_broker_history.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import traceback

# ---------------------------------------------------------------------------
# Structural safety: mirror core/broker_protocol.py forbidden list
# ---------------------------------------------------------------------------
FORBIDDEN_SDK_MEMBERS = (
    "unlock_trade",
    "place_order",
    "modify_order",
    "cancel_order",
    "place_combo_order",
    "place_crypto_order",
    "cancel_crypto_order",
    "modify_crypto_order",
)

# Read-side query members we intentionally call. Anything else is off-limits.
ALLOWED_QUERY_MEMBERS = (
    "get_acc_list",
    "accinfo_query",
    "position_list_query",
    "order_list_query",
    "history_order_list_query",
    "deal_list_query",
    "history_deal_list_query",
    "order_fee_query",
    "get_acc_cash_flow",
    "get_account_list",
)

MIN_CALL_SPACING_SEC = 0.15  # keep well inside the app's 45 req/60s budget
PROBE_OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".tmp-outcome-loop",
    "probe-findings.md",
)


class ReadOnlyBrokerFacade:
    """Expose ONLY the read-side query members of a trade context.

    Any attempt to reach a forbidden member (or an unlisted member) raises
    RuntimeError, so a typo or future edit cannot silently reach execution.
    """

    def __init__(self, ctx):
        self._ctx = ctx
        self.used_members = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in FORBIDDEN_SDK_MEMBERS:
            raise RuntimeError(
                f"BLOCKED: {name} is a forbidden SDK member "
                "(mirrors core/broker_protocol.py). Probe is read-only."
            )
        if name == "close":
            self.used_members.append(name)
            return self._ctx.close
        if name not in ALLOWED_QUERY_MEMBERS:
            raise AttributeError(
                f"{name} is not in the allowlist of this read-only probe."
            )
        impl = getattr(self._ctx, name)

        def guarded(*args, **kwargs):
            self.used_members.append(name)
            _pace()
            return impl(*args, **kwargs)

        return guarded


def _pace():
    time.sleep(MIN_CALL_SPACING_SEC)


def _load_config():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import Config

    cfg = Config()
    return {
        "host": cfg.get("host", "127.0.0.1"),
        "port": int(cfg.get("port", 11111)),
        "portfolio_env": cfg.get("portfolio_env", "REAL"),
        "security_firm": cfg.get("security_firm", "FUTUSECURITIES"),
        "account_id": (cfg.get("account_id", "") or "").strip(),
    }


class Probe:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.finding: dict = {
            "headline": "",
            "opend_reachable": None,
            "sdK_error": None,
            "account": {},
            "available_accounts": [],
            "trade_history": {"ok": None, "rows": None, "columns": [], "note": ""},
            "order_history": {"ok": None, "rows": None, "columns": [], "note": ""},
            "today_fills": {"ok": None, "rows": None, "note": ""},
            "open_orders": {"ok": None, "rows": None, "note": ""},
            "fees": {"ok": None, "queried_order_ids": 0, "rows_with_fee": 0, "sample": [], "note": ""},
            "cash_flow": {"ok": None, "rows": None, "distinct_types": {}, "min_clearing_date": None, "max_clearing_date": None, "note": ""},
            "positions": {"ok": None, "rows": None, "columns": [], "option_positions": []},
            "accinfo": {"ok": None, "note": ""},
            "currency_evidence": {},
            "timestamp_evidence": {},
            "unavailable": [],
        }

    # -- connection ----------------------------------------------------------
    def connect(self):
        # Pre-flight TCP check (mirrors core/context_factory.probe_opend_status).
        # Avoids the SDK's long connection-retry loop when OpenD is down.
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.5)
        try:
            reachable = sock.connect_ex((self.cfg["host"], self.cfg["port"])) == 0
        except Exception as exc:  # noqa: BLE001
            reachable = False
            self.finding["sdK_error"] = f"TCP pre-flight raised: {exc}"
        finally:
            sock.close()

        if not reachable:
            self.finding["opend_reachable"] = False
            self.finding["sdK_error"] = (
                f"TCP pre-flight connect to {self.cfg['host']}:{self.cfg['port']} failed "
                "(no listener / refused). OpenD is not running on the configured port."
            )
            return False

        try:
            from moomoo import OpenQuoteContext, OpenSecTradeContext, TrdMarket, SecurityFirm, RET_OK  # noqa

            firm = getattr(SecurityFirm, str(self.cfg["security_firm"]).upper(), SecurityFirm.FUTUAU)
            self.quote_ctx = OpenQuoteContext(host=self.cfg["host"], port=self.cfg["port"])
            raw_ctx = OpenSecTradeContext(
                host=self.cfg["host"],
                port=self.cfg["port"],
                filter_trdmarket=TrdMarket.NONE,
                security_firm=firm,
            )
            self.trd = ReadOnlyBrokerFacade(raw_ctx)
            self.RET_OK = RET_OK
            self.finding["opend_reachable"] = True
            return True
        except Exception as exc:  # noqa: BLE001
            self.finding["opend_reachable"] = False
            self.finding["sdK_error"] = f"{type(exc).__name__}: {exc}"
            return False

    def _ok(self, ret):
        return ret == self.RET_OK

    # -- probes --------------------------------------------------------------
    def probe_accounts(self):
        try:
            ret, data = self.trd.get_acc_list()
            if not self._ok(ret) or getattr(data, "empty", True):
                self.finding["available_accounts"] = []
                self.finding["account"]["note"] = f"get_acc_list -> ret={ret} {data}"
                return
            accounts = []
            for r in data.to_dict("records"):
                accounts.append({k: str(r.get(k, "")).strip() for k in ("acc_id", "trd_env", "security_firm")})
            self.finding["available_accounts"] = accounts
            cfg_id = self.cfg["account_id"]
            match = next((a for a in accounts if a["acc_id"] == cfg_id), None) if cfg_id else None
            if cfg_id and not match:
                self.finding["account"]["note"] = (
                    f"configured account_id {cfg_id!r} NOT found in OpenD account list."
                )
                return
            acc = match or (accounts[0] if accounts else None)
            if acc:
                self.finding["account"] = {
                    "configured_id": cfg_id,
                    "resolved_id": acc["acc_id"],
                    "trd_env": acc["trd_env"],
                    "security_firm": acc["security_firm"],
                }
        except Exception as exc:  # noqa: BLE001
            self.finding["account"]["note"] = f"get_acc_list raised: {exc}"

    def _resolve_trd(self):
        acc = self.finding["account"]
        if not acc.get("resolved_id"):
            return None, None
        from moomoo import TrdEnv

        env_text = (acc.get("trd_env") or self.cfg["portfolio_env"] or "REAL").upper()
        trd_env = TrdEnv.REAL if "REAL" in env_text else TrdEnv.SIMULATE
        return trd_env, int(acc["resolved_id"])

    def probe_history_deals(self):
        trd_env, acc_id = self._resolve_trd()
        if acc_id is None:
            self.finding["trade_history"]["note"] = "no resolved account; skipped"
            return
        try:
            ret, data = self.trd.history_deal_list_query(trd_env=trd_env, acc_id=acc_id)
            if not self._ok(ret) or getattr(data, "empty", True):
                self.finding["trade_history"] = {"ok": False, "note": f"ret={ret} {data}", "rows": 0, "columns": []}
                return
            rows = data.to_dict("records")
            self.finding["trade_history"] = {
                "ok": True,
                "rows": len(rows),
                "columns": list(data.columns),
                "first": rows[0] if rows else None,
                "last": rows[-1] if rows else None,
                "note": "history_deal_list_query default 90-day window",
            }
        except Exception as exc:  # noqa: BLE001
            self.finding["trade_history"] = {"ok": False, "note": f"raised: {exc}", "rows": 0, "columns": []}

    def probe_history_orders(self):
        trd_env, acc_id = self._resolve_trd()
        if acc_id is None:
            self.finding["order_history"]["note"] = "no resolved account; skipped"
            return
        try:
            ret, data = self.trd.history_order_list_query(trd_env=trd_env, acc_id=acc_id)
            if not self._ok(ret) or getattr(data, "empty", True):
                self.finding["order_history"] = {"ok": False, "note": f"ret={ret} {data}", "rows": 0, "columns": []}
                return
            rows = data.to_dict("records")
            order_ids = []
            for r in rows:
                oid = str(r.get("order_id", "")).strip()
                if oid and oid not in order_ids:
                    order_ids.append(oid)
            self.finding["order_history"] = {
                "ok": True,
                "rows": len(rows),
                "distinct_order_ids": len(order_ids),
                "columns": list(data.columns),
                "first": rows[0] if rows else None,
                "last": rows[-1] if rows else None,
                "note": "history_order_list_query default 90-day window",
            }
            self._order_ids = order_ids
        except Exception as exc:  # noqa: BLE001
            self.finding["order_history"] = {"ok": False, "note": f"raised: {exc}", "rows": 0, "columns": []}
            self._order_ids = []

    def probe_today(self):
        trd_env, acc_id = self._resolve_trd()
        if acc_id is None:
            return
        try:
            ret, data = self.trd.deal_list_query(trd_env=trd_env, acc_id=acc_id)
            self.finding["today_fills"]["ok"] = self._ok(ret)
            self.finding["today_fills"]["rows"] = 0 if getattr(data, "empty", True) else len(data)
            if not self._ok(ret):
                self.finding["today_fills"]["note"] = f"ret={ret} {data}"
        except Exception as exc:  # noqa: BLE001
            self.finding["today_fills"]["ok"] = False
            self.finding["today_fills"]["note"] = f"raised: {exc}"
        try:
            ret, data = self.trd.order_list_query(trd_env=trd_env, acc_id=acc_id)
            self.finding["open_orders"]["ok"] = self._ok(ret)
            self.finding["open_orders"]["rows"] = 0 if getattr(data, "empty", True) else len(data)
            if not self._ok(ret):
                self.finding["open_orders"]["note"] = f"ret={ret} {data}"
        except Exception as exc:  # noqa: BLE001
            self.finding["open_orders"]["ok"] = False
            self.finding["open_orders"]["note"] = f"raised: {exc}"

    def probe_fees(self, max_ids=8):
        trd_env, acc_id = self._resolve_trd()
        order_ids = getattr(self, "_order_ids", [])
        if acc_id is None or not order_ids:
            self.finding["fees"]["note"] = "no order ids to query"
            return
        try:
            sampled = order_ids[:max_ids]
            ret, data = self.trd.order_fee_query(order_id_list=sampled, trd_env=trd_env, acc_id=acc_id)
            if not self._ok(ret) or getattr(data, "empty", True):
                self.finding["fees"] = {"ok": False, "note": f"ret={ret} {data}", "queried_order_ids": len(sampled), "rows_with_fee": 0, "sample": []}
                return
            rows = data.to_dict("records")
            with_fee = [r for r in rows if str(r.get("fee_amount", "0")).strip() not in ("", "0", "0.0", "0.00")]
            self.finding["fees"] = {
                "ok": True,
                "queried_order_ids": len(sampled),
                "rows_with_fee": len(with_fee),
                "sample": rows[:5],
                "note": "order_fee_query is ID-driven only; sampled first orders",
            }
        except Exception as exc:  # noqa: BLE001
            self.finding["fees"] = {"ok": False, "note": f"raised: {exc}", "queried_order_ids": 0, "rows_with_fee": 0, "sample": []}

    def probe_cash_flow(self, max_days=30):
        trd_env, acc_id = self._resolve_trd()
        if acc_id is None:
            self.finding["cash_flow"]["note"] = "no resolved account; skipped"
            return
        from moomoo import CashFlowDirection

        distinct_types = {}
        min_date, max_date = None, None
        tries = 0
        try:
            # First try no clearing_date (may fail for securities accounts).
            ret, data = self.trd.get_acc_cash_flow(trd_env=trd_env, acc_id=acc_id)
            if self._ok(ret) and not getattr(data, "empty", True):
                for r in data.to_dict("records"):
                    t = str(r.get("cashflow_type", "")).strip()
                    if t:
                        distinct_types[t] = distinct_types.get(t, 0) + 1
                    cd = str(r.get("clearing_date", "")).strip()
                    if cd:
                        min_date = min(min_date, cd) if min_date else cd
                        max_date = max(max_date, cd) if max_date else cd
            # Then walk back day-by-day to establish server-defined depth.
            today = dt.date.today()
            for back in range(0, max_days):
                day = (today - dt.timedelta(days=back)).isoformat()
                tries += 1
                ret, data = self.trd.get_acc_cash_flow(
                    clearing_date=day, trd_env=trd_env, acc_id=acc_id,
                    cashflow_direction=CashFlowDirection.NONE,
                )
                if not self._ok(ret) or getattr(data, "empty", True):
                    continue
                for r in data.to_dict("records"):
                    t = str(r.get("cashflow_type", "")).strip()
                    if t:
                        distinct_types[t] = distinct_types.get(t, 0) + 1
                    cd = str(r.get("clearing_date", "")).strip()
                    if cd:
                        min_date = min(min_date, cd) if min_date else cd
                        max_date = max(max_date, cd) if max_date else cd
                # Stop once we've found an early empty date beyond the first data day
                if min_date and day < min_date:
                    break
            total_rows = sum(distinct_types.values())
            self.finding["cash_flow"] = {
                "ok": True,
                "rows": total_rows,
                "distinct_types": distinct_types,
                "min_clearing_date": min_date,
                "max_clearing_date": max_date,
                "clearing_dates_probed": tries,
                "note": "get_acc_cash_flow is keyed by clearing_date for securities accounts; start/end are crypto-only",
            }
        except Exception as exc:  # noqa: BLE001
            self.finding["cash_flow"]["ok"] = False
            self.finding["cash_flow"]["note"] = f"raised after {tries} tries: {exc}"

    def probe_positions(self):
        trd_env, acc_id = self._resolve_trd()
        if acc_id is None:
            self.finding["positions"]["note"] = "no resolved account; skipped"
            return
        # _parse_option_code_metadata lives in core.connection_constants, not core.ticker_utils.
        from core.connection_constants import _parse_option_code_metadata

        try:
            ret, data = self.trd.position_list_query(trd_env=trd_env, acc_id=acc_id)
            if not self._ok(ret) or getattr(data, "empty", True):
                self.finding["positions"] = {"ok": False, "note": f"ret={ret} {data}", "rows": 0, "columns": []}
                return
            rows = data.to_dict("records")
            opts = []
            undecodable = []
            for r in rows:
                code = str(r.get("code", ""))
                meta = _parse_option_code_metadata(code) if code else None
                if meta:
                    opts.append({"code": code, "decoded": meta, "qty": r.get("qty"), "position_side": r.get("position_side")})
                else:
                    undecodable.append(code)
            self.finding["positions"] = {
                "ok": True,
                "rows": len(rows),
                "columns": list(data.columns),
                "option_positions": opts,
                "undecodable_codes": undecodable,
                "note": "Position proto has no strike/expiry columns; expiry/strike must be decoded from the option code string",
            }
        except Exception as exc:  # noqa: BLE001
            self.finding["positions"] = {"ok": False, "note": f"raised: {exc}", "rows": 0, "columns": []}

    def probe_accinfo(self):
        trd_env, acc_id = self._resolve_trd()
        if acc_id is None:
            self.finding["accinfo"]["note"] = "no resolved account; skipped"
            return
        try:
            ret, data = self.trd.accinfo_query(trd_env=trd_env, acc_id=acc_id)
            self.finding["accinfo"]["ok"] = self._ok(ret)
            if not self._ok(ret) or getattr(data, "empty", True):
                self.finding["accinfo"]["note"] = f"ret={ret} {data}"
        except Exception as exc:  # noqa: BLE001
            self.finding["accinfo"]["ok"] = False
            self.finding["accinfo"]["note"] = f"raised: {exc}"

    # -- driver --------------------------------------------------------------
    def run(self):
        ok = self.connect()
        if not ok:
            self.finding["headline"] = (
                f"OpenD not reachable at {self.cfg['host']}:{self.cfg['port']} — "
                "no broker data could be read. Captured as-is, nothing fabricated."
            )
            return
        try:
            self.probe_accounts()
            self.probe_history_deals()
            self.probe_history_orders()
            self.probe_today()
            self.probe_fees()
            self.probe_cash_flow()
            self.probe_positions()
            self.probe_accinfo()
        except Exception as exc:  # noqa: BLE001
            self.finding["surprise_error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        finally:
            try:
                self.trd.close()
                self.quote_ctx.close()
            except Exception as exc:  # noqa: BLE001
                self.finding.setdefault("close_errors", []).append(str(exc))

        self._compose_headline()

    def _compose_headline(self):
        f = self.finding
        parts = []
        th = f["trade_history"]
        if th.get("ok"):
            parts.append(f"fills={th.get('rows')}")
        else:
            parts.append(f"fills=UNREACHABLE({th.get('note','')})")
        oh = f["order_history"]
        if oh.get("ok"):
            parts.append(f"orders={oh.get('rows')}")
        cf = f["cash_flow"]
        if cf.get("ok") and cf.get("rows") is not None:
            parts.append(f"cashflow={cf.get('rows')} rows, depth->{cf.get('min_clearing_date')}")
        pos = f["positions"]
        if pos.get("ok"):
            parts.append(f"positions={pos.get('rows')}")
        f["headline"] = " | ".join(parts)


_MARKDOWN = """# Broker history probe — findings

> Generated by `tools/probe_broker_history.py` (READ-ONLY). No order/unlock/cancel/modify
> calls were ever issued; all access went through a read-only facade that mirrors
> `core/broker_protocol.py` forbidden-member list.

## Status

- OpenD reachable: **{opend_reachable}** at `{host}:{port}`
- SDK error (if unreachable): `{sdK_error}`

{headline_block}

## Account

```
{account_json}
```

Available accounts exposed by OpenD:

```
{accounts_json}
```

## Trade / fill history (90-day window)

```
{trade_history_json}
```

## Order history with fees (90-day window)

```
{order_history_json}
```

```
{fees_json}
```

## Cash movements (deposits / withdrawals)

`get_acc_cash_flow` is keyed by `clearing_date` for securities accounts
(`start`/`end` bounds are crypto-only). History depth is server-defined.

```
{cash_flow_json}
```

## Position snapshot (current)

```
{positions_json}
```

## Account info (accinfo_query)

```
{accinfo_json}
```

## Timestamps & currency

Observed timestamp / currency behavior is captured inline in the raw first/last
records above. Summary of the evidence:

```
{ts_currency_json}
```

## Explicitly UNAVAILABLE (so far)

- **Assignment/expiry event flag** — no SDK field labels a fill/order as
  assignment/exercise; it must be inferred across deal history + positions +
  cash-flow strings.
- **Fees on deal rows** — fills carry no commission; fees require
  `order_fee_query` keyed by `order_id` (ID-driven, no date-range bulk report).
- **Strike/expiry on positions** — the Position proto has no strike/expiry
  columns; must be decoded from the option code string.
- **Realized-PL per closed position** — no per-closed-position ledger; only
  current position `realized_pl` and account-level `realized_pl`.
- **`cashflow_type` enum** — raw server string passthrough; enumerated in
  `cash_flow.distinct_types` above only when data was reachable.
- **>90-day history in a single call** — `history_order_list_query` /
  `history_deal_list_query` each cap at a 90-day window; older data needs
  repeated windowed calls.

## Completeness gaps

- `{gap_summary}`

## Method guard audit

Forbidden members this script refuses to call: `{forbidden}`.
Members actually used by this run: `{used_members}`.
"""


def _fmt(obj):
    return json.dumps(obj, indent=2, default=str)


def render(finding: dict) -> str:
    def nb(rec):
        # headline block from raw finding
        return finding.get("headline", "no probe run")

    gap = []
    if finding.get("opend_reachable") is not True:
        gap.append("OpenD unreachable — no data read at all; findings are anticipated/unverified.")
    th = finding.get("trade_history", {})
    if th.get("note") and not th.get("ok"):
        gap.append(f"trade history: {th.get('note')}")
    cf = finding.get("cash_flow", {})
    if cf.get("ok"):
        if not cf.get("distinct_types"):
            gap.append("cash-flow probe returned no rows across probed clearing dates (all empty).")
        if cf.get("min_clearing_date") is None:
            gap.append("cash-flow depth could not be established (no data).")
    pos = finding.get("positions", {})
    if pos.get("note") and not pos.get("ok"):
        gap.append(f"positions: {pos.get('note')}")

    return _MARKDOWN.format(
        opend_reachable=finding.get("opend_reachable"),
        host=finding.get("_host", "?"),
        port=finding.get("_port", "?"),
        sdK_error=finding.get("sdK_error"),
        headline_block=nb(finding),
        account_json=_fmt(finding.get("account", {})),
        accounts_json=_fmt(finding.get("available_accounts", [])),
        trade_history_json=_fmt(th),
        order_history_json=_fmt(finding.get("order_history", {})),
        fees_json=_fmt(finding.get("fees", {})),
        cash_flow_json=_fmt(cf),
        positions_json=_fmt(pos),
        accinfo_json=_fmt(finding.get("accinfo", {})),
        ts_currency_json=_fmt(finding.get("currency_evidence", {})),
        gap_summary="; ".join(gap) if gap else "no additional gaps observed beyond the unavailable list above.",
        forbidden=", ".join(FORBIDDEN_SDK_MEMBERS),
        used_members=", ".join(finding.get("_used_members", [])),
    )


def main():
    cfg = _load_config()
    probe = Probe(cfg)
    probe.finding["_host"] = cfg["host"]
    probe.finding["_port"] = cfg["port"]
    probe.run()
    probe.finding["_used_members"] = sorted(
        set(getattr(getattr(probe, "trd", None), "used_members", []))
    )
    md = render(probe.finding)

    os.makedirs(os.path.dirname(PROBE_OUT), exist_ok=True)
    with open(PROBE_OUT, "w", encoding="utf-8") as fh:
        fh.write(md)

    print("=" * 72)
    print("PROBE COMPLETE")
    print("=" * 72)
    print(md)
    print("=" * 72)
    print(f"Written to: {PROBE_OUT}")


if __name__ == "__main__":
    main()
