"""Focused tests for core/outcome_attribution.py — pure attribution helpers.

Covers: net-of-fee outcome math, slippage/leakage, UNKNOWN propagation
(never fabricate), capital-days rules (CSP, covered calls, partial closes,
assignment continuity), and the owner-efficiency summary.
"""

import unittest
from datetime import datetime

from core.outcome_attribution import (
    capital_base_cc,
    capital_base_csp,
    capital_days_from_lots,
    combined_outcome,
    gross_premium_dollars,
    is_capital_movement_cash_flow,
    leakage_dollars,
    max_drawdown,
    net_event_pnl,
    owner_efficiency,
    owner_summary,
    parse_timestamp,
    share_leg_pnl,
    slippage_dollars,
)


class TestMoneyMath(unittest.TestCase):
    def test_gross_premium_dollars_multiplies_contracts(self):
        self.assertAlmostEqual(gross_premium_dollars(1.5, 2), 300.0)
        self.assertAlmostEqual(gross_premium_dollars(50.0, 100, multiplier=1), 5000.0)

    def test_net_event_pnl_subtracts_fees(self):
        self.assertAlmostEqual(net_event_pnl(premium_in=500, premium_out=200, fees=5.5), 294.5)

    def test_parse_timestamp_iso_and_none(self):
        self.assertEqual(parse_timestamp("2026-01-02T03:04:05"), datetime(2026, 1, 2, 3, 4, 5))
        self.assertEqual(parse_timestamp("2026-01-02 03:04:05"), datetime(2026, 1, 2, 3, 4, 5))
        self.assertIsNone(parse_timestamp(""))
        self.assertIsNone(parse_timestamp(None))
        self.assertIsNone(parse_timestamp("not-a-date"))


class TestSlippage(unittest.TestCase):
    def test_sell_fill_below_quote_is_unfavorable(self):
        # Quoted 1.10, filled 1.05 → gave up $5.00 per contract.
        self.assertAlmostEqual(slippage_dollars(1.05, 1.10, 1, "SELL"), -5.0)
        self.assertAlmostEqual(leakage_dollars(1.05, 1.10, 1, "SELL"), 5.0)

    def test_sell_fill_above_quote_is_favorable(self):
        self.assertAlmostEqual(slippage_dollars(1.15, 1.10, 1, "SELL"), 5.0)
        self.assertAlmostEqual(leakage_dollars(1.15, 1.10, 1, "SELL"), 0.0)

    def test_buyback_below_reference_is_favorable(self):
        self.assertAlmostEqual(slippage_dollars(0.50, 0.60, 2, "BUY"), 20.0)
        self.assertAlmostEqual(leakage_dollars(0.50, 0.60, 2, "BUY"), 0.0)

    def test_unknown_side_yields_zero_never_a_guess(self):
        self.assertEqual(slippage_dollars(1.0, 2.0, 1, ""), 0.0)
        self.assertEqual(leakage_dollars(1.0, 2.0, 1, "HOLD"), 0.0)


class TestCombinedOutcome(unittest.TestCase):
    def test_option_only_measured(self):
        result = combined_outcome(
            {"premium_in": 500.0, "premium_out": 150.0, "fees": 2.0}, None
        )
        self.assertEqual(result["status"], "measured")
        self.assertAlmostEqual(result["net_pnl"], 348.0)

    def test_unknown_share_basis_makes_whole_outcome_unknown(self):
        result = combined_outcome(
            {"premium_in": 500.0, "premium_out": 0.0, "fees": 0.0},
            {"basis_price": None, "exit_price": 55.0, "shares": 100, "fees": 0.0},
        )
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["net_pnl"])

    def test_measured_share_leg_combines_with_option_leg(self):
        result = combined_outcome(
            {"premium_in": 500.0, "premium_out": 0.0, "fees": 1.0},
            {"basis_price": 150.0, "exit_price": 148.0, "shares": 100, "fees": 2.0},
        )
        self.assertEqual(result["status"], "measured")
        # 499 option + (−200 shares − 2 share fees) = 297
        self.assertAlmostEqual(result["net_pnl"], 297.0)

    def test_no_legs_is_unknown(self):
        self.assertEqual(combined_outcome(None, None)["status"], "unknown")

    def test_option_leg_pnl_can_be_negative_and_still_measured(self):
        result = combined_outcome(
            {"premium_in": 100.0, "premium_out": 350.0, "fees": 1.0}, None
        )
        self.assertEqual(result["status"], "measured")
        self.assertAlmostEqual(result["net_pnl"], -251.0)


class TestShareLegPnl(unittest.TestCase):
    def test_basic_share_pnl(self):
        self.assertAlmostEqual(share_leg_pnl(50.0, 55.0, 100, fees=2.0), 498.0)

    def test_missing_basis_is_unknown_not_zero(self):
        self.assertIsNone(share_leg_pnl(None, 55.0, 100))
        self.assertIsNone(share_leg_pnl("", 55.0, 100))


class TestCapitalDays(unittest.TestCase):
    def test_capital_bases(self):
        self.assertAlmostEqual(capital_base_csp(150.0, 1), 15000.0)
        self.assertAlmostEqual(capital_base_cc(148.0, 1), 14800.0)

    def test_single_closed_lot(self):
        days_value = capital_days_from_lots(
            [("2026-01-01T00:00:00", "2026-01-11T00:00:00", 1)],
            capital_per_contract=15000.0,
        )
        self.assertAlmostEqual(days_value, 15000.0 * 10)

    def test_partial_close_uses_per_tranche_days(self):
        # 2 contracts secured 15000 each: one closed after 5 days, the other
        # still open at as_of (day 10). Denominator = 15000*5 + 15000*10.
        lots = [
            ("2026-01-01T00:00:00", "2026-01-06T00:00:00", 1),
            ("2026-01-01T00:00:00", None, 1),
        ]
        days_value = capital_days_from_lots(lots, 15000.0, as_of="2026-01-11T00:00:00")
        self.assertAlmostEqual(days_value, 15000.0 * 5 + 15000.0 * 10)

    def test_assignment_continues_capital_without_reset(self):
        # A put assigned on day 10 does not reset the clock: the lot keeps
        # accruing the same capital base until shares finally close on day 20.
        continuous = capital_days_from_lots(
            [("2026-01-01T00:00:00", "2026-01-21T00:00:00", 1)], 15000.0
        )
        self.assertAlmostEqual(continuous, 15000.0 * 20)

    def test_open_lot_without_as_of_contributes_zero(self):
        # Unknown horizon is never fabricated against "now".
        days_value = capital_days_from_lots(
            [("2026-01-01T00:00:00", None, 1)], 15000.0, as_of=None
        )
        self.assertEqual(days_value, 0.0)

    def test_unknown_entry_timestamp_contributes_zero(self):
        days_value = capital_days_from_lots(
            [("", "2026-01-11T00:00:00", 1)], 15000.0, as_of="2026-01-11T00:00:00"
        )
        self.assertEqual(days_value, 0.0)

    def test_zero_capital_base_yields_zero(self):
        days_value = capital_days_from_lots(
            [("2026-01-01T00:00:00", "2026-01-11T00:00:00", 1)], 0.0
        )
        self.assertEqual(days_value, 0.0)


class TestOwnerEfficiency(unittest.TestCase):
    def test_efficiency_is_pnl_per_capital_day(self):
        self.assertAlmostEqual(owner_efficiency(500.0, 100000.0), 0.005)

    def test_unknown_capital_days_returns_none(self):
        self.assertIsNone(owner_efficiency(500.0, None))
        self.assertIsNone(owner_efficiency(500.0, 0.0))

    def test_max_drawdown_peak_to_trough(self):
        # Cumulative: 100, 150, 80, 120 → peak 150, trough 80 → 70.
        self.assertAlmostEqual(max_drawdown([100, 50, -70, 40]), 70.0)
        self.assertAlmostEqual(max_drawdown([]), 0.0)
        self.assertAlmostEqual(max_drawdown([10, 20, 30]), 0.0)


class TestOwnerSummary(unittest.TestCase):
    def test_summary_excludes_unknown_and_reports_efficiency(self):
        outcomes = [
            {"net_pnl": 500.0, "capital_days": 10000.0, "open": False},
            {"net_pnl": -200.0, "capital_days": 5000.0, "open": False},
            {"net_pnl": None, "capital_days": None, "open": False},  # unknown
            {"net_pnl": -50.0, "capital_days": 2000.0, "open": True},  # open loss
        ]
        summary = owner_summary(outcomes)
        # Net dollars includes open measured losses (500 − 200 − 50 = 250);
        # open_losses surfaces them separately for display.
        self.assertAlmostEqual(summary["net_dollars"], 250.0)
        self.assertEqual(summary["measured_count"], 3)
        self.assertEqual(summary["unknown_count"], 1)
        self.assertAlmostEqual(summary["capital_days"], 17000.0)
        self.assertAlmostEqual(summary["owner_efficiency"], 250.0 / 17000.0)
        self.assertAlmostEqual(summary["open_losses"], -50.0)
        # Cumulative measured: 500, 300, 250 → peak 500, trough 250 → 250.
        self.assertAlmostEqual(summary["drawdown"], 250.0)

    def test_summary_drawdown_and_unknown_efficiency(self):
        summary = owner_summary(
            [
                {"net_pnl": 300.0, "capital_days": 1000.0, "open": False},
                {"net_pnl": -120.0, "capital_days": 1000.0, "open": False},
            ]
        )
        self.assertAlmostEqual(summary["drawdown"], 120.0)
        self.assertAlmostEqual(summary["owner_efficiency"], 180.0 / 2000.0)

        empty = owner_summary([])
        self.assertIsNone(empty["owner_efficiency"])
        self.assertIsNone(empty["capital_days"])
        self.assertAlmostEqual(empty["net_dollars"], 0.0)


class TestCashFlowClassification(unittest.TestCase):
    def test_deposit_and_withdraw_are_capital_movements(self):
        self.assertTrue(is_capital_movement_cash_flow("Deposit"))
        self.assertTrue(is_capital_movement_cash_flow("Withdrawal"))
        self.assertTrue(is_capital_movement_cash_flow("Something", remark="FUND TRANSFER IN"))

    def test_unmatched_types_are_conservatively_trading_related(self):
        self.assertFalse(is_capital_movement_cash_flow(""))
        self.assertFalse(is_capital_movement_cash_flow("UNKNOWN_SERVER_STRING"))
        self.assertFalse(is_capital_movement_cash_flow(None))


if __name__ == "__main__":
    unittest.main()
