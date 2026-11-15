"""Measurement-only wheel paths from published broker signals and daily candles.

No I/O, order surface, learned policy, or invented option quotes. Each published
put starts an independent one-contract path. Calls use the first later published
no-loss call for that account and ticker, in persisted order, after assignment.
These paths can overlap and must never be summed as a portfolio backtest.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import date, time
from zoneinfo import ZoneInfo

from api.services.recommendation_ranking import candidate_field
from core.scoring_factors import parse_broker_timestamp
from core.utils import safe_float

NY = ZoneInfo("America/New_York")
SLIPPAGE = (0.0, 0.25, 0.5)
NOTES = [
    "SIMULATED: independent one-contract paths, not actual fills or a portfolio backtest. Do not sum overlapping paths.",
    "Only published Moomoo signals are used; no option quotes are generated or fetched from another provider.",
    "Entry uses bid minus slippage times spread. A captured quote does not prove a fill at the next open.",
    "Assignment and call-away are modeled only at expiry from the underlying close; early exercise is not modeled.",
    "Calls must be published after assignment, have no recorded blockers, and have strike at least the put strike. "
    "The first eligible signal in persisted order is used. No repair-call or roll policy is added.",
    "The stock-sale fee estimate is also charged for assignment purchases. These are fee estimates, not exact costs.",
    "Open shares use the last common-session broker close. An open option without a mark has unknown P&L.",
    "Dividends, interest, taxes, and corporate actions below the replay detection threshold are not modeled. "
    "Missing adjusted prices limit corporate-action detection.",
    "This section does not change the CSP walk-forward verdict, app ranking, presets, or broker-history capability gates.",
]


def _session(published, dates):
    local = published.astimezone(NY)
    pos = bisect_left(dates, local.date()) if local.time() < time(9, 30) else bisect_right(dates, local.date())
    return dates[pos] if pos < len(dates) else None


def _signal(raw, published, entry):
    """Validate captured quote evidence; return normalized leg or a counted reason."""
    kind = str(raw.get("option_type") or "").upper()
    if kind not in {"PUT", "CALL"}:
        return "unsupported_option_type"
    if candidate_field(raw, "held_position"):
        return "held_position"
    if any(
        candidate_field(raw, key) for key in ("hard_blockers", "blocked_reason_codes", "risk_gate", "research_only")
    ):
        return "blocked_signal"
    if (
        candidate_field(raw, "from_yfinance")
        or candidate_field(raw, "price_source") != "moomoo"
        or candidate_field(raw, "chain_source", "broker") not in {"broker", "moomoo"}
    ):
        return "non_broker_quote"
    bid, ask, strike = (safe_float(candidate_field(raw, key), None) for key in ("bid", "ask", "strike"))
    if any(v is None or not math.isfinite(v) for v in (bid, ask, strike)) or not (strike > 0 and ask >= bid > 0):
        return "invalid_quote"
    ticker = str(raw.get("ticker") or "").strip().upper()
    if not ticker:
        return "missing_ticker"
    text = str(raw.get("expiration") or "")
    try:
        if len(text) == 8 and text.isdigit():
            text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
        expiry = date.fromisoformat(text[:10])
    except ValueError:
        return "invalid_expiration"
    quote_value = candidate_field(raw, "quote_update_time")
    quote = parse_broker_timestamp(quote_value)
    if quote is None:
        return "invalid_quote_time"
    if quote > published:
        return "future_quote"
    if (entry - quote.astimezone(NY).date()).days > 4:
        return "stale_quote"
    return {
        "ticker": ticker,
        "option_type": kind,
        "strike": strike,
        "expiration": expiry,
        "bid": bid,
        "ask": ask,
        "entry": entry,
    }


def _decisions(snapshots, dates):
    latest = {}
    skipped = Counter()
    for snap in sorted(snapshots, key=lambda s: (s["published_at"], s["snapshot_id"])):
        scope = (snap.get("env", ""), snap.get("account_id", ""))
        if not all(scope):
            skipped["unscoped_snapshot"] += 1
            continue
        entry = _session(snap["published_at"], dates)
        if entry is not None:
            latest[(scope, entry)] = snap
        else:
            skipped["no_entry_session"] += 1
    decisions = []
    for (scope, entry), snap in sorted(latest.items(), key=lambda item: (item[0][1], item[0][0])):
        seen = set()
        for raw in snap["signals"]:
            leg = _signal(raw, snap["published_at"], entry)
            if isinstance(leg, str):
                skipped[leg] += 1
                continue
            key = (leg["ticker"], leg["option_type"], leg["strike"], leg["expiration"])
            if key not in seen:
                decisions.append(
                    {**leg, "scope": scope, "snapshot_id": snap["snapshot_id"], "published_at": snap["published_at"]}
                )
                seen.add(key)
    return decisions, dict(skipped)


def _bar(prices, ticker, day, anchor=None):
    series = prices.get(ticker)
    bar = series.bars.get(day) if series is not None else None
    if bar is None or any(not math.isfinite(v) or v <= 0 for v in (bar.open, bar.close)):
        return "missing_candle"
    if anchor is not None and anchor.adj_close is not None and bar.adj_close is not None:
        if min(anchor.adj_close, bar.adj_close) <= 0:
            return "corporate_action"
        ratio = (bar.close / bar.adj_close) / (anchor.close / anchor.adj_close)
        if not math.isfinite(ratio) or abs(ratio - 1) > 0.05:
            return "corporate_action"
    return bar


def _path(put, calls, prices, fees, dates):
    """Keep premium cash separate from the share purchase and eventual sale/mark."""
    entry, ticker, basis = put["entry"], put["ticker"], put["strike"]
    path = {
        "snapshot_id": put["snapshot_id"],
        "ticker": ticker,
        "entry": entry.isoformat(),
        "capital": basis * 100,
        "complete": False,
        "legs": [],
        "pnl_by_slippage": None,
        "fees": fees,
    }
    anchor, spy_anchor = _bar(prices, ticker, entry), _bar(prices, "SPY", entry)
    if isinstance(anchor, str) or isinstance(spy_anchor, str):
        return {**path, "status": "unresolved", "reason": "missing_candle"}
    cash = {str(s): 0.0 for s in SLIPPAGE}
    leg, shares, end, status = put, 0, dates[-1], "unresolved"
    while leg is not None:
        path["legs"].append(
            {
                key: value.isoformat() if isinstance(value, date) else value
                for key, value in leg.items()
                if key not in {"scope", "published_at"}
            }
        )
        if leg["expiration"] <= leg["entry"]:
            return {**path, "status": "unresolved", "reason": "expires_on_or_before_entry"}
        for s in SLIPPAGE:
            cash[str(s)] += 100 * (leg["bid"] - s * (leg["ask"] - leg["bid"])) - fees["option_fee_per_contract"]
        if leg["expiration"] > dates[-1]:
            return {**path, "status": "put_open" if shares == 0 else "call_open", "reason": "option_mark_unavailable"}
        pos = bisect_right(dates, leg["expiration"]) - 1
        end = dates[pos]
        stock_dates = prices[ticker].dates
        stock_pos = bisect_right(stock_dates, leg["expiration"]) - 1
        if stock_pos < 0 or stock_dates[stock_pos] != end or (leg["expiration"] - end).days > 4:
            return {**path, "status": "unresolved", "reason": "missing_candle"}
        # Check the entire held interval, not just its endpoints, for corporate actions.
        for day in dates[bisect_left(dates, entry) : pos + 1]:
            stock = _bar(prices, ticker, day, anchor)
            spy = _bar(prices, "SPY", day, spy_anchor)
            if isinstance(stock, str) or isinstance(spy, str):
                return {**path, "status": "unresolved", "reason": stock if isinstance(stock, str) else spy}
        if shares == 0:
            if stock.close >= basis:
                status = "put_expired"
                break
            shares = 100
            cash = {s: value - basis * 100 - fees["stock_sale_fee"] for s, value in cash.items()}
        elif stock.close > leg["strike"]:
            cash = {s: value + leg["strike"] * 100 - fees["stock_sale_fee"] for s, value in cash.items()}
            shares, status = 0, "called_away"
            break
        # Never use a signal known before this expiry, even if its next entry is later.
        expiry = leg["expiration"]
        leg = next(
            (
                call
                for call in calls
                if call["scope"] == put["scope"]
                and call["ticker"] == ticker
                and call["entry"] > expiry
                and call["published_at"].astimezone(NY).date() > expiry
                and call["strike"] >= basis
            ),
            None,
        )
        if leg is None:
            end, status = dates[-1], "shares_open"
    final = _bar(prices, ticker, end, anchor)
    spy_final = _bar(prices, "SPY", end, spy_anchor)
    if isinstance(final, str) or isinstance(spy_final, str):
        return {**path, "status": "unresolved", "reason": final if isinstance(final, str) else spy_final}
    # Validate bars in the waiting-for-call interval too.
    for day in dates[bisect_left(dates, entry) : bisect_right(dates, end)]:
        for symbol, start_bar in ((ticker, anchor), ("SPY", spy_anchor)):
            bar = _bar(prices, symbol, day, start_bar)
            if isinstance(bar, str):
                return {**path, "status": "unresolved", "reason": bar}
    days = max(1, (end - entry).days)
    capital = path["capital"]
    pnl = {s: round(value + shares * final.close, 6) for s, value in cash.items()}
    return {
        **path,
        "status": status,
        "complete": status in {"put_expired", "called_away"},
        "end": end.isoformat(),
        "days": days,
        "capital_days": capital * days,
        "pnl_by_slippage": pnl,
        "return_by_slippage": {s: value / capital for s, value in pnl.items()},
        "stock_pnl": capital * (final.close / anchor.open - 1) - fees["stock_sale_fee"],
        "spy_pnl": capital * (spy_final.close / spy_anchor.open - 1) - fees["stock_sale_fee"],
    }


def replay_wheel(snapshots, prices, fees, last_date, *, fees_by_scope=None):
    """Replay captured paths; missing evidence stays unknown and has a counted reason."""
    spy = prices.get("SPY")
    dates = [day for day in spy.dates if day <= last_date] if spy is not None else []
    decisions, skipped = _decisions(snapshots, dates)
    calls = [leg for leg in decisions if leg["option_type"] == "CALL"]
    paths = [
        _path(put, calls, prices, fees_by_scope[put["scope"]] if fees_by_scope is not None else fees, dates)
        for put in decisions
        if put["option_type"] == "PUT"
    ]
    return {
        "simulated": True,
        "paths": paths,
        "status_counts": dict(Counter(p["status"] for p in paths)),
        "unresolved_reasons": dict(Counter(p["reason"] for p in paths if p.get("reason"))),
        "skipped_signals": skipped,
        "limitations": NOTES,
    }


def render_wheel(report):
    """Keep all unknown and open paths visible beside completed paths."""
    lines = [
        "",
        "## Wheel lifecycles (SIMULATED; measurement only)",
        "",
        *[f"- {note}" for note in report["limitations"]],
        "",
        f"Status counts: {report['status_counts']}",
        f"Unknown reasons: {report['unresolved_reasons']}",
        f"Skipped evidence: {report['skipped_signals']}",
        "",
        "| Ticker | Entry | End | Status | Legs | Net P&L (s=0 / 0.25 / 0.5) | Capital-days | STOCK / SPY P&L | Fees | Reason |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for path in report["paths"]:
        pnl = path["pnl_by_slippage"]
        values = " / ".join(f"{pnl[str(s)]:.2f}" for s in SLIPPAGE) if pnl is not None else "unknown"
        baseline = f"{path['stock_pnl']:.2f} / {path['spy_pnl']:.2f}" if pnl is not None else "unknown"
        fee_note = "calibrated estimates" if path["fees"].get("calibrated") else "fallback estimates"
        lines.append(
            f"| {path['ticker']} | {path['entry']} | {path.get('end', 'unknown')} | {path['status']} | "
            f"{len(path['legs'])} | {values} | {path.get('capital_days', 'unknown')} | {baseline} | "
            f"{fee_note} | {path.get('reason', '')} |"
        )
    return lines
