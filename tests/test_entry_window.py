"""Tests for core/utils.entry_window_advice — deterministic entry-timing guidance."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from core.utils import entry_window_advice

ET = ZoneInfo("America/New_York")


def _et(hour, minute, weekday=2):
    # 2026-08-19 is a Wednesday; 2026-08-23 is a Sunday.
    base_day = 19 if weekday != 6 else 23
    return datetime(2026, 8, base_day, hour, minute, tzinfo=ET)


class TestEntryWindowAdvice(unittest.TestCase):
    def test_weekend_closed(self):
        advice = entry_window_advice(_et(12, 0, weekday=6))
        self.assertEqual(advice["quality"], "closed")

    def test_premarket_closed(self):
        advice = entry_window_advice(_et(8, 0))
        self.assertEqual(advice["quality"], "closed")
        self.assertIn("stage", advice["message"].lower())

    def test_after_hours_closed(self):
        advice = entry_window_advice(_et(17, 30))
        self.assertEqual(advice["quality"], "closed")

    def test_first_15_minutes_poor(self):
        advice = entry_window_advice(_et(9, 40))
        self.assertEqual(advice["quality"], "poor")

    def test_last_30_minutes_caution(self):
        advice = entry_window_advice(_et(15, 45))
        self.assertEqual(advice["quality"], "caution")

    def test_midday_fair(self):
        advice = entry_window_advice(_et(12, 30))
        self.assertEqual(advice["quality"], "fair")

    def test_mid_session_good(self):
        advice = entry_window_advice(_et(10, 30))
        self.assertEqual(advice["quality"], "good")
        advice = entry_window_advice(_et(14, 0))
        self.assertEqual(advice["quality"], "good")


if __name__ == "__main__":
    unittest.main()
