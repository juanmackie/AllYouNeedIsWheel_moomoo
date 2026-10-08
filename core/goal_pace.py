"""Flow-adjusted, chain-linked progress toward a durable account goal."""

from __future__ import annotations

import math
from datetime import date, datetime, time

from core.outcome_attribution import is_capital_movement_cash_flow
from core.utils import market_now

MIN_GOAL_OBSERVATION_DAYS = 60.0


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed


def _flow_value(flow):
    if not is_capital_movement_cash_flow(flow.get("flow_type"), flow.get("remark", "")):
        return 0.0
    try:
        amount = abs(float(flow.get("amount", 0) or 0))
    except (TypeError, ValueError):
        return 0.0
    direction = str(flow.get("direction", "")).upper()
    return -amount if "OUT" in direction or "WITHDRAW" in direction else amount


def goal_pace(
    snapshots,
    cash_flows,
    *,
    baseline_nav,
    baseline_date,
    target_multiple=5.0,
    horizon_end="2029-08-22",
    as_of=None,
    flows_complete=True,
):
    """Estimate time-weighted progress with Modified-Dietz subperiod returns.

    Only explicit deposits/withdrawals are neutralized. Flows are weighted by
    the fraction of each snapshot interval for which the contributed capital
    was invested, then interval returns are geometrically chain-linked.
    """
    base_nav = _number(baseline_nav)
    base_day = _date(baseline_date)
    end_day = _date(horizon_end)
    today = _date(as_of) if as_of is not None else market_now().date()
    rows = []
    for snapshot in snapshots or []:
        if not isinstance(snapshot, dict):
            continue
        timestamp = _timestamp(snapshot.get("captured_at"))
        nav = _number(snapshot.get("net_liquidation"))
        if timestamp and nav is not None and nav > 0:
            rows.append((timestamp, nav))
    rows.sort(key=lambda row: row[0])
    flows = []
    for flow in cash_flows or []:
        if not isinstance(flow, dict):
            continue
        timestamp = _timestamp(flow.get("clearing_date") or flow.get("settlement_date"))
        amount = _flow_value(flow)
        if timestamp and amount:
            flows.append((timestamp, amount))
    flows.sort(key=lambda row: row[0])

    if base_nav is None or base_nav <= 0 or base_day is None or end_day is None:
        return _empty_result(
            baseline_nav=base_nav if base_nav is not None else 0.0,
            baseline_date=str(baseline_date) if base_day is not None else None,
            flows_complete=flows_complete,
        )
    baseline_stamp = next((stamp for stamp, _ in rows if stamp.date() >= base_day), None)
    if baseline_stamp is None:
        return _empty_result(baseline_nav=base_nav, baseline_date=str(baseline_date), flows_complete=flows_complete)

    if not flows_complete:
        last_time, current_nav = rows[-1]
        observation_days = max(0.0, (last_time.date() - base_day).days)
        return _incomplete_result(
            base_nav,
            current_nav,
            baseline_date,
            last_time,
            target_multiple,
            horizon_end,
            observation_days,
        )

    previous_time = next((stamp for stamp, _ in rows if stamp.date() == base_day), None)
    if previous_time is None:
        previous_time = datetime.combine(base_day, time.min, tzinfo=baseline_stamp.tzinfo)
    flows = [
        (stamp.replace(tzinfo=baseline_stamp.tzinfo) if stamp.tzinfo is None else stamp, amount)
        for stamp, amount in flows
    ]
    previous_nav = base_nav
    index = 1.0
    used_periods = 0
    for timestamp, nav in rows:
        if timestamp <= previous_time:
            continue
        total_days = (timestamp - previous_time).total_seconds() / 86400.0
        period_flows = [(when, amount) for when, amount in flows if previous_time < when <= timestamp]
        flow_total = sum(amount for _, amount in period_flows)
        weighted_flows = 0.0
        if total_days > 0:
            for when, amount in period_flows:
                remaining = max(0.0, min(total_days, (timestamp - when).total_seconds() / 86400.0))
                weighted_flows += amount * remaining / total_days
        denominator = previous_nav + weighted_flows
        if denominator > 0:
            period_return = (nav - previous_nav - flow_total) / denominator
            if math.isfinite(period_return) and period_return > -1.0:
                index *= 1.0 + period_return
                used_periods += 1
        previous_time, previous_nav = timestamp, nav

    last_time, current_nav = rows[-1]
    observation_days = max(0.0, (last_time.date() - base_day).days)
    reached = index >= float(target_multiple)
    realized_cagr = None
    if observation_days >= MIN_GOAL_OBSERVATION_DAYS and index > 0:
        try:
            realized_cagr = index ** (365.25 / observation_days) - 1.0
        except (OverflowError, ZeroDivisionError):
            realized_cagr = None
        if realized_cagr is not None and not math.isfinite(realized_cagr):
            realized_cagr = None
    years_left = max(0.0, (end_day - today).days / 365.25)
    required_cagr = None
    if reached:
        required_cagr = 0.0
    elif years_left > 0 and index > 0:
        try:
            required_cagr = (float(target_multiple) / index) ** (1.0 / years_left) - 1.0
        except (OverflowError, ZeroDivisionError):
            required_cagr = None
    gap_pp = (realized_cagr - required_cagr) * 100 if realized_cagr is not None and required_cagr is not None else None
    years_at_pace = None
    if reached:
        years_at_pace = 0.0
    elif realized_cagr is not None and realized_cagr > 0 and index > 0:
        years_at_pace = math.log(float(target_multiple) / index) / math.log1p(realized_cagr)
    progress_pct = 100.0 * (index - 1.0) / (float(target_multiple) - 1.0) if target_multiple > 1 else 100.0
    target_nav = base_nav * float(target_multiple)
    daily_required_rate = (1.0 + required_cagr) ** (1.0 / 365.25) - 1.0 if required_cagr is not None else None
    required_premium_per_day = max(0.0, current_nav * daily_required_rate) if daily_required_rate is not None else None
    return {
        "baseline_nav": round(base_nav, 2),
        "current_nav": round(current_nav, 2),
        "baseline_date": str(baseline_date),
        "last_captured_at": last_time.isoformat(),
        "target_multiple": float(target_multiple),
        "horizon_end": str(horizon_end),
        "goal_index": round(index, 8),
        "target_nav": round(target_nav, 2),
        "progress_pct": round(max(0.0, min(100.0, progress_pct)), 2),
        "reached": reached,
        "target_reached": reached,
        "observation_days": observation_days,
        "observation_ready": observation_days >= MIN_GOAL_OBSERVATION_DAYS,
        "method": "modified_dietz_chain_linked",
        "periods_with_snapshots": used_periods,
        "required_cagr": None if required_cagr is None else round(required_cagr, 6),
        "realized_cagr": None if realized_cagr is None else round(realized_cagr, 6),
        "annualized_pace": None if realized_cagr is None else round(realized_cagr, 6),
        "gap_pp": None if gap_pp is None else round(gap_pp, 2),
        "years_at_current_pace": None if years_at_pace is None else round(years_at_pace, 2),
        "eta_days": None if years_at_pace is None else round(years_at_pace * 365.25, 1),
        "required_premium_per_day": None if required_premium_per_day is None else round(required_premium_per_day, 2),
        "on_track": None if realized_cagr is None or required_cagr is None else realized_cagr >= required_cagr,
        "flows_complete": True,
        "confidence_state": "complete",
    }


def _number(value):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _incomplete_result(base_nav, current_nav, baseline_date, last_time, target_multiple, horizon_end, observation_days):
    target_multiple = float(target_multiple)
    return {
        "baseline_nav": round(base_nav, 2),
        "current_nav": round(current_nav, 2),
        "baseline_date": str(baseline_date),
        "last_captured_at": last_time.isoformat(),
        "target_multiple": target_multiple,
        "horizon_end": str(horizon_end),
        "goal_index": None,
        "target_nav": round(base_nav * target_multiple, 2),
        "progress_pct": None,
        "reached": None,
        "target_reached": None,
        "observation_days": observation_days,
        "observation_ready": False,
        "method": "modified_dietz_chain_linked",
        "periods_with_snapshots": 0,
        "required_cagr": None,
        "realized_cagr": None,
        "annualized_pace": None,
        "gap_pp": None,
        "years_at_current_pace": None,
        "eta_days": None,
        "required_premium_per_day": None,
        "on_track": None,
        "flows_complete": False,
        "confidence_state": "flows_incomplete",
    }


def _empty_result(baseline_nav=0.0, baseline_date=None, flows_complete=True):
    return {
        "baseline_nav": baseline_nav,
        "current_nav": None,
        "baseline_date": baseline_date,
        "target_multiple": None,
        "horizon_end": None,
        "goal_index": None,
        "target_nav": None,
        "progress_pct": None,
        "reached": None,
        "target_reached": None,
        "observation_days": 0,
        "observation_ready": False,
        "method": "modified_dietz_chain_linked",
        "periods_with_snapshots": 0,
        "required_cagr": None,
        "realized_cagr": None,
        "annualized_pace": None,
        "gap_pp": None,
        "years_at_current_pace": None,
        "eta_days": None,
        "required_premium_per_day": None,
        "on_track": None,
        "flows_complete": bool(flows_complete),
        "confidence_state": "insufficient_history" if flows_complete else "flows_incomplete",
    }
