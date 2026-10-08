"""Pure realized-volatility math for display-only market diagnostics."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable


def annualized_historical_volatility(closes: Iterable[float], window_sessions: int = 20) -> float | None:
    """Return sample standard deviation of daily log returns, annualized in percent.

    ``window_sessions`` returns require one additional closing price. Invalid or
    incomplete price windows stay unknown instead of producing a partial metric.
    """
    try:
        window = int(window_sessions)
    except (TypeError, ValueError):
        return None
    if window < 2:
        return None

    try:
        values = [float(value) for value in closes]
    except (TypeError, ValueError):
        return None
    if len(values) < window + 1:
        return None

    values = values[-(window + 1) :]
    if any(not math.isfinite(value) or value <= 0 for value in values):
        return None

    returns = [math.log(current / previous) for previous, current in zip(values, values[1:])]
    volatility = statistics.stdev(returns) * math.sqrt(252) * 100
    return volatility if math.isfinite(volatility) else None


def falling_knife_diagnostic(
    closes: Iterable[float],
    decline_window_sessions: int = 20,
    moving_average_sessions: int = 50,
    decline_threshold_pct: float = 15.0,
) -> dict[str, float | int | bool | str | None]:
    """Flag a sharp recent decline below the 50-session average for display only.

    A decline window of 20 sessions needs 21 closes. The moving average uses the
    latest 50 closes. Incomplete or invalid broker history remains unavailable.
    """
    try:
        decline_window = int(decline_window_sessions)
        average_window = int(moving_average_sessions)
        threshold = float(decline_threshold_pct)
        values = [float(value) for value in closes]
    except (TypeError, ValueError):
        values = []
        decline_window = 0
        average_window = 0
        threshold = float("nan")

    required = max(decline_window + 1, average_window)
    unavailable = {
        "status": "unavailable",
        "decline_window_sessions": max(decline_window, 0),
        "moving_average_sessions": max(average_window, 0),
        "decline_pct": None,
        "moving_average_price": None,
        "below_moving_average": None,
        "flagged": None,
    }
    if (
        decline_window < 1
        or average_window < 2
        or not math.isfinite(threshold)
        or threshold <= 0
        or len(values) < required
        or any(not math.isfinite(value) or value <= 0 for value in values[-required:])
    ):
        return unavailable

    recent = values[-required:]
    decline_pct = (recent[-1] / recent[-decline_window - 1] - 1) * 100
    moving_average = statistics.fmean(recent[-average_window:])
    if not math.isfinite(decline_pct) or not math.isfinite(moving_average):
        return unavailable
    below_moving_average = recent[-1] < moving_average
    return {
        "status": "available",
        "decline_window_sessions": decline_window,
        "moving_average_sessions": average_window,
        "decline_pct": decline_pct,
        "moving_average_price": moving_average,
        "below_moving_average": below_moving_average,
        "flagged": decline_pct <= -threshold and below_moving_average,
    }
