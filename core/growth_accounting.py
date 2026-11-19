"""Flow-adjusted growth accounting (pure, no I/O).

The 5x goal has two different questions:

* **Account size** — did net liquidation reach ``baseline * multiple``? A deposit
  answers yes. ``core.growth_mode.growth_pace`` already measures that.
* **Investment performance** — did the money already in the account grow? Only
  a cash-flow-adjusted return can answer this.

This module answers the second question with a chained Modified-Dietz return.
Every interval between two NAV observations gets

    r = (V1 - V0 - F) / (V0 + sum(amount_i * weight_i))

where ``F`` is the net external capital movement (deposit minus withdrawal)
dated inside the interval and ``weight_i`` is the share of the interval still
available to trade after that movement. Interval returns are chained
multiplicatively into one multiple, so a deposit cannot raise it and a genuine
loss lowers it.

Truth rules:

* Absence of cash-flow evidence is not evidence of no cash flow. Without a
  cash-flow backfill that spans the window, the result is ``unknown`` with a
  named gap — never a silently unadjusted ratio.
* A capital movement without a date, a movement in a currency other than the
  measured one, or a non-positive capital base makes the window unmeasurable.
* ``contributed`` and ``balance_change`` are still reported when they are known
  locally, so the user sees what happened even when the multiple is unknown.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from core.outcome_attribution import is_capital_movement_cash_flow
from core.utils import safe_float

# A broker cash-flow read of "today" is not final until settlement clears; the
# same 4-day lag the account P&L reconciliation uses.
COVERAGE_LAG_DAYS = 4

GAP_UNDATED_CAPITAL_MOVEMENT = "undated_capital_movement"
GAP_UNPARSEABLE_CAPITAL_MOVEMENT = "unparseable_capital_movement_amount"
GAP_UNSUPPORTED_CURRENCY = "unsupported_currency"
GAP_COVERAGE_INCOMPLETE = "cash_flow_coverage_incomplete"
GAP_NONPOSITIVE_CAPITAL_BASE = "nonpositive_capital_base"
GAP_TOO_FEW_OBSERVATIONS = "fewer_than_two_nav_observations"


def _parse_datetime(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_day(value) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _money(value: float) -> float:
    return round(value, 2) + 0.0


def coverage_spans(coverage, start_day: date | None, end_day: date | None) -> bool:
    """True when the cash-flow backfill covers the whole NAV window.

    ``coverage`` is the persisted ``{earliest, latest}`` span of ingested broker
    cash flows. A missing span, a missing boundary, or a span that starts late
    or ends more than ``COVERAGE_LAG_DAYS`` before the window is not coverage.
    """
    if not isinstance(coverage, dict) or start_day is None or end_day is None:
        return False
    earliest = _parse_day(coverage.get("earliest"))
    latest = _parse_day(coverage.get("latest"))
    if earliest is None or latest is None:
        return False
    return earliest <= start_day and latest >= end_day - timedelta(days=COVERAGE_LAG_DAYS)


def _nav_points(nav_points) -> list[dict]:
    """One NAV observation per calendar date, oldest first (last of the day wins)."""
    by_day: dict[date, dict] = {}
    for point in nav_points or ():
        if not isinstance(point, dict):
            continue
        captured_at = _parse_datetime(point.get("captured_at"))
        nav = safe_float(point.get("net_liquidation"), None)
        if captured_at is None or nav is None or nav <= 0:
            continue
        by_day[captured_at.date()] = {
            "day": captured_at.date(),
            "captured_at": str(point.get("captured_at") or ""),
            "nav": nav,
        }
    return [by_day[day] for day in sorted(by_day)]


def _capital_movements(cash_flows) -> tuple[list[dict], list[str]]:
    """Return dated capital movements and the gaps that prevent measurement."""
    movements: list[dict] = []
    gaps: list[str] = []
    for row in cash_flows or ():
        if not isinstance(row, dict):
            continue
        flow_type = str(row.get("flow_type") or "")
        if not is_capital_movement_cash_flow(flow_type, str(row.get("remark") or "")):
            continue
        amount = safe_float(row.get("amount"), None)
        if amount is None:
            # A movement we cannot value makes the window unmeasurable: dropping
            # it silently would let the flow reappear as performance.
            gaps.append(GAP_UNPARSEABLE_CAPITAL_MOVEMENT)
            continue
        movement = {
            "day": _parse_day(row.get("clearing_date")),
            "amount": amount,
            "currency": str(row.get("currency") or "").strip().upper(),
            "flow_type": flow_type,
        }
        if movement["day"] is None:
            gaps.append(GAP_UNDATED_CAPITAL_MOVEMENT)
        movements.append(movement)
    return movements, gaps


def _balance_block(points: list[dict]) -> dict:
    start_nav = points[0]["nav"]
    end_nav = points[-1]["nav"]
    change = end_nav - start_nav
    return {
        "start_nav": _money(start_nav),
        "end_nav": _money(end_nav),
        "change": _money(change),
        "change_pct": round(change / start_nav * 100.0, 2) + 0.0 if start_nav > 0 else None,
    }


def _window(points: list[dict]) -> dict:
    days = (points[-1]["day"] - points[0]["day"]).days
    return {"from": points[0]["captured_at"], "to": points[-1]["captured_at"], "days": float(days)}


def _chained_dietz(points: list[dict], movements: list[dict]) -> tuple[float | None, list[str]]:
    """Chain per-interval Modified-Dietz returns; None when any interval is unusable."""
    multiple = 1.0
    for index, (start, end) in enumerate(zip(points, points[1:])):
        total_days = float((end["day"] - start["day"]).days)
        if total_days <= 0:
            continue
        # A movement dated on the window's FIRST observation day is inside the
        # first interval: the start NAV cannot have included it without the
        # start value already showing it, so counting it as return would be the
        # deposit-as-growth bug. A movement on an interior boundary day belongs
        # to the interval that ends there (weight 0), never to both.
        flows = [
            m
            for m in movements
            if m["day"] is not None
            and ((index == 0 and m["day"] == start["day"]) or start["day"] < m["day"] <= end["day"])
        ]
        net_flow = sum(m["amount"] for m in flows)
        weighted = 0.0
        for movement in flows:
            remaining = float((end["day"] - movement["day"]).days)
            weight = min(max(remaining / total_days, 0.0), 1.0)
            weighted += movement["amount"] * weight
        base = start["nav"] + weighted
        if base <= 0:
            return None, [GAP_NONPOSITIVE_CAPITAL_BASE]
        multiple *= 1.0 + (end["nav"] - start["nav"] - net_flow) / base
        if not math.isfinite(multiple) or multiple <= 0:
            return None, [GAP_NONPOSITIVE_CAPITAL_BASE]
    return multiple, []


def _since_last_capital_movement(points: list[dict], movements: list[dict], coverage) -> dict | None:
    """Return the observed return after the last dated capital movement, if it verifies."""
    dated = [m["day"] for m in movements if m["day"] is not None]
    if not dated:
        return None
    after = [point for point in points if point["day"] > max(dated)]
    if len(after) < 2:
        return None
    verified = coverage_spans(coverage, after[0]["day"], after[-1]["day"])
    return {
        "from": after[0]["captured_at"],
        "to": after[-1]["captured_at"],
        "start_nav": _money(after[0]["nav"]),
        "end_nav": _money(after[-1]["nav"]),
        "change_pct": round((after[-1]["nav"] - after[0]["nav"]) / after[0]["nav"] * 100.0, 2) + 0.0,
        "verified": verified,
    }


def flow_adjusted_growth(nav_points, cash_flows=(), coverage=None, currency: str = "USD") -> dict:
    """Measure investment growth after removing external capital movement.

    ``nav_points`` are oldest-first broker NAV observations (``captured_at``,
    ``net_liquidation``). ``cash_flows`` are persisted broker cash-flow rows
    (``flow_type``, ``clearing_date``, ``amount``, ``currency``, ``remark``).
    ``coverage`` is the persisted cash-flow backfill span, ``{earliest, latest}``.
    """
    currency = str(currency or "USD").upper()
    points = _nav_points(nav_points)
    movements, gaps = _capital_movements(cash_flows)
    unique_gaps = list(dict.fromkeys(gaps))

    base = {
        "status": "no_data",
        "investment_multiple": None,
        "investment_return_pct": None,
        "window": None,
        "balance_change": None,
        "contributed": None,
        "unexplained_change": None,
        "gaps": unique_gaps,
        "since_last_capital_movement": None,
        "coverage": {
            "earliest": (coverage or {}).get("earliest") if isinstance(coverage, dict) else None,
            "latest": (coverage or {}).get("latest") if isinstance(coverage, dict) else None,
            "verified": False,
        },
    }
    if not points:
        return base

    window = _window(points)
    base["window"] = window
    base["balance_change"] = _balance_block(points)

    in_window = [m for m in movements if m["day"] is not None and points[0]["day"] <= m["day"] <= points[-1]["day"]]
    # An empty currency is unknown, not assumed: it cannot be shown to be the
    # measured currency, so the window is not measurable.
    if any(m["currency"] != currency for m in movements):
        unique_gaps.append(GAP_UNSUPPORTED_CURRENCY)
    verified = coverage_spans(coverage, points[0]["day"], points[-1]["day"])
    base["coverage"]["verified"] = verified
    base["contributed"] = {
        "net": _money(sum(m["amount"] for m in in_window)),
        "count": len(in_window),
        "currency": currency,
        "complete": verified and not gaps and GAP_UNSUPPORTED_CURRENCY not in unique_gaps,
    }
    base["unexplained_change"] = _money(base["balance_change"]["change"] - base["contributed"]["net"])
    base["since_last_capital_movement"] = _since_last_capital_movement(points, movements, coverage)

    if len(points) < 2:
        unique_gaps.append(GAP_TOO_FEW_OBSERVATIONS)
        base["status"] = "insufficient"
        base["gaps"] = list(dict.fromkeys(unique_gaps))
        return base

    if not verified:
        unique_gaps.append(GAP_COVERAGE_INCOMPLETE)
    base["gaps"] = list(dict.fromkeys(unique_gaps))
    if base["gaps"]:
        base["status"] = "unknown"
        return base

    multiple, interval_gaps = _chained_dietz(points, movements)
    if multiple is None:
        base["gaps"] = list(dict.fromkeys(unique_gaps + interval_gaps))
        base["status"] = "unknown"
        return base
    base["status"] = "measured"
    base["investment_multiple"] = round(multiple, 6)
    base["investment_return_pct"] = round((multiple - 1.0) * 100.0, 2) + 0.0
    return base
