"""Tests for core/sizing.py — portfolio-aware exposure arithmetic."""

import unittest

from core.scoring_factors import _compute_recommended_contracts
from core.sizing import deployment_plan, existing_short_exposure_by_underlying
from core.wheel_decision import WheelDecision


class TestExistingShortExposure(unittest.TestCase):
    def test_empty_and_missing(self):
        self.assertEqual(existing_short_exposure_by_underlying(None), {})
        self.assertEqual(existing_short_exposure_by_underlying({}), {})
        self.assertEqual(existing_short_exposure_by_underlying({"short_puts": None}), {})

    def test_combines_puts_and_calls_per_underlying(self):
        ctx = {
            "short_puts": {"TSLA260904P00300000": 2, "AAPL": 1},
            "short_calls": {"TSLA261016C00320000": 1},
        }
        exposure = existing_short_exposure_by_underlying(ctx)
        self.assertEqual(exposure["TSLA"], 3)
        self.assertEqual(exposure["AAPL"], 1)

    def test_negative_counts_clamped(self):
        ctx = {"short_puts": {"SPY": -3}}
        self.assertEqual(existing_short_exposure_by_underlying(ctx)["SPY"], 0)


class TestRecommendedContracts(unittest.TestCase):
    def test_uses_preset_cash_percentage(self):
        decision = WheelDecision(option_type="PUT", cash_required=4_000, max_contracts=3)
        context = {"cash_available_for_csp": 10_000, "account_value": 100_000}
        self.assertEqual(_compute_recommended_contracts(decision, context, {"max_buying_power_pct_per_csp": 80}), 2)

    def test_does_not_force_contract_above_configured_cap(self):
        decision = WheelDecision(option_type="PUT", cash_required=8_000, max_contracts=1)
        context = {"cash_available_for_csp": 10_000, "account_value": 100_000}
        self.assertEqual(_compute_recommended_contracts(decision, context, {"max_buying_power_pct_per_csp": 50}), 0)

    def test_caps_at_true_cash_capacity(self):
        decision = WheelDecision(option_type="PUT", cash_required=4_000, max_contracts=3)
        context = {"cash_available_for_csp": 10_000, "account_value": 100_000}
        self.assertEqual(_compute_recommended_contracts(decision, context, {"max_buying_power_pct_per_csp": 100}), 2)


class TestDeploymentPlan(unittest.TestCase):
    def test_allocates_ranked_affordable_puts_and_tracks_remaining_cash(self):
        candidates = [
            {
                "ticker": "AAA",
                "option_type": "PUT",
                "cash_required": 6_000,
                "recommended_contracts": 1,
                "bid_premium_per_contract": 120,
            },
            {
                "ticker": "BBB",
                "option_type": "PUT",
                "cash_required": 5_000,
                "recommended_contracts": 1,
                "bid_premium_per_contract": 80,
            },
        ]
        plan = deployment_plan(candidates, 10_000)
        self.assertEqual([item["ticker"] for item in plan], ["AAA"])
        self.assertEqual(plan[0]["deployment_income"], 120)
        self.assertEqual(plan[0]["deployment_cash_remaining"], 4_000)

    def test_ignores_calls_and_duplicate_underlyings(self):
        candidates = [
            {"ticker": "AAA", "option_type": "CALL", "cash_required": 1, "recommended_contracts": 1},
            {"ticker": "AAA", "option_type": "PUT", "cash_required": 2_000, "recommended_contracts": 1},
            {"ticker": "AAA", "option_type": "PUT", "cash_required": 1_000, "recommended_contracts": 1},
        ]
        plan = deployment_plan(candidates, 5_000)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["ticker"], "AAA")


if __name__ == "__main__":
    unittest.main()
