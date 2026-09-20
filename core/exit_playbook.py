"""Deterministic exit-rule engine for short option positions.

Given the observable state of an open short option (DTE, delta, OTM distance,
credit captured vs entry, earnings and ex-dividend timing), return one verdict:

    HOLD | TAKE_PROFIT | ROLL | CLOSE

plus ranked human-readable reasons. Pure and broker-free so tests exercise it
without connectivity. Thresholds come from the active wheel preset where
available; defaults encode the classic wheel playbook.

Rules fire in priority order — first match wins, remaining rules become
context notes:

1. CLOSE       — earnings land before expiry while the position is at risk
                 (ITM or within 5% of the strike).
1b. CLOSE      — ex-dividend lands before expiry on an ITM short CALL:
                 assignment before ex-div is likely, so the shares are called
                 away before the dividend is captured (roll past ex-div or
                 close). Dividends drive early exercise of calls only, so a
                 short PUT never triggers this rule; a near-OTM call (within 5%)
                 only gets a context note.
2. CLOSE       — deeply ITM beyond ``deep_itm_pct`` (capital at risk dominates).
3. CLOSE       — |delta| >= ``exit_delta`` (market has moved against the trade).
4. TAKE_PROFIT — captured >= ``profit_take_pct``% of the entry credit.
4b. CLOSE      — loss stop: captured <= ``stop_loss_pct`` (default -100%,
                 i.e. the mark reached 2x the entry credit). Fires before the
                 roll window so an already-losing position cannot be quietly
                 rolled.
5. ROLL        — DTE <= ``roll_dte`` while still safely OTM.
6. HOLD        — nothing triggered; proximity warnings ride along.

Ex-dividend modelling scope: the enrichment supplies the ex-dividend *date*
(yfinance), not the dividend *amount*, so this rule keys on "ITM with ex-div
before expiry" rather than the exact extrinsic-value-vs-dividend comparison
that decides early exercise. The unmodelled half is deliberate: fabricating a
payout figure would violate the broker-truth contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field

VERDICT_HOLD = "HOLD"
VERDICT_TAKE_PROFIT = "TAKE_PROFIT"
VERDICT_ROLL = "ROLL"
VERDICT_CLOSE = "CLOSE"


@dataclass(frozen=True)
class ExitThresholds:
    """Preset-driven exit thresholds (percent units where noted)."""

    profit_take_pct: float = 50.0  # % of entry credit captured -> buy back
    roll_dte: int = 21  # roll window opens at this DTE
    exit_delta: float = 0.65  # |delta| at which the position is closed
    deep_itm_pct: float = 15.0  # ITM beyond this % -> close
    stop_loss_pct: float = -100.0  # captured % at or below -> close (-100 = 2x entry credit)


@dataclass
class ExitVerdict:
    verdict: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "reasons": list(self.reasons)}


def evaluate_exit(
    option_type: str,
    dte: int,
    delta: float,
    otm_pct: float,
    captured_profit_pct: float | None = None,
    days_to_earnings: int | None = None,
    days_to_ex_dividend: int | None = None,
    thresholds: ExitThresholds | None = None,
) -> ExitVerdict:
    """Evaluate exit rules for one open short option position.

    Args:
        option_type: "CALL" or "PUT".
        dte: days to expiration (>= 0).
        delta: signed greek delta; magnitude is used.
        otm_pct: positive = distance OTM, negative = amount ITM.
        captured_profit_pct: signed % of entry credit captured so far (negative
            = the short is now worth more than it was sold for), or None when
            entry price is unknown (rules 4 and 4b then cannot fire).
        days_to_earnings: calendar days until next earnings, or None.
        days_to_ex_dividend: calendar days until the next ex-dividend date, or
            None when unknown. Only meaningful for short CALLs.
        thresholds: preset overrides; defaults when omitted.
    """
    t = thresholds or ExitThresholds()
    abs_delta = abs(float(delta or 0))
    otm = float(otm_pct or 0)
    is_itm = otm < 0
    reasons: list[str] = []

    def _verdict(verdict: str) -> ExitVerdict:
        return ExitVerdict(verdict=verdict, reasons=reasons)

    # 1. Earnings before expiry while at risk.
    if days_to_earnings is not None and 0 <= days_to_earnings <= max(dte, 0):
        if is_itm or otm < 5.0:
            reasons.append(
                f"Earnings in {days_to_earnings}d falls before expiry ({dte}d) and the "
                f"position is {'ITM' if is_itm else f'only {otm:.1f}% OTM'}"
            )
            return _verdict(VERDICT_CLOSE)
        reasons.append(f"Earnings in {days_to_earnings}d lands inside this contract's life")

    # 1b. Ex-dividend before expiry on an ITM short call. Dividend-driven early
    # exercise only affects calls; assignment before ex-div forfeits the payout.
    if option_type == "CALL" and days_to_ex_dividend is not None and 0 <= days_to_ex_dividend <= max(dte, 0):
        if is_itm:
            reasons.append(
                f"Ex-dividend in {days_to_ex_dividend}d falls before expiry ({dte}d) and the call is "
                f"ITM by {abs(otm):.1f}% — assignment before ex-div is likely; roll past ex-div or close"
            )
            return _verdict(VERDICT_CLOSE)
        if otm < 5.0:
            reasons.append(
                f"Ex-dividend in {days_to_ex_dividend}d lands before expiry while only {otm:.1f}% OTM — "
                f"assignment risk if it crosses"
            )

    # 2. Deeply ITM.
    if is_itm and abs(otm) >= t.deep_itm_pct:
        reasons.append(f"Deeply ITM by {abs(otm):.1f}% (threshold {t.deep_itm_pct:.0f}%)")
        return _verdict(VERDICT_CLOSE)

    # 3. Delta breached.
    if abs_delta >= t.exit_delta:
        reasons.append(f"|Delta| {abs_delta:.2f} breached exit level {t.exit_delta:.2f}")
        return _verdict(VERDICT_CLOSE)

    # 4. Profit target captured.
    if captured_profit_pct is not None and captured_profit_pct >= t.profit_take_pct > 0:
        reasons.append(f"{captured_profit_pct:.0f}% of entry credit captured (target {t.profit_take_pct:.0f}%)")
        return _verdict(VERDICT_TAKE_PROFIT)

    # 4b. Loss stop, before the roll window so a losing position cannot be
    # silently rolled instead of closed.
    if captured_profit_pct is not None and captured_profit_pct <= t.stop_loss_pct < 0:
        multiple = (abs(captured_profit_pct) / 100.0) + 1.0
        reasons.append(
            f"Loss stop: mark is {multiple:.1f}x entry credit "
            f"({captured_profit_pct:.0f}% captured, stop {t.stop_loss_pct:.0f}%)"
        )
        return _verdict(VERDICT_CLOSE)

    # 5. Roll window for a safe OTM position.
    if 0 <= dte <= t.roll_dte and not is_itm:
        reasons.append(f"DTE {dte} entered roll window (<= {t.roll_dte}) while {otm:.1f}% OTM")
        return _verdict(VERDICT_ROLL)

    # 6. Hold, with context notes.
    if captured_profit_pct is not None:
        reasons.append(f"{captured_profit_pct:.0f}% of credit captured so far")
    if is_itm:
        reasons.append(f"ITM by {abs(otm):.1f}% — monitor closely")
    elif otm < 5.0:
        reasons.append(f"Only {otm:.1f}% OTM — strike proximity watch")
    if dte <= 7:
        reasons.append(f"Only {dte} DTE remaining")
    if not reasons:
        reasons.append(f"Within plan: {otm:.1f}% OTM, {dte} DTE, |delta| {abs_delta:.2f}")
    return _verdict(VERDICT_HOLD)


def captured_profit_pct_for_short(entry_credit_per_contract: float, current_mark_per_contract: float) -> float | None:
    """Signed % of the original credit captured by buying back at the current mark.

    Positive means the short is winning (the buy-back is cheaper than the
    credit taken in); negative means it is losing (the buy-back is more
    expensive). The value is deliberately NOT floored at 0: the loss-stop rule
    needs the real magnitude of a losing position.

    Returns None when the entry credit is unknown/zero — callers should treat
    None as "profit-take and loss-stop rules cannot fire", never as 100% or 0%.
    """
    entry = float(entry_credit_per_contract or 0)
    if entry <= 0:
        return None
    mark = max(float(current_mark_per_contract or 0), 0.0)
    return round(((entry - mark) / entry) * 100.0, 1)
