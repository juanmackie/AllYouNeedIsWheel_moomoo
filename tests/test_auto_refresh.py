"""Tests for the optional once-per-market-day refresh trigger."""

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core import wheel_runner

MARKET_TZ = ZoneInfo("America/New_York")


class TestOpenRefresh(unittest.TestCase):
    def setUp(self):
        wheel_runner._last_auto_refresh_date = None

    def tearDown(self):
        wheel_runner._last_auto_refresh_date = None

    def test_disabled_by_default(self):
        now = datetime(2026, 8, 27, 10, 0, tzinfo=MARKET_TZ)
        with patch.object(wheel_runner, "start_background_refresh") as start:
            self.assertFalse(wheel_runner.maybe_start_open_refresh(object(), {}, now=now))
        start.assert_not_called()

    @patch.object(wheel_runner, "is_market_open", return_value=True)
    @patch.object(wheel_runner, "start_background_refresh", return_value=True)
    def test_enabled_fires_once_per_market_day(self, start, market_open):
        now = datetime(2026, 8, 27, 10, 0, tzinfo=MARKET_TZ)
        config = {"auto_refresh_at_open": True}

        self.assertTrue(wheel_runner.maybe_start_open_refresh(object(), config, now=now))
        self.assertFalse(wheel_runner.maybe_start_open_refresh(object(), config, now=now))

        start.assert_called_once()
        self.assertEqual(market_open.call_count, 2)

    @patch.object(wheel_runner, "is_market_open", return_value=True)
    @patch.object(wheel_runner, "start_background_refresh", return_value=True)
    def test_snapshot_published_today_suppresses_refresh(self, start, _market_open):
        now = datetime(2026, 8, 27, 10, 0, tzinfo=MARKET_TZ)
        snapshot = {"run": {"published_at": "2026-08-27T09:45:00-04:00"}}

        self.assertFalse(
            wheel_runner.maybe_start_open_refresh(object(), {"auto_refresh_at_open": True}, snapshot=snapshot, now=now)
        )
        start.assert_not_called()

    @patch.object(wheel_runner, "is_market_open", return_value=False)
    @patch.object(wheel_runner, "start_background_refresh", return_value=True)
    def test_closed_market_suppresses_refresh(self, start, _market_open):
        now = datetime(2026, 8, 29, 10, 0, tzinfo=MARKET_TZ)

        self.assertFalse(wheel_runner.maybe_start_open_refresh(object(), {"auto_refresh_at_open": True}, now=now))
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
