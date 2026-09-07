"""Tests for core/growth_mode.growth_pace — 5x path math (C07/C08)."""

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
        self.assertEqual(pace["status"], "no_data")
        self.assertFalse(pace["reached"])

    def test_single_snapshot_no_pace(self):
        pace = growth_pace([_snap(10_000.0, "2026-08-20T15:00:00+00:00")])
        self.assertEqual(pace["current_nav"], 10_000.0)
        # C07: target derives from the durable baseline, not the moving current NAV.
        self.assertEqual(pace["baseline_nav"], 10_000.0)
        self.assertEqual(pace["target_nav"], 50_000.0)
        self.assertEqual(pace["status"], "insufficient")
        self.assertIsNone(pace["annualized_pace"])
        self.assertIsNone(pace["eta_days"])
        self.assertFalse(pace["reached"])

    def test_growing_account_compounding(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # 2x over one year ≈ 100% annualized.
        history = [
            _snap(10_000.0, start.isoformat()),
            _snap(11_000.0, (start + timedelta(days=180)).isoformat()),
            _snap(20_000.0, (start + timedelta(days=365)).isoformat()),
        ]
        pace = growth_pace(history, target_multiple=5.0)
        self.assertAlmostEqual(pace["annualized_pace"], 1.0, places=2)
        self.assertTrue(pace["on_track"])
        self.assertEqual(pace["status"], "progressing")
        # C07: target is baseline * 5x. ETA from 20k toward 50k at 100%/yr
        # ≈ log(2.5)/log(2) ≈ 1.32 years.
        self.assertEqual(pace["target_nav"], 50_000.0)
        self.assertIsNotNone(pace["eta_days"])
        self.assertGreater(pace["eta_days"], 365)
        self.assertLess(pace["eta_days"], 365 * 2)
        # Required premium/day fills the remaining gap over the ETA window.
        expected_daily = (50_000.0 - 20_000.0) / pace["eta_days"]
        self.assertAlmostEqual(pace["required_premium_per_day"], round(expected_daily, 2), places=1)

    def test_short_window_is_not_annualized(self):
        # C08: a 1% gain across one minute must not overflow or fabricate a
        # 10000x annualized pace; report insufficient history instead.
        start = datetime(2026, 5, 1, tzinfo=timezone.utc)
        history = [
            _snap(10_000.0, start.isoformat()),
            _snap(10_100.0, (start + timedelta(seconds=60)).isoformat()),
        ]
        pace = growth_pace(history, target_multiple=5.0)
        self.assertIsNone(pace["annualized_pace"])
        self.assertIsNone(pace["eta_days"])
        self.assertIsNone(pace["on_track"])
        self.assertEqual(pace["status"], "insufficient")

    def test_declining_account_not_on_track(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        history = [
            _snap(10_000.0, start.isoformat()),
            _snap(9_000.0, (start + timedelta(days=365)).isoformat()),
        ]
        pace = growth_pace(history, target_multiple=5.0)
        # C08: a decline over an adequate window is an observed shortfall, not
        # "collecting data".
        self.assertEqual(pace["status"], "declining")
        self.assertLess(pace["annualized_pace"], 0)
        self.assertFalse(pace["on_track"])
        self.assertLessEqual(pace["progress_pct"], 0.0)

    def test_flat_account_honest(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        history = [
            _snap(10_000.0, start.isoformat()),
            _snap(10_000.0, (start + timedelta(days=365)).isoformat()),
        ]
        pace = growth_pace(history, target_multiple=5.0)
        self.assertEqual(pace["status"], "declining")
        self.assertAlmostEqual(pace["annualized_pace"], 0.0, places=6)

    def test_reached_target(self):
        # C07: once NAV crosses baseline * target, report reached with zero gap.
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        history = [
            _snap(10_000.0, start.isoformat()),
            _snap(55_000.0, (start + timedelta(days=365)).isoformat()),
        ]
        pace = growth_pace(history, target_multiple=5.0)
        self.assertTrue(pace["reached"])
        self.assertEqual(pace["status"], "reached")
        self.assertEqual(pace["progress_pct"], 100.0)
        self.assertEqual(pace["eta_days"], 0.0)
        self.assertEqual(pace["required_premium_per_day"], 0.0)


if __name__ == "__main__":
    unittest.main()
