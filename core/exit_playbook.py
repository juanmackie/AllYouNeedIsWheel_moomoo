"""Deterministic exit-rule engine for short option positions.

Given the observable state of an open short option (DTE, delta, OTM distance,
credit captured vs entry, earnings timing), return one verdict:

    HOLD | TAKE_PROFIT | ROLL | CLOSE

plus ranked human-readable reasons. Pure and broker-free so tests exercise it
without connectivity. Thresholds come from the active wheel preset where
available; defaults encode the classic wheel playbook.

Rules fire in priority order — first match wins, remaining rules become
context notes:

1. CLOSE       — earnings land before expiry while the position is at risk
                 (ITM or within 5% of the strike).
2. CLOSE       — deeply ITM beyond ``deep_itm_pct`` (capital at risk dominates).
3. CLOSE       — |delta| >= ``exit_delta`` (market has moved against the trade).
4. TAKE_PROFIT — captured >= ``profit_take_pct``% of the entry credit.
5. ROLL        — DTE <= ``roll_dte`` while still safely OTM.
6. HOLD        — nothing triggered; proximity warnings ride along.

Note: early-assignment/dividend risk for ITM short calls is intentionally NOT
modeled — no free-tier Moomoo dividend feed exists, and fabricating one would
violate the broker-truth contract. ITM short calls surface through rules 2–3.
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
    thresholds: ExitThresholds | None = None,
) -> ExitVerdict:
    """Evaluate exit rules for one open short option position.

    Args:
        option_type: "CALL" or "PUT".
        dte: days to expiration (>= 0).
        delta: signed greek delta; magnitude is used.
        otm_pct: positive = distance OTM, negative = amount ITM.
        captured_profit_pct: % of entry credit captured so far, or None when
            entry price is unknown (rule then can't fire).
        days_to_earnings: calendar days until next earnings, or None.
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
    """% of the original credit captured by buying back at the current mark.

    Returns None when the entry credit is unknown/zero — callers should treat
    None as "profit-take rule cannot fire", never as 100% or 0%.
    """
    entry = float(entry_credit_per_contract or 0)
    if entry <= 0:
        return None
    mark = max(float(current_mark_per_contract or 0), 0.0)
    return round(max((entry - mark) / entry, 0.0) * 100.0, 1)
