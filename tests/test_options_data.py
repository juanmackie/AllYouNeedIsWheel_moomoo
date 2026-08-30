"""
Tests for api/services/options_data.py - OptionsDataService candidate handling.
"""

import os
import sys
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAfterHoursChainFetch(unittest.TestCase):
    @patch("api.services.options_data.is_market_open", return_value=False)
    def test_closed_market_fetches_live_chain_before_persisted_cache(self, _market_open):
        from api.services.options_data import fetch_option_chain_live_first

        conn = MagicMock()
        conn.get_option_chain.return_value = {
            "right": "P",
            "stock_price": 100.0,
            "options": [{"strike": 90.0, "bid": 1.0}],
        }
        db = MagicMock()

        result = fetch_option_chain_live_first(
            conn,
            db,
            {"broker_cache_after_hours": True},
            "AAPL",
            "20260918",
            "P",
            target_strike=90.0,
        )

        self.assertEqual(result["chain_source"], "broker")
        conn.get_option_chain.assert_called_once_with("AAPL", "20260918", "P", target_strike=90.0, force_refresh=True)
        db.get_latest_option_chain.assert_not_called()
        db.save_option_chain_snapshot.assert_called_once()

    @patch("api.services.options_data.is_market_open", return_value=False)
    def test_closed_market_falls_back_to_persisted_broker_chain(self, _market_open):
        from api.services.options_data import fetch_option_chain_live_first

        conn = MagicMock()
        conn.get_option_chain.return_value = None
        db = MagicMock()
        db.get_latest_option_chain.return_value = {
            "as_of": "2026-08-28T20:00:00+00:00",
            "source": "broker",
            "chain_data": {
                "right": "P",
                "options": [{"strike": 90.0, "bid": 1.0}],
            },
        }

        result = fetch_option_chain_live_first(conn, db, {"broker_cache_after_hours": True}, "AAPL", "20260918", "P")

        self.assertEqual(result["chain_source"], "persisted-broker")
        self.assertEqual(result["quote_timestamp"], "2026-08-28T20:00:00+00:00")
        db.get_latest_option_chain.assert_called_once_with("AAPL", "P", max_age_hours=168)


class TestRecommendationOptionValidation(unittest.TestCase):
    def test_broker_option_without_dte_is_validated_from_expiration_later(self):
        from api.services.recommendations import _is_valid_external_option

        option = {
            "strike": 140.0,
            "option_type": "PUT",
            "bid": 1.5,
            "ask": 1.6,
            "last": 1.55,
        }

        self.assertTrue(_is_valid_external_option(option, 150.0))


class TestOptionsDataServiceCandidateFiltering(unittest.TestCase):
    def _make_service(self, config=None):
        from api.services.options_data import OptionsDataService

        iv_earnings = MagicMock()
        iv_earnings.get_iv_environment_score.return_value = (0, 0.5, "normal")
        iv_earnings.get_earnings_score_impact.return_value = (0, None)
        iv_earnings.get_earnings_info.return_value = {}

        config_provider = MagicMock()
        config_provider.config = config or {"cash_reserve_enabled": True}

        screening_provider = MagicMock()
        screening_provider.get_screening_profile.return_value = {
            "min_dte": 7,
            "max_dte": 45,
            "preferred_dte": 21,
        }

        return OptionsDataService(
            connection_provider=MagicMock(),
            config_provider=config_provider,
            db=MagicMock(),
            iv_earnings_service=iv_earnings,
            screening_profile_provider=screening_provider,
            portfolio_context_provider=MagicMock(),
        )

    @patch("api.services.options_data.score_contract")
    def test_build_candidate_filters_hard_blocked_contracts(self, mock_score):
        service = self._make_service()

        decision = MagicMock()
        decision.ticker = "AAPL"
        decision.option_type = "PUT"
        decision.expiration = "20260529"
        decision.strike = 282.5
        decision.hard_blockers = ["Insufficient cash: requires $28250, available $12699"]
        mock_score.return_value = decision

        result = service._build_candidate(
            ticker="AAPL",
            option={
                "strike": 282.5,
                "expiration": "20260529",
                "option_type": "PUT",
                "bid": 0.01,
                "ask": 0.09,
                "last": 0.05,
                "implied_volatility": 0.3,
            },
            stock_price=300.23,
            desired_otm=6,
            profile={},
            portfolio_context={
                "cash_balance": 12699.31,
                "available_cash": 12699.31,
                "cash_available_for_csp": 12699.31,
            },
        )

        self.assertIsNone(result)

    @patch("api.services.options_data.score_contract")
    def test_direct_candidate_scoring_uses_growth_profile_from_toggle(self, mock_score):
        service = self._make_service(
            {
                "cash_reserve_enabled": True,
                "growth_mode": {
                    "enabled": True,
                    "objective": "time_to_2x",
                    "target_account_multiple": 2.0,
                    "max_drawdown_pct": 0.40,
                    "execution_scope": "short_premium_wheel",
                    "long_options_mode": "research_only",
                },
            }
        )

        decision = MagicMock()
        decision.ticker = "INTC"
        decision.option_type = "PUT"
        decision.expiration = "20260529"
        decision.strike = 30.0
        decision.hard_blockers = ["stop after scoring"]
        mock_score.return_value = decision

        service._build_candidate(
            ticker="INTC",
            option={
                "strike": 30.0,
                "expiration": "20260529",
                "option_type": "PUT",
                "chain_source": "broker",
                "bid": 0.25,
                "ask": 0.30,
                "last": 0.28,
                "implied_volatility": 0.35,
            },
            stock_price=35.0,
            desired_otm=6,
            profile={},
            portfolio_context={
                "cash_balance": 12699.31,
                "available_cash": 12699.31,
                "cash_available_for_csp": 12699.31,
            },
        )

        # The resolved screening profile is forwarded so preset growth
        # targets can be applied by the unified scorer.
        growth_profile = mock_score.call_args.kwargs.get("growth_profile")
        self.assertEqual(growth_profile, {})

    def test_put_expirations_use_growth_dte_window_when_toggle_enabled(self):
        from moomoo import RET_OK

        service = self._make_service(
            {
                "cash_reserve_enabled": True,
                "growth_mode": {
                    "enabled": True,
                    "screener_profile": {
                        "csp_min_dte": 30,
                        "csp_max_dte": 45,
                        "csp_preferred_dte": 37,
                        "csp_default_otm_pct": 10,
                        "csp_min_otm_pct": 5,
                        "csp_max_otm_pct": 15,
                    },
                },
            }
        )
        service._screening_profile_provider.get_screening_profile.return_value = {
            "min_dte": 30,
            "max_dte": 45,
            "preferred_dte": 37,
        }

        expirations = [
            (date.today() + timedelta(days=14)).strftime("%Y-%m-%d"),
            (date.today() + timedelta(days=37)).strftime("%Y-%m-%d"),
            (date.today() + timedelta(days=45)).strftime("%Y-%m-%d"),
            (date.today() + timedelta(days=50)).strftime("%Y-%m-%d"),
        ]
        conn = MagicMock()
        conn.get_option_expiration_dates.return_value = (
            RET_OK,
            pd.DataFrame({"expiration_date": expirations}),
        )
        service._connection_provider._ensure_connection.return_value = conn

        result = service.get_option_expirations("INTC", "PUT")

        self.assertEqual(
            [item["dte"] for item in result["expirations"]],
            [37, 45],
        )
        service._screening_profile_provider.get_screening_profile.assert_called_with(
            "PUT",
            growth_mode_config=service._get_config().get("growth_mode", {}),
        )

    def test_option_expirations_accept_compact_broker_dates(self):
        from moomoo import RET_OK

        service = self._make_service()
        service._screening_profile_provider.get_screening_profile.return_value = {
            "min_dte": 7,
            "max_dte": 45,
            "preferred_dte": 21,
        }

        expiration = (date.today() + timedelta(days=14)).strftime("%Y%m%d")
        conn = MagicMock()
        conn.get_option_expiration_dates.return_value = (
            RET_OK,
            pd.DataFrame({"expiration_date": [expiration]}),
        )
        service._connection_provider._ensure_connection.return_value = conn

        result = service.get_option_expirations("UBER", "PUT")

        self.assertEqual(result["expirations"][0]["value"], expiration)
        self.assertEqual(result["expirations"][0]["label"], f"{expiration[0:4]}-{expiration[4:6]}-{expiration[6:8]}")


if __name__ == "__main__":
    unittest.main()
