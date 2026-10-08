"""Pure classification and folding for CSP/covered-call wheel cycles.

Inputs are normalized Moomoo fill rows and snapshot reconciliation evidence.
This module never contacts the broker or writes to the database.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Any

from core.outcome_attribution import combined_outcome
from core.utils import market_now, safe_float


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed


def _same_ticker(left: dict, right: dict) -> bool:
    return str(left.get("ticker", "") or "").upper() == str(right.get("ticker", "") or "").upper()


def _fee_known(fill: dict) -> bool:
    return fill.get("fees") is not None


def _fee(fill: dict) -> float:
    try:
        return float(fill.get("fees")) if fill.get("fees") is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _qty(fill: dict) -> float:
    try:
        return abs(float(fill.get("qty", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def _price(fill: dict) -> float:
    try:
        return float(fill.get("price", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _contract_key(fill: dict) -> tuple[str, str, str, float | None]:
    try:
        strike = float(fill.get("strike")) if fill.get("strike") is not None else None
    except (TypeError, ValueError):
        strike = None
    return (
        str(fill.get("ticker", "") or "").upper(),
        str(fill.get("expiration", "") or ""),
        str(fill.get("option_type", "") or "").upper(),
        strike,
    )


def _event_time(fill: dict) -> datetime:
    return _timestamp(fill.get("captured_at") or fill.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)


def _paired_stock_fill(option_fill: dict, stock_fills: list[dict], consumed: set[str]) -> dict | None:
    option_time = _event_time(option_fill)
    option_type = str(option_fill.get("option_type", "")).upper()
    expected_stock_side = "BUY" if option_type == "PUT" else "SELL" if option_type == "CALL" else ""
    wanted_qty = _qty(option_fill) * 100
    matches = []
    for stock in stock_fills:
        fill_id = str(stock.get("fill_id", "") or "")
        stock_time = _event_time(stock)
        if fill_id in consumed or not _same_ticker(option_fill, stock):
            continue
        if str(stock.get("security_type", "")).upper() != "STK":
            continue
        if str(stock.get("side", "")).upper() != expected_stock_side or not expected_stock_side:
            continue
        if abs(_qty(stock) - wanted_qty) > 0.0001:
            continue
        delta = abs((stock_time - option_time).total_seconds())
        if delta <= 5:
            matches.append((delta, stock_time, stock))
    return min(matches, key=lambda item: (item[0], item[1]))[2] if matches else None


def _fill_event(fill: dict, event_type: str, **extra) -> dict:
    known = _fee_known(fill)
    price_cash = _price(fill) * 100 * _qty(fill)
    premium_net = price_cash - _fee(fill) if known else None
    return {
        "event_type": event_type,
        "ticker": str(fill.get("ticker", "") or "").upper(),
        "timestamp": str(fill.get("captured_at") or fill.get("timestamp") or ""),
        "env": str(fill.get("env", "") or ""),
        "account_id": str(fill.get("account_id", "") or ""),
        "fill_ids": [str(fill.get("fill_id", "") or "")],
        "option_type": str(fill.get("option_type", "") or "").upper(),
        "strike": fill.get("strike"),
        "expiration": str(fill.get("expiration", "") or ""),
        "contracts": _qty(fill),
        "shares": 0,
        "fees_known": known,
        "premium_net": premium_net,
        **extra,
    }


def classify_fills(
    fills: list[dict] | None,
    previous_short_contracts: list[dict] | None = None,
    current_short_contracts: list[dict] | None = None,
    today: date | None = None,
) -> list[dict]:
    """Classify normalized fills and snapshot diffs into conservative events.

    A zero-price option buy is an assignment only when a same-underlying stock
    leg with matching contract share quantity appears within five seconds.
    The called-away mirror is marked provisional because live broker evidence
    has not yet confirmed that shape.
    """
    rows = [row for row in fills or [] if isinstance(row, dict)]
    options = [row for row in rows if str(row.get("security_type", "")).upper() == "OPT"]
    stocks = [row for row in rows if str(row.get("security_type", "")).upper() == "STK"]
    events: list[dict] = []
    consumed_stocks: set[str] = set()
    paired_contracts: set[tuple[str, str, str, float | None]] = set()
    bought_back_contracts: set[tuple[str, str, str, float | None]] = set()

    for option in sorted(options, key=_event_time):
        fill_id = str(option.get("fill_id", "") or "")
        side = str(option.get("side", "")).upper()
        price = _price(option)
        if price == 0 and side == "BUY":
            stock = _paired_stock_fill(option, stocks, consumed_stocks)
            if stock is not None:
                consumed_stocks.add(str(stock.get("fill_id", "") or ""))
                paired_contracts.add(_contract_key(option))
                option_type = str(option.get("option_type", "")).upper()
                event_type = "assigned_put" if option_type == "PUT" else "called_away"
                try:
                    basis = float(option.get("strike"))
                except (TypeError, ValueError):
                    basis = None
                events.append(
                    {
                        "event_type": event_type,
                        "ticker": str(option.get("ticker", "") or "").upper(),
                        "timestamp": str(option.get("captured_at") or option.get("timestamp") or ""),
                        "env": str(option.get("env", "") or ""),
                        "account_id": str(option.get("account_id", "") or ""),
                        "fill_ids": [fill_id, str(stock.get("fill_id", "") or "")],
                        "option_type": option_type,
                        "strike": option.get("strike"),
                        "expiration": str(option.get("expiration", "") or ""),
                        "contracts": _qty(option),
                        "shares": int(_qty(stock)),
                        "gross_basis_per_share": basis if event_type == "assigned_put" else None,
                        "share_fill_price": _price(stock),
                        "share_fill_fees": stock.get("fees"),
                        "share_fee_known": _fee_known(stock),
                        "option_exercise_fees": option.get("fees"),
                        "option_exercise_fee_known": _fee_known(option),
                        "fees_known": _fee_known(option) and _fee_known(stock),
                        "premium_net": None,
                        "provisional": event_type == "called_away",
                    }
                )
            else:
                events.append(_fill_event(option, "unclassified_zero_price_option_fill"))
            continue

        if price == 0 and side == "SELL":
            # Zero-price option sells have not been established as an expiry or
            # assignment shape and must not become income.
            continue

        if price <= 0:
            continue
        option_type = str(option.get("option_type", "")).upper()
        if side == "SELL" and option_type in {"PUT", "CALL"}:
            event = _fill_event(option, "open_put" if option_type == "PUT" else "open_call")
            events.append(event)
        elif side == "BUY":
            event = _fill_event(option, "close_option")
            event["premium_net"] = -(_price(option) * 100 * _qty(option) + _fee(option)) if _fee_known(option) else None
            events.append(event)
            bought_back_contracts.add(_contract_key(option))

    current_keys = {_contract_key(row) for row in current_short_contracts or []}
    market_day = today or market_now().date()
    for previous in previous_short_contracts or []:
        key = _contract_key(previous)
        if key in current_keys or key in paired_contracts or key in bought_back_contracts:
            continue
        try:
            expiration = datetime.strptime(str(previous.get("expiration", "")).replace("-", ""), "%Y%m%d").date()
        except ValueError:
            continue
        if expiration >= market_day:
            continue
        events.append(
            {
                "event_type": "expired_unverified",
                "ticker": key[0],
                "timestamp": datetime.combine(expiration, datetime.min.time()).isoformat(),
                "env": str(previous.get("env", "") or ""),
                "account_id": str(previous.get("account_id", "") or ""),
                "fill_ids": [],
                "option_type": key[2],
                "strike": key[3],
                "expiration": str(previous.get("expiration", "") or ""),
                "shares": 0,
                "fees_known": False,
                "premium_net": None,
                "provisional": True,
            }
        )
    return sorted(events, key=lambda event: str(event.get("timestamp", "")))


def _cycle_id(env: str, account_id: str, ticker: str, opened_at: str) -> str:
    payload = "\0".join((env, account_id, ticker, opened_at))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _new_cycle(event: dict) -> dict:
    ticker = str(event.get("ticker", "") or "").upper()
    env = str(event.get("env", "") or "")
    account_id = str(event.get("account_id", "") or "")
    opened_at = str(event.get("timestamp", "") or "")
    premium = event.get("premium_net")
    fees_known = bool(event.get("fees_known"))
    reasons = [] if fees_known else ["fees_unknown"]
    return {
        "cycle_id": _cycle_id(env, account_id, ticker, opened_at),
        "env": env,
        "account_id": account_id,
        "ticker": ticker,
        "state": "csp_open",
        "origin": "cash_secured_put",
        "opened_at": opened_at,
        "closed_at": None,
        "shares": 0,
        "gross_basis_per_share": None,
        "credits_net": 0.0,
        "open_credit": premium,
        "fees_known": fees_known,
        "completeness": "partial",
        "reasons": reasons,
        "adjusted_basis_per_share": None,
        "events": [event],
    }


def _realize_open_credit(cycle: dict, fees_known: bool):
    if cycle.get("open_credit") is not None and fees_known:
        cycle["credits_net"] = float(cycle.get("credits_net", 0) or 0) + float(cycle["open_credit"])
    elif cycle.get("open_credit") is None:
        cycle.setdefault("reasons", []).append("opening_credit_unknown")
    cycle["open_credit"] = 0.0
    cycle["fees_known"] = bool(cycle.get("fees_known")) and fees_known
    if not fees_known:
        cycle.setdefault("reasons", []).append("fees_unknown")


def fold_cycles(events: list[dict] | None, evidence: dict | None = None) -> list[dict]:
    """Fold normalized wheel events into current cycle materializations."""
    cycles: list[dict] = []
    current_by_ticker: dict[tuple[str, str, str], dict] = {}
    for event in sorted(events or [], key=lambda item: str(item.get("timestamp", ""))):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type", ""))
        ticker = str(event.get("ticker", "") or "").upper()
        identity = (
            str(event.get("env", "") or ""),
            str(event.get("account_id", "") or ""),
            ticker,
        )
        cycle = current_by_ticker.get(identity)
        if event_type == "open_put":
            cycle = _new_cycle(event)
            cycles.append(cycle)
            current_by_ticker[identity] = cycle
        elif event_type == "unclassified_zero_price_option_fill":
            if cycle is None:
                cycle = _new_cycle({**event, "event_type": "needs_review"})
                cycle["origin"] = "reconciled_needs_review"
                cycle["state"] = "needs_review"
                cycles.append(cycle)
                current_by_ticker[identity] = cycle
            cycle.setdefault("reasons", []).append("unclassified_zero_price_option_fill")
            cycle["fees_known"] = False
            cycle["events"].append(event)
        elif event_type in {"assigned_put", "open_call", "called_away", "expired_unverified", "close_option"}:
            if cycle is None:
                cycle = _new_cycle({**event, "event_type": "needs_review"})
                cycle["origin"] = "reconciled_needs_review"
                cycle["state"] = "needs_review"
                cycles.append(cycle)
                current_by_ticker[identity] = cycle
            cycle["events"].append(event)
            if event_type == "assigned_put":
                _realize_open_credit(cycle, bool(event.get("fees_known")))
                cycle["state"] = "assigned_uncovered"
                cycle["shares"] = int(event.get("shares", 0) or 0)
                cycle["gross_basis_per_share"] = event.get("gross_basis_per_share")
            elif event_type == "open_call":
                cycle["state"] = "cc_open"
                cycle["open_credit"] = event.get("premium_net")
                cycle["fees_known"] = bool(cycle.get("fees_known")) and bool(event.get("fees_known"))
                if event.get("premium_net") is None:
                    cycle.setdefault("reasons", []).append("opening_credit_unknown")
            elif event_type == "close_option":
                close_net = event.get("premium_net")
                if cycle.get("open_credit") is not None and close_net is not None:
                    cycle["credits_net"] = (
                        float(cycle.get("credits_net", 0) or 0) + float(cycle["open_credit"]) + float(close_net)
                    )
                    cycle["open_credit"] = 0.0
                _realize_open_credit(cycle, bool(event.get("fees_known")))
                # Buying back a covered call closes only the option leg. The
                # assigned stock remains in the active wheel cycle.
                if safe_float(cycle.get("shares"), 0.0) > 0:
                    cycle["state"] = "assigned_uncovered"
                else:
                    cycle["state"] = "closed"
                    cycle["closed_at"] = event.get("timestamp")
            elif event_type == "called_away":
                _realize_open_credit(cycle, bool(event.get("fees_known")))
                cycle["state"] = "closed"
                cycle["shares"] = 0
                cycle["closed_at"] = event.get("timestamp")
                if event.get("provisional"):
                    cycle.setdefault("reasons", []).append("called_away_pair_provisional")
            elif event_type == "expired_unverified":
                _realize_open_credit(cycle, False)
                cycle["state"] = "closed"
                cycle["closed_at"] = event.get("timestamp")
                cycle.setdefault("reasons", []).append("expired_unverified")

    for cycle in cycles:
        adjusted, reasons = adjusted_basis(cycle)
        cycle["adjusted_basis_per_share"] = adjusted
        cycle["reasons"] = sorted(set(cycle.get("reasons", []) + reasons))
        evidence = evidence if isinstance(evidence, dict) else {}
        window_start = _timestamp(evidence.get("window_start"))
        cycle_opened = _timestamp(cycle.get("opened_at"))
        try:
            ledger_shares = float(evidence.get("ledger_shares"))
            broker_shares = float(evidence.get("broker_shares"))
            shares_match = abs(ledger_shares - broker_shares) < 0.0001
        except (TypeError, ValueError):
            shares_match = False
        history_covered = bool(window_start and cycle_opened and window_start <= cycle_opened)
        if not history_covered:
            cycle["reasons"].append("fill_history_does_not_cover_cycle_origin")
        if not shares_match:
            cycle["reasons"].append("ledger_shares_do_not_match_broker")
        if evidence.get("no_unclassified_zero_price_fills") is not True:
            cycle["reasons"].append("unclassified_zero_price_fills_unknown")
        cycle["reasons"] = sorted(set(cycle["reasons"]))
        proven = (
            bool(cycle.get("fees_known"))
            and cycle.get("shares", 0) > 0
            and cycle.get("gross_basis_per_share") is not None
            and history_covered
            and shares_match
            and evidence.get("no_unclassified_zero_price_fills") is True
            and cycle.get("cycle_id") in evidence.get("eligible_cycle_ids", [])
            and not cycle["reasons"]
        )
        if cycle.get("cycle_id") not in evidence.get("eligible_cycle_ids", []):
            cycle["reasons"].append("cycle_not_single_active_inventory")
            cycle["reasons"] = sorted(set(cycle["reasons"]))
        cycle["completeness"] = "proven" if proven else "partial"
        cycle["updated_at"] = market_now().isoformat()
        cycle["cycle_outcome"] = realized_cycle_outcome(cycle)
    return cycles


def realized_cycle_outcome(cycle: dict | None) -> dict:
    """Return combined realized wheel P&L only when all legs are evidenced.

    Open inventory, unverified expiry, provisional called-away matching, missing
    fees/prices, or mismatched share quantities leave the result unknown. Option
    fill premiums already include their own fees; paired exercise fees and stock
    fill fees are applied separately.
    """
    if not isinstance(cycle, dict):
        return {"status": "unknown", "net_pnl": None, "reason": "cycle_missing"}
    if cycle.get("state") in {"csp_open", "assigned_uncovered", "cc_open"}:
        return {"status": "open", "net_pnl": None, "reason": "cycle_still_open"}
    if cycle.get("state") != "closed":
        return {"status": "unknown", "net_pnl": None, "reason": "cycle_state_unverified"}
    if cycle.get("origin") != "cash_secured_put":
        return {"status": "unknown", "net_pnl": None, "reason": "cycle_origin_unverified"}

    events = cycle.get("events") if isinstance(cycle.get("events"), list) else []
    types = [str(event.get("event_type") or "") for event in events if isinstance(event, dict)]
    if "expired_unverified" in types:
        return {"status": "unknown", "net_pnl": None, "reason": "expiry_payoff_unverified"}

    option_events = [
        event
        for event in events
        if isinstance(event, dict) and event.get("event_type") in {"open_put", "open_call", "close_option"}
    ]
    if not option_events or any(event.get("premium_net") is None for event in option_events):
        return {"status": "unknown", "net_pnl": None, "reason": "option_fill_or_fee_evidence_missing"}
    option_net = sum(safe_float(event.get("premium_net"), 0.0) for event in option_events)

    assignments = [event for event in events if event.get("event_type") == "assigned_put"]
    called_away = [event for event in events if event.get("event_type") == "called_away"]
    if not assignments and not called_away:
        result = combined_outcome(
            {
                "premium_in": max(0.0, option_net),
                "premium_out": max(0.0, -option_net),
                "fees": 0.0,
            },
            None,
        )
        return {**result, "pnl_scope": "wheel_cycle", "option_leg_net_pnl": result.get("net_pnl")}
    if len(assignments) != 1 or len(called_away) != 1:
        return {"status": "unknown", "net_pnl": None, "reason": "share_leg_not_fully_closed"}

    assigned, exited = assignments[0], called_away[0]
    if exited.get("provisional"):
        return {"status": "unknown", "net_pnl": None, "reason": "called_away_pair_provisional"}
    if assigned.get("fees_known") is not True or exited.get("fees_known") is not True:
        return {"status": "unknown", "net_pnl": None, "reason": "share_or_exercise_fees_unknown"}
    shares_in = safe_float(assigned.get("shares"), None)
    shares_out = safe_float(exited.get("shares"), None)
    assigned_contracts = safe_float(assigned.get("contracts"), None)
    called_contracts = safe_float(exited.get("contracts"), None)
    basis = safe_float(assigned.get("share_fill_price"), None)
    exit_price = safe_float(exited.get("share_fill_price"), None)
    put_contracts = sum(
        safe_float(event.get("contracts"), 0.0) for event in option_events if event.get("event_type") == "open_put"
    )
    put_buybacks = sum(
        safe_float(event.get("contracts"), 0.0)
        for event in option_events
        if event.get("option_type") == "PUT" and event.get("event_type") == "close_option"
    )
    call_contracts = sum(
        safe_float(event.get("contracts"), 0.0) for event in option_events if event.get("event_type") == "open_call"
    )
    call_buybacks = sum(
        safe_float(event.get("contracts"), 0.0)
        for event in option_events
        if event.get("option_type") == "CALL" and event.get("event_type") == "close_option"
    )
    if (
        shares_in is None
        or shares_out is None
        or shares_in <= 0
        or abs(shares_in - shares_out) > 0.0001
        or assigned_contracts is None
        or called_contracts is None
        or abs(put_contracts - put_buybacks - assigned_contracts) > 0.0001
        or abs(call_contracts - call_buybacks - called_contracts) > 0.0001
        or abs(shares_in - assigned_contracts * 100) > 0.0001
        or abs(shares_out - called_contracts * 100) > 0.0001
        or basis is None
        or basis <= 0
        or exit_price is None
        or exit_price <= 0
    ):
        return {"status": "unknown", "net_pnl": None, "reason": "share_price_or_quantity_mismatch"}

    exercise_fees = sum(safe_float(event.get("option_exercise_fees"), 0.0) for event in (assigned, exited))
    option_leg = {
        "premium_in": max(0.0, option_net),
        "premium_out": max(0.0, -option_net),
        "fees": exercise_fees,
    }
    share_leg_fees = safe_float(assigned.get("share_fill_fees"), 0.0) + safe_float(exited.get("share_fill_fees"), 0.0)
    share_leg = {
        "basis_price": basis,
        "exit_price": exit_price,
        "shares": shares_in,
        "fees": share_leg_fees,
    }
    result = combined_outcome(option_leg, share_leg)
    return {
        **result,
        "pnl_scope": "wheel_cycle",
        "net_pnl": round(result["net_pnl"], 2) if result.get("net_pnl") is not None else None,
        "option_leg_net_pnl": round(option_net - exercise_fees, 2),
        "share_leg_net_pnl": round((exit_price - basis) * shares_in - share_leg_fees, 2),
    }


def adjusted_basis(cycle: dict) -> tuple[float | None, list[str]]:
    """Compute gross basis less realized net credits per share."""
    if not isinstance(cycle, dict):
        return None, ["cycle_missing"]
    try:
        shares = float(cycle.get("shares"))
        gross_basis = float(cycle.get("gross_basis_per_share"))
        credits_net = float(cycle.get("credits_net", 0) or 0)
    except (TypeError, ValueError):
        return None, ["basis_evidence_missing"]
    if shares <= 0 or gross_basis <= 0:
        return None, ["basis_evidence_missing"]
    # `open_credit` is intentionally excluded. An open call credit remains a
    # liability until its expiry or buyback is evidenced.
    return round(gross_basis - credits_net / shares, 8), []


def gate_basis(cycle: dict | None, broker_avg_cost: Any, diluted_cost: Any = None) -> tuple[float | None, str]:
    """Prefer a proven ledger basis; otherwise fall back to broker average cost.

    ``diluted_cost`` is intentionally ignored until its broker semantics are
    separately probed and owner-approved (roadmap decision O3).
    """
    del diluted_cost
    if isinstance(cycle, dict) and cycle.get("completeness") == "proven":
        basis, reasons = adjusted_basis(cycle)
        if basis is not None and not reasons:
            return basis, "ledger_adjusted"
    try:
        avg_cost = float(broker_avg_cost)
    except (TypeError, ValueError):
        avg_cost = 0.0
    return (avg_cost, "moomoo_avg_cost") if avg_cost > 0 else (None, "missing")
