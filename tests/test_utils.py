from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core import utils


def test_get_closest_friday_uses_us_market_date():
    market_datetime = datetime(2026, 12, 18, 15, 30, tzinfo=ZoneInfo("America/New_York"))
    with patch.object(utils, "market_now", return_value=market_datetime) as market_clock:
        result = utils.get_closest_friday()

    assert result == date(2026, 12, 18)
    market_clock.assert_called_once_with()


def test_get_next_monthly_expiration_uses_us_market_date():
    market_datetime = datetime(2026, 12, 18, 15, 30, tzinfo=ZoneInfo("America/New_York"))
    with patch.object(utils, "market_now", return_value=market_datetime) as market_clock:
        result = utils.get_next_monthly_expiration()

    assert result == "20261218"
    market_clock.assert_called_once_with()


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
