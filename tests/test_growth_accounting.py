"""Tests for core.growth_accounting — flow-adjusted investment growth.

The 5x contract: a deposit must not read as investment performance, and an
unmeasured window must say so instead of showing an unadjusted ratio.
"""

import unittest

from core.growth_accounting import (
    GAP_COVERAGE_INCOMPLETE,
    GAP_TOO_FEW_OBSERVATIONS,
    GAP_UNDATED_CAPITAL_MOVEMENT,
    GAP_UNPARSEABLE_CAPITAL_MOVEMENT,
    GAP_UNSUPPORTED_CURRENCY,
    coverage_spans,
    flow_adjusted_growth,
)

COVERED = {"earliest": "2025-12-01", "latest": "2026-12-31"}


def point(nav, captured_at):
    return {"captured_at": captured_at, "net_liquidation": nav}


def flow(amount, clearing_date, flow_type="Deposit", currency="USD"):
    return {
        "flow_type": flow_type,
        "clearing_date": clearing_date,
        "amount": amount,
        "currency": currency,
        "remark": "",
    }


class TestCoverageSpans(unittest.TestCase):
    def test_missing_or_late_coverage_is_not_coverage(self):
        from datetime import date

        start, end = date(2026, 3, 1), date(2026, 3, 10)
        self.assertTrue(coverage_spans(COVERED, start, end))
        self.assertFalse(coverage_spans(None, start, end))
        self.assertFalse(coverage_spans({}, start, end))
        # A backfill that starts after the window cannot prove no deposit happened.
        self.assertFalse(coverage_spans({"earliest": "2026-03-05", "latest": "2026-12-31"}, start, end))
        # The settle lag tolerates a latest boundary a few days before the window end.
        self.assertTrue(coverage_spans({"earliest": "2026-01-01", "latest": "2026-03-07"}, start, end))


class TestFlowAdjustedGrowth(unittest.TestCase):
    def test_deposit_is_not_investment_growth(self):
        """The review's reproduction: $10k grows to $50k only because of a $40k deposit."""
        points = [point(10_000, "2026-01-01T15:00:00+00:00"), point(50_000, "2026-01-03T15:00:00+00:00")]
        result = flow_adjusted_growth(points, [flow(40_000, "2026-01-03")], COVERED)
        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["gaps"], [])
        self.assertAlmostEqual(result["investment_multiple"], 1.0, places=6)
        self.assertAlmostEqual(result["investment_return_pct"], 0.0, places=2)
        self.assertEqual(result["contributed"]["net"], 40_000.0)
        self.assertEqual(result["contributed"]["complete"], True)
        self.assertEqual(result["unexplained_change"], 0.0)
        self.assertEqual(result["balance_change"]["change"], 40_000.0)

    def test_withdrawal_alone_leaves_performance_unchanged(self):
        points = [point(10_000, "2026-01-01T15:00:00+00:00"), point(5_000, "2026-01-03T15:00:00+00:00")]
        result = flow_adjusted_growth(points, [flow(-5_000, "2026-01-03", "Withdrawal")], COVERED)
        self.assertEqual(result["status"], "measured")
        self.assertAlmostEqual(result["investment_multiple"], 1.0, places=6)
        self.assertEqual(result["contributed"]["net"], -5_000.0)

    def test_genuine_loss_stays_below_one(self):
        points = [point(10_000, "2026-01-01T15:00:00+00:00"), point(9_000, "2026-01-11T15:00:00+00:00")]
        result = flow_adjusted_growth(points, [], COVERED)
        self.assertEqual(result["status"], "measured")
        self.assertAlmostEqual(result["investment_multiple"], 0.9, places=6)
        self.assertAlmostEqual(result["investment_return_pct"], -10.0, places=2)

    def test_gain_with_flow_is_attributed_to_investment(self):
        # +$1,000 trading gain plus a $40,000 deposit into a $10,000 book.
        # The deposit lands at the end of a 10-day interval (zero weight).
        points = [point(10_000, "2026-01-01T15:00:00+00:00"), point(51_000, "2026-01-11T15:00:00+00:00")]
        result = flow_adjusted_growth(points, [flow(40_000, "2026-01-11")], COVERED)
        self.assertEqual(result["status"], "measured")
        self.assertAlmostEqual(result["investment_multiple"], 1.1, places=6)
        self.assertEqual(result["unexplained_change"], 1_000.0)

    def test_mid_window_flow_is_time_weighted(self):
        # $10k, a $10k deposit on day 5 of 10, ending at $21,000.
        # Dietz base = 10,000 + 10,000 * 0.5 = 15,000; gain = 1,000 -> 6.6667%.
        points = [point(10_000, "2026-01-01T15:00:00+00:00"), point(21_000, "2026-01-11T15:00:00+00:00")]
        result = flow_adjusted_growth(points, [flow(10_000, "2026-01-06")], COVERED)
        self.assertEqual(result["status"], "measured")
        self.assertAlmostEqual(result["investment_multiple"], 1.066667, places=5)

    def test_compound_intervals_chain(self):
        points = [
            point(10_000, "2026-01-01T15:00:00+00:00"),
            point(11_000, "2026-01-11T15:00:00+00:00"),
            point(12_100, "2026-01-21T15:00:00+00:00"),
        ]
        result = flow_adjusted_growth(points, [], COVERED)
        self.assertEqual(result["status"], "measured")
        self.assertAlmostEqual(result["investment_multiple"], 1.21, places=6)

    def test_missing_cash_flow_coverage_is_a_named_gap(self):
        points = [point(10_000, "2026-01-01T15:00:00+00:00"), point(50_000, "2026-01-03T15:00:00+00:00")]
        result = flow_adjusted_growth(points, [], None)
        self.assertEqual(result["status"], "unknown")
        self.assertIn(GAP_COVERAGE_INCOMPLETE, result["gaps"])
        self.assertIsNone(result["investment_multiple"])
        self.assertIsNone(result["investment_return_pct"])
        # Locally known facts are still reported.
        self.assertEqual(result["balance_change"]["change"], 40_000.0)
        self.assertEqual(result["contributed"]["complete"], False)

    def test_undated_capital_movement_blocks_measurement(self):
        points = [point(10_000, "2026-01-01T15:00:00+00:00"), point(12_000, "2026-01-11T15:00:00+00:00")]
        result = flow_adjusted_growth(points, [flow(2_000, "")], COVERED)
        self.assertEqual(result["status"], "unknown")
        self.assertIn(GAP_UNDATED_CAPITAL_MOVEMENT, result["gaps"])
        self.assertIsNone(result["investment_multiple"])

    def test_non_usd_flow_blocks_measurement(self):
        points = [point(10_000, "2026-01-01T15:00:00+00:00"), point(12_000, "2026-01-11T15:00:00+00:00")]
        result = flow_adjusted_growth(points, [flow(2_000, "2026-01-05", currency="AUD")], COVERED)
        self.assertEqual(result["status"], "unknown")
        self.assertIn(GAP_UNSUPPORTED_CURRENCY, result["gaps"])

    def test_single_observation_is_insufficient(self):
        result = flow_adjusted_growth([point(10_000, "2026-01-01T15:00:00+00:00")], [], COVERED)
        self.assertEqual(result["status"], "insufficient")
        self.assertIn(GAP_TOO_FEW_OBSERVATIONS, result["gaps"])
        self.assertIsNone(result["investment_multiple"])

    def test_no_observations_is_no_data(self):
        result = flow_adjusted_growth([], [flow(1_000, "2026-01-05")], COVERED)
        self.assertEqual(result["status"], "no_data")
        self.assertIsNone(result["balance_change"])

    def test_same_day_observations_collapse_to_last(self):
        points = [
            point(10_000, "2026-01-01T15:00:00+00:00"),
            point(10_500, "2026-01-01T20:00:00+00:00"),
            point(11_000, "2026-01-11T15:00:00+00:00"),
        ]
        result = flow_adjusted_growth(points, [], COVERED)
        self.assertEqual(result["status"], "measured")
        self.assertAlmostEqual(result["investment_multiple"], 1.047619, places=5)

    def test_since_last_capital_movement_window_is_reported(self):
        points = [
            point(10_000, "2026-01-01T15:00:00+00:00"),
            point(50_000, "2026-01-10T15:00:00+00:00"),
            point(50_000, "2026-01-20T15:00:00+00:00"),
            point(50_500, "2026-01-25T15:00:00+00:00"),
        ]
        result = flow_adjusted_growth(points, [flow(40_000, "2026-01-10")], COVERED)
        self.assertEqual(result["status"], "measured")
        since = result["since_last_capital_movement"]
        self.assertEqual(since["verified"], True)
        self.assertAlmostEqual(since["change_pct"], 1.0, places=2)

    def test_deposit_dated_on_the_first_observation_day_is_not_growth(self):
        """A flow dated on the window's first NAV day cannot be return.

        The start NAV cannot have included it without already showing it, so the
        first interval must subtract it (weight 1.0). Excluding it would turn a
        deposit into the exact fabricated 5x this contract forbids.
        """
        points = [point(10_000, "2026-01-01T15:00:00+00:00"), point(50_000, "2026-01-03T15:00:00+00:00")]
        result = flow_adjusted_growth(points, [flow(40_000, "2026-01-01")], COVERED)
        self.assertEqual(result["status"], "measured")
        self.assertAlmostEqual(result["investment_multiple"], 1.0, places=6)
        self.assertEqual(result["contributed"]["net"], 40_000.0)
        self.assertEqual(result["unexplained_change"], 0.0)

    def test_interior_boundary_flow_is_counted_in_exactly_one_interval(self):
        # $10k deposit dated on the middle observation day: interval 1 subtracts
        # it at weight 0, interval 2 must not subtract it again.
        points = [
            point(10_000, "2026-01-01T15:00:00+00:00"),
            point(20_000, "2026-01-11T15:00:00+00:00"),
            point(22_000, "2026-01-21T15:00:00+00:00"),
        ]
        result = flow_adjusted_growth(points, [flow(10_000, "2026-01-11")], COVERED)
        self.assertEqual(result["status"], "measured")
        self.assertAlmostEqual(result["investment_multiple"], 1.1, places=6)
        self.assertEqual(result["contributed"]["net"], 10_000.0)

    def test_unparseable_capital_movement_blocks_measurement(self):
        points = [point(10_000, "2026-01-01T15:00:00+00:00"), point(50_000, "2026-01-03T15:00:00+00:00")]
        result = flow_adjusted_growth(
            points,
            [{"flow_type": "Deposit", "clearing_date": "2026-01-02", "amount": None, "currency": "USD"}],
            COVERED,
        )
        self.assertEqual(result["status"], "unknown")
        self.assertIn(GAP_UNPARSEABLE_CAPITAL_MOVEMENT, result["gaps"])
        self.assertIsNone(result["investment_multiple"])

    def test_empty_currency_is_not_assumed_to_be_the_measured_currency(self):
        points = [point(10_000, "2026-01-01T15:00:00+00:00"), point(12_000, "2026-01-11T15:00:00+00:00")]
        result = flow_adjusted_growth(points, [flow(2_000, "2026-01-05", currency="")], COVERED)
        self.assertEqual(result["status"], "unknown")
        self.assertIn(GAP_UNSUPPORTED_CURRENCY, result["gaps"])


if __name__ == "__main__":
    unittest.main()
