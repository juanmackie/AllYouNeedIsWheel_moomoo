"""Pure broker-verified outcome attribution helpers.

Deterministic, broker-free, no I/O. These functions turn verified broker
fills (deals, fees, share movements) into owner-facing outcome numbers:

- net P&L per recommendation = option premium P&L + underlying share P&L − fees
- slippage/leakage of a fill against the quoted premium that produced the signal
- capital-days accounting (the denominator of the owner-efficiency metric)
- owner summary: net dollars, net P&L / capital-day, open losses, drawdown

Hard rules encoded here (never weaken them at call sites):

1. **Never fabricate.** A missing cost basis or missing fill yields an
   explicitly UNKNOWN outcome (``None`` pnl / ``status="unknown"``), never a
   constructed 0.0 or an assumed price.
2. **Deposits/withdrawals are not trading profit.** Trading P&L is computed
   only from fills (premium in/out, share basis/exit, fees); cash movements
   never enter the arithmetic. They are persisted separately so the UI can
   corroborate account growth without polluting the metric.
3. **Capital-days must not flatter results.** The denominator is the sum over
   tranches of ``capital × days held``:

   - Cash-secured put: capital = strike × 100 × contracts, accruing from the
     entry fill until the obligation is extinguished (buyback close,
     expiration worthless, or assignment).
   - Covered call: the shares ARE the deployed capital — capital =
     share price at entry × shares, accruing while the recommendation is live.
   - Partial closes: each tranche stops accruing at its own close; remaining
     contracts keep accruing until closed. Capital-days therefore use
     per-tranche arithmetic, never a single average capital (which would
     understate the denominator after a partial close) and never a single
     end-date for the full original size (which would overstate it).
   - Assignment: the put's capital (strike × 100) transfers to the share
     position on the assignment date; days continue accruing without reset
     until the shares are sold or a covered call against them closes.

Deterministic, broker-free, no I/O — testable like ``core.position_diff``.
"""

from __future__ import annotations

from datetime import datetime

# One US equity option contract covers 100 shares.
CONTRACT_MULTIPLIER = 100

# cashflow_type/remark substrings (case-insensitive) that identify capital
# movements. Server strings are a raw passthrough (unverified enum), so this
# matcher is deliberately conservative: anything unmatched returns False and
# is treated as trading-related, never silently folded into deposits.
_CAPITAL_MOVEMENT_MARKERS: tuple[str, ...] = (
    "deposit",
    "withdraw",
    "fund in",
    "fund out",
    "fund transfer",
    "cash transfer",
    "transfer in",
    "transfer out",
)


def _to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_timestamp(value) -> "datetime | None":
    """Parse an ISO-8601-ish broker timestamp into a naive datetime.

    Returns None when the value is missing/unparseable — callers must treat
    None as unknown, never substitute another timestamp.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


# ── Fill money math ──────────────────────────────────────────────────────


def gross_premium_dollars(price_per_contract: float, qty: float, multiplier: int = CONTRACT_MULTIPLIER) -> float:
    """Dollar value of one fill: per-contract price × contracts × multiplier."""
    return _to_float(price_per_contract) * _to_float(qty) * multiplier


def net_event_pnl(premium_in: float, premium_out: float, fees: float) -> float:
    """Option-leg net P&L: premium collected − premium paid back − fees."""
    return _to_float(premium_in) - _to_float(premium_out) - _to_float(fees)


def slippage_dollars(
    fill_price: float,
    reference_price: float,
    qty: float,
    side: str,
    multiplier: int = CONTRACT_MULTIPLIER,
) -> float:
    """Signed slippage of a fill vs. the reference (quoted) price, in dollars.

    Positive = favorable (sold above the quote / bought back below it).
    ``side`` is normalized to BUY or SELL (short-premium entries are SELLs).
    """
    side_norm = str(side or "").upper()
    if "SELL" in side_norm:
        signed_diff = _to_float(fill_price) - _to_float(reference_price)
    elif "BUY" in side_norm:
        signed_diff = _to_float(reference_price) - _to_float(fill_price)
    else:
        return 0.0
    return signed_diff * _to_float(qty) * multiplier


def leakage_dollars(
    fill_price: float,
    reference_price: float,
    qty: float,
    side: str,
    multiplier: int = CONTRACT_MULTIPLIER,
) -> float:
    """Unfavorable part of slippage: ≥ 0 dollars lost to execution, else 0."""
    slippage = slippage_dollars(fill_price, reference_price, qty, side, multiplier)
    return max(0.0, -slippage)


# ── Combined per-recommendation outcome ──────────────────────────────────


def share_leg_pnl(basis_price, exit_price, shares: float, fees: float = 0.0):
    """Underlying share P&L, or None when the cost basis is unknown.

    ``basis_price`` may be the assignment strike (shares delivered at strike)
    or the recorded purchase basis. A share leg without a known basis is an
    explicitly UNKNOWN outcome — never fabricated from a market price.
    """
    if basis_price is None or str(basis_price).strip() == "":
        return None
    return (_to_float(exit_price) - _to_float(basis_price)) * _to_float(shares) - _to_float(fees)


def combined_outcome(option_leg: dict | None, share_leg: dict | None) -> dict:
    """Combine the option leg and the underlying share leg into one outcome.

    ``option_leg``: {"premium_in", "premium_out", "fees"} or None (no option
    leg observed). ``share_leg``: {"basis_price", "exit_price", "shares",
    "fees"} or None (no share leg observed); ``basis_price`` None ⇒ unknown.

    Rules:
    - any leg present-but-unmeasurable ⇒ the whole outcome is UNKNOWN
      (status "unknown", net_pnl None);
    - measured legs are summed; a None share_leg simply doesn't contribute.
    """
    option_pnl = None
    if option_leg is not None:
        option_pnl = net_event_pnl(
            option_leg.get("premium_in", 0.0),
            option_leg.get("premium_out", 0.0),
            option_leg.get("fees", 0.0),
        )
    share_pnl = None
    if share_leg is not None:
        share_pnl = share_leg_pnl(
            share_leg.get("basis_price"),
            share_leg.get("exit_price", 0.0),
            share_leg.get("shares", 0),
            share_leg.get("fees", 0.0),
        )

    if option_leg is not None and share_leg is not None and share_pnl is None:
        # Share leg exists but its basis is unknown → combined is UNKNOWN.
        return {"status": "unknown", "net_pnl": None}
    if option_leg is None and share_leg is None:
        return {"status": "unknown", "net_pnl": None}
    if option_pnl is None and share_pnl is None:
        return {"status": "unknown", "net_pnl": None}

    total = (option_pnl or 0.0) + (share_pnl or 0.0)
    return {"status": "measured", "net_pnl": total}


# ── Capital-days accounting ──────────────────────────────────────────────


def capital_base_csp(strike: float, contracts: float) -> float:
    """Cash secured by a short put: strike × 100 × contracts."""
    return _to_float(strike) * CONTRACT_MULTIPLIER * _to_float(contracts)


def capital_base_cc(share_price: float, contracts: float) -> float:
    """Capital covered by a short call: share price × 100 × contracts."""
    return _to_float(share_price) * CONTRACT_MULTIPLIER * _to_float(contracts)


def capital_days_from_lots(lots, capital_per_contract: float, as_of=None) -> float:
    """Sum of capital × days over position lots.

    ``lots``: iterable of (entry_ts, exit_ts | None, qty). Open lots accrue
    capital-days up to ``as_of``; closed lots stop at their exit timestamp.
    ``as_of`` is required for open lots — when it is None (or unparseable),
    open lots contribute 0 (an unknown horizon is never fabricated against
    "now", keeping this function deterministic). Timestamps parse via
    ``parse_timestamp``; unparseable entry timestamps contribute 0 days.

    Rules by situation (see module docstring for the full contract):
    - CSP: ``capital_per_contract = strike × 100`` per contract.
    - Covered call: ``capital_per_contract = share price at entry × 100``.
    - Partial close: emit one lot per exited tranche (exit at its fill time)
      plus one open lot for the remainder — each tranche stops accruing at
      its own close.
    - Assignment: keep the same lot open with the same capital base (the cash
      that secured the put becomes the shares); days continue without reset.
    """
    end_default = parse_timestamp(as_of) if as_of is not None else None

    capital_each = _to_float(capital_per_contract)
    if capital_each <= 0:
        return 0.0

    total = 0.0
    for entry_ts, exit_ts, qty in lots or []:
        start = parse_timestamp(entry_ts)
        if start is None:
            continue  # unknown entry time ⇒ unknown holding period, contribute 0
        if exit_ts:
            end = parse_timestamp(exit_ts)
        else:
            end = end_default
        if end is None:
            continue  # open lot without a parseable as_of ⇒ unknown horizon
        days = (end - start).total_seconds() / 86400.0
        if days <= 0:
            continue
        total += capital_each * _to_float(qty) * days
    return total


def owner_efficiency(net_pnl, capital_days):
    """Owner-efficiency metric: net P&L per capital-day (dollars per $-day).

    Returns None when capital-days is unknown/non-positive — an unknown
    denominator is reported as unknown, never flattened to 0.
    """
    days = _to_float(capital_days, 0.0)
    if days <= 0:
        return None
    return _to_float(net_pnl, 0.0) / days


def max_drawdown(realized_pnl_sequence) -> float:
    """Max peak-to-trough drawdown (≥ 0 dollars) over a cumulative P&L series.

    ``realized_pnl_sequence``: closed outcomes in chronological order.
    """
    cumulative = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in realized_pnl_sequence or []:
        cumulative += _to_float(pnl, 0.0)
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    return drawdown


def owner_summary(outcomes) -> dict:
    """Summarize per-recommendation outcomes for the owner-efficiency view.

    ``outcomes``: iterable of {"net_pnl": float | None, "capital_days":
    float | None, "open": bool} — one record per recommendation.

    Returns:
    - ``net_dollars``: sum of measured outcomes (unknowns excluded from the
      sum but counted in ``unknown_count``);
    - ``capital_days``: sum of known capital-days;
    - ``owner_efficiency``: net_dollars / capital_days, or None when
      capital-days is unknown;
    - ``open_losses``: sum of negative measured pnl on still-open positions;
    - ``drawdown``: max drawdown of the cumulative realized series.
    """
    net_dollars = 0.0
    capital_days = 0.0
    has_capital_days = False
    open_losses = 0.0
    realized_sequence = []
    measured = 0
    unknown = 0

    for outcome in outcomes or []:
        if not isinstance(outcome, dict):
            continue
        pnl = outcome.get("net_pnl")
        days = outcome.get("capital_days")
        is_open = bool(outcome.get("open"))
        if pnl is None:
            unknown += 1
        else:
            measured += 1
            pnl = _to_float(pnl, 0.0)
            net_dollars += pnl
            realized_sequence.append(pnl)
            if is_open and pnl < 0:
                open_losses += pnl
        if days is not None:
            days = _to_float(days, 0.0)
            if days > 0:
                capital_days += days
                has_capital_days = True

    return {
        "net_dollars": net_dollars,
        "measured_count": measured,
        "unknown_count": unknown,
        "capital_days": capital_days if has_capital_days else None,
        "owner_efficiency": owner_efficiency(net_dollars, capital_days) if has_capital_days else None,
        "open_losses": open_losses,
        "drawdown": max_drawdown(realized_sequence),
    }


# ── Cash-movement classification ─────────────────────────────────────────


def is_capital_movement_cash_flow(flow_type, remark: str = "") -> bool:
    """Conservatively identify deposit/withdrawal-style cash movements.

    ``cashflow_type`` strings are a server passthrough with no SDK enum;
    until the probe enumerates them, only explicit deposit/withdraw/transfer
    markers classify as capital movement. Unmatched strings return False —
    they are treated as trading-related and excluded from capital math.
    """
    haystack = " ".join([str(flow_type or ""), str(remark or "")]).lower()
    return any(marker in haystack for marker in _CAPITAL_MOVEMENT_MARKERS)
