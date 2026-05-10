"""
Read-only earnings volatility signal scoring.

This module deliberately produces plain signal labels rather than trade orders.
It is meant to help the UI say "worth researching" or "avoid" in simple terms.
"""

from dataclasses import asdict, dataclass, field
from typing import Optional


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


@dataclass
class EarningsVolSignal:
    ticker: str
    signal: str
    label: str
    score: float
    earnings_date: Optional[str] = None
    days_to_earnings: Optional[int] = None
    time_of_day: Optional[str] = None
    earnings_source: Optional[str] = None
    front_expiration: Optional[str] = None
    back_expiration: Optional[str] = None
    front_iv: Optional[float] = None
    back_iv: Optional[float] = None
    iv_edge: Optional[float] = None
    term_structure_ratio: Optional[float] = None
    rv30: Optional[float] = None
    iv_rv_ratio: Optional[float] = None
    avg_volume_30d: Optional[float] = None
    atm_strike: Optional[float] = None
    estimated_calendar_debit: Optional[float] = None
    max_risk_per_contract: Optional[float] = None
    spread_pct: Optional[float] = None
    open_interest: Optional[int] = None
    option_volume: Optional[int] = None
    structure: str = "ATM calendar"
    entry_plan: str = ""
    exit_plan: str = ""
    profit_target: str = ""
    invalidation: str = ""
    notes: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _has_complete_trade_plan(metrics: dict) -> bool:
    return all([
        metrics.get("atm_strike") is not None,
        bool(metrics.get("front_expiration")),
        bool(metrics.get("back_expiration")),
        metrics.get("estimated_calendar_debit") is not None,
    ])


def _build_plan_text(signal: str, days, blockers, incomplete_plan_fields: bool = False):
    too_early = days is not None and days > 10

    if signal == "AVOID" and blockers:
        entry_plan = "Not actionable - resolve blockers first"
        exit_plan = "N/A"
        profit_target = "N/A"
        invalidation = "; ".join(blockers[:2])
    elif incomplete_plan_fields:
        entry_plan = "Waiting on strike, expirations, or debit"
        exit_plan = "Close after earnings IV crush"
        profit_target = "Target 20-40% where liquidity allows"
        invalidation = "Missing strike, expirations, or debit"
    elif too_early:
        entry_plan = "Revisit within 10d of earnings"
        exit_plan = "Close after earnings IV crush"
        profit_target = "Target 20-40% where liquidity allows"
        invalidation = "Spread widens, IV edge reverses, stock gaps beyond ATM strike"
    else:
        entry_plan = "Enter while front IV premium is positive"
        exit_plan = "Close after earnings IV crush"
        profit_target = "Target 20-40% where liquidity allows"
        invalidation = "Spread widens, IV edge reverses, stock gaps beyond ATM strike"

    return "ATM calendar", entry_plan, exit_plan, profit_target, invalidation


def classify_earnings_vol_signal(metrics: dict) -> EarningsVolSignal:
    """
    Convert raw earnings-vol metrics into a simple read-only signal.

    The scoring intentionally favors defined-risk calendar candidates:
    near earnings, front IV above back IV, IV above recent realized volatility,
    and tradable liquidity.
    """
    ticker = str(metrics.get("ticker", "")).upper()
    days = metrics.get("days_to_earnings")
    front_iv = metrics.get("front_iv")
    back_iv = metrics.get("back_iv")
    rv30 = metrics.get("rv30")
    avg_volume = metrics.get("avg_volume_30d") or 0
    spread_pct = metrics.get("spread_pct")
    open_interest = metrics.get("open_interest") or 0
    option_volume = metrics.get("option_volume") or 0

    blockers: list[str] = []
    notes: list[str] = []

    if days is None:
        blockers.append("No confirmed earnings date")
    elif days < 0:
        blockers.append("Earnings already passed")
    elif days > 10:
        notes.append(f"Too early - earnings {days}d away")

    if not front_iv or not back_iv:
        blockers.append("Missing front/back IV")

    iv_edge = None
    term_structure_ratio = None
    if front_iv and back_iv and back_iv > 0:
        iv_edge = front_iv - back_iv
        term_structure_ratio = front_iv / back_iv
        if iv_edge <= 0:
            blockers.append("Front IV is not elevated over back IV")
        elif term_structure_ratio >= 1.25:
            notes.append("Strong earnings IV premium")
        elif term_structure_ratio >= 1.12:
            notes.append("Moderate earnings IV premium")
        else:
            notes.append("Small earnings IV premium")

    iv_rv_ratio = None
    if front_iv and rv30 and rv30 > 0:
        iv_rv_ratio = front_iv / rv30
        if iv_rv_ratio >= 1.35:
            notes.append("IV is rich versus recent realized volatility")
        elif iv_rv_ratio < 1.05:
            notes.append("IV/RV edge is weak")
    elif front_iv:
        notes.append("Realized volatility unavailable")

    if avg_volume < 1_000_000:
        notes.append("Stock volume is below the ideal signal threshold")
    if spread_pct is not None and spread_pct > 18:
        blockers.append("Options spread is too wide")
    elif spread_pct is not None and spread_pct > 10:
        notes.append("Options spread is acceptable but not tight")
    if open_interest < 250 and option_volume < 50:
        notes.append("Options liquidity is thin")

    score = 0.0
    if days is not None:
        if 0 <= days <= 3:
            score += 20
        elif days <= 7:
            score += 16
        elif days <= 10:
            score += 8

    if term_structure_ratio:
        score += _clamp((term_structure_ratio - 1.0) / 0.45 * 30)
    if iv_rv_ratio:
        score += _clamp((iv_rv_ratio - 1.0) / 0.6 * 20)
    if avg_volume:
        score += _clamp(avg_volume / 3_000_000 * 15)
    if spread_pct is not None:
        score += _clamp((20 - spread_pct) / 20 * 10)
    if open_interest or option_volume:
        score += _clamp(max(open_interest / 1000, option_volume / 250) * 5)

    incomplete_plan_fields = not _has_complete_trade_plan(metrics)
    if incomplete_plan_fields:
        blockers.append("Incomplete trade plan fields")

    hard_blockers = [blocker for blocker in blockers if blocker != "Incomplete trade plan fields"]
    too_early = days is not None and days > 10

    if hard_blockers:
        score = min(score, 39)
    elif incomplete_plan_fields or too_early:
        score = min(score, 59)

    score = round(_clamp(score), 1)
    if hard_blockers:
        signal = "AVOID"
        label = "Avoid"
    elif incomplete_plan_fields:
        signal = "WATCH"
        label = "Watch"
    elif too_early:
        signal = "WATCH"
        label = "Watch"
    elif score >= 75:
        signal = "GREEN"
        label = "Qualified"
    elif score >= 60:
        signal = "YELLOW"
        label = "Consider"
    elif score >= 40:
        signal = "WATCH"
        label = "Watch"
    else:
        signal = "AVOID"
        label = "Avoid"

    structure, entry_plan, exit_plan, profit_target, invalidation = _build_plan_text(
        signal, days, blockers, incomplete_plan_fields=incomplete_plan_fields,
    )

    return EarningsVolSignal(
        ticker=ticker,
        signal=signal,
        label=label,
        score=score,
        earnings_date=metrics.get("earnings_date"),
        days_to_earnings=days,
        time_of_day=metrics.get("time_of_day"),
        earnings_source=metrics.get("earnings_source"),
        front_expiration=metrics.get("front_expiration"),
        back_expiration=metrics.get("back_expiration"),
        front_iv=round(front_iv, 4) if front_iv is not None else None,
        back_iv=round(back_iv, 4) if back_iv is not None else None,
        iv_edge=round(iv_edge, 4) if iv_edge is not None else None,
        term_structure_ratio=round(term_structure_ratio, 3) if term_structure_ratio is not None else None,
        rv30=round(rv30, 4) if rv30 is not None else None,
        iv_rv_ratio=round(iv_rv_ratio, 3) if iv_rv_ratio is not None else None,
        avg_volume_30d=round(avg_volume, 0) if avg_volume else None,
        atm_strike=metrics.get("atm_strike"),
        estimated_calendar_debit=metrics.get("estimated_calendar_debit"),
        max_risk_per_contract=metrics.get("max_risk_per_contract"),
        spread_pct=round(spread_pct, 1) if spread_pct is not None else None,
        open_interest=int(open_interest) if open_interest else None,
        option_volume=int(option_volume) if option_volume else None,
        structure=structure,
        entry_plan=entry_plan,
        exit_plan=exit_plan,
        profit_target=profit_target,
        invalidation=invalidation,
        notes=notes[:4],
        blockers=blockers,
    )
