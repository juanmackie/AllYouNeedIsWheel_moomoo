import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from api.services.portfolio_scoring import build_portfolio_context, score_position
from core.utils import market_now


class TestPortfolioScoringFees(unittest.TestCase):
    @patch("api.services.fee_model.get_measured_option_fee_model")
    @patch("api.services.config.get_current_identity")
    @patch("api.services.config.get_config")
    def test_context_loads_fee_evidence_for_the_active_account(self, get_config, get_identity, get_fee_model):
        get_config.return_value.get.return_value = "configured-account"
        get_identity.return_value = ("REAL", "opaque-account")
        get_fee_model.return_value = {"BUY": {"fee_per_contract": 0.53}, "SELL": {"fee_per_contract": 0.55}}
        portfolio = MagicMock()
        portfolio.get_portfolio_summary.return_value = {"available_cash": 1000, "account_value": 10000}
        portfolio._get_db.return_value = "database"

        context, _, _ = build_portfolio_context([], portfolio)

        get_fee_model.assert_called_once_with("database", "REAL", "opaque-account")
        self.assertEqual(context["measured_option_fees"]["BUY"]["fee_per_contract"], 0.53)

    def test_roll_out_down_uses_measured_side_specific_fees(self):
        connection = MagicMock()
        connection.get_stock_price.return_value = 100.0
        iv_earnings = MagicMock()
        iv_earnings.get_iv_environment_score.return_value = (0, 0, "insufficient_history")
        iv_earnings.get_earnings_score_impact.return_value = (0, {})
        iv_earnings.get_iv_percentile.return_value = 0
        iv_earnings.get_earnings_info.return_value = {}
        portfolio_context = {
            "positions": {},
            "cash_balance": 10000,
            "account_value": 50000,
            "measured_option_fees": {
                "lookback_days": 90,
                "BUY": {"fee_per_contract": 0.53, "sample_orders": 20},
                "SELL": {"fee_per_contract": 0.55, "sample_orders": 20},
            },
            "exit_thresholds": {
                "profit_take_pct": 50,
                "roll_dte": 4,
                "exit_delta": 0.65,
                "deep_itm_pct": 15,
                "stop_loss_pct": -100,
                "rotate_min_multiplier": 2,
            },
        }
        current_expiration = (market_now().date() + timedelta(days=16)).strftime("%Y%m%d")
        target_expiration = (market_now().date() + timedelta(days=23)).strftime("%Y%m%d")
        position = {
            "symbol": "AAPL",
            "option_type": "PUT",
            "strike": 105,
            "expiration": current_expiration,
            "position": -1,
            "bid": 1.0,
            "ask": 1.1,
            "last": 1.05,
            "delta": -0.3,
            "avg_cost": 2.0,
            "implied_volatility": 0.2,
        }
        candidate = {
            "copy_eligible": True,
            "ticker": "AAPL",
            "option_type": "PUT",
            "expiration": target_expiration,
            "strike": 100,
            "bid": 2.0,
            "ask": 2.1,
            "dte": 23,
            "capital_velocity_per_day": 1.0,
            "quote_age_sec": 1,
            "stock_price": 100,
            "cash_required": 10000,
        }
        decision = score_position(
            position,
            connection,
            portfolio_context,
            iv_earnings,
            fresh_candidates=[candidate],
        )

        self.assertEqual(decision.exit_verdict, "ROLL_OUT_DOWN")
        self.assertAlmostEqual(decision.roll_target["net_credit_after_fees"], 88.92)
        self.assertTrue(decision.roll_target["fees_known"])
        self.assertIn("measured from your fills", decision.roll_target["fee_model_note"].lower())
        self.assertEqual(decision.roll_target["fee_sample_orders"], {"BUY": 20, "SELL": 20})

    def test_missing_side_fee_does_not_create_zero_fee_net_credit(self):
        connection = MagicMock()
        connection.get_stock_price.return_value = 100.0
        iv_earnings = MagicMock()
        iv_earnings.get_iv_environment_score.return_value = (0, 0, "insufficient_history")
        iv_earnings.get_earnings_score_impact.return_value = (0, {})
        iv_earnings.get_iv_percentile.return_value = 0
        iv_earnings.get_earnings_info.return_value = {}
        portfolio_context = {
            "positions": {},
            "cash_balance": 10000,
            "account_value": 50000,
            "measured_option_fees": {
                "BUY": {"fee_per_contract": 0.53, "sample_orders": 20},
                "SELL": {"fee_per_contract": None, "sample_orders": 0},
            },
            "exit_thresholds": {
                "profit_take_pct": 50,
                "roll_dte": 4,
                "exit_delta": 0.65,
                "deep_itm_pct": 15,
                "stop_loss_pct": -100,
                "rotate_min_multiplier": 2,
            },
        }
        current_expiration = (market_now().date() + timedelta(days=16)).strftime("%Y%m%d")
        target_expiration = (market_now().date() + timedelta(days=23)).strftime("%Y%m%d")
        position = {
            "symbol": "AAPL",
            "option_type": "PUT",
            "strike": 105,
            "expiration": current_expiration,
            "position": -1,
            "bid": 1.0,
            "ask": 1.1,
            "last": 1.05,
            "delta": -0.3,
            "avg_cost": 2.0,
            "implied_volatility": 0.2,
        }
        candidate = {
            "copy_eligible": True,
            "ticker": "AAPL",
            "option_type": "PUT",
            "expiration": target_expiration,
            "strike": 100,
            "bid": 2.0,
            "ask": 2.1,
            "dte": 23,
            "capital_velocity_per_day": 1.0,
            "quote_age_sec": 1,
            "stock_price": 100,
            "cash_required": 10000,
        }
        decision = score_position(
            position,
            connection,
            portfolio_context,
            iv_earnings,
            fresh_candidates=[candidate],
        )

        self.assertNotEqual(decision.exit_verdict, "ROLL_OUT_DOWN")
        self.assertIsNone(decision.roll_target)
        self.assertTrue(any("unknown fees are not zero" in reason for reason in decision.exit_reasons))


if __name__ == "__main__":
    unittest.main()
