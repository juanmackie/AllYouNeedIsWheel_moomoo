"""
Tests for api/services/options_lab.py — Options Lab payoff/scenario analysis
"""

import unittest
import math
from api.services.options_lab import (
    compute_options_lab,
    compute_roll_comparison,
    _compute_breakeven,
    _compute_assignment_cost,
    _compute_expected_move_buffer,
    _compute_max_loss,
    _compute_max_profit,
    _compute_iv_crush_sensitivity,
    _compute_stress_loss,
    _compute_max_contracts,
    _compute_cash_required,
    _compute_return_if_unchanged,
    _compute_return_if_assigned,
    _compute_pop,
)


class TestComputeBreakeven(unittest.TestCase):

    def test_put_breakeven(self):
        result = _compute_breakeven(100.0, 95.0, 2.50, "PUT")
        self.assertEqual(result, 92.50)

    def test_call_breakeven(self):
        result = _compute_breakeven(100.0, 105.0, 2.50, "CALL")
        self.assertEqual(result, 107.50)

    def test_put_breakeven_atm(self):
        result = _compute_breakeven(100.0, 100.0, 3.00, "PUT")
        self.assertEqual(result, 97.00)


class TestComputeAssignmentCost(unittest.TestCase):

    def test_put_assignment_cost(self):
        result = _compute_assignment_cost(95.0, "PUT")
        self.assertEqual(result, 9500.00)

    def test_call_assignment_cost_zero(self):
        result = _compute_assignment_cost(100.0, "CALL")
        self.assertEqual(result, 0.0)

    def test_put_assignment_cost_zero_strike(self):
        result = _compute_assignment_cost(0.0, "PUT")
        self.assertEqual(result, 0.0)


class TestComputeExpectedMoveBuffer(unittest.TestCase):

    def test_put_positive_buffer(self):
        result = _compute_expected_move_buffer(100.0, 0.20, 7, 95.0, "PUT")
        self.assertGreater(result, 0.0)

    def test_put_negative_buffer(self):
        result = _compute_expected_move_buffer(100.0, 0.50, 30, 95.0, "PUT")
        self.assertLess(result, 0.0)

    def test_call_positive_buffer(self):
        result = _compute_expected_move_buffer(100.0, 0.20, 7, 105.0, "CALL")
        self.assertGreater(result, 0.0)

    def test_zero_stock_price(self):
        result = _compute_expected_move_buffer(0.0, 0.20, 7, 95.0, "PUT")
        self.assertEqual(result, 0.0)

    def test_zero_iv(self):
        result = _compute_expected_move_buffer(100.0, 0.0, 7, 95.0, "PUT")
        self.assertEqual(result, 0.0)

    def test_zero_dte(self):
        result = _compute_expected_move_buffer(100.0, 0.20, 0, 95.0, "PUT")
        self.assertEqual(result, 0.0)

    def test_percentage_iv_normalized(self):
        result_pct = _compute_expected_move_buffer(100.0, 30.0, 21, 95.0, "PUT")
        result_dec = _compute_expected_move_buffer(100.0, 0.30, 21, 95.0, "PUT")
        self.assertAlmostEqual(result_pct, result_dec, delta=0.01)


class TestComputeMaxLoss(unittest.TestCase):

    def test_put_max_loss(self):
        result = _compute_max_loss(95.0, 2.50, "PUT")
        self.assertEqual(result, 9250.0)

    def test_call_max_loss(self):
        result = _compute_max_loss(100.0, 2.50, "CALL")
        self.assertEqual(result, 250.0)

    def test_put_max_loss_zero_strike(self):
        result = _compute_max_loss(0.0, 2.50, "PUT")
        self.assertEqual(result, -250.0)


class TestComputeMaxProfit(unittest.TestCase):

    def test_put_max_profit(self):
        result = _compute_max_profit(95.0, 2.50, 100.0, "PUT")
        self.assertEqual(result, 250.0)

    def test_call_max_profit_otm(self):
        result = _compute_max_profit(105.0, 2.50, 100.0, "CALL")
        self.assertEqual(result, 750.0)

    def test_call_max_profit_itm(self):
        result = _compute_max_profit(90.0, 2.50, 100.0, "CALL")
        self.assertEqual(result, 250.0)


class TestComputeIVCrushSensitivity(unittest.TestCase):

    def test_crush_1pt(self):
        result = _compute_iv_crush_sensitivity(100.0, 95.0, 0.30, 21, 0.15, "PUT")
        self.assertGreater(result["crush_1pt"], 0.0)
        self.assertGreater(result["crush_2pt"], result["crush_1pt"])

    def test_crush_zero_iv(self):
        result = _compute_iv_crush_sensitivity(100.0, 95.0, 0.0, 21, 0.15, "PUT")
        self.assertEqual(result["crush_1pt"], 0.0)
        self.assertEqual(result["crush_2pt"], 0.0)

    def test_crush_zero_dte(self):
        result = _compute_iv_crush_sensitivity(100.0, 95.0, 0.30, 0, 0.15, "PUT")
        self.assertEqual(result["crush_1pt"], 0.0)

    def test_crush_negative_vega(self):
        result = _compute_iv_crush_sensitivity(100.0, 95.0, 0.30, 21, -0.15, "PUT")
        self.assertEqual(result["crush_1pt"], 15.0)
        self.assertEqual(result["crush_2pt"], 30.0)

    def test_crush_premium_loss_matches_crush(self):
        result = _compute_iv_crush_sensitivity(100.0, 95.0, 0.30, 21, 0.15, "PUT")
        self.assertEqual(result["premium_loss_1pt"], result["crush_1pt"])
        self.assertEqual(result["premium_loss_2pt"], result["crush_2pt"])

    def test_crush_new_iv_decreases(self):
        result = _compute_iv_crush_sensitivity(100.0, 95.0, 0.30, 21, 0.15, "PUT")
        self.assertLess(result["new_iv_2pt"], result["new_iv_1pt"])


class TestComputeStressLoss(unittest.TestCase):

    def test_put_stress_loss(self):
        result = _compute_stress_loss(100.0, 95.0, 250.0, -0.20, "PUT")
        expected = (95.0 - 100.0 * 0.8) * 100 - 250.0
        self.assertEqual(result, round(expected, 2))

    def test_call_stress_loss(self):
        result = _compute_stress_loss(100.0, 105.0, 250.0, 0.20, "CALL")
        self.assertEqual(result, 250.0)

    def test_put_stress_loss_atm(self):
        result = _compute_stress_loss(95.0, 95.0, 200.0, -0.50, "PUT")
        expected = (95.0 - 95.0 * 0.8) * 100 - 200.0
        self.assertEqual(result, round(expected, 2))


class TestComputeMaxContracts(unittest.TestCase):

    def test_put_by_cash(self):
        ctx = {"account_value": 100000.0, "cash_balance": 50000.0, "available_cash": 50000.0}
        result = _compute_max_contracts(100.0, 250.0, "PUT", ctx)
        self.assertGreater(result["by_cash"], 0)
        self.assertGreater(result["by_risk"], 0)
        self.assertGreater(result["recommended"], 0)

    def test_put_by_cash_limited(self):
        ctx = {"account_value": 10000.0, "cash_balance": 2000.0, "available_cash": 2000.0}
        result = _compute_max_contracts(100.0, 250.0, "PUT", ctx)
        self.assertEqual(result["by_cash"], 0)

    def test_call_returns_zero_defaults(self):
        ctx = {"account_value": 100000.0, "cash_balance": 50000.0}
        result = _compute_max_contracts(0.0, 250.0, "CALL", ctx)
        self.assertEqual(result["by_cash"], 0)
        self.assertEqual(result["by_risk"], 0)
        self.assertEqual(result["recommended"], 0)

    def test_put_zero_cash_required(self):
        ctx = {"account_value": 100000.0, "cash_balance": 50000.0, "available_cash": 50000.0}
        result = _compute_max_contracts(0.0, 250.0, "PUT", ctx)
        self.assertEqual(result["by_cash"], 0)
        self.assertEqual(result["recommended"], 0)

    def test_put_uses_broker_buying_power(self):
        ctx = {
            "account_value": 100000.0,
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "broker_buying_power": 30000.0,
        }
        result = _compute_max_contracts(100.0, 250.0, "PUT", ctx)
        self.assertEqual(result["by_cash"], 3)

    def test_put_recommended_is_min_of_cash_and_risk(self):
        ctx = {"account_value": 5000.0, "cash_balance": 50000.0, "available_cash": 50000.0}
        result = _compute_max_contracts(100.0, 250.0, "PUT", ctx)
        self.assertEqual(result["recommended"], min(result["by_cash"], result["by_risk"]))


class TestComputeCashRequired(unittest.TestCase):

    def test_put_cash_required(self):
        result = _compute_cash_required(95.0, "PUT")
        self.assertEqual(result, 9500.0)

    def test_call_cash_required_zero(self):
        result = _compute_cash_required(100.0, "CALL")
        self.assertEqual(result, 0.0)

    def test_put_zero_strike(self):
        result = _compute_cash_required(0.0, "PUT")
        self.assertEqual(result, 0.0)


class TestComputeReturnIfUnchanged(unittest.TestCase):

    def test_put_return(self):
        result = _compute_return_if_unchanged(250.0, 100.0, "PUT", 30)
        self.assertGreater(result["return_pct"], 0.0)
        self.assertGreater(result["annualized_pct"], 0.0)

    def test_put_zero_strike(self):
        result = _compute_return_if_unchanged(250.0, 0.0, "PUT", 30)
        self.assertEqual(result["return_pct"], 0.0)
        self.assertEqual(result["annualized_pct"], 0.0)

    def test_put_zero_dte(self):
        result = _compute_return_if_unchanged(250.0, 100.0, "PUT", 0)
        self.assertEqual(result["annualized_pct"], 0.0)

    def test_call_returns_empty(self):
        result = _compute_return_if_unchanged(250.0, 100.0, "CALL", 30)
        self.assertEqual(result["return_pct"], 0.0)


class TestComputeReturnIfAssigned(unittest.TestCase):

    def test_put_assigned(self):
        result = _compute_return_if_assigned(100.0, 95.0, 2.50, "PUT")
        self.assertIn("cost_basis", result)
        self.assertIn("return_pct", result)
        self.assertGreater(result["return_pct"], 0.0)

    def test_put_zero_stock_price(self):
        result = _compute_return_if_assigned(0.0, 95.0, 2.50, "PUT")
        self.assertEqual(result, {})

    def test_call_assigned(self):
        result = _compute_return_if_assigned(100.0, 105.0, 2.50, "CALL")
        self.assertIn("strike_gain_pct", result)
        self.assertIn("total_return_pct", result)
        self.assertGreater(result["total_return_pct"], 0.0)

    def test_call_zero_stock_price(self):
        result = _compute_return_if_assigned(0.0, 105.0, 2.50, "CALL")
        self.assertEqual(result, {})


class TestComputePop(unittest.TestCase):

    def test_put_pop(self):
        result = _compute_pop(-0.20, "PUT")
        self.assertEqual(result, 80.0)

    def test_call_pop(self):
        result = _compute_pop(0.20, "CALL")
        self.assertEqual(result, 20.0)

    def test_put_atm_pop(self):
        result = _compute_pop(-0.50, "PUT")
        self.assertEqual(result, 50.0)

    def test_put_deep_otm_pop(self):
        result = _compute_pop(-0.05, "PUT")
        self.assertEqual(result, 95.0)


class TestComputeOptionsLab(unittest.TestCase):

    def setUp(self):
        self.contract = {
            "option_type": "PUT",
            "strike": 95.0,
            "expiration": "20261220",
            "stock_price": 100.0,
            "mid_price": 2.50,
            "bid": 2.30,
            "ask": 2.70,
            "implied_volatility": 0.30,
            "delta": -0.20,
            "gamma": 0.05,
            "theta": -0.05,
            "vega": 0.15,
            "dte": 21,
            "open_interest": 500,
            "volume": 100,
        }
        self.portfolio = {
            "cash_balance": 50000.0,
            "account_value": 100000.0,
            "available_cash": 50000.0,
        }

    def test_full_analysis_contains_all_keys(self):
        result = compute_options_lab(self.contract, self.portfolio)
        expected_keys = {
            "breakeven", "assignment_cost", "expected_move_buffer",
            "max_loss", "max_profit", "iv_crush_sensitivity",
            "stress_loss", "max_contracts", "cash_required",
            "return_if_unchanged", "return_if_assigned", "pop",
            "greeks", "parameters",
        }
        self.assertEqual(set(result.keys()), expected_keys)

    def test_breakeven_is_reasonable(self):
        result = compute_options_lab(self.contract, self.portfolio)
        self.assertAlmostEqual(result["breakeven"], 92.50)

    def test_assignment_cost_matches_strike(self):
        result = compute_options_lab(self.contract, self.portfolio)
        self.assertEqual(result["assignment_cost"], 9500.0)

    def test_max_loss_is_less_than_assignment_cost(self):
        result = compute_options_lab(self.contract, self.portfolio)
        self.assertLess(result["max_loss"], result["assignment_cost"])

    def test_pop_matches_delta(self):
        result = compute_options_lab(self.contract, self.portfolio)
        self.assertEqual(result["pop"], 80.0)

    def test_iv_crush_sensitivity_present(self):
        result = compute_options_lab(self.contract, self.portfolio)
        crush = result["iv_crush_sensitivity"]
        self.assertIn("crush_1pt", crush)
        self.assertIn("crush_2pt", crush)
        self.assertIn("premium_loss_1pt", crush)

    def test_greeks_present(self):
        result = compute_options_lab(self.contract, self.portfolio)
        greeks = result["greeks"]
        self.assertIn("delta", greeks)
        self.assertIn("gamma", greeks)
        self.assertIn("theta", greeks)
        self.assertIn("vega", greeks)
        self.assertAlmostEqual(greeks["delta"], -0.20, places=2)

    def test_parameters_match_input(self):
        result = compute_options_lab(self.contract, self.portfolio)
        params = result["parameters"]
        self.assertEqual(params["strike"], 95.0)
        self.assertEqual(params["stock_price"], 100.0)
        self.assertEqual(params["dte"], 21)
        self.assertEqual(params["option_type"], "PUT")

    def test_call_analysis(self):
        call_contract = dict(self.contract, option_type="CALL", strike=105.0, delta=0.20)
        result = compute_options_lab(call_contract, self.portfolio)
        self.assertEqual(result["parameters"]["option_type"], "CALL")
        self.assertEqual(result["assignment_cost"], 0.0)
        self.assertGreater(result["max_loss"], 0.0)

    def test_empty_portfolio_falls_back_gracefully(self):
        result = compute_options_lab(self.contract, {})
        self.assertIsNotNone(result)
        self.assertEqual(result["cash_required"], 9500.0)
        self.assertEqual(result["max_contracts"]["by_cash"], 0)

    def test_missing_fields_handled(self):
        minimal = {"option_type": "PUT", "strike": 95.0}
        result = compute_options_lab(minimal, {})
        self.assertIsNotNone(result)
        self.assertEqual(result["breakeven"], 95.0)

    def test_premium_calculated_correctly(self):
        result = compute_options_lab(self.contract, self.portfolio)
        self.assertEqual(result["parameters"]["premium"], 250.0)

    def test_expected_move_buffer_plausible(self):
        result = compute_options_lab(self.contract, self.portfolio)
        buf = result["expected_move_buffer"]
        self.assertGreater(buf, -100.0)
        self.assertLess(buf, 100.0)

    def test_stress_loss_plausible(self):
        result = compute_options_lab(self.contract, self.portfolio)
        self.assertGreater(result["stress_loss"], 0.0)


class TestOptionsLabRoutePureCalculator(unittest.TestCase):
    """Route-level tests verifying /api/options-lab/analyze is a pure calculator."""

    def setUp(self):
        from flask import Flask
        from api.routes.options_lab import bp
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def test_analyze_rejects_missing_strike(self):
        resp = self.client.post(
            '/api/options-lab/analyze',
            json={"contract": {"option_type": "PUT"}},
        )
        self.assertEqual(resp.status_code, 400)

    def test_analyze_rejects_invalid_option_type(self):
        resp = self.client.post(
            '/api/options-lab/analyze',
            json={"contract": {"strike": 95.0, "option_type": "SPREAD"}},
        )
        self.assertEqual(resp.status_code, 400)

    def test_analyze_ignores_client_portfolio_context(self):
        """The pure calculator should not use client-supplied portfolio context."""
        resp = self.client.post(
            '/api/options-lab/analyze',
            json={
                "contract": {
                    "strike": 95.0,
                    "option_type": "PUT",
                    "stock_price": 100.0,
                    "mid_price": 2.50,
                    "bid": 2.30,
                    "ask": 2.70,
                    "implied_volatility": 0.30,
                    "delta": -0.20,
                    "dte": 21,
                },
                "portfolio_context": {
                    "account_value": 1000000.0,
                    "broker_buying_power": 900000.0,
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        analysis = data.get("analysis", {})
        max_contracts = analysis.get("max_contracts", {})
        self.assertEqual(max_contracts.get("by_cash", -1), 0)

    def test_analyze_call_accepted(self):
        resp = self.client.post(
            '/api/options-lab/analyze',
            json={
                "contract": {
                    "strike": 105.0,
                    "option_type": "CALL",
                    "stock_price": 100.0,
                    "mid_price": 2.50,
                    "bid": 2.30,
                    "ask": 2.70,
                    "dte": 21,
                },
            },
        )
        self.assertEqual(resp.status_code, 200)


class TestComputeRollComparison(unittest.TestCase):

    def setUp(self):
        self.current = {
            "option_type": "PUT",
            "strike": 95.0,
            "expiration": "20261220",
            "stock_price": 100.0,
            "mid_price": 2.50,
            "bid": 2.30,
            "ask": 2.70,
            "implied_volatility": 0.30,
            "delta": -0.20,
            "gamma": 0.05,
            "theta": -0.05,
            "vega": 0.15,
            "dte": 7,
        }
        self.roll_target = {
            "option_type": "PUT",
            "strike": 95.0,
            "expiration": "20270120",
            "stock_price": 100.0,
            "mid_price": 3.50,
            "bid": 3.30,
            "ask": 3.70,
            "implied_volatility": 0.30,
            "delta": -0.20,
            "gamma": 0.05,
            "theta": -0.04,
            "vega": 0.15,
            "dte": 35,
        }

    def test_roll_comparison_contains_all_keys(self):
        result = compute_roll_comparison(self.current, self.roll_target)
        expected_keys = {"current", "roll_target", "premium_difference", "dte_difference", "net_credit", "net_debit", "recommendation"}
        self.assertEqual(set(result.keys()), expected_keys)

    def test_roll_premium_difference_positive(self):
        result = compute_roll_comparison(self.current, self.roll_target)
        self.assertGreater(result["premium_difference"], 0.0)

    def test_roll_dte_difference_positive(self):
        result = compute_roll_comparison(self.current, self.roll_target)
        self.assertGreater(result["dte_difference"], 0)

    def test_roll_is_net_credit(self):
        result = compute_roll_comparison(self.current, self.roll_target)
        self.assertTrue(result["net_credit"])
        self.assertFalse(result["net_debit"])

    def test_roll_net_debit_when_premium_drops(self):
        lower_premium = dict(self.roll_target, mid_price=1.50)
        result = compute_roll_comparison(self.current, lower_premium)
        self.assertTrue(result["net_debit"])
        self.assertFalse(result["net_credit"])

    def test_roll_recommendation_credit(self):
        result = compute_roll_comparison(self.current, self.roll_target)
        self.assertIn("additional premium", result["recommendation"].lower())

    def test_roll_recommendation_debit_large(self):
        much_lower = dict(self.roll_target, mid_price=0.50)
        result = compute_roll_comparison(self.current, much_lower)
        self.assertIn("net debit", result["recommendation"].lower())

    def test_roll_recommendation_neutral(self):
        same_premium = dict(self.roll_target, mid_price=2.50)
        result = compute_roll_comparison(self.current, same_premium)
        self.assertIn("neutral", result["recommendation"].lower())

    def test_roll_current_has_lab_metrics(self):
        result = compute_roll_comparison(self.current, self.roll_target)
        self.assertIn("breakeven", result["current"])
        self.assertIn("parameters", result["current"])

    def test_roll_target_has_lab_metrics(self):
        result = compute_roll_comparison(self.current, self.roll_target)
        self.assertIn("breakeven", result["roll_target"])
        self.assertIn("parameters", result["roll_target"])


if __name__ == "__main__":
    unittest.main()
