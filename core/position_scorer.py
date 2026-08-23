"""Open-position scoring for the exit playbook.

``score_existing_position`` scores a held short option (entry details + live
quote) and assigns the deterministic HOLD/TAKE_PROFIT/ROLL/CLOSE verdict.
Candidate-contract scoring lives in ``wheel_decision.score_contract``; this
module exists because open positions have divergent responsibilities (entry
credit, profit capture, roll windows).

Extracted from ``core/wheel_decision.py`` (F-S1).
"""

from __future__ import annotations

import logging

from core.connection_constants import _normalize_iv
from core.exit_playbook import ExitThresholds, captured_profit_pct_for_short, evaluate_exit
from core.scoring_factors import (
    _calculate_mid_price,
    _compute_expected_move_buffer,
    _compute_profit_target_progress,
    _compute_roll_pressure,
    _compute_size_fit,
)
from core.wheel_decision import WheelDecision

logger = logging.getLogger("core.position_scorer")


def score_existing_position(
    ticker: str,
    position_data: dict,
    current_stock_price: float,
    portfolio_context: dict,
    iv_env_adjustment: float = 0.0,
    iv_rank: float = 0.0,
    iv_status_str: str = "normal",
    earnings_adjustment: float = 0.0,
    earnings_info: dict | None = None,
) -> WheelDecision:
    """
    Score an existing open option position for roll/hold/close decisions.

    Unlike score_contract(), this works with position data (entry details,
    current market price, etc.) rather than candidate contracts.
    """
    earnings_info = earnings_info or {}

    option_type = str(position_data.get("option_type", "") or "").upper()
    strike = float(position_data.get("strike", 0) or 0)
    expiration = str(position_data.get("expiration", "") or "")
    dte = int(position_data.get("dte", 0) or 0)

    # Current market data
    bid = float(position_data.get("bid", 0) or 0)
    ask = float(position_data.get("ask", 0) or 0)
    last = float(position_data.get("last", 0) or 0)
    mid_price = _calculate_mid_price(bid, ask, last)
    premium_per_contract = mid_price * 100

    # Greeks
    delta = float(position_data.get("delta", 0) or 0)
    theta = float(position_data.get("theta", 0) or 0)
    iv = _normalize_iv(position_data.get("implied_volatility", 0))

    # Extrinsic value approximation: option price - intrinsic
    if option_type == "CALL":
        intrinsic = max(current_stock_price - strike, 0)
    else:
        intrinsic = max(strike - current_stock_price, 0)
    extrinsic = max(mid_price - intrinsic, 0)

    # OTM %
    if current_stock_price > 0:
        if option_type == "CALL":
            otm_pct = ((strike - current_stock_price) / current_stock_price) * 100
        else:
            otm_pct = ((current_stock_price - strike) / current_stock_price) * 100
    else:
        otm_pct = 0.0

    decision = WheelDecision(
        ticker=ticker,
        option_type=option_type,
        strike=strike,
        expiration=expiration,
        dte=dte,
        stock_price=current_stock_price,
        bid=bid,
        ask=ask,
        mid_price=round(mid_price, 4),
        premium_per_contract=round(premium_per_contract, 2),
        delta=round(delta, 5),
        theta=round(theta, 5),
        implied_volatility=round(iv, 2),
        extrinsic_remaining=round(extrinsic, 2),
        otm_pct=round(otm_pct, 2),
        iv_rank=round(iv_rank * 100, 1),
        iv_status=iv_status_str,
        iv_env_adjustment=iv_env_adjustment,
        earnings_adjustment=earnings_adjustment,
        vix_regime=portfolio_context.get("vix_regime", {}).get("regime", "normal"),
        vix_level=portfolio_context.get("vix_regime", {}).get("vix", 20.0),
    )

    # Compute roll pressure
    decision.roll_pressure = _compute_roll_pressure(decision)

    # Compute profit target progress
    decision.profit_target_progress = _compute_profit_target_progress(decision)

    # Exit playbook verdict (deterministic rules, preset-driven thresholds).
    entry_credit = float(position_data.get("avg_cost", 0) or 0)
    decision.exit_verdict, decision.exit_reasons = _evaluate_position_exit(
        decision,
        entry_credit_per_contract=entry_credit,
        earnings_info=earnings_info or {},
        thresholds=ExitThresholds(
            profit_take_pct=float(portfolio_context.get("exit_profit_take_pct", 50.0) or 50.0),
            roll_dte=int(portfolio_context.get("exit_roll_dte", 21) or 21),
            exit_delta=float(portfolio_context.get("exit_delta", 0.65) or 0.65),
            deep_itm_pct=float(portfolio_context.get("exit_deep_itm_pct", 15.0) or 15.0),
        ),
    )

    # Size fit
    decision.size_fit = _compute_size_fit(decision, portfolio_context)

    # Expected move buffer
    decision.expected_move_buffer = _compute_expected_move_buffer(decision)

    # Simple warnings
    if decision.dte <= 7:
        decision.warnings.append(f"Only {decision.dte} DTE remaining")
    if decision.roll_pressure >= 70:
        decision.warnings.append(f"High roll pressure ({decision.roll_pressure:.0f}%)")
    if otm_pct < 5 and otm_pct >= 0:
        decision.warnings.append(f"Approaching strike ({otm_pct:.1f}% OTM)")
    elif otm_pct < 0:
        decision.warnings.append(f"Strike crossed ({abs(otm_pct):.1f}% ITM)")

    logger.info(
        "score_existing_position ticker=%s type=%s strike=%.2f exp=%s dte=%d "
        "roll_pressure=%.1f profit_progress=%.1f extrinsic=%.2f",
        ticker,
        option_type,
        strike,
        expiration,
        dte,
        decision.roll_pressure,
        decision.profit_target_progress,
        extrinsic,
    )
    return decision


def _evaluate_position_exit(
    decision: WheelDecision,
    entry_credit_per_contract: float,
    earnings_info: dict,
    thresholds: ExitThresholds | None = None,
):
    """Bridge a scored open position into the exit playbook.

    Returns (verdict, reasons). Days-to-earnings prefers the enriched earnings
    info, then whatever the decision already carries. Entry credit unknown ->
    profit-take rule cannot fire (explicitly modeled as None).
    """
    days_to_earnings = None
    for source in (
        earnings_info.get("days_to_earnings") if isinstance(earnings_info, dict) else None,
        decision.days_to_earnings,
    ):
        if source is not None:
            try:
                days_to_earnings = int(source)
                break
            except (TypeError, ValueError):
                continue

    captured = captured_profit_pct_for_short(
        entry_credit_per_contract=entry_credit_per_contract,
        current_mark_per_contract=decision.mid_price,
    )
    verdict = evaluate_exit(
        option_type=decision.option_type,
        dte=int(decision.dte or 0),
        delta=float(decision.delta or 0),
        otm_pct=float(decision.otm_pct or 0),
        captured_profit_pct=captured,
        days_to_earnings=days_to_earnings,
        thresholds=thresholds,
    )
    return verdict.verdict, verdict.reasons
