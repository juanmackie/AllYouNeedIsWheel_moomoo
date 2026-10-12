"""Pure holdings campaign accounting from broker positions and fills.

The broker-history capability flag is deliberately false by default. Expiry
and assignment interpretation can only be enabled after the query-only G1
probe has measured the deployed OpenD behavior.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from math import isfinite

from core.outcome_attribution import broker_price_to_contract_credit
from core.ticker_utils import earnings_underlying_ticker, parse_moomoo_symbol
from core.utils import parse_position_qty


def _timestamp(value) -> str:
    return str(value or "").strip()


def _finite(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _fill_ticker(fill):
    return str(fill.get("ticker", "") or "").strip().upper()


def _parse_timestamp(value):
    try:
        return datetime.fromisoformat(_timestamp(value)[:19])
    except ValueError:
        return None


def _contract_key(fill):
    return (
        _fill_ticker(fill),
        str(fill.get("expiration", "") or ""),
        str(fill.get("option_type", "") or "").upper(),
        _finite(fill.get("strike")),
    )


def _closed_option_economics(fills):
    """Return realized option credit/debit/fees and unmatched short legs."""
    by_contract = defaultdict(list)
    for fill in fills:
        if str(fill.get("security_type", "") or "").upper() != "OPT":
            continue
        ticker = _fill_ticker(fill)
        side = str(fill.get("side", "") or "").upper()
        qty = abs(_finite(fill.get("qty")) or 0.0)
        price = _finite(fill.get("price"))
        if ticker and side in {"BUY", "SELL"} and qty > 0 and price is not None:
            by_contract[_contract_key(fill)].append((fill, side, qty, price))

    credits = debits = fees_total = call_credits = call_debits = call_fees = 0.0
    fee_unknown = False
    open_legs = []
    for key, rows in by_contract.items():
        rows.sort(key=lambda item: (_timestamp(item[0].get("captured_at")), str(item[0].get("fill_id", ""))))
        short_lots = deque()
        for fill, side, qty, price in rows:
            fee = _finite(fill.get("fees"))
            if side == "SELL":
                short_lots.append(
                    {"fill": fill, "qty": qty, "price": price, "fee_per_unit": fee / qty if fee is not None else None}
                )
                continue
            remaining = qty
            while remaining > 0 and short_lots:
                lot = short_lots[0]
                matched = min(remaining, lot["qty"])
                credits += broker_price_to_contract_credit(lot["price"]) * matched
                debits += broker_price_to_contract_credit(price) * matched
                opening_fee = lot["fee_per_unit"]
                closing_fee = fee / qty if fee is not None else None
                if opening_fee is None or closing_fee is None:
                    fee_unknown = True
                else:
                    fees_total += (opening_fee + closing_fee) * matched
                    if key[2].startswith("CALL"):
                        call_fees += (opening_fee + closing_fee) * matched
                if key[2].startswith("CALL"):
                    call_credits += broker_price_to_contract_credit(lot["price"]) * matched
                    call_debits += broker_price_to_contract_credit(price) * matched
                lot["qty"] -= matched
                remaining -= matched
                if lot["qty"] <= 1e-9:
                    short_lots.popleft()
        for lot in short_lots:
            fill = lot["fill"]
            open_legs.append(
                {
                    "ticker": key[0],
                    "expiration": key[1],
                    "option_type": key[2],
                    "strike": key[3],
                    "contracts": round(lot["qty"], 6),
                    "opening_credit_per_contract": round(broker_price_to_contract_credit(lot["price"]), 2),
                    "opened_at": _timestamp(fill.get("captured_at")),
                }
            )
    return credits, debits, fees_total, fee_unknown, open_legs, call_credits - call_debits - call_fees


def _paired_assignment_put_credits(fills, lot_anchor=None):
    """Find put opening sells explicitly paired with zero-price assignment rows."""
    stock_buys = [
        f
        for f in fills
        if str(f.get("security_type", "") or "").upper() == "STK"
        and str(f.get("side", "") or "").upper() == "BUY"
        and (not lot_anchor or _timestamp(f.get("captured_at")) >= lot_anchor)
    ]
    option_rows = [f for f in fills if str(f.get("security_type", "") or "").upper() == "OPT"]
    included = set()
    for close in option_rows:
        if str(close.get("side", "") or "").upper() != "BUY" or (_finite(close.get("price")) or 0) != 0:
            continue
        if not str(close.get("option_type", "") or "").upper().startswith("PUT"):
            continue
        close_time = _parse_timestamp(close.get("captured_at"))
        if close_time is None:
            continue
        qty = abs(_finite(close.get("qty")) or 0) * 100
        strike = _finite(close.get("strike"))
        ticker = _fill_ticker(close)
        pair_found = False
        for stock in stock_buys:
            stock_time = _parse_timestamp(stock.get("captured_at"))
            stock_price = _finite(stock.get("price"))
            if (
                _fill_ticker(stock) == ticker
                and stock_time is not None
                and abs((stock_time - close_time).total_seconds()) <= 2
                and abs((abs(_finite(stock.get("qty")) or 0)) - qty) <= 1e-6
                and strike is not None
                and stock_price is not None
                and abs(stock_price - strike) <= 0.01
            ):
                pair_found = True
                break
        if not pair_found:
            continue
        candidates = [
            f
            for f in option_rows
            if _contract_key(f) == _contract_key(close)
            and str(f.get("side", "") or "").upper() == "SELL"
            and (_parse_timestamp(f.get("captured_at")) or close_time) <= close_time
        ]
        if candidates:
            candidate = max(candidates, key=lambda f: _timestamp(f.get("captured_at")))
            included.add(str(candidate.get("fill_id", "")))
            included.add(str(close.get("fill_id", "")))
    return included


def build_campaign(
    position: dict,
    fills: list[dict] | None,
    *,
    fill_pull_ok: bool = False,
    capability_measured: bool = False,
    open_option_positions: list[dict] | None = None,
    as_of=None,
) -> dict:
    """Build an honest campaign record for one Moomoo stock position.

    Unknown history, fees, or stock-lot reconciliation always falls back to
    the broker's average cost. ``capability_measured`` is an explicit opt-in
    from the G1 probe; it defaults off so unmeasured expiry rules cannot leak
    into production basis.
    """
    position = dict(position or {})
    ticker = parse_moomoo_symbol(str(position.get("symbol", "") or ""))
    shares = parse_position_qty(position.get("position", position.get("shares", 0)) or 0)
    broker_avg_cost = _finite(position.get("avg_cost"))
    relevant = [dict(f) for f in (fills or []) if isinstance(f, dict) and _fill_ticker(f) == ticker]
    stock_fills = [f for f in relevant if str(f.get("security_type", "") or "").upper() == "STK"]
    stock_balance = sum(
        (1 if str(f.get("side", "") or "").upper() == "BUY" else -1) * abs(_finite(f.get("qty")) or 0.0)
        for f in stock_fills
    )
    reasons = []
    if not ticker:
        reasons.append("missing_underlying_ticker")
    if not fill_pull_ok:
        reasons.append("fill_pull_not_successful_this_refresh")
    if not capability_measured:
        reasons.append("broker_history_capability_unmeasured")
    if stock_fills and abs(stock_balance - shares) > 1e-6:
        reasons.append("stock_fills_do_not_reconcile_position")
    elif not stock_fills and not relevant:
        reasons.append("no_fills_in_ledger")

    timestamps = [_timestamp(f.get("captured_at")) for f in relevant if _timestamp(f.get("captured_at"))]
    lot_anchor = None
    if stock_fills and abs(stock_balance - shares) <= 1e-6:
        buys = [f for f in stock_fills if str(f.get("side", "") or "").upper() == "BUY"]
        lot_anchor = max((_timestamp(f.get("captured_at")) for f in buys), default=None)
    elif not stock_fills:
        lot_anchor = min(timestamps, default=None)

    assigned_put_credit_ids = _paired_assignment_put_credits(relevant, lot_anchor) if capability_measured else set()
    campaign_fills = [
        f
        for f in relevant
        if not lot_anchor
        or _timestamp(f.get("captured_at")) >= lot_anchor
        or str(f.get("fill_id", "")) in assigned_put_credit_ids
    ]
    credits, debits, fees, fee_unknown, open_legs, call_net_premium = _closed_option_economics(campaign_fills)
    net_premium = round(credits - debits - fees, 2)
    if fee_unknown:
        reasons.append("fees_unknown_on_counted_fills")
    if open_option_positions is None:
        reasons.append("open_short_positions_unavailable")
    else:
        expected_open = defaultdict(float)
        for option in open_option_positions:
            if not isinstance(option, dict) or str(option.get("security_type", "") or "").upper() != "OPT":
                continue
            try:
                qty = abs(parse_position_qty(option.get("position", option.get("shares", 0)) or 0))
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0 or parse_position_qty(option.get("position", option.get("shares", 0)) or 0) >= 0:
                continue
            expected_open[
                (
                    earnings_underlying_ticker(str(option.get("symbol", "") or "")).upper(),
                    str(option.get("expiration", "") or ""),
                    str(option.get("option_type", "") or "").upper(),
                    _finite(option.get("strike")),
                )
            ] += qty
        recorded_open = {
            (leg["ticker"], leg["expiration"], leg["option_type"], leg["strike"]): leg["contracts"] for leg in open_legs
        }
        if any(recorded_open.get(key, 0) + 1e-6 < qty for key, qty in expected_open.items()):
            reasons.append("open_short_leg_missing_from_ledger")
        if not expected_open and open_legs and not capability_measured:
            reasons.append("unverified_expired_or_assigned_short_legs")
    if broker_avg_cost is None:
        reasons.append("broker_average_cost_unavailable")
    if shares <= 0:
        reasons.append("no_positive_shares_held")

    usable = not reasons and broker_avg_cost is not None and shares > 0
    adjusted_basis = round(broker_avg_cost - net_premium / shares, 4) if usable else broker_avg_cost
    source = "premium_adjusted" if usable else "moomoo_avg_cost"
    # Complete requires both an explained stock lot and its paired assigned-put
    # credit. Otherwise the result remains a conservative upper bound.
    confidence = "unverified"
    if usable:
        confidence = (
            "complete"
            if stock_fills and abs(stock_balance - shares) <= 1e-6 and assigned_put_credit_ids
            else "conservative"
        )
    if not usable and not reasons:
        reasons.append("campaign_not_verified")
    as_of_text = _timestamp(as_of) if as_of is not None else datetime.now().isoformat()
    elapsed_weeks = 0.0
    if lot_anchor:
        try:
            elapsed = datetime.fromisoformat(as_of_text[:19]) - datetime.fromisoformat(lot_anchor[:19])
            elapsed_weeks = max(0.0, elapsed.total_seconds() / 604800)
        except ValueError:
            pass
    premium_per_week = round(call_net_premium / elapsed_weeks, 2) if usable and elapsed_weeks > 0 else None
    market_price = _finite(position.get("market_price"))
    open_call_contracts = (
        sum(
            qty
            for (symbol, _expiration, option_type, _strike), qty in expected_open.items()
            if symbol == ticker and option_type.startswith("CALL")
        )
        if open_option_positions is not None
        else 0
    )
    gap_per_share = None
    gap_total = None
    weeks_to_exit = None
    if adjusted_basis is not None and market_price is not None:
        gap_per_share = round(max(0.0, adjusted_basis - market_price), 4)
        gap_total = round(gap_per_share * max(0, shares), 2)
        if gap_per_share > 0 and premium_per_week and premium_per_week > 0:
            weeks_to_exit = round(gap_total / premium_per_week, 1)

    return {
        "ticker": ticker,
        "shares": shares,
        "available_contracts": max(0, shares // 100),
        "available_calls": max(0, shares // 100 - int(open_call_contracts)),
        "broker_avg_cost": broker_avg_cost,
        "adjusted_basis": adjusted_basis,
        "basis_source": source,
        "basis_confidence": confidence,
        "basis_reasons": reasons,
        "lot_anchor": lot_anchor,
        "net_option_premium": net_premium,
        "fees": round(fees, 2),
        "realized_premium_per_week": premium_per_week,
        "realized_call_income_per_week": premium_per_week,
        "open_legs": open_legs,
        "market_price": market_price,
        "break_even": {
            "market_price": market_price,
            "gap_per_share": gap_per_share,
            "gap_total": gap_total,
            "weeks_to_no_loss_exit": weeks_to_exit,
            "capital_tied_up": round(max(0, shares) * market_price, 2) if market_price is not None else None,
        },
    }


def build_campaigns(
    positions: list[dict],
    fills: list[dict] | None,
    *,
    fill_pull_ok: bool = False,
    capability_measured: bool = False,
    open_option_positions: list[dict] | None = None,
    as_of=None,
) -> list[dict]:
    """Build campaigns only for broker-reported stock positions."""
    output = []
    for position in positions or []:
        if not isinstance(position, dict):
            continue
        if str(position.get("security_type", "") or "").upper() not in {"", "STK"}:
            continue
        output.append(
            build_campaign(
                position,
                fills,
                fill_pull_ok=fill_pull_ok,
                capability_measured=capability_measured,
                open_option_positions=open_option_positions,
                as_of=as_of,
            )
        )
    return output
