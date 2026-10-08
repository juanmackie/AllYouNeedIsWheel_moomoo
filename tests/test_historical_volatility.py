import math

import pytest

from core.historical_volatility import annualized_historical_volatility, falling_knife_diagnostic


def test_annualized_historical_volatility_uses_sample_standard_deviation_of_log_returns():
    closes = [100.0]
    for daily_return in (0.01, -0.02, 0.015, -0.005):
        closes.append(closes[-1] * math.exp(daily_return))

    actual = annualized_historical_volatility(closes, window_sessions=4)
    returns = (0.01, -0.02, 0.015, -0.005)
    mean_return = sum(returns) / len(returns)
    sample_variance = sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1)
    expected = math.sqrt(252) * 100 * math.sqrt(sample_variance)

    assert actual == pytest.approx(expected)


def test_annualized_historical_volatility_requires_complete_positive_finite_prices():
    assert annualized_historical_volatility([100, 101, 102], window_sessions=3) is None
    assert annualized_historical_volatility([100, 101, float("nan"), 103], window_sessions=3) is None
    assert annualized_historical_volatility([100, 101, 0, 103], window_sessions=3) is None
    assert annualized_historical_volatility([100, 101, 102], window_sessions=1) is None


def test_falling_knife_flag_requires_large_20_session_decline_below_50_session_average():
    closes = [100.0] * 45 + [100.0, 96.0, 92.0, 88.0, 84.0]

    diagnostic = falling_knife_diagnostic(
        closes,
        decline_window_sessions=4,
        moving_average_sessions=5,
        decline_threshold_pct=15,
    )

    assert diagnostic["status"] == "available"
    assert diagnostic["decline_pct"] == pytest.approx(-16.0)
    assert diagnostic["below_moving_average"] is True
    assert diagnostic["flagged"] is True


def test_falling_knife_flag_stays_false_without_both_conditions_or_full_history():
    modest_decline = falling_knife_diagnostic(
        [100.0] * 45 + [100.0, 98.0, 96.0, 94.0, 92.0],
        decline_window_sessions=4,
        moving_average_sessions=5,
        decline_threshold_pct=15,
    )
    insufficient = falling_knife_diagnostic([100.0] * 49)

    assert modest_decline["status"] == "available"
    assert modest_decline["flagged"] is False
    assert insufficient["status"] == "unavailable"
    assert insufficient["flagged"] is None
