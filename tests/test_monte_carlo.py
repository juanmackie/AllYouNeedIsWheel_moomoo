"""Tests for core/monte_carlo.py — Moomoo-calibrated NAV projection."""

import math
import unittest
from datetime import datetime, timedelta, timezone

from core.monte_carlo import (
    daily_series,
    estimate_gbm,
    project_nav,
    summarize_option_fills,
)


def _snap(nav, captured_at):
    return {"captured_at": captured_at, "net_liquidation": nav}


def _daily_history(start, navs, step_days=7):
    return [_snap(nav, (start + timedelta(days=i * step_days)).isoformat()) for i, nav in enumerate(navs)]


class TestDailySeries(unittest.TestCase):
    def test_empty(self):
        points, diag = daily_series([])
        self.assertEqual(points, [])
        self.assertEqual(diag["daily_points"], 0)

    def test_zero_nav_excluded(self):
        start = datetime(2026, 8, 22, tzinfo=timezone.utc)
        history = [
            _snap(0.0, start.isoformat()),
            _snap(-5.0, (start + timedelta(days=1)).isoformat()),
            _snap(60000.0, (start + timedelta(days=2)).isoformat()),
        ]
        points, diag = daily_series(history)
        self.assertEqual(len(points), 1)
        self.assertEqual(diag["excluded_nonpositive"], 2)

    def test_burst_collapses_to_last_per_day(self):
        day = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)
        history = [
            _snap(59501.0, (day).isoformat()),
            _snap(59502.0, (day + timedelta(minutes=5)).isoformat()),
            _snap(59503.0, (day + timedelta(minutes=10)).isoformat()),
            _snap(60000.0, (day + timedelta(days=8)).isoformat()),
        ]
        points, diag = daily_series(history)
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0][1], 59503.0)
        self.assertEqual(diag["burst_collapsed"], 2)


class TestEstimateGbm(unittest.TestCase):
    def test_steady_doubling_over_year(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        points = [
            (start, 10000.0),
            (start + timedelta(days=365), 20000.0),
        ]
        calib = estimate_gbm(points)
        self.assertAlmostEqual(calib["mu_log_daily"], math.log(2) / 365, places=9)
        expected = math.exp(math.log(2) * 365.25 / 365) - 1.0
        self.assertAlmostEqual(calib["annualized_median_growth"], expected, places=9)
        self.assertAlmostEqual(calib["sigma_daily"], 0.0, places=9)


class TestProjectNav(unittest.TestCase):
    def test_no_data(self):
        out = project_nav([], horizon_days=365, n_paths=200)
        self.assertEqual(out["status"], "no_data")

    def test_insufficient_single_snapshot(self):
        out = project_nav([_snap(60000.0, "2026-08-22T09:00:00+00:00")], horizon_days=365, n_paths=200)
        self.assertEqual(out["status"], "insufficient")

    def test_insufficient_short_window(self):
        start = datetime(2026, 8, 22, tzinfo=timezone.utc)
        history = _daily_history(start, [60000.0, 60100.0], step_days=1)
        out = project_nav(history, horizon_days=365, n_paths=200)
        self.assertEqual(out["status"], "insufficient")

    def test_ok_growing_account(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        navs = [60000.0 * (1.001 ** (i * 7)) for i in range(10)]
        history = _daily_history(start, navs, step_days=7)
        out = project_nav(history, target_multiple=5.0, horizon_days=365, n_paths=500, seed=7)
        self.assertEqual(out["status"], "ok")
        self.assertFalse(out["reached"])
        self.assertGreater(out["calibration"]["mu_log_daily"], 0)
        for band in out["bands"]:
            self.assertLessEqual(band["p10"], band["p50"])
            self.assertLessEqual(band["p50"], band["p90"])
        self.assertGreaterEqual(out["prob_target_ever"], out["prob_target_at_horizon"])
        self.assertGreaterEqual(out["prob_target_ever"], 0.0)
        self.assertLessEqual(out["prob_target_ever"], 1.0)
        # Calibration matches the pace methodology: median drift ≈ realized ratio.
        self.assertAlmostEqual(out["baseline_nav"], navs[0], places=1)
        self.assertAlmostEqual(out["target_nav"], navs[0] * 5.0, places=0)

    def test_deterministic_same_seed(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        history = _daily_history(start, [60000.0, 60600.0, 60300.0, 61000.0, 60800.0], step_days=7)
        first = project_nav(history, horizon_days=180, n_paths=300, seed=99)
        second = project_nav(history, horizon_days=180, n_paths=300, seed=99)
        self.assertEqual(first, second)

    def test_reached_target(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # 4 daily points over 60 days satisfies the minimums; NAV crossed 5x.
        history = _daily_history(start, [10000.0, 30000.0, 40000.0, 55000.0], step_days=20)
        out = project_nav(history, target_multiple=5.0, horizon_days=365, n_paths=200, seed=1)
        self.assertTrue(out["reached"])
        self.assertEqual(out["status"], "reached")
        self.assertEqual(out["prob_target_ever"], 1.0)
        self.assertEqual(out["median_eta_days"], 0.0)

    def test_declining_account_low_probability(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        history = _daily_history(start, [60000.0, 59000.0, 58000.0, 57000.0, 56000.0], step_days=7)
        out = project_nav(history, horizon_days=365, n_paths=500, seed=3)
        self.assertEqual(out["status"], "ok")
        self.assertLess(out["calibration"]["mu_log_daily"], 0)
        self.assertLess(out["prob_target_ever"], 0.2)

    def test_invalid_params_raise(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        history = _daily_history(start, [60000.0, 61000.0, 62000.0, 63000.0, 64000.0], step_days=7)
        with self.assertRaises(ValueError):
            project_nav(history, horizon_days=0, n_paths=200)
        with self.assertRaises(ValueError):
            project_nav(history, horizon_days=365, n_paths=10)


class TestSummarizeFills(unittest.TestCase):
    def test_option_leg_totals(self):
        fills = [
            {
                "security_type": "OPT",
                "side": "SELL",
                "qty": 1,
                "price": 1.68,
                "fees": 0.55,
                "ticker": "X",
                "expiration": "20260828",
                "option_type": "PUT",
                "strike": 35,
            },
            {
                "security_type": "OPT",
                "side": "BUY",
                "qty": 1,
                "price": 0.11,
                "fees": 0.53,
                "ticker": "X",
                "expiration": "20260828",
                "option_type": "PUT",
                "strike": 35,
            },
            {
                "security_type": "OPT",
                "side": "BUY",
                "qty": 1,
                "price": 0.0,
                "fees": 0.0,
                "ticker": "Y",
                "expiration": "",
                "option_type": "",
                "strike": None,
            },
            {
                "security_type": "STK",
                "side": "BUY",
                "qty": 100,
                "price": 18.5,
                "fees": 0.0,
                "ticker": "Y",
                "expiration": "",
                "option_type": "",
                "strike": None,
            },
        ]
        summary = summarize_option_fills(fills)
        self.assertEqual(summary["n_option_fills"], 3)
        self.assertEqual(summary["zero_price_movement_count"], 1)
        self.assertAlmostEqual(summary["gross_sell_premium"], 168.0)
        self.assertAlmostEqual(summary["gross_buyback_cost"], 11.0)
        self.assertEqual(summary["scope"], "option_leg")


if __name__ == "__main__":
    unittest.main()
