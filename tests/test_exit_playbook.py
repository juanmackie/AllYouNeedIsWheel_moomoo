"""Tests for core/exit_playbook.py — deterministic exit-rule engine."""

import unittest

from core.exit_playbook import (
    VERDICT_CLOSE,
    VERDICT_HOLD,
    VERDICT_ROLL,
    VERDICT_TAKE_PROFIT,
    ExitThresholds,
    captured_profit_pct_for_short,
    evaluate_exit,
)


class TestEvaluateExit(unittest.TestCase):
    def test_healthy_position_holds(self):
        verdict = evaluate_exit(option_type="PUT", dte=35, delta=-0.30, otm_pct=8.0, captured_profit_pct=10.0)
        self.assertEqual(verdict.verdict, VERDICT_HOLD)
        self.assertTrue(any("credit captured" in r for r in verdict.reasons))

    def test_profit_take_fires_at_50pct(self):
        verdict = evaluate_exit(option_type="PUT", dte=25, delta=-0.25, otm_pct=6.0, captured_profit_pct=55.0)
        self.assertEqual(verdict.verdict, VERDICT_TAKE_PROFIT)
        self.assertTrue(any("55% of entry credit" in r for r in verdict.reasons))

    def test_unknown_entry_credit_disables_profit_take(self):
        verdict = evaluate_exit(option_type="PUT", dte=25, delta=-0.25, otm_pct=6.0, captured_profit_pct=None)
        self.assertNotEqual(verdict.verdict, VERDICT_TAKE_PROFIT)

    def test_roll_window_otm(self):
        verdict = evaluate_exit(option_type="PUT", dte=18, delta=-0.28, otm_pct=5.0)
        self.assertEqual(verdict.verdict, VERDICT_ROLL)
        self.assertTrue(any("roll window" in r for r in verdict.reasons))

    def test_no_roll_when_itm(self):
        verdict = evaluate_exit(option_type="PUT", dte=10, delta=-0.60, otm_pct=-3.0)
        self.assertNotEqual(verdict.verdict, VERDICT_ROLL)

    def test_delta_breach_closes(self):
        verdict = evaluate_exit(option_type="PUT", dte=30, delta=-0.70, otm_pct=1.0)
        self.assertEqual(verdict.verdict, VERDICT_CLOSE)
        self.assertTrue(any("Delta" in r for r in verdict.reasons))

    def test_deep_itm_closes(self):
        verdict = evaluate_exit(option_type="CALL", dte=20, delta=0.80, otm_pct=-18.0)
        self.assertEqual(verdict.verdict, VERDICT_CLOSE)
        self.assertTrue(any("Deeply ITM" in r for r in verdict.reasons))

    def test_earnings_before_expiry_at_risk_closes(self):
        verdict = evaluate_exit(option_type="PUT", dte=14, delta=-0.30, otm_pct=3.0, days_to_earnings=10)
        self.assertEqual(verdict.verdict, VERDICT_CLOSE)
        self.assertTrue(any("Earnings" in r for r in verdict.reasons))

    def test_earnings_far_otm_only_warns(self):
        verdict = evaluate_exit(option_type="PUT", dte=40, delta=-0.20, otm_pct=12.0, days_to_earnings=10)
        self.assertEqual(verdict.verdict, VERDICT_HOLD)
        self.assertTrue(any("Earnings" in r for r in verdict.reasons))

    def test_priority_close_beats_profit_take(self):
        # Delta breach AND profit target hit: CLOSE wins (first match).
        verdict = evaluate_exit(option_type="PUT", dte=30, delta=-0.70, otm_pct=2.0, captured_profit_pct=80.0)
        self.assertEqual(verdict.verdict, VERDICT_CLOSE)

    def test_custom_thresholds(self):
        t = ExitThresholds(profit_take_pct=25.0, roll_dte=30, exit_delta=0.50, deep_itm_pct=10.0)
        verdict = evaluate_exit(
            option_type="PUT", dte=35, delta=-0.20, otm_pct=8.0, captured_profit_pct=30.0, thresholds=t
        )
        self.assertEqual(verdict.verdict, VERDICT_TAKE_PROFIT)


class TestCapturedProfitPct(unittest.TestCase):
    def test_basic_capture(self):
        self.assertEqual(captured_profit_pct_for_short(2.00, 1.00), 50.0)
        self.assertEqual(captured_profit_pct_for_short(1.00, 0.25), 75.0)

    def test_mark_above_entry_clamps_to_zero(self):
        self.assertEqual(captured_profit_pct_for_short(1.00, 1.50), 0.0)

    def test_unknown_entry_returns_none(self):
        self.assertIsNone(captured_profit_pct_for_short(0, 0.50))
        self.assertIsNone(captured_profit_pct_for_short(None, 0.50))


if __name__ == "__main__":
    unittest.main()
