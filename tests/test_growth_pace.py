"""Tests for core/growth_mode.growth_pace — 10x path math."""

import unittest
from datetime import datetime, timedelta, timezone

from core.growth_mode import growth_pace


def _snap(nav, captured_at):
    return {"captured_at": captured_at, "net_liquidation": nav}


class TestGrowthPace(unittest.TestCase):
    def test_empty_history(self):
        pace = growth_pace([])
        self.assertIsNone(pace["annualized_pace"])
        self.assertIsNone(pace["on_track"])
        self.assertEqual(pace["current_nav"], 0.0)

    def test_single_snapshot_no_pace(self):
        pace = growth_pace([_snap(10_000.0, "2026-08-20T15:00:00+00:00")])
        self.assertEqual(pace["current_nav"], 10_000.0)
        self.assertEqual(pace["target_nav"], 50_000.0)
        self.assertIsNone(pace["annualized_pace"])
        self.assertIsNone(pace["eta_days"])

    def test_growing_account_compounding(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # 2x over one year ≈ 100% annualized.
        history = [
            _snap(10_000.0, start.isoformat()),
            _snap(11_000.0, (start + timedelta(days=180)).isoformat()),
            _snap(20_000.0, (start + timedelta(days=365)).isoformat()),
        ]
        pace = growth_pace(history, target_multiple=10.0)
        self.assertAlmostEqual(pace["annualized_pace"], 1.0, places=2)
        self.assertTrue(pace["on_track"])
        # ETA from 20k to 200k at 100%/yr ≈ log(10)/log(2) ≈ 3.32 years.
        self.assertIsNotNone(pace["eta_days"])
        self.assertGreater(pace["eta_days"], 365 * 3)
        self.assertLess(pace["eta_days"], 365 * 4)
        # Required premium/day fills the remaining gap over the ETA window.
        expected_daily = (200_000.0 - 20_000.0) / pace["eta_days"]
        self.assertAlmostEqual(pace["required_premium_per_day"], round(expected_daily, 2), places=1)

    def test_declining_account_not_on_track(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        history = [
            _snap(20_000.0, start.isoformat()),
            _snap(15_000.0, (start + timedelta(days=90)).isoformat()),
        ]
        pace = growth_pace(history)
        self.assertFalse(pace["on_track"])
        self.assertIsNone(pace["eta_days"])

    def test_progress_pct_capped(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        history = [
            _snap(1_000.0, start.isoformat()),
            _snap(50_000.0, (start + timedelta(days=365)).isoformat()),
        ]
        pace = growth_pace(history, target_multiple=10.0)
        self.assertLessEqual(pace["progress_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
