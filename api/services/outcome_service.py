"""Broker-verified outcome summary service (read-only serving).

Turns ingested broker fills (``option_fills``, schema v10) plus the immutable
published run snapshots (recommendation signals) into owner-facing outcome
aggregates:

- quoted (recommended) credit vs. filled credit, and the slippage between them;
- net results after fees (unknown fees ⇒ unknown outcome, never zero);
- capital-days and net P&L / capital-day (owner efficiency) via the pure
  ``core.outcome_attribution`` helpers;
- groups by preset, DTE bucket, ticker, and event tier (display-only tiers —
  they never gate or reorder anything).

Every aggregate exposes sample size, coverage %, and unknown-outcome count,
and every outcome record carries the supporting fill transactions for
drill-down. Attribution here covers the option leg (fills-only); share-leg
attribution (assignment cost basis) lands with the position-diff
reconciliation stage and is never fabricated in the meantime.

All matching/aggregation helpers at module level are pure and deterministic —
unit-testable without Flask, the database, or a broker connection.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta

from core.outcome_attribution import (
    CONTRACT_MULTIPLIER,
    capital_base_cc,
    capital_base_csp,
    capital_days_from_lots,
    combined_outcome,
    owner_efficiency,
    owner_summary,
    slippage_dollars,
)

logger = logging.getLogger("api.services.outcomes")

# Fixed DTE bucket edges (inclusive upper bound, label).
_DTE_BUCKET_EDGES = (
    (7, "0-7"),
    (14, "8-14"),
    (30, "15-30"),
    (45, "31-45"),
    (60, "46-60"),
    (90, "61-90"),
)

_UNTIERED = "untiered"


def dte_bucket(dte) -> str:
    """Fixed DTE bucket label; unknown DTE never falls into a real bucket."""
    if dte is None:
        return "unknown"
    try:
        value = int(dte)
    except (TypeError, ValueError):
        return "unknown"
    for edge, label in _DTE_BUCKET_EDGES:
        if value <= edge:
            return label
    return "90+"


def normalize_expiration_key(value) -> str:
    """Canonical yyyymmdd expiration for join keys (mirrors options_data)."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10].replace("-", "")
    return text.replace("-", "")


def _safe_float(value, default=None):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:  # NaN
        return default
    return parsed


def signal_identity(signal) -> tuple | None:
    """Canonical contract identity for a recommendation signal, or None.

    Identity is (ticker, expiration, option_type, strike) with numeric strike
    comparison so string-formatting drift (``70`` vs ``70.0``) can never
    split one contract into two outcomes.
    """
    if not isinstance(signal, dict):
        return None
    ticker = str(signal.get("ticker", "") or "").strip().upper()
    option_type = str(signal.get("option_type", "") or "").strip().upper()
    expiration = normalize_expiration_key(signal.get("expiration"))
    strike = _safe_float(signal.get("strike"))
    if not ticker or option_type not in ("CALL", "PUT") or len(expiration) != 8 or strike is None:
        return None
    return (ticker, expiration, option_type, round(strike, 4))


def fill_identity(fill) -> tuple | None:
    """Canonical contract identity for an option fill row, or None."""
    if not isinstance(fill, dict):
        return None
    if str(fill.get("security_type", "") or "").upper() != "OPT":
        return None
    ticker = str(fill.get("ticker", "") or "").strip().upper()
    option_type = str(fill.get("option_type", "") or "").strip().upper()
    expiration = normalize_expiration_key(fill.get("expiration"))
    strike = _safe_float(fill.get("strike"))
    if not ticker or option_type not in ("CALL", "PUT") or len(expiration) != 8 or strike is None:
        return None
    return (ticker, expiration, option_type, round(strike, 4))


def quoted_credit_per_contract(signal):
    """The recommended executable credit: bid → limit target → premium."""
    for key in ("bid_premium_per_contract", "limit_target_per_contract", "premium_per_contract"):
        value = _safe_float((signal or {}).get(key))
        if value is not None and value > 0:
            return value
    return None


def signal_type_of(signal) -> str:
    """Display strategy label from the signal (csp / covered_call / other)."""
    if not isinstance(signal, dict):
        return ""
    label = str(signal.get("signal_type", "") or "").strip().lower()
    if label:
        return label
    option_type = str(signal.get("option_type", "") or "").strip().upper()
    return {"CALL": "covered_call", "PUT": "csp"}.get(option_type, option_type.lower())


def _capital_per_contract(signal, option_type: str, strike: float):
    """Per-contract deployed capital, or None when it cannot be known.

    CSP: strike × 100 (cash secured). Covered call: share price × 100 —
    requires the signal's stock_price; without it capital-days stay unknown.
    """
    if option_type == "PUT":
        return capital_base_csp(strike, 1)
    stock_price = _safe_float((signal or {}).get("stock_price"))
    if stock_price is None or stock_price <= 0:
        return None
    return capital_base_cc(stock_price, 1)


def _fifo_lots(entry_fills, close_fills):
    """Match closing BUY fills against entry SELL fills FIFO.

    Returns (lots, open_qty, closed_qty) where lots are
    (entry_ts, exit_ts | None, qty): one lot per exited tranche plus one open
    lot for any remainder — per-tranche capital-days, never a single average.
    Buy quantity beyond what was sold (exercise/assignment movement) is not
    fabricated into a close.
    """
    entries = deque(
        (str(fill.get("captured_at", "") or ""), float(fill.get("qty", 0) or 0))
        for fill in sorted(entry_fills, key=lambda f: (str(f.get("captured_at", "") or ""), str(f.get("fill_id", "") or "")))
        if float(fill.get("qty", 0) or 0) > 0
    )
    lots = []
    closed_qty = 0.0
    for close in sorted(close_fills, key=lambda f: (str(f.get("captured_at", "") or ""), str(f.get("fill_id", "") or ""))):
        remaining = float(close.get("qty", 0) or 0)
        exit_ts = str(close.get("captured_at", "") or "")
        if not exit_ts:
            continue
        while remaining > 0 and entries:
            entry_ts, entry_qty = entries[0]
            matched = min(remaining, entry_qty)
            lots.append((entry_ts, exit_ts, matched))
            closed_qty += matched
            remaining -= matched
            if matched >= entry_qty:
                entries.popleft()
            else:
                entries[0] = (entry_ts, entry_qty - matched)
    open_qty = sum(qty for _, qty in entries)
    lots.extend((entry_ts, None, qty) for entry_ts, qty in entries)
    return lots, open_qty, closed_qty


def _fill_transaction(fill) -> dict:
    """Drill-down view of one supporting fill."""
    return {
        "fill_id": fill.get("fill_id", ""),
        "order_id": fill.get("order_id", ""),
        "captured_at": fill.get("captured_at", ""),
        "side": fill.get("side", ""),
        "qty": fill.get("qty", 0),
        "price": fill.get("price", 0),
        "fees": fill.get("fees"),
        "security_type": fill.get("security_type", ""),
    }


def build_outcome_records(run_snapshots, option_fills, now=None) -> list[dict]:
    """Join recommendation signals to option fills and attribute outcomes.

    ``run_snapshots``: published snapshot dicts (any order; deduped to the
    earliest recommendation per contract identity). ``option_fills``: fill
    rows for the same identity scope. ``now``: as-of for open-lot capital
    days. Returns one record per unique signal identity.
    """
    now = now or datetime.now()

    # Earliest recommendation per identity, across signals + pick lists.
    signals_by_identity: dict[tuple, dict] = {}
    snapshots = sorted(
        [s for s in run_snapshots if isinstance(s, dict)],
        key=lambda s: str((s.get("run") or {}).get("generated_at", "") or ""),
    )
    for snapshot in snapshots:
        run = snapshot.get("run") or {}
        preset_key = str(run.get("preset_key", "") or "")
        generated_at = str(run.get("generated_at", "") or "")
        run_id = str(run.get("run_id", "") or "")
        candidates = []
        for key in ("signals", "csp_picks", "cc_decisions"):
            value = snapshot.get(key)
            if isinstance(value, list):
                candidates.extend(item for item in value if isinstance(item, dict))
        for signal in candidates:
            identity = signal_identity(signal)
            if identity is None or identity in signals_by_identity:
                continue
            signals_by_identity[identity] = {
                "signal": signal,
                "preset_key": preset_key,
                "generated_at": generated_at,
                "run_id": run_id,
            }

    # Option fills grouped by contract identity.
    fills_by_identity: dict[tuple, list] = {}
    for fill in option_fills or []:
        identity = fill_identity(fill)
        if identity is None:
            continue
        fills_by_identity.setdefault(identity, []).append(fill)

    records = []
    for identity, meta in signals_by_identity.items():
        signal = meta["signal"]
        ticker, expiration, option_type, strike = identity
        fills = fills_by_identity.pop(identity, [])
        record = _build_record(signal, meta, ticker, expiration, option_type, strike, fills, now)
        records.append(record)

    # Fills with no matching stored signal are still real transactions; they
    # are surfaced as unmatched evidence, never silently dropped and never
    # given a fabricated quoted price.
    for identity, fills in fills_by_identity.items():
        ticker, expiration, option_type, strike = identity
        record = _build_record(
            None,
            {"signal": None, "preset_key": "", "generated_at": "", "run_id": ""},
            ticker,
            expiration,
            option_type,
            strike,
            fills,
            now,
        )
        record["signal_type"] = "unmatched"
        records.append(record)

    records.sort(key=lambda r: (str(r.get("first_recommended_at", "") or ""), r["identity"]))
    return records


def _build_record(signal, meta, ticker, expiration, option_type, strike, fills, now) -> dict:
    quoted = quoted_credit_per_contract(signal) if signal is not None else None
    dte = signal.get("dte") if signal is not None else None

    entry_fills = [f for f in fills if str(f.get("side", "")).upper() == "SELL"]
    close_fills = [f for f in fills if str(f.get("side", "")).upper() == "BUY"]

    contracts_sold = sum(float(f.get("qty", 0) or 0) for f in entry_fills)
    contracts_bought = sum(float(f.get("qty", 0) or 0) for f in close_fills)

    filled_credit = None
    if contracts_sold > 0:
        sell_value = sum(float(f.get("price", 0) or 0) * float(f.get("qty", 0) or 0) for f in entry_fills)
        filled_credit = sell_value / contracts_sold

    fees_known = bool(fills) and all(f.get("fees") is not None for f in fills)
    fees_total = sum(float(f.get("fees", 0) or 0) for f in fills) if fees_known else None

    sell_value_total = sum(float(f.get("price", 0) or 0) * float(f.get("qty", 0) or 0) for f in entry_fills)
    buy_value_total = sum(float(f.get("price", 0) or 0) * float(f.get("qty", 0) or 0) for f in close_fills)
    gross_option_pnl = (sell_value_total - buy_value_total) * CONTRACT_MULTIPLIER if fills else None

    slippage_per_contract = None
    slippage_dollars_total = None
    if filled_credit is not None and quoted is not None:
        slippage_per_contract = filled_credit - quoted
        slippage_dollars_total = slippage_dollars(filled_credit, quoted, contracts_sold, "SELL")

    capital_per = _capital_per_contract(signal, option_type, strike) if signal is not None else None
    capital_days = None
    open_contracts = 0.0
    if fills:
        lots, open_contracts, _closed = _fifo_lots(entry_fills, close_fills)
        if capital_per is not None:
            capital_days = capital_days_from_lots(lots, capital_per, as_of=now)

    if not fills:
        status = "pending"
        net_pnl = None
    elif not fees_known:
        status = "unknown"  # fees unresolved: unknown ≠ zero, never fabricate
        net_pnl = None
    else:
        outcome = combined_outcome(
            {"premium_in": sell_value_total * CONTRACT_MULTIPLIER, "premium_out": buy_value_total * CONTRACT_MULTIPLIER, "fees": fees_total or 0.0},
            None,
        )
        status = outcome["status"]
        net_pnl = outcome["net_pnl"]

    return {
        "identity": "|".join([ticker, expiration, option_type, f"{strike:g}"]),
        "ticker": ticker,
        "expiration": expiration,
        "option_type": option_type,
        "strike": strike,
        "signal_type": signal_type_of(signal) if signal is not None else "unmatched",
        "preset_key": meta.get("preset_key", ""),
        "event_tier": str((signal or {}).get("event_tier", "") or "").strip() or _UNTIERED,
        "quality_tier": str((signal or {}).get("quality_tier", "") or "").strip() or _UNTIERED,
        "dte": dte,
        "dte_bucket": dte_bucket(dte),
        "first_recommended_at": meta.get("generated_at", ""),
        "run_id": meta.get("run_id", ""),
        "quoted_credit_per_contract": quoted,
        "filled_credit_per_contract": filled_credit,
        "contracts_sold": contracts_sold,
        "contracts_bought_back": contracts_bought,
        "open_contracts": open_contracts,
        "slippage_per_contract": slippage_per_contract,
        "slippage_dollars": slippage_dollars_total,
        "fees_known": fees_known,
        "fees_total": fees_total,
        "gross_premium_pnl": gross_option_pnl,
        "net_pnl": net_pnl,
        "outcome_status": status,
        "capital_days": capital_days,
        "owner_efficiency": owner_efficiency(net_pnl, capital_days) if net_pnl is not None else None,
        "open": bool(fills) and open_contracts > 0,
        "fills": [_fill_transaction(f) for f in sorted(fills, key=lambda f: str(f.get("captured_at", "") or ""))],
    }


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return sum(values) / len(values)


def aggregate_group(records) -> dict:
    """Aggregate outcome records with mandatory evidence metadata.

    Every aggregate exposes sample_size, coverage_pct (share of the sample
    with fill evidence), and unknown_count (matched-but-unmeasurable) so a
    number is never read without its evidence quality.
    """
    records = [r for r in records if isinstance(r, dict)]
    sample_size = len(records)
    matched = [r for r in records if r.get("outcome_status") in ("measured", "unknown")]
    measured = [r for r in records if r.get("outcome_status") == "measured"]
    unknown = [r for r in records if r.get("outcome_status") == "unknown"]
    pending = [r for r in records if r.get("outcome_status") == "pending"]

    owner = owner_summary(
        [
            {
                "net_pnl": r.get("net_pnl"),
                "capital_days": r.get("capital_days"),
                "open": bool(r.get("open")),
            }
            for r in measured
        ]
    )

    fees_known_sum = sum(r.get("fees_total") or 0.0 for r in matched if r.get("fees_known"))
    fees_unknown_count = sum(1 for r in matched if not r.get("fees_known"))

    return {
        "sample_size": sample_size,
        "matched_count": len(matched),
        "pending_count": len(pending),
        "measured_count": len(measured),
        "unknown_count": len(unknown),
        "coverage_pct": round(len(matched) * 100.0 / sample_size, 1) if sample_size else 0.0,
        "net_dollars": owner["net_dollars"],
        "capital_days": owner["capital_days"],
        "owner_efficiency": owner["owner_efficiency"],
        "open_losses": owner["open_losses"],
        "drawdown": owner["drawdown"],
        "quoted_credit_avg_per_contract": _mean([r.get("quoted_credit_per_contract") for r in records]),
        "filled_credit_avg_per_contract": _mean([r.get("filled_credit_per_contract") for r in matched]),
        "avg_slippage_per_contract": _mean([r.get("slippage_per_contract") for r in matched]),
        "fees_total_known": fees_known_sum,
        "fees_unknown_count": fees_unknown_count,
    }


def group_records(records, key_fn, order=None) -> list[dict]:
    """Group records by a key function into [{key, ...aggregate}] entries."""
    groups: dict[str, list] = {}
    for record in records:
        key = key_fn(record)
        groups.setdefault(key, []).append(record)
    keys = list(groups.keys())
    if order is not None:
        keys.sort(key=lambda k: order.index(k) if k in order else len(order))
    else:
        keys.sort()
    return [{"key": key, **aggregate_group(groups[key])} for key in keys]


_DTE_ORDER = [label for _, label in _DTE_BUCKET_EDGES] + ["90+", "unknown"]


def filter_records(records, ticker=None, preset=None, event_tier=None, dte_bucket_filter=None) -> list[dict]:
    """Apply drill-down filters; None/empty values pass everything through."""
    out = []
    for record in records:
        if ticker and str(record.get("ticker", "")).upper() != str(ticker).upper():
            continue
        if preset and str(record.get("preset_key", "")) != str(preset):
            continue
        if event_tier and str(record.get("event_tier", "")) != str(event_tier):
            continue
        if dte_bucket_filter and str(record.get("dte_bucket", "")) != str(dte_bucket_filter):
            continue
        out.append(record)
    return records if all(v in (None, "") for v in (ticker, preset, event_tier, dte_bucket_filter)) else out


class OutcomeService:
    """Serves broker-verified outcome summaries; ingests on explicit request."""

    def __init__(self, database, connection_provider=None):
        self._db = database
        self._connection_provider = connection_provider

    # -- ingestion ---------------------------------------------------------

    def ingest_broker_evidence(self, days: int = 90, cash_flow_days: int = 7) -> dict:
        """Pull fills + fees (and recent cash movements) from OpenD.

        Query-only; reuses the shared connection via the injected provider.
        Returns a combined result; failures are reported, never swallowed.
        """
        from api.services.fills_service import FillsService

        connection = self._connection_provider() if self._connection_provider else None
        if connection is None:
            return {"ok": False, "error": "Broker connection unavailable", "ingested": 0}

        fills_service = FillsService(connection, self._db)
        fills_result = fills_service.ingest_history_fills(days=days)

        cash_result: dict = {"ok": True, "ingested": 0}
        try:
            cash_days = max(0, int(cash_flow_days))
        except (TypeError, ValueError):
            cash_days = 0
        if cash_days:
            dates = [(datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(cash_days)]
            cash_result = fills_service.ingest_cash_flows(dates)

        ok = bool(fills_result.get("ok")) and bool(cash_result.get("ok", True))
        return {
            "ok": ok,
            "fills": fills_result,
            "cash_flows": cash_result,
        }

    # -- serving -----------------------------------------------------------

    def get_outcome_summary(
        self,
        env,
        account_id,
        ticker=None,
        preset=None,
        event_tier=None,
        dte_bucket=None,
        snapshot_limit=500,
        fill_limit=5000,
    ) -> dict:
        """Build the outcome summary payload for the current identity.

        Reads local SQLite only (fills + published run snapshots); never
        gates on live OpenD and never touches the ranking path.
        """
        fills = self._db.get_fills(
            env=env,
            account_id=account_id,
            security_type="OPT",
            limit=max(1, int(fill_limit)),
        )
        snapshots = self._db.get_run_snapshots(env=env, account_id=account_id, limit=max(1, int(snapshot_limit)))
        records = build_outcome_records(snapshots, fills)
        records = filter_records(records, ticker=ticker, preset=preset, event_tier=event_tier, dte_bucket_filter=dte_bucket)

        event_order = sorted({str(r.get("event_tier", "")) for r in records} - {_UNTIERED})
        event_order.append(_UNTIERED)

        return {
            "generated_at": datetime.now().isoformat(),
            "filters": {
                "ticker": ticker or "",
                "preset": preset or "",
                "event_tier": event_tier or "",
                "dte_bucket": dte_bucket or "",
            },
            "totals": aggregate_group(records),
            "groups": {
                "by_preset": group_records(records, lambda r: str(r.get("preset_key", "")) or _UNTIERED),
                "by_dte_bucket": group_records(records, lambda r: str(r.get("dte_bucket", "")), order=_DTE_ORDER),
                "by_ticker": group_records(records, lambda r: str(r.get("ticker", ""))),
                "by_event_tier": group_records(records, lambda r: str(r.get("event_tier", "")), order=event_order),
            },
            "outcomes": records,
            "count": len(records),
        }
