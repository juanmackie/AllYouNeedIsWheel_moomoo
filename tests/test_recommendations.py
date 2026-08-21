"""
Tests for api/services/recommendations.py — RecommendationEngine class
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRecommendationEngine(unittest.TestCase):
    """Test RecommendationEngine with fully mocked context."""

    def setUp(self):
        self.mock_connection_provider = MagicMock()
        self.mock_config_provider = MagicMock()
        self.mock_config_provider.config = {"cash_reserve_enabled": True}
        self.mock_db = MagicMock()
        self.mock_iv_earnings = MagicMock()
        self.mock_portfolio_context_provider = MagicMock()
        self.mock_portfolio_service_provider = MagicMock()
        self.mock_watchlist_manager = MagicMock()
        self.mock_options_data = MagicMock()
        self.mock_cash_calculator = MagicMock()
        self.mock_iv_earnings.get_iv_environment_score.return_value = (0, 0.5, "normal")
        self.mock_iv_earnings.get_earnings_score_impact.return_value = (0, None)
        self.mock_iv_earnings.get_earnings_info.return_value = {}

        self.mock_conn = MagicMock()
        self.mock_conn.get_stock_price.return_value = 150.0
        self.mock_connection_provider._ensure_connection.return_value = self.mock_conn

        self.mock_portfolio_context = {
            "positions": {
                "AAPL": {"position": 200, "market_price": 150.0, "avg_cost": 145.0},
            },
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "broker_buying_power": 50000.0,
            "broker_buying_power_source": "available_cash",
            "cash_available_for_csp": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "excess_liquidity": 50000.0,
            "short_calls": {},
            "short_puts": {},
        }
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = self.mock_portfolio_context

        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

        self.mock_options_data._process_ticker_for_otm.return_value = {
            "calls": [
                {
                    "strike": 160,
                    "expiration": "20240315",
                    "bid": 2.0,
                    "ask": 2.10,
                    "last": 2.05,
                    "delta": 0.20,
                    "implied_volatility": 0.30,
                    "open_interest": 500,
                    "volume": 100,
                    "dte": 21,
                },
            ],
            "puts": [
                {
                    "strike": 140,
                    "expiration": "20240315",
                    "bid": 1.50,
                    "ask": 1.60,
                    "last": 1.55,
                    "delta": 0.18,
                    "implied_volatility": 0.35,
                    "open_interest": 300,
                    "volume": 200,
                    "dte": 21,
                },
            ],
        }

    def _import_engine(self):
        from api.services.recommendations import RecommendationEngine

        return RecommendationEngine(
            self.mock_connection_provider,
            self.mock_config_provider,
            self.mock_db,
            self.mock_iv_earnings,
            self.mock_portfolio_context_provider,
            self.mock_portfolio_service_provider,
            self.mock_watchlist_manager,
            self.mock_options_data,
            self.mock_cash_calculator,
        )

    def test_init_stores_context(self):
        engine = self._import_engine()
        self.assertIs(engine._connection_provider, self.mock_connection_provider)
        self.assertIs(engine.config, self.mock_config_provider.config)
        self.assertIs(engine.db, self.mock_db)

    def test_get_top_recommendations_no_connection(self):
        self.mock_connection_provider._ensure_connection.return_value = None
        engine = self._import_engine()

        result = engine.get_top_recommendations(limit=5)

        self.assertIn("error", result)

    def test_get_top_recommendations_empty_portfolio(self):
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            "positions": {},
            "cash_balance": 0,
            "short_calls": {},
            "short_puts": {},
        }
        engine = self._import_engine()

        result = engine.get_top_recommendations(limit=5)

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)

    def test_get_top_recommendations_scans_complete_watchlist_union(self):
        """A feasible scan evaluates every watchlist symbol; it never truncates."""
        tickers = [f"TICK{i}" for i in range(20)]
        self.mock_watchlist_manager.get_effective_watchlist.return_value = tickers
        self.mock_watchlist_manager.get_effective_watchlist_with_origins.return_value = [
            {"ticker": t, "origins": ["config"]} for t in tickers
        ]
        self.mock_watchlist_manager.preflight_scan_feasibility.return_value = {
            "feasible": True,
            "watchlist_size": 20,
            "estimated_scan_sec": 60.0,
            "freshness_window_sec": 120,
            "chain_calls": 40,
            "chain_quota_ok": True,
            "recommended_max_size": 12,
        }
        engine = self._import_engine()

        with patch.object(engine, "_fetch_watchlist_ticker_csp") as mock_fetch:
            mock_fetch.return_value = []
            with patch("api.services.recommendations.is_market_open", return_value=True):
                engine.get_top_recommendations(limit=5)

        self.assertEqual(mock_fetch.call_count, 20)
        called_tickers = [call[0][0] for call in mock_fetch.call_args_list]
        self.assertEqual(called_tickers, tickers)

    def test_get_top_recommendations_ranks_by_premium_velocity(self):
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["AAA", "BBB"]
        engine = self._import_engine()

        faster_daily = {
            "ticker": "AAA",
            "stock_price": 200.0,
            "option_type": "PUT",
            "strike": 200.0,
            "expiration": "20240315",
            "dte": 10,
            "mid_price": 1.00,
            "premium_per_contract": 110.0,
            "bid": 1.10,
            "ask": 1.05,
            "annualized_return": 18.25,
            "iv_adjusted_return": 30.0,
            "otm_pct": 5.0,
            "delta": -0.20,
            "implied_volatility": 0.30,
            "open_interest": 500,
            "volume": 100,
            "score": 80.0,
            "profile_type": "monthly",
            "research_only": False,
            "warnings": [],
            "wheel_decision": {"contract_score": 80.0, "confidence_score": 100},
        }
        slower_daily = {
            "ticker": "BBB",
            "stock_price": 50.0,
            "option_type": "PUT",
            "strike": 50.0,
            "expiration": "20240315",
            "dte": 20,
            "mid_price": 1.20,
            "premium_per_contract": 120.0,
            "bid": 1.15,
            "ask": 1.25,
            "annualized_return": 43.80,
            "iv_adjusted_return": 45.0,
            "otm_pct": 5.0,
            "delta": -0.20,
            "implied_volatility": 0.30,
            "open_interest": 500,
            "volume": 100,
            "score": 90.0,
            "profile_type": "monthly",
            "research_only": False,
            "warnings": [],
            "wheel_decision": {"contract_score": 90.0, "confidence_score": 100},
        }

        with (
            patch.object(engine, "_fetch_watchlist_ticker_csp", side_effect=[[faster_daily], [slower_daily]]),
            patch("api.services.recommendations.is_market_open", return_value=True),
        ):
            result = engine.get_top_recommendations(limit=5)

        self.assertGreater(len(result["signals"]), 1)
        self.assertEqual(result["signals"][0]["ticker"], "AAA")
        ranked = {signal["ticker"]: signal for signal in result["signals"]}
        self.assertLess(ranked["AAA"]["annualized_return"], ranked["BBB"]["annualized_return"])
        self.assertGreater(
            ranked["AAA"]["premium_per_contract"] / ranked["AAA"]["dte"],
            ranked["BBB"]["premium_per_contract"] / ranked["BBB"]["dte"],
        )

    def test_get_top_recommendations_keeps_post_rank_enrichment_off_hot_path(self):
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["AAPL"]
        engine = self._import_engine()
        csp = {
            "ticker": "AAPL",
            "stock_price": 150.0,
            "option_type": "PUT",
            "strike": 140.0,
            "expiration": "20240315",
            "dte": 21,
            "mid_price": 1.55,
            "premium_per_contract": 155.0,
            "bid": 1.5,
            "ask": 1.6,
            "annualized_return": 20.0,
            "iv_adjusted_return": 50.0,
            "otm_pct": 6.7,
            "delta": -0.18,
            "implied_volatility": 0.35,
            "open_interest": 300,
            "volume": 200,
            "score": 85.0,
            "profile_type": "monthly",
            "warnings": [],
            "wheel_decision": {"contract_score": 85.0, "confidence_score": 100},
        }

        with (
            patch.object(engine, "_fetch_watchlist_ticker_csp", return_value=[csp]),
        ):
            result = engine.get_top_recommendations(limit=5)

        self.assertEqual(result["watchlist_csps"]["count"], 1)
        self.assertEqual(result["enrichment"]["mode"], "none")

    def test_get_top_recommendations_processes_positions(self):
        engine = self._import_engine()

        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = "20240315"
            mock_decision.dte = 21
            mock_decision.mid_price = 2.05
            mock_decision.premium_per_contract = 205.0
            mock_decision.annualized_return = 35.0
            mock_decision.iv_adjusted_return = 30.0
            mock_decision.otm_pct = 6.67
            mock_decision.delta = 0.20
            mock_decision.implied_volatility = 0.30
            mock_decision.open_interest = 500
            mock_decision.volume = 100
            mock_decision.iv_rank = 0.6
            mock_decision.iv_status = "normal"
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = "monthly"
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ["Good premium"]
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {"score": 85.0}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        self.assertTrue(result["success"])
        self.assertGreater(result["total_scored"], 0)
        self.assertIn("signals", result)
        self.assertIn("blocked_signals", result)
        self.assertIn("covered_calls", result)
        self.assertIn("watchlist_csps", result)
        self.assertNotIn("recommendations", result)
        self.assertNotIn("best_plays", result)
        self.assertNotIn("lanes", result)
        self.assertNotIn("blocked_candidates", result)

    def test_get_top_recommendations_respects_limit(self):
        engine = self._import_engine()

        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = "20240315"
            mock_decision.dte = 21
            mock_decision.mid_price = 2.05
            mock_decision.premium_per_contract = 205.0
            mock_decision.annualized_return = 35.0
            mock_decision.iv_adjusted_return = 30.0
            mock_decision.otm_pct = 6.67
            mock_decision.delta = 0.20
            mock_decision.implied_volatility = 0.30
            mock_decision.open_interest = 500
            mock_decision.volume = 100
            mock_decision.iv_rank = 0.6
            mock_decision.iv_status = "normal"
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = "monthly"
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ["Good premium"]
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=1)

        self.assertTrue(result["success"])
        self.assertLessEqual(result["count"], 1)


class TestRecommendationEngineStripPrefix(unittest.TestCase):
    """Test ticker prefix stripping delegates to utils."""

    def test_strip_ticker_prefix_delegates(self):
        with patch("api.services.recommendations.clean_yfinance_ticker") as mock_clean:
            mock_clean.return_value = "AAPL"
            from api.services.recommendations import RecommendationEngine

            engine = RecommendationEngine(
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
            )
            result = engine._strip_ticker_prefix("US.AAPL")

            self.assertEqual(result, "AAPL")
            mock_clean.assert_called_once_with("US.AAPL")


class TestRecommendationEngineSignals(unittest.TestCase):
    """Test the unified signal recommendation structure."""

    def setUp(self):
        self.mock_connection_provider = MagicMock()
        self.mock_config_provider = MagicMock()
        self.mock_config_provider.config = {"cash_reserve_enabled": True}
        self.mock_db = MagicMock()
        self.mock_iv_earnings = MagicMock()
        self.mock_portfolio_context_provider = MagicMock()
        self.mock_portfolio_service_provider = MagicMock()
        self.mock_watchlist_manager = MagicMock()
        self.mock_options_data = MagicMock()
        self.mock_cash_calculator = MagicMock()
        self.mock_iv_earnings.get_iv_environment_score.return_value = (0, 0.5, "normal")
        self.mock_iv_earnings.get_earnings_score_impact.return_value = (0, None)
        self.mock_iv_earnings.get_earnings_info.return_value = {}

        self.mock_conn = MagicMock()
        self.mock_conn.get_stock_price.return_value = 150.0
        self.mock_connection_provider._ensure_connection.return_value = self.mock_conn

        self.mock_portfolio_context = {
            "positions": {
                "AAPL": {"position": 200, "market_price": 150.0, "avg_cost": 145.0},
            },
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "broker_buying_power": 50000.0,
            "broker_buying_power_source": "available_cash",
            "cash_available_for_csp": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "open_short_put_collateral": 0.0,
            "excess_liquidity": 50000.0,
            "short_calls": {},
            "short_puts": {},
            "vix_regime": {"regime": "normal", "vix": 18.0},
        }
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = self.mock_portfolio_context
        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

        self.mock_options_data._process_ticker_for_otm.return_value = {
            "calls": [
                {
                    "strike": 160,
                    "expiration": "20240315",
                    "bid": 2.0,
                    "ask": 2.10,
                    "last": 2.05,
                    "delta": 0.20,
                    "implied_volatility": 0.30,
                    "open_interest": 500,
                    "volume": 100,
                    "dte": 21,
                },
            ],
            "puts": [
                {
                    "strike": 140,
                    "expiration": "20240315",
                    "bid": 1.50,
                    "ask": 1.60,
                    "last": 1.55,
                    "delta": 0.18,
                    "implied_volatility": 0.35,
                    "open_interest": 300,
                    "volume": 200,
                    "dte": 21,
                },
            ],
        }

    def test_format_recommendation_preserves_source_fields(self):
        engine = self._import_engine()

        rec = engine._format_recommendation(
            {
                "ticker": "AAPL",
                "option_type": "PUT",
                "strike": 150.0,
                "expiration": "20260529",
                "dte": 21,
                "mid_price": 1.25,
                "premium_per_contract": 125.0,
                "score": 88.0,
                "annualized_return": 21.0,
                "iv_adjusted_return": 18.0,
                "otm_pct": 7.5,
                "delta": -0.18,
                "iv_rank": 52.0,
                "iv_status": "normal",
                "days_to_earnings": None,
                "earnings_date": None,
                "warnings": [],
                "rationale": ["Strong"],
                "max_contracts": 1,
                "existing_position": 0,
                "profile_type": "monthly",
                "stock_price": 162.0,
                "bid": 1.20,
                "ask": 1.30,
                "open_interest": 500,
                "volume": 100,
                "implied_volatility": 0.32,
                "score_details": {},
                "size_fit": 1.0,
                "expected_move_buffer": 0.0,
                "wheel_decision": {
                    "price_source": "broker",
                    "chain_source": "yfinance",
                    "iv_source": "yfinance",
                },
                "from_watchlist": True,
                "held_position": False,
                "cash_required": 15000,
                "breakeven": 148.75,
                "breakeven_buffer_pct": 1.5,
                "macro_multiplier": 1.0,
                "score_rationale": "Balanced",
                "remaining_gap_to_target": 0,
                "risk_budget_used_pct": 0,
                "stress_loss": 0,
                "confidence_score": 72,
                "covered_call_intent": "",
                "signal_type": "csp",
                "strategy": "wheel",
                "broker_feasible": True,
                "capital_required": 15000,
                "risk_budget_used": 0,
                "data_source": "broker",
                "confidence": 72,
                "price_source": "broker",
                "chain_source": "yfinance",
                "iv_source": "yfinance",
                "from_yfinance": True,
                "quote_quality": "tradable",
                "blocked_reason_codes": [],
                "research_only": False,
            },
            rank=1,
        )

        self.assertEqual(rec["price_source"], "broker")
        self.assertEqual(rec["chain_source"], "yfinance")
        self.assertEqual(rec["iv_source"], "yfinance")
        self.assertTrue(rec["from_yfinance"])

    def test_get_top_recommendations_keeps_low_confidence_yfinance_candidates_as_research_only(self):
        engine = self._import_engine()
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["AAPL"]
        self.mock_options_data._process_ticker_for_otm.return_value = {"calls": [], "puts": []}

        low_conf_candidate = {
            "ticker": "AAPL",
            "option_type": "PUT",
            "strike": 150.0,
            "expiration": "20260529",
            "dte": 21,
            "mid_price": 1.25,
            "premium_per_contract": 125.0,
            "score": 88.0,
            "annualized_return": 21.0,
            "iv_adjusted_return": 18.0,
            "otm_pct": 7.5,
            "delta": -0.18,
            "iv_rank": 52.0,
            "iv_status": "normal",
            "days_to_earnings": None,
            "earnings_date": None,
            "warnings": [],
            "rationale": ["Fallback"],
            "max_contracts": 1,
            "existing_position": 0,
            "profile_type": "monthly",
            "stock_price": 162.0,
            "bid": 1.20,
            "ask": 1.30,
            "open_interest": 500,
            "volume": 100,
            "implied_volatility": 0.32,
            "score_details": {},
            "size_fit": 1.0,
            "expected_move_buffer": 0.0,
            "wheel_decision": {
                "contract_score": 88.0,
                "confidence_score": 65,
                "price_source": "yfinance",
                "chain_source": "yfinance",
                "iv_source": "yfinance",
            },
            "from_watchlist": True,
            "held_position": False,
            "cash_required": 15000,
            "breakeven": 148.75,
            "breakeven_buffer_pct": 1.5,
            "macro_multiplier": 1.0,
            "score_rationale": "Fallback",
            "remaining_gap_to_target": 0,
            "risk_budget_used_pct": 0,
            "stress_loss": 0,
            "confidence_score": 65,
            "covered_call_intent": "",
            "signal_type": "csp",
            "strategy": "wheel",
            "broker_feasible": True,
            "capital_required": 15000,
            "risk_budget_used": 0,
            "data_source": "yfinance",
            "confidence": 65,
            "price_source": "yfinance",
            "chain_source": "yfinance",
            "iv_source": "yfinance",
            "from_yfinance": True,
            "quote_quality": "tradable",
            "blocked_reason_codes": [],
            "research_only": False,
        }

        with patch.object(engine, "_fetch_watchlist_ticker_csp", return_value=[low_conf_candidate]):
            result = engine.get_top_recommendations(limit=5)

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["blocked_reason_counts"].get("data_quality_blocked", 0), 0)
        self.assertFalse(result["blocked_signals"])
        self.assertTrue(result["signals"][0]["research_only"])
        self.assertTrue(any("yfinance" in warning.lower() for warning in result["signals"][0]["warnings"]))

    def test_get_top_recommendations_blocks_hard_blocked_yfinance_candidates(self):
        engine = self._import_engine()
        self.mock_portfolio_context["positions"] = {}
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["AAPL"]
        self.mock_options_data._process_ticker_for_otm.return_value = {"calls": [], "puts": []}

        hard_blocked_candidate = {
            "ticker": "AAPL",
            "option_type": "PUT",
            "strike": 150.0,
            "expiration": "20260529",
            "dte": 21,
            "mid_price": 1.25,
            "premium_per_contract": 125.0,
            "score": 88.0,
            "annualized_return": 21.0,
            "iv_adjusted_return": 18.0,
            "otm_pct": 7.5,
            "delta": -0.18,
            "iv_rank": 52.0,
            "iv_status": "normal",
            "days_to_earnings": None,
            "earnings_date": None,
            "warnings": [],
            "rationale": ["Fallback"],
            "max_contracts": 1,
            "existing_position": 0,
            "profile_type": "monthly",
            "stock_price": 162.0,
            "bid": 1.20,
            "ask": 1.30,
            "open_interest": 500,
            "volume": 100,
            "implied_volatility": 0.32,
            "score_details": {},
            "size_fit": 1.0,
            "expected_move_buffer": 0.0,
            "wheel_decision": {
                "contract_score": 88.0,
                "confidence_score": 65,
                "price_source": "yfinance",
                "chain_source": "yfinance",
                "iv_source": "yfinance",
            },
            "hard_blockers": ["missing_iv"],
            "blocked_reason_codes": [],
            "from_watchlist": True,
            "held_position": False,
            "cash_required": 15000,
            "breakeven": 148.75,
            "breakeven_buffer_pct": 1.5,
            "macro_multiplier": 1.0,
            "score_rationale": "Fallback",
            "remaining_gap_to_target": 0,
            "risk_budget_used_pct": 0,
            "stress_loss": 0,
            "confidence_score": 65,
            "covered_call_intent": "",
            "signal_type": "csp",
            "strategy": "wheel",
            "broker_feasible": True,
            "capital_required": 15000,
            "risk_budget_used": 0,
            "data_source": "yfinance",
            "confidence": 65,
            "price_source": "yfinance",
            "chain_source": "yfinance",
            "iv_source": "yfinance",
            "from_yfinance": True,
            "quote_quality": "tradable",
            "research_only": False,
        }

        with patch.object(engine, "_fetch_watchlist_ticker_csp", return_value=[hard_blocked_candidate]):
            result = engine.get_top_recommendations(limit=5)

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["blocked_reason_counts"].get("data_quality_blocked"), 1)
        self.assertEqual(result["blocked_signals"][0]["reason_text"], "Hard blockers present")
        self.assertTrue(result["blocked_signals"][0]["from_yfinance"])

    def test_zero_cash_watchlist_csp_skips_before_chain(self):
        engine = self._import_engine()
        self.mock_portfolio_context["positions"] = {}
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["AAPL"]
        self.mock_portfolio_context["cash_available_for_csp"] = 0.0
        moomoo = pytest.importorskip("moomoo")
        future_exp = (datetime.now() + timedelta(days=35)).strftime("%Y%m%d")

        class FakeConnection:
            def is_connected(self):
                return True

            def get_cached_stock_price(self, ticker):
                return 150.0

            def get_stock_price(self, ticker):
                return 150.0

            def get_option_expiration_dates(self, ticker):
                return moomoo.RET_OK, pd.DataFrame({"expiration_date": [future_exp]})

            def get_option_chain(self, ticker, exp_str, right, target_strike=None):
                return {
                    "options": [
                        {
                            "strike": 140.0,
                            "expiration": exp_str,
                            "option_type": "PUT",
                            "dte": 35,
                            "bid": 1.5,
                            "ask": 1.6,
                            "last": 1.55,
                            "open_interest": 300,
                            "volume": 200,
                            "delta": -0.18,
                            "gamma": 0.02,
                            "theta": -0.03,
                            "vega": 0.04,
                        }
                    ]
                }

        fake_decision = MagicMock()
        fake_decision.hard_blockers = []
        fake_decision.contract_score = 75.0
        fake_decision.max_contracts = 1
        fake_decision.recommended_contracts = 1
        fake_decision.strike = 140.0
        fake_decision.expiration = future_exp
        fake_decision.dte = 21
        fake_decision.mid_price = 1.55
        fake_decision.premium_per_contract = 155.0
        fake_decision.bid = 1.5
        fake_decision.ask = 1.6
        fake_decision.annualized_return = 18.0
        fake_decision.iv_adjusted_return = 16.0
        fake_decision.otm_pct = 6.7
        fake_decision.delta = -0.18
        fake_decision.implied_volatility = 0.32
        fake_decision.open_interest = 300
        fake_decision.volume = 200
        fake_decision.iv_rank = 50.0
        fake_decision.iv_status = "normal"
        fake_decision.iv_env_adjustment = 0
        fake_decision.profile_type = "monthly"
        fake_decision.earnings_date = None
        fake_decision.days_to_earnings = None
        fake_decision.earnings_adjustment = 0
        fake_decision.size_fit = 1.0
        fake_decision.expected_move_buffer = 0.0
        fake_decision.wheel_decision = {}
        fake_decision.score_details = {}
        fake_decision.rationale = ["Good premium"]
        fake_decision.warnings = []
        fake_decision.breakeven = 138.45
        fake_decision.breakeven_buffer_pct = 1.0
        fake_decision.cash_required = 14000.0
        fake_decision.from_yfinance = False
        fake_decision.price_source = "broker"
        fake_decision.chain_source = "broker"
        fake_decision.iv_source = "broker"
        fake_decision.data_source = "broker"
        fake_decision.to_dict.return_value = {}

        with (
            patch.object(engine, "_get_connection", return_value=FakeConnection()),
            patch.object(engine, "_score_csp_contract", return_value=fake_decision),
            patch("api.services.recommendations.is_market_open", return_value=True),
        ):
            result = engine._fetch_watchlist_csp_moomoo("AAPL", self.mock_portfolio_context)

        self.assertIsInstance(result, list)
        self.assertTrue(result[0].get("_skip_diagnostic"))
        self.assertEqual(result[0].get("reason_code"), "no_cash_fit")

    def _import_engine(self):
        from api.services.recommendations import RecommendationEngine

        return RecommendationEngine(
            self.mock_connection_provider,
            self.mock_config_provider,
            self.mock_db,
            self.mock_iv_earnings,
            self.mock_portfolio_context_provider,
            self.mock_portfolio_service_provider,
            self.mock_watchlist_manager,
            self.mock_options_data,
            self.mock_cash_calculator,
        )

    def test_signals_present_in_response(self):
        """Response should contain a unified signals list and no legacy wrappers."""
        engine = self._import_engine()
        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = "20240315"
            mock_decision.dte = 21
            mock_decision.mid_price = 2.05
            mock_decision.premium_per_contract = 205.0
            mock_decision.annualized_return = 35.0
            mock_decision.iv_adjusted_return = 30.0
            mock_decision.otm_pct = 6.67
            mock_decision.delta = 0.20
            mock_decision.implied_volatility = 0.30
            mock_decision.open_interest = 500
            mock_decision.volume = 100
            mock_decision.iv_rank = 0.6
            mock_decision.iv_status = "normal"
            mock_decision.profile_type = "monthly"
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ["Good premium"]
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {"score": 85.0}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        self.assertIn("signals", result)
        self.assertIn("blocked_signals", result)
        self.assertNotIn("lanes", result)
        self.assertNotIn("best_plays", result)
        self.assertNotIn("recommendations", result)
        self.assertNotIn("blocked_candidates", result)
        self.assertIn("broker_buying_power", result)
        self.assertIn("cash_available_for_csp", result)
        self.assertIn("cash_reserved_for_csp", result)

    def test_signals_contains_only_calls_for_covered_call_signals(self):
        """Covered call signals should only contain CALL options."""
        engine = self._import_engine()
        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = "20240315"
            mock_decision.dte = 21
            mock_decision.mid_price = 2.05
            mock_decision.premium_per_contract = 205.0
            mock_decision.annualized_return = 35.0
            mock_decision.iv_adjusted_return = 30.0
            mock_decision.otm_pct = 6.67
            mock_decision.delta = 0.20
            mock_decision.implied_volatility = 0.30
            mock_decision.open_interest = 500
            mock_decision.volume = 100
            mock_decision.iv_rank = 0.6
            mock_decision.iv_status = "normal"
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = "monthly"
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ["Good premium"]
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {"score": 85.0}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        for rec in result["signals"]:
            if rec["signal_type"] != "covered_call":
                continue
            self.assertEqual(rec["option_type"], "CALL")

    def test_held_position_flag_in_watchlist_csp(self):
        """Watchlist CSP signals should have held_position flag."""
        engine = self._import_engine()

        # Set up watchlist with AAPL (which is already in positions)
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["AAPL"]

        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = "20240315"
            mock_decision.dte = 21
            mock_decision.mid_price = 2.05
            mock_decision.premium_per_contract = 205.0
            mock_decision.annualized_return = 35.0
            mock_decision.iv_adjusted_return = 30.0
            mock_decision.otm_pct = 6.67
            mock_decision.delta = 0.20
            mock_decision.implied_volatility = 0.30
            mock_decision.open_interest = 500
            mock_decision.volume = 100
            mock_decision.iv_rank = 0.6
            mock_decision.iv_status = "normal"
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = "monthly"
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ["Good premium"]
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {"score": 85.0}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        # Check that held_position appears in the CSP signal subset
        for rec in result["signals"]:
            if rec["signal_type"] != "csp":
                continue
            self.assertIn("held_position", rec)

    def test_legacy_recommendation_fields_removed(self):
        """Legacy top-level recommendation fields should no longer be present."""
        engine = self._import_engine()
        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = "20240315"
            mock_decision.dte = 21
            mock_decision.mid_price = 2.05
            mock_decision.premium_per_contract = 205.0
            mock_decision.annualized_return = 35.0
            mock_decision.iv_adjusted_return = 30.0
            mock_decision.otm_pct = 6.67
            mock_decision.delta = 0.20
            mock_decision.implied_volatility = 0.30
            mock_decision.open_interest = 500
            mock_decision.volume = 100
            mock_decision.iv_rank = 0.6
            mock_decision.iv_status = "normal"
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = "monthly"
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ["Good premium"]
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {"score": 85.0}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        self.assertIn("signals", result)
        self.assertNotIn("recommendations", result)
        self.assertNotIn("best_plays", result)
        self.assertNotIn("lanes", result)

    def _import_engine(self):
        from api.services.recommendations import RecommendationEngine

        return RecommendationEngine(
            self.mock_connection_provider,
            self.mock_config_provider,
            self.mock_db,
            self.mock_iv_earnings,
            self.mock_portfolio_context_provider,
            self.mock_portfolio_service_provider,
            self.mock_watchlist_manager,
            self.mock_options_data,
            self.mock_cash_calculator,
        )

    def test_init_stores_context(self):
        engine = self._import_engine()
        self.assertIs(engine._connection_provider, self.mock_connection_provider)
        self.assertIs(engine.config, self.mock_config_provider.config)
        self.assertIs(engine.db, self.mock_db)

    def test_get_top_recommendations_no_connection(self):
        self.mock_connection_provider._ensure_connection.return_value = None
        engine = self._import_engine()

        result = engine.get_top_recommendations(limit=5)

        self.assertIn("error", result)

    def test_get_top_recommendations_empty_portfolio(self):
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            "positions": {},
            "cash_balance": 0,
            "short_calls": {},
            "short_puts": {},
        }
        engine = self._import_engine()

        result = engine.get_top_recommendations(limit=5)

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)

    def test_get_top_recommendations_scans_complete_watchlist_union(self):
        """A feasible scan evaluates every watchlist symbol; it never truncates."""
        tickers = [f"TICK{i}" for i in range(20)]
        self.mock_watchlist_manager.get_effective_watchlist.return_value = tickers
        self.mock_watchlist_manager.get_effective_watchlist_with_origins.return_value = [
            {"ticker": t, "origins": ["config"]} for t in tickers
        ]
        self.mock_watchlist_manager.preflight_scan_feasibility.return_value = {
            "feasible": True,
            "watchlist_size": 20,
            "estimated_scan_sec": 60.0,
            "freshness_window_sec": 120,
            "chain_calls": 40,
            "chain_quota_ok": True,
            "recommended_max_size": 12,
        }
        engine = self._import_engine()

        with patch.object(engine, "_fetch_watchlist_ticker_csp") as mock_fetch:
            mock_fetch.return_value = []
            with patch("api.services.recommendations.is_market_open", return_value=True):
                engine.get_top_recommendations(limit=5)

        self.assertEqual(mock_fetch.call_count, 20)
        called_tickers = [call[0][0] for call in mock_fetch.call_args_list]
        self.assertEqual(called_tickers, tickers)

    def test_get_top_recommendations_ranks_by_premium_velocity(self):
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["AAA", "BBB"]
        engine = self._import_engine()

        faster_daily = {
            "ticker": "AAA",
            "stock_price": 200.0,
            "option_type": "PUT",
            "strike": 200.0,
            "expiration": "20240315",
            "dte": 10,
            "mid_price": 1.00,
            "premium_per_contract": 110.0,
            "bid": 1.10,
            "ask": 1.05,
            "annualized_return": 18.25,
            "iv_adjusted_return": 30.0,
            "otm_pct": 5.0,
            "delta": -0.20,
            "implied_volatility": 0.30,
            "open_interest": 500,
            "volume": 100,
            "score": 80.0,
            "profile_type": "monthly",
            "research_only": False,
            "warnings": [],
            "wheel_decision": {"contract_score": 80.0, "confidence_score": 100},
        }
        slower_daily = {
            "ticker": "BBB",
            "stock_price": 50.0,
            "option_type": "PUT",
            "strike": 50.0,
            "expiration": "20240315",
            "dte": 20,
            "mid_price": 1.20,
            "premium_per_contract": 120.0,
            "bid": 1.15,
            "ask": 1.25,
            "annualized_return": 43.80,
            "iv_adjusted_return": 45.0,
            "otm_pct": 5.0,
            "delta": -0.20,
            "implied_volatility": 0.30,
            "open_interest": 500,
            "volume": 100,
            "score": 90.0,
            "profile_type": "monthly",
            "research_only": False,
            "warnings": [],
            "wheel_decision": {"contract_score": 90.0, "confidence_score": 100},
        }

        with (
            patch.object(engine, "_fetch_watchlist_ticker_csp", side_effect=[[faster_daily], [slower_daily]]),
            patch("api.services.recommendations.is_market_open", return_value=True),
        ):
            result = engine.get_top_recommendations(limit=5)

        self.assertGreater(len(result["signals"]), 1)
        self.assertEqual(result["signals"][0]["ticker"], "AAA")
        ranked = {signal["ticker"]: signal for signal in result["signals"]}
        self.assertLess(ranked["AAA"]["annualized_return"], ranked["BBB"]["annualized_return"])
        self.assertGreater(
            ranked["AAA"]["premium_per_contract"] / ranked["AAA"]["dte"],
            ranked["BBB"]["premium_per_contract"] / ranked["BBB"]["dte"],
        )

    def test_get_top_recommendations_keeps_post_rank_enrichment_off_hot_path(self):
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["AAPL"]
        engine = self._import_engine()
        csp = {
            "ticker": "AAPL",
            "stock_price": 150.0,
            "option_type": "PUT",
            "strike": 140.0,
            "expiration": "20240315",
            "dte": 21,
            "mid_price": 1.55,
            "premium_per_contract": 155.0,
            "bid": 1.5,
            "ask": 1.6,
            "annualized_return": 20.0,
            "iv_adjusted_return": 50.0,
            "otm_pct": 6.7,
            "delta": -0.18,
            "implied_volatility": 0.35,
            "open_interest": 300,
            "volume": 200,
            "score": 85.0,
            "profile_type": "monthly",
            "warnings": [],
            "wheel_decision": {"contract_score": 85.0, "confidence_score": 100},
        }

        with (
            patch.object(engine, "_fetch_watchlist_ticker_csp", return_value=[csp]),
        ):
            result = engine.get_top_recommendations(limit=5)

        self.assertEqual(result["watchlist_csps"]["count"], 1)
        self.assertEqual(result["enrichment"]["mode"], "none")

    def test_get_top_recommendations_processes_positions(self):
        engine = self._import_engine()

        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = "20240315"
            mock_decision.dte = 21
            mock_decision.mid_price = 2.05
            mock_decision.premium_per_contract = 205.0
            mock_decision.annualized_return = 35.0
            mock_decision.iv_adjusted_return = 30.0
            mock_decision.otm_pct = 6.67
            mock_decision.delta = 0.20
            mock_decision.implied_volatility = 0.30
            mock_decision.open_interest = 500
            mock_decision.volume = 100
            mock_decision.iv_rank = 0.6
            mock_decision.iv_status = "normal"
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = "monthly"
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ["Good premium"]
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {"score": 85.0}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        self.assertTrue(result["success"])
        self.assertGreater(result["total_scored"], 0)
        self.assertIn("signals", result)
        self.assertIn("blocked_signals", result)
        self.assertIn("covered_calls", result)
        self.assertIn("watchlist_csps", result)
        self.assertNotIn("recommendations", result)
        self.assertNotIn("best_plays", result)
        self.assertNotIn("lanes", result)
        self.assertNotIn("blocked_candidates", result)

    def test_get_top_recommendations_respects_limit(self):
        engine = self._import_engine()

        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = "20240315"
            mock_decision.dte = 21
            mock_decision.mid_price = 2.05
            mock_decision.premium_per_contract = 205.0
            mock_decision.annualized_return = 35.0
            mock_decision.iv_adjusted_return = 30.0
            mock_decision.otm_pct = 6.67
            mock_decision.delta = 0.20
            mock_decision.implied_volatility = 0.30
            mock_decision.open_interest = 500
            mock_decision.volume = 100
            mock_decision.iv_rank = 0.6
            mock_decision.iv_status = "normal"
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = "monthly"
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ["Good premium"]
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=1)

        self.assertTrue(result["success"])
        self.assertLessEqual(result["count"], 1)


class TestRecommendationEngineStripPrefix(unittest.TestCase):
    """Test ticker prefix stripping delegates to utils."""

    def test_strip_ticker_prefix_delegates(self):
        with patch("api.services.recommendations.clean_yfinance_ticker") as mock_clean:
            mock_clean.return_value = "AAPL"
            from api.services.recommendations import RecommendationEngine

            engine = RecommendationEngine(
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
            )
            result = engine._strip_ticker_prefix("US.AAPL")

            self.assertEqual(result, "AAPL")
            mock_clean.assert_called_once_with("US.AAPL")


class TestRecommendationEngineSignals(unittest.TestCase):
    """Test the unified signal recommendation structure."""

    def setUp(self):
        self.mock_connection_provider = MagicMock()
        self.mock_config_provider = MagicMock()
        self.mock_config_provider.config = {"cash_reserve_enabled": True}
        self.mock_db = MagicMock()
        self.mock_iv_earnings = MagicMock()
        self.mock_portfolio_context_provider = MagicMock()
        self.mock_portfolio_service_provider = MagicMock()
        self.mock_watchlist_manager = MagicMock()
        self.mock_options_data = MagicMock()
        self.mock_cash_calculator = MagicMock()
        self.mock_iv_earnings.get_iv_environment_score.return_value = (0, 0.5, "normal")
        self.mock_iv_earnings.get_earnings_score_impact.return_value = (0, None)
        self.mock_iv_earnings.get_earnings_info.return_value = {}

        self.mock_conn = MagicMock()
        self.mock_conn.get_stock_price.return_value = 150.0
        self.mock_connection_provider._ensure_connection.return_value = self.mock_conn

        self.mock_portfolio_context = {
            "positions": {
                "AAPL": {"position": 200, "market_price": 150.0, "avg_cost": 145.0},
            },
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "broker_buying_power": 50000.0,
            "broker_buying_power_source": "available_cash",
            "cash_available_for_csp": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "open_short_put_collateral": 0.0,
            "excess_liquidity": 50000.0,
            "short_calls": {},
            "short_puts": {},
            "vix_regime": {"regime": "normal", "vix": 18.0},
        }
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = self.mock_portfolio_context
        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

        self.mock_options_data._process_ticker_for_otm.return_value = {
            "calls": [
                {
                    "strike": 160,
                    "expiration": "20240315",
                    "bid": 2.0,
                    "ask": 2.10,
                    "last": 2.05,
                    "delta": 0.20,
                    "implied_volatility": 0.30,
                    "open_interest": 500,
                    "volume": 100,
                    "dte": 21,
                },
            ],
            "puts": [
                {
                    "strike": 140,
                    "expiration": "20240315",
                    "bid": 1.50,
                    "ask": 1.60,
                    "last": 1.55,
                    "delta": 0.18,
                    "implied_volatility": 0.35,
                    "open_interest": 300,
                    "volume": 200,
                    "dte": 21,
                },
            ],
        }

    def test_format_recommendation_preserves_source_fields(self):
        engine = self._import_engine()

        rec = engine._format_recommendation(
            {
                "ticker": "AAPL",
                "option_type": "PUT",
                "strike": 150.0,
                "expiration": "20260529",
                "dte": 21,
                "mid_price": 1.25,
                "premium_per_contract": 125.0,
                "score": 88.0,
                "annualized_return": 21.0,
                "iv_adjusted_return": 18.0,
                "otm_pct": 7.5,
                "delta": -0.18,
                "iv_rank": 52.0,
                "iv_status": "normal",
                "days_to_earnings": None,
                "earnings_date": None,
                "warnings": [],
                "rationale": ["Strong"],
                "max_contracts": 1,
                "existing_position": 0,
                "profile_type": "monthly",
                "stock_price": 162.0,
                "bid": 1.20,
                "ask": 1.30,
                "open_interest": 500,
                "volume": 100,
                "implied_volatility": 0.32,
                "score_details": {},
                "size_fit": 1.0,
                "expected_move_buffer": 0.0,
                "wheel_decision": {
                    "price_source": "broker",
                    "chain_source": "yfinance",
                    "iv_source": "yfinance",
                },
                "from_watchlist": True,
                "held_position": False,
                "cash_required": 15000,
                "breakeven": 148.75,
                "breakeven_buffer_pct": 1.5,
                "macro_multiplier": 1.0,
                "score_rationale": "Balanced",
                "remaining_gap_to_target": 0,
                "risk_budget_used_pct": 0,
                "stress_loss": 0,
                "confidence_score": 72,
                "covered_call_intent": "",
                "signal_type": "csp",
                "strategy": "wheel",
                "broker_feasible": True,
                "capital_required": 15000,
                "risk_budget_used": 0,
                "data_source": "broker",
                "confidence": 72,
                "price_source": "broker",
                "chain_source": "yfinance",
                "iv_source": "yfinance",
                "from_yfinance": True,
                "quote_quality": "tradable",
                "blocked_reason_codes": [],
                "research_only": False,
            },
            rank=1,
        )

        self.assertEqual(rec["price_source"], "broker")
        self.assertEqual(rec["chain_source"], "yfinance")
        self.assertEqual(rec["iv_source"], "yfinance")
        self.assertTrue(rec["from_yfinance"])

    def test_get_top_recommendations_keeps_low_confidence_yfinance_candidates_as_research_only(self):
        engine = self._import_engine()
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["AAPL"]
        self.mock_options_data._process_ticker_for_otm.return_value = {"calls": [], "puts": []}

        low_conf_candidate = {
            "ticker": "AAPL",
            "option_type": "PUT",
            "strike": 150.0,
            "expiration": "20260529",
            "dte": 21,
            "mid_price": 1.25,
            "premium_per_contract": 125.0,
            "score": 88.0,
            "annualized_return": 21.0,
            "iv_adjusted_return": 18.0,
            "otm_pct": 7.5,
            "delta": -0.18,
            "iv_rank": 52.0,
            "iv_status": "normal",
            "days_to_earnings": None,
            "earnings_date": None,
            "warnings": [],
            "rationale": ["Fallback"],
            "max_contracts": 1,
            "existing_position": 0,
            "profile_type": "monthly",
            "stock_price": 162.0,
            "bid": 1.20,
            "ask": 1.30,
            "open_interest": 500,
            "volume": 100,
            "implied_volatility": 0.32,
            "score_details": {},
            "size_fit": 1.0,
            "expected_move_buffer": 0.0,
            "wheel_decision": {
                "contract_score": 88.0,
                "confidence_score": 65,
                "price_source": "yfinance",
                "chain_source": "yfinance",
                "iv_source": "yfinance",
            },
            "from_watchlist": True,
            "held_position": False,
            "cash_required": 15000,
            "breakeven": 148.75,
            "breakeven_buffer_pct": 1.5,
            "macro_multiplier": 1.0,
            "score_rationale": "Fallback",
            "remaining_gap_to_target": 0,
            "risk_budget_used_pct": 0,
            "stress_loss": 0,
            "confidence_score": 65,
            "covered_call_intent": "",
            "signal_type": "csp",
            "strategy": "wheel",
            "broker_feasible": True,
            "capital_required": 15000,
            "risk_budget_used": 0,
            "data_source": "yfinance",
            "confidence": 65,
            "price_source": "yfinance",
            "chain_source": "yfinance",
            "iv_source": "yfinance",
            "from_yfinance": True,
            "quote_quality": "tradable",
            "blocked_reason_codes": [],
            "research_only": False,
        }

        with patch.object(engine, "_fetch_watchlist_ticker_csp", return_value=[low_conf_candidate]):
            result = engine.get_top_recommendations(limit=5)

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["blocked_reason_counts"].get("data_quality_blocked", 0), 0)
        self.assertFalse(result["blocked_signals"])
        self.assertTrue(result["signals"][0]["research_only"])
        self.assertTrue(any("yfinance" in warning.lower() for warning in result["signals"][0]["warnings"]))

    def test_get_top_recommendations_blocks_hard_blocked_yfinance_candidates(self):
        engine = self._import_engine()
        self.mock_portfolio_context["positions"] = {}
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["AAPL"]
        self.mock_options_data._process_ticker_for_otm.return_value = {"calls": [], "puts": []}

        hard_blocked_candidate = {
            "ticker": "AAPL",
            "option_type": "PUT",
            "strike": 150.0,
            "expiration": "20260529",
            "dte": 21,
            "mid_price": 1.25,
            "premium_per_contract": 125.0,
            "score": 88.0,
            "annualized_return": 21.0,
            "iv_adjusted_return": 18.0,
            "otm_pct": 7.5,
            "delta": -0.18,
            "iv_rank": 52.0,
            "iv_status": "normal",
            "days_to_earnings": None,
            "earnings_date": None,
            "warnings": [],
            "rationale": ["Fallback"],
            "max_contracts": 1,
            "existing_position": 0,
            "profile_type": "monthly",
            "stock_price": 162.0,
            "bid": 1.20,
            "ask": 1.30,
            "open_interest": 500,
            "volume": 100,
            "implied_volatility": 0.32,
            "score_details": {},
            "size_fit": 1.0,
            "expected_move_buffer": 0.0,
            "wheel_decision": {
                "contract_score": 88.0,
                "confidence_score": 65,
                "price_source": "yfinance",
                "chain_source": "yfinance",
                "iv_source": "yfinance",
            },
            "hard_blockers": ["missing_iv"],
            "blocked_reason_codes": [],
            "from_watchlist": True,
            "held_position": False,
            "cash_required": 15000,
            "breakeven": 148.75,
            "breakeven_buffer_pct": 1.5,
            "macro_multiplier": 1.0,
            "score_rationale": "Fallback",
            "remaining_gap_to_target": 0,
            "risk_budget_used_pct": 0,
            "stress_loss": 0,
            "confidence_score": 65,
            "covered_call_intent": "",
            "signal_type": "csp",
            "strategy": "wheel",
            "broker_feasible": True,
            "capital_required": 15000,
            "risk_budget_used": 0,
            "data_source": "yfinance",
            "confidence": 65,
            "price_source": "yfinance",
            "chain_source": "yfinance",
            "iv_source": "yfinance",
            "from_yfinance": True,
            "quote_quality": "tradable",
            "research_only": False,
        }

        with patch.object(engine, "_fetch_watchlist_ticker_csp", return_value=[hard_blocked_candidate]):
            result = engine.get_top_recommendations(limit=5)

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["blocked_reason_counts"].get("data_quality_blocked"), 1)
        self.assertEqual(result["blocked_signals"][0]["reason_text"], "Hard blockers present")
        self.assertTrue(result["blocked_signals"][0]["from_yfinance"])

    def test_zero_cash_watchlist_csp_skips_before_chain(self):
        engine = self._import_engine()
        self.mock_portfolio_context["positions"] = {}
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["AAPL"]
        self.mock_portfolio_context["cash_available_for_csp"] = 0.0
        moomoo = pytest.importorskip("moomoo")
        future_exp = (datetime.now() + timedelta(days=35)).strftime("%Y%m%d")

        class FakeConnection:
            def is_connected(self):
                return True

            def get_cached_stock_price(self, ticker):
                return 150.0

            def get_stock_price(self, ticker):
                return 150.0

            def get_option_expiration_dates(self, ticker):
                return moomoo.RET_OK, pd.DataFrame({"expiration_date": [future_exp]})

            def get_option_chain(self, ticker, exp_str, right, target_strike=None):
                return {
                    "options": [
                        {
                            "strike": 140.0,
                            "expiration": exp_str,
                            "option_type": "PUT",
                            "dte": 35,
                            "bid": 1.5,
                            "ask": 1.6,
                            "last": 1.55,
                            "open_interest": 300,
                            "volume": 200,
                            "delta": -0.18,
                            "gamma": 0.02,
                            "theta": -0.03,
                            "vega": 0.04,
                        }
                    ]
                }

        fake_decision = MagicMock()
        fake_decision.hard_blockers = []
        fake_decision.contract_score = 75.0
        fake_decision.max_contracts = 1
        fake_decision.recommended_contracts = 1
        fake_decision.strike = 140.0
        fake_decision.expiration = future_exp
        fake_decision.dte = 21
        fake_decision.mid_price = 1.55
        fake_decision.premium_per_contract = 155.0
        fake_decision.bid = 1.5
        fake_decision.ask = 1.6
        fake_decision.annualized_return = 18.0
        fake_decision.iv_adjusted_return = 16.0
        fake_decision.otm_pct = 6.7
        fake_decision.delta = -0.18
        fake_decision.implied_volatility = 0.32
        fake_decision.open_interest = 300
        fake_decision.volume = 200
        fake_decision.iv_rank = 50.0
        fake_decision.iv_status = "normal"
        fake_decision.iv_env_adjustment = 0
        fake_decision.profile_type = "monthly"
        fake_decision.earnings_date = None
        fake_decision.days_to_earnings = None
        fake_decision.earnings_adjustment = 0
        fake_decision.size_fit = 1.0
        fake_decision.expected_move_buffer = 0.0
        fake_decision.wheel_decision = {}
        fake_decision.score_details = {}
        fake_decision.rationale = ["Good premium"]
        fake_decision.warnings = []
        fake_decision.breakeven = 138.45
        fake_decision.breakeven_buffer_pct = 1.0
        fake_decision.cash_required = 14000.0
        fake_decision.from_yfinance = False
        fake_decision.price_source = "broker"
        fake_decision.chain_source = "broker"
        fake_decision.iv_source = "broker"
        fake_decision.data_source = "broker"
        fake_decision.to_dict.return_value = {}

        with (
            patch.object(engine, "_get_connection", return_value=FakeConnection()),
            patch.object(engine, "_score_csp_contract", return_value=fake_decision),
            patch("api.services.recommendations.is_market_open", return_value=True),
        ):
            result = engine._fetch_watchlist_csp_moomoo("AAPL", self.mock_portfolio_context)

        self.assertIsInstance(result, list)
        self.assertTrue(result[0].get("_skip_diagnostic"))
        self.assertEqual(result[0].get("reason_code"), "no_cash_fit")

    def _import_engine(self):
        from api.services.recommendations import RecommendationEngine

        return RecommendationEngine(
            self.mock_connection_provider,
            self.mock_config_provider,
            self.mock_db,
            self.mock_iv_earnings,
            self.mock_portfolio_context_provider,
            self.mock_portfolio_service_provider,
            self.mock_watchlist_manager,
            self.mock_options_data,
            self.mock_cash_calculator,
        )

    def test_signals_present_in_response(self):
        """Response should contain a unified signals list and no legacy wrappers."""
        engine = self._import_engine()
        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = "20240315"
            mock_decision.dte = 21
            mock_decision.mid_price = 2.05
            mock_decision.premium_per_contract = 205.0
            mock_decision.annualized_return = 35.0
            mock_decision.iv_adjusted_return = 30.0
            mock_decision.otm_pct = 6.67
            mock_decision.delta = 0.20
            mock_decision.implied_volatility = 0.30
            mock_decision.open_interest = 500
            mock_decision.volume = 100
            mock_decision.iv_rank = 0.6
            mock_decision.iv_status = "normal"
            mock_decision.profile_type = "monthly"
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ["Good premium"]
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {"score": 85.0}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        self.assertIn("signals", result)
        self.assertIn("blocked_signals", result)
        self.assertNotIn("lanes", result)
        self.assertNotIn("best_plays", result)
        self.assertNotIn("recommendations", result)
        self.assertNotIn("blocked_candidates", result)
        self.assertIn("broker_buying_power", result)
        self.assertIn("cash_available_for_csp", result)
        self.assertIn("cash_reserved_for_csp", result)

    def test_signals_contains_only_calls_for_covered_call_signals(self):
        """Covered call signals should only contain CALL options."""
        engine = self._import_engine()
        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = "20240315"
            mock_decision.dte = 21
            mock_decision.mid_price = 2.05
            mock_decision.premium_per_contract = 205.0
            mock_decision.annualized_return = 35.0
            mock_decision.iv_adjusted_return = 30.0
            mock_decision.otm_pct = 6.67
            mock_decision.delta = 0.20
            mock_decision.implied_volatility = 0.30
            mock_decision.open_interest = 500
            mock_decision.volume = 100
            mock_decision.iv_rank = 0.6
            mock_decision.iv_status = "normal"
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = "monthly"
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ["Good premium"]
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {"score": 85.0}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        for rec in result["signals"]:
            if rec["signal_type"] != "covered_call":
                continue
            self.assertEqual(rec["option_type"], "CALL")

    def test_held_position_flag_in_watchlist_csp(self):
        """Watchlist CSP signals should have held_position flag."""
        engine = self._import_engine()

        # Set up watchlist with AAPL (which is already in positions)
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["AAPL"]

        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = "20240315"
            mock_decision.dte = 21
            mock_decision.mid_price = 2.05
            mock_decision.premium_per_contract = 205.0
            mock_decision.annualized_return = 35.0
            mock_decision.iv_adjusted_return = 30.0
            mock_decision.otm_pct = 6.67
            mock_decision.delta = 0.20
            mock_decision.implied_volatility = 0.30
            mock_decision.open_interest = 500
            mock_decision.volume = 100
            mock_decision.iv_rank = 0.6
            mock_decision.iv_status = "normal"
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = "monthly"
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ["Good premium"]
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {"score": 85.0}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        # Check that held_position appears in the CSP signal subset
        for rec in result["signals"]:
            if rec["signal_type"] != "csp":
                continue
            self.assertIn("held_position", rec)

    def test_legacy_recommendation_fields_removed(self):
        """Legacy top-level recommendation fields should no longer be present."""
        engine = self._import_engine()
        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = "20240315"
            mock_decision.dte = 21
            mock_decision.mid_price = 2.05
            mock_decision.premium_per_contract = 205.0
            mock_decision.annualized_return = 35.0
            mock_decision.iv_adjusted_return = 30.0
            mock_decision.otm_pct = 6.67
            mock_decision.delta = 0.20
            mock_decision.implied_volatility = 0.30
            mock_decision.open_interest = 500
            mock_decision.volume = 100
            mock_decision.iv_rank = 0.6
            mock_decision.iv_status = "normal"
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = "monthly"
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ["Good premium"]
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {"score": 85.0}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        self.assertIn("signals", result)
        self.assertNotIn("recommendations", result)
        self.assertNotIn("best_plays", result)
        self.assertNotIn("lanes", result)

    def test_yfinance_csp_skips_chain_when_no_research_only_strike_fits(self):
        """Research-only mode still skips impossible CSPs before chain fetch."""
        engine = self._import_engine()
        portfolio_context = dict(self.mock_portfolio_context)
        portfolio_context["cash_available_for_csp"] = 100.0

        with patch.object(engine, "_fetch_watchlist_csp_moomoo") as mock_fetch:
            mock_fetch.return_value = [
                engine._make_skip_diagnostic("AAPL", "no_cash_fit", "No CSP strike fits buying power")
            ]
            result = engine._fetch_watchlist_ticker_csp("AAPL", portfolio_context)

        self.assertTrue(any(r.get("reason_code") == "no_cash_fit" for r in result))

    def test_get_top_recommendations_skips_watchlist_csp_scan_when_buying_power_too_low(self):
        """Low buying power should mark CSP candidates as research-only, not skip entirely."""
        engine = self._import_engine()
        self.mock_portfolio_context["positions"] = {}
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
        self.mock_portfolio_context["cash_available_for_csp"] = 1210.19

        result = engine.get_top_recommendations(limit=5)

        # With research_only_mode, we still scan but mark candidates as research_only
        # The lane should run, not be skipped entirely
        self.assertGreaterEqual(self.mock_conn.get_stock_price.call_count, 0)
        # Should not have a blocked signal for this reason
        blocked = result.get("blocked_signals", [])
        self.assertFalse(
            any(b.get("reason_code") == "watchlist_csp_skipped_low_buying_power" for b in blocked),
            f"Should not have watchlist_csp_skipped_low_buying_power blocked signal, got: {blocked}",
        )

    def test_moomoo_csp_preflight_skips_chain_when_no_strike_can_fit_cash(self):
        """Use quote-before-chain to avoid spending option-chain calls on impossible CSPs."""
        engine = self._import_engine()
        portfolio_context = dict(self.mock_portfolio_context)
        portfolio_context["cash_available_for_csp"] = 1209.66
        self.mock_conn.get_cached_stock_price.return_value = None
        self.mock_conn.get_stock_price.return_value = 982.0

        with patch("api.services.recommendations.is_market_open", return_value=True):
            result = engine._fetch_watchlist_csp_moomoo("COST", portfolio_context)

        self.assertEqual(result[0]["reason_code"], "no_cash_fit")
        self.mock_conn.get_stock_price.assert_called_once_with("COST")
        self.mock_conn.get_option_expiration_dates.assert_not_called()
        self.mock_conn.get_option_chain.assert_not_called()


class TestRecommendationEngineDedup(unittest.TestCase):
    """Test symbol deduplication by canonical underlying."""

    def setUp(self):
        self.mock_connection_provider = MagicMock()
        self.mock_config_provider = MagicMock()
        self.mock_config_provider.config = {"cash_reserve_enabled": True}
        self.mock_db = MagicMock()
        self.mock_iv_earnings = MagicMock()
        self.mock_portfolio_context_provider = MagicMock()
        self.mock_portfolio_service_provider = MagicMock()
        self.mock_watchlist_manager = MagicMock()
        self.mock_options_data = MagicMock()
        self.mock_cash_calculator = MagicMock()

        self.mock_conn = MagicMock()
        self.mock_conn.get_stock_price.return_value = 70.0
        self.mock_connection_provider._ensure_connection.return_value = self.mock_conn

        self.mock_portfolio_context = {
            "positions": {
                "UBER": {"position": 200, "market_price": 70.0, "avg_cost": 65.0},
                "XPEV": {"position": 100, "market_price": 30.0, "avg_cost": 28.0},
            },
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "broker_buying_power": 50000.0,
            "broker_buying_power_source": "available_cash",
            "cash_available_for_csp": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "open_short_put_collateral": 0.0,
            "excess_liquidity": 50000.0,
            "short_calls": {},
            "short_puts": {},
            "vix_regime": {"regime": "normal", "vix": 18.0},
        }
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = self.mock_portfolio_context
        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

    def _import_engine(self):
        from api.services.recommendations import RecommendationEngine

        return RecommendationEngine(
            self.mock_connection_provider,
            self.mock_config_provider,
            self.mock_db,
            self.mock_iv_earnings,
            self.mock_portfolio_context_provider,
            self.mock_portfolio_service_provider,
            self.mock_watchlist_manager,
            self.mock_options_data,
            self.mock_cash_calculator,
        )

    def _mock_csp_return(self, ticker):
        """Build a mock CSP candidate return for a given ticker."""
        return [
            {
                "strike": 60.0,
                "expiration": "20240315",
                "option_type": "PUT",
                "bid": 1.50,
                "ask": 1.60,
                "last": 1.55,
                "dte": 21,
                "implied_volatility": 0.35,
                "open_interest": 300,
                "volume": 200,
                "mid_price": 1.55,
                "premium_per_contract": 155.0,
                "annualized_return": 35.0,
                "score": 85.0,
                "ticker": ticker,
                "delta": 0.18,
                "iv_rank": 0.6,
                "otm_pct": 14.29,
                "breakeven": 58.45,
                "breakeven_buffer_pct": 0.0258,
                "cash_required": 6000.0,
                "rationale": ["Good premium"],
                "warnings": [],
                "score_details": {},
            }
        ]

    def test_watchlist_dedup_drops_duplicate_underlying(self):
        """Watchlist with US.UBER and UBER should deduplicate to one entry."""
        self.mock_watchlist_manager.get_effective_watchlist.return_value = [
            "UBER",
            "US.UBER",
        ]
        self.mock_watchlist_manager.get_effective_watchlist_with_origins.return_value = [
            {"ticker": "UBER", "origins": ["config"]},
        ]
        engine = self._import_engine()

        with patch.object(engine, "_fetch_watchlist_ticker_csp") as mock_fetch:
            mock_fetch.side_effect = lambda t, pc, **kwargs: self._mock_csp_return(t)

            engine.get_top_recommendations(limit=5)

        # _fetch_watchlist_ticker_csp should be called once after dedup
        self.assertEqual(mock_fetch.call_count, 1)
        called_ticker = mock_fetch.call_args[0][0]
        from core.ticker_utils import canonical_underlying

        self.assertEqual(canonical_underlying(called_ticker), "UBER")

    def test_positions_dedup_by_canonical_underlying(self):
        """Positions with UBER and US.UBER should dedup to one covered call row."""
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            "positions": {
                "UBER": {"position": 200, "market_price": 70.0, "avg_cost": 65.0},
                "US.UBER": {"position": 200, "market_price": 70.0, "avg_cost": 65.0},
            },
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "cash_available_for_csp": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "excess_liquidity": 50000.0,
            "short_calls": {},
            "short_puts": {},
            "vix_regime": {"regime": "normal", "vix": 18.0},
        }
        engine = self._import_engine()

        # Need to avoid the CSP path since it's not relevant for this test
        self.mock_portfolio_context["cash_available_for_csp"] = 0

        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 75
            mock_decision.expiration = "20240315"
            mock_decision.dte = 21
            mock_decision.mid_price = 1.05
            mock_decision.premium_per_contract = 105.0
            mock_decision.annualized_return = 35.0
            mock_decision.iv_adjusted_return = 30.0
            mock_decision.otm_pct = 6.67
            mock_decision.delta = 0.20
            mock_decision.implied_volatility = 0.30
            mock_decision.open_interest = 500
            mock_decision.volume = 100
            mock_decision.iv_rank = 0.6
            mock_decision.iv_status = "normal"
            mock_decision.profile_type = "monthly"
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 73.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 7500.0
            mock_decision.score_details = {}
            mock_decision.rationale = ["Good premium"]
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {
                "score": 85.0,
                "covered_call_intent": "income",
                "score_rationale": "",
                "stress_loss": 0,
                "risk_budget_used_pct": 0,
            }
            mock_score.return_value = mock_decision

            engine.get_top_recommendations(limit=5)

        # get_stock_price should be called once per canonical underlying
        uber_calls = [c for c in self.mock_conn.get_stock_price.call_args_list if c[0][0] in ("UBER", "US.UBER")]
        self.assertEqual(len(uber_calls), 1)

    def test_select_top_deduplicates_by_canonical(self):
        """_select_top should not include multiple candidates for same underlying."""
        engine = self._import_engine()

        with patch.object(engine, "_fetch_watchlist_ticker_csp") as mock_fetch:
            mock_fetch.side_effect = lambda t, pc, **kwargs: self._mock_csp_return(t)
            self.mock_watchlist_manager.get_effective_watchlist.return_value = [
                "UBER",
                "XPEV",
            ]

            result = engine.get_top_recommendations(limit=5)

        from core.ticker_utils import canonical_underlying

        seen = set()
        for rec in result.get("signals", []):
            cu = canonical_underlying(rec["ticker"])
            self.assertNotIn(cu, seen, f"Duplicate underlying {cu} found")
            seen.add(cu)


class TestRecommendationEngineCashFields(unittest.TestCase):
    """Test cash field consistency in recommendations."""

    def setUp(self):
        self.mock_connection_provider = MagicMock()
        self.mock_config_provider = MagicMock()
        self.mock_config_provider.config = {"cash_reserve_enabled": True}
        self.mock_db = MagicMock()
        self.mock_iv_earnings = MagicMock()
        self.mock_portfolio_context_provider = MagicMock()
        self.mock_portfolio_service_provider = MagicMock()
        self.mock_watchlist_manager = MagicMock()
        self.mock_options_data = MagicMock()
        self.mock_cash_calculator = MagicMock()

        self.mock_conn = MagicMock()
        self.mock_conn.get_stock_price.return_value = 150.0
        self.mock_connection_provider._ensure_connection.return_value = self.mock_conn

    def _import_engine(self):
        from api.services.recommendations import RecommendationEngine

        return RecommendationEngine(
            self.mock_connection_provider,
            self.mock_config_provider,
            self.mock_db,
            self.mock_iv_earnings,
            self.mock_portfolio_context_provider,
            self.mock_portfolio_service_provider,
            self.mock_watchlist_manager,
            self.mock_options_data,
            self.mock_cash_calculator,
        )

    def test_cash_fields_in_response(self):
        """Response should include broker_buying_power, cash_available_for_csp, cash_reserved_for_csp."""
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            "positions": {},
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "broker_buying_power": 50000.0,
            "broker_buying_power_source": "available_cash",
            "cash_available_for_csp": 50000.0,
            "cash_reserved_for_csp": 15000.0,
            "open_short_put_collateral": 15000.0,
            "excess_liquidity": 50000.0,
            "short_calls": {},
            "short_puts": {},
        }
        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

        engine = self._import_engine()
        result = engine.get_top_recommendations(limit=5)

        self.assertEqual(result.get("broker_buying_power"), 50000.0)
        self.assertEqual(result.get("cash_available_for_csp"), 50000.0)
        self.assertEqual(result.get("cash_reserved_for_csp"), 15000.0)


class TestRecommendationEngineSignalFields(unittest.TestCase):
    """Test signal-only fields in recommendations."""

    def setUp(self):
        self.mock_connection_provider = MagicMock()
        self.mock_config_provider = MagicMock()
        self.mock_config_provider.config = {"cash_reserve_enabled": True}
        self.mock_db = MagicMock()
        self.mock_iv_earnings = MagicMock()
        self.mock_portfolio_context_provider = MagicMock()
        self.mock_portfolio_service_provider = MagicMock()
        self.mock_watchlist_manager = MagicMock()
        self.mock_options_data = MagicMock()
        self.mock_cash_calculator = MagicMock()

        self.mock_conn = MagicMock()
        self.mock_conn.get_stock_price.return_value = 150.0
        self.mock_connection_provider._ensure_connection.return_value = self.mock_conn

    def _import_engine(self):
        from api.services.recommendations import RecommendationEngine

        return RecommendationEngine(
            self.mock_connection_provider,
            self.mock_config_provider,
            self.mock_db,
            self.mock_iv_earnings,
            self.mock_portfolio_context_provider,
            self.mock_portfolio_service_provider,
            self.mock_watchlist_manager,
            self.mock_options_data,
            self.mock_cash_calculator,
        )

    def test_signals_contains_signal_fields(self):
        """signals items should include signal-specific fields."""
        from core.wheel_decision import WheelDecision

        mock_decision = MagicMock(spec=WheelDecision)
        mock_decision.hard_blockers = []
        mock_decision.strike = 145.0
        mock_decision.expiration = "20260510"
        mock_decision.dte = 14
        mock_decision.mid_price = 0.85
        mock_decision.premium_per_contract = 85.0
        mock_decision.bid = 0.80
        mock_decision.ask = 0.90
        mock_decision.annualized_return = 15.0
        mock_decision.iv_adjusted_return = 12.0
        mock_decision.otm_pct = 6.0
        mock_decision.delta = 0.30
        mock_decision.implied_volatility = 0.35
        mock_decision.open_interest = 1000
        mock_decision.volume = 500
        mock_decision.iv_rank = 0.6
        mock_decision.iv_status = "normal"
        mock_decision.iv_env_adjustment = 0
        mock_decision.profile_type = "standard"
        mock_decision.size_fit = 1.0
        mock_decision.expected_move_buffer = 0.05
        mock_decision.breakeven = 73.0
        mock_decision.breakeven_buffer_pct = 0.05
        mock_decision.cash_required = 7500.0
        mock_decision.score_details = {}
        mock_decision.rationale = ["Good premium"]
        mock_decision.warnings = []
        mock_decision.to_dict.return_value = {
            "score": 85.0,
            "covered_call_intent": "income",
            "score_rationale": "Strong growth candidate",
            "stress_loss": 500,
            "risk_budget_used_pct": 0.15,
            "price_source": "moomoo",
            "chain_source": "moomoo",
            "confidence_score": 95,
            "hard_blockers": [],
        }

        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            "positions": {},
            "available_cash": 50000.0,
            "broker_buying_power": 50000.0,
            "broker_buying_power_source": "available_cash",
            "cash_available_for_csp": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "short_calls": {},
            "short_puts": {},
        }
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["AAPL"]

        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_score.return_value = mock_decision
            engine = self._import_engine()
            result = engine.get_top_recommendations(limit=5)

        # Verify signals contains signal fields
        for rec in result.get("signals", []):
            self.assertIn("signal_type", rec)
            self.assertIn("strategy", rec)
            self.assertEqual(rec["strategy"], "wheel")
            self.assertIn("broker_feasible", rec)
            self.assertIn("capital_required", rec)
            self.assertIn("risk_budget_used", rec)
            self.assertIn("data_source", rec)
            self.assertIn("confidence", rec)
            self.assertIn("blocked_reason_codes", rec)
            self.assertIn("research_only", rec)
            # CSP signals should have signal_type 'csp'
            if rec.get("option_type") == "PUT":
                self.assertEqual(rec["signal_type"], "csp")

    def test_signals_has_no_execution_cta_fields(self):
        """signals items should NOT contain execution-oriented CTA fields."""
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            "positions": {},
            "available_cash": 50000.0,
            "broker_buying_power": 50000.0,
            "broker_buying_power_source": "available_cash",
            "cash_available_for_csp": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "short_calls": {},
            "short_puts": {},
        }
        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

        engine = self._import_engine()
        result = engine.get_top_recommendations(limit=5)

        for rec in result.get("signals", []):
            self.assertNotIn(
                "execution_blocked", rec, msg="execution_blocked should not appear in signal-only recommendations"
            )
            self.assertNotIn(
                "execution_blocked_reason",
                rec,
                msg="execution_blocked_reason should not appear in signal-only recommendations",
            )

    def test_blocked_reason_counts_in_response(self):
        """Response should include blocked_reason_counts dict."""
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            "positions": {},
            "available_cash": 50000.0,
            "broker_buying_power": 50000.0,
            "broker_buying_power_source": "available_cash",
            "cash_available_for_csp": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "short_calls": {},
            "short_puts": {},
        }
        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

        engine = self._import_engine()
        result = engine.get_top_recommendations(limit=5)

        self.assertIn("blocked_reason_counts", result)

    def test_covered_calls_are_actionable_signals(self):
        """Covered call signals should have research_only=False."""
        from core.wheel_decision import WheelDecision

        mock_decision = MagicMock(spec=WheelDecision)
        mock_decision.hard_blockers = []
        mock_decision.strike = 155.0
        mock_decision.expiration = "20260510"
        mock_decision.dte = 14
        mock_decision.mid_price = 1.20
        mock_decision.premium_per_contract = 120.0
        mock_decision.bid = 1.15
        mock_decision.ask = 1.25
        mock_decision.annualized_return = 18.0
        mock_decision.iv_adjusted_return = 15.0
        mock_decision.otm_pct = 5.0
        mock_decision.delta = 0.30
        mock_decision.implied_volatility = 0.35
        mock_decision.open_interest = 2000
        mock_decision.volume = 1000
        mock_decision.iv_rank = 0.6
        mock_decision.iv_status = "normal"
        mock_decision.iv_env_adjustment = 0
        mock_decision.profile_type = "standard"
        mock_decision.size_fit = 1.0
        mock_decision.expected_move_buffer = 0.05
        mock_decision.breakeven = 73.0
        mock_decision.breakeven_buffer_pct = 0.05
        mock_decision.cash_required = 0
        mock_decision.score_details = {}
        mock_decision.rationale = ["Good call premium"]
        mock_decision.warnings = []
        mock_decision.to_dict.return_value = {
            "score": 85.0,
            "covered_call_intent": "income",
            "score_rationale": "Strong growth candidate",
            "stress_loss": 300,
            "risk_budget_used_pct": 0.10,
            "price_source": "moomoo",
            "chain_source": "moomoo",
            "confidence_score": 95,
            "hard_blockers": [],
        }

        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            "positions": {"AAPL": {"position": 300, "market_price": 148.0, "avg_cost": 140.0}},
            "available_cash": 50000.0,
            "broker_buying_power": 50000.0,
            "broker_buying_power_source": "available_cash",
            "cash_available_for_csp": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "short_calls": {},
            "short_puts": {},
        }
        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

        # Mock the options data provider to return CALL data
        self.mock_options_data._process_ticker_for_otm.return_value = {
            "calls": [
                {
                    "strike": 155.0,
                    "expiration": "20260510",
                    "dte": 14,
                    "bid": 1.15,
                    "ask": 1.25,
                    "last": 1.20,
                    "mid_price": 1.20,
                    "premium_per_contract": 120.0,
                    "annualized_return": 18.0,
                    "otm_pct": 5.0,
                    "delta": 0.30,
                    "implied_volatility": 0.35,
                    "open_interest": 2000,
                    "volume": 1000,
                    "score": 85.0,
                    "contract_score": 85.0,
                    "wheel_decision": mock_decision.to_dict(),
                    "cash_required": 0,
                    "breakeven": 155.0,
                    "breakeven_buffer_pct": 0.05,
                }
            ],
            "puts": [],
        }
        self.mock_conn.get_stock_price.return_value = 148.0

        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_score.return_value = mock_decision
            engine = self._import_engine()
            result = engine.get_top_recommendations(limit=5)

        for rec in result.get("signals", []):
            if rec.get("option_type") == "CALL":
                self.assertEqual(rec["signal_type"], "covered_call")
                self.assertFalse(rec["research_only"], msg="Covered calls should have research_only=False")


class TestRecommendationNonDuplication(unittest.TestCase):
    """Test that the unified signal list does not duplicate underlyings."""

    def setUp(self):
        self.mock_connection_provider = MagicMock()
        self.mock_config_provider = MagicMock()
        self.mock_config_provider.config = {"cash_reserve_enabled": True}
        self.mock_db = MagicMock()
        self.mock_iv_earnings = MagicMock()
        self.mock_portfolio_context_provider = MagicMock()
        self.mock_portfolio_service_provider = MagicMock()
        self.mock_watchlist_manager = MagicMock()
        self.mock_options_data = MagicMock()
        self.mock_cash_calculator = MagicMock()

        self.mock_conn = MagicMock()
        self.mock_conn.get_stock_price.return_value = 150.0
        self.mock_connection_provider._ensure_connection.return_value = self.mock_conn

        self.mock_portfolio_context = {
            "positions": {
                "AAPL": {"position": 200, "market_price": 150.0, "avg_cost": 145.0},
            },
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "broker_buying_power": 50000.0,
            "broker_buying_power_source": "available_cash",
            "cash_available_for_csp": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "excess_liquidity": 50000.0,
            "short_calls": {},
            "short_puts": {},
        }
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = self.mock_portfolio_context
        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

        self.mock_options_data._process_ticker_for_otm.return_value = {
            "calls": [
                {
                    "strike": 160,
                    "expiration": "20240315",
                    "bid": 2.0,
                    "ask": 2.10,
                    "last": 2.05,
                    "delta": 0.20,
                    "implied_volatility": 0.30,
                    "open_interest": 500,
                    "volume": 100,
                    "dte": 21,
                },
            ],
            "puts": [],
        }

    def _import_engine(self):
        from api.services.recommendations import RecommendationEngine

        return RecommendationEngine(
            self.mock_connection_provider,
            self.mock_config_provider,
            self.mock_db,
            self.mock_iv_earnings,
            self.mock_portfolio_context_provider,
            self.mock_portfolio_service_provider,
            self.mock_watchlist_manager,
            self.mock_options_data,
            self.mock_cash_calculator,
        )

    def _make_mock_decision(self):
        from core.wheel_decision import WheelDecision

        d = MagicMock(spec=WheelDecision)
        d.contract_score = 85.0
        d.strike = 160
        d.expiration = "20240315"
        d.dte = 21
        d.mid_price = 2.05
        d.premium_per_contract = 205.0
        d.annualized_return = 35.0
        d.iv_adjusted_return = 30.0
        d.otm_pct = 6.67
        d.delta = 0.20
        d.implied_volatility = 0.30
        d.open_interest = 500
        d.volume = 100
        d.iv_rank = 0.6
        d.iv_status = "normal"
        d.iv_env_adjustment = 0
        d.profile_type = "monthly"
        d.size_fit = 1.0
        d.expected_move_buffer = 0.05
        d.breakeven = 158.0
        d.breakeven_buffer_pct = 0.05
        d.cash_required = 16000.0
        d.score_details = {}
        d.rationale = ["Good premium"]
        d.warnings = []
        d.quote_quality = "tradable"
        d.blocked_reason_codes = []
        d.to_dict.return_value = {"score": 85.0, "quote_quality": "tradable", "blocked_reason_codes": []}
        return d

    def test_signals_present_then_legacy_fields_absent(self):
        """The unified signal payload should not expose legacy top-level fields."""
        engine = self._import_engine()
        mock_decision = self._make_mock_decision()

        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_score.return_value = mock_decision
            with patch.object(engine, "_fetch_watchlist_ticker_csp") as mock_fetch:
                mock_fetch.return_value = [
                    {
                        "ticker": "AAPL",
                        "stock_price": 150.0,
                        "option_type": "PUT",
                        "max_contracts": 1,
                        "existing_position": 0,
                        "from_watchlist": True,
                        "strike": 140.0,
                        "expiration": "20240315",
                        "dte": 21,
                        "mid_price": 2.75,
                        "premium_per_contract": 275.0,
                        "bid": 2.50,
                        "ask": 3.00,
                        "annualized_return": 18.0,
                        "iv_adjusted_return": 50.0,
                        "otm_pct": 6.67,
                        "delta": -0.20,
                        "implied_volatility": 0.35,
                        "open_interest": 500,
                        "volume": 200,
                        "score": 75.0,
                        "profile_type": "monthly",
                        "warnings": [],
                        "wheel_decision": {"contract_score": 75.0, "confidence_score": 100},
                    }
                ]
                result = engine.get_top_recommendations(limit=5)

        self.assertGreater(len(result.get("signals", [])), 0)
        self.assertNotIn("best_plays", result)
        self.assertNotIn("recommendations", result)
        self.assertNotIn("lanes", result)

    def test_no_duplicate_tickers_across_signals(self):
        """Tickers in signals should not duplicate underlyings."""
        engine = self._import_engine()
        from core.ticker_utils import canonical_underlying

        # Set up watchlist with AAPL + additional tickers
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["MSFT"]
        self.mock_portfolio_context["positions"]["MSFT"] = {"position": 100, "market_price": 300.0, "avg_cost": 290.0}

        # Set up OTM data for both tickers
        def process_side_effect(conn, ticker, otm_percentage, portfolio_context, expiration=None, option_type=None):
            if ticker == "MSFT":
                return {
                    "calls": [],
                    "puts": [
                        {
                            "strike": 280,
                            "expiration": "20240315",
                            "bid": 3.0,
                            "ask": 3.20,
                            "last": 3.10,
                            "delta": 0.18,
                            "implied_volatility": 0.30,
                            "open_interest": 500,
                            "volume": 100,
                            "dte": 21,
                        },
                    ],
                }
            return {
                "calls": [
                    {
                        "strike": 160,
                        "expiration": "20240315",
                        "bid": 2.0,
                        "ask": 2.10,
                        "last": 2.05,
                        "delta": 0.20,
                        "implied_volatility": 0.30,
                        "open_interest": 500,
                        "volume": 100,
                        "dte": 21,
                    }
                ],
                "puts": [],
            }

        self.mock_options_data._process_ticker_for_otm.side_effect = process_side_effect

        mock_decision = self._make_mock_decision()

        with patch("api.services.recommendations.score_contract") as mock_score:
            mock_score.return_value = mock_decision
            with patch.object(engine, "_fetch_watchlist_ticker_csp") as mock_fetch:
                mock_fetch.return_value = [
                    {
                        "strike": 280,
                        "expiration": "20240315",
                        "option_type": "PUT",
                        "bid": 3.0,
                        "ask": 3.20,
                        "last": 3.10,
                        "dte": 21,
                        "implied_volatility": 0.30,
                        "open_interest": 500,
                        "volume": 100,
                        "mid_price": 3.10,
                        "premium_per_contract": 310.0,
                        "annualized_return": 30.0,
                        "score": 80.0,
                        "ticker": "MSFT",
                        "delta": 0.18,
                        "iv_rank": 0.6,
                        "otm_pct": 6.67,
                        "breakeven": 276.9,
                        "breakeven_buffer_pct": 0.03,
                        "cash_required": 28000.0,
                        "rationale": ["Good premium"],
                        "warnings": [],
                        "score_details": {},
                        "wheel_decision": {"score": 80.0, "quote_quality": "tradable", "blocked_reason_codes": []},
                    }
                ]
                result = engine.get_top_recommendations(limit=5)

        # signals should have items and stay deduplicated by underlying
        self.assertGreater(len(result.get("signals", [])), 0)
        seen = set()
        for rec in result.get("signals", []):
            cu = canonical_underlying(rec["ticker"])
            self.assertNotIn(cu, seen, f"Duplicate underlying {cu} found")
            seen.add(cu)

    def setUp(self):
        self.mock_connection_provider = MagicMock()
        self.mock_config_provider = MagicMock()
        self.mock_config_provider.config = {"cash_reserve_enabled": True}
        self.mock_db = MagicMock()
        self.mock_iv_earnings = MagicMock()
        self.mock_portfolio_context_provider = MagicMock()
        self.mock_portfolio_service_provider = MagicMock()
        self.mock_watchlist_manager = MagicMock()
        self.mock_options_data = MagicMock()
        self.mock_cash_calculator = MagicMock()

        self.mock_conn = MagicMock()
        self.mock_conn.get_stock_price.return_value = 150.0
        self.mock_connection_provider._ensure_connection.return_value = self.mock_conn

        self.mock_portfolio_context = {
            "positions": {},
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "broker_buying_power": 50000.0,
            "broker_buying_power_source": "available_cash",
            "cash_available_for_csp": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "excess_liquidity": 50000.0,
            "short_calls": {},
            "short_puts": {},
        }
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = self.mock_portfolio_context
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["ASKONLY"]

    def test_skip_diagnostics_surface_in_blocked_signals(self):
        """Watchlist CSP skip diagnostics should appear in blocked_signals."""
        from api.services.recommendations import RecommendationEngine

        engine = RecommendationEngine(
            self.mock_connection_provider,
            self.mock_config_provider,
            self.mock_db,
            self.mock_iv_earnings,
            self.mock_portfolio_context_provider,
            self.mock_portfolio_service_provider,
            self.mock_watchlist_manager,
            self.mock_options_data,
            self.mock_cash_calculator,
        )

        with patch.object(engine, "_fetch_watchlist_ticker_csp") as mock_fetch:
            mock_fetch.return_value = [
                engine._make_skip_diagnostic("ASKONLY", "no_bid", "No executable bid - ask-only quote")
            ]
            result = engine.get_top_recommendations(limit=5)

        self.assertIn("blocked_signals", result)
        blocked = result["blocked_signals"]
        self.assertGreater(len(blocked), 0)
        askonly_blocked = [b for b in blocked if b.get("ticker") == "ASKONLY"]
        self.assertEqual(len(askonly_blocked), 1)
        self.assertEqual(askonly_blocked[0]["reason_code"], "no_bid")
        self.assertIn("No executable bid", askonly_blocked[0]["reason_text"])
        self.assertIn("blocked_reason_counts", result)
        self.assertIn("no_bid", result["blocked_reason_counts"])

    def test_score_contract_blocked_signals_appear_in_skipped_diagnostics(self):
        """When score_contract blocks a watchlist CSP, the skip diagnostic should surface."""
        from api.services.recommendations import RecommendationEngine

        engine = RecommendationEngine(
            self.mock_connection_provider,
            self.mock_config_provider,
            self.mock_db,
            self.mock_iv_earnings,
            self.mock_portfolio_context_provider,
            self.mock_portfolio_service_provider,
            self.mock_watchlist_manager,
            self.mock_options_data,
            self.mock_cash_calculator,
        )

        with patch.object(engine, "_fetch_watchlist_csp_moomoo") as mock_fetch:
            mock_fetch.return_value = [
                engine._make_skip_diagnostic("BLOCKED", "no_bid", "No executable bid - ask-only quote")
            ]
            result = engine.get_top_recommendations(limit=5)

        self.assertIn("blocked_signals", result)
        if result.get("blocked_signals"):
            has_quote_quality = any(
                b.get("reason_code") in ("no_bid", "no_ask", "no_market", "wide_spread", "zero_mark", "low_liquidity")
                for b in result["blocked_signals"]
            )
            self.assertTrue(has_quote_quality, msg="Blocked candidates should include quote-quality reason codes")

    def test_iv_rank_plumbed_into_scored_decision(self):
        """get_iv_environment_score values should appear in the WheelDecision."""
        self.mock_iv_earnings.get_iv_environment_score.return_value = (5, 0.65, "above_avg")

        from api.services.recommendations import RecommendationEngine

        engine = RecommendationEngine(
            self.mock_connection_provider,
            self.mock_config_provider,
            self.mock_db,
            self.mock_iv_earnings,
            self.mock_portfolio_context_provider,
            self.mock_portfolio_service_provider,
            self.mock_watchlist_manager,
            self.mock_options_data,
            self.mock_cash_calculator,
        )

        future_date = (datetime.now() + timedelta(days=37)).strftime("%Y%m%d")
        {
            "strike": 140.0,
            "expiration": future_date.replace("-", ""),
            "option_type": "PUT",
            "bid": 2.50,
            "ask": 3.00,
            "last": 2.75,
            "delta": -0.20,
            "gamma": 0.05,
            "theta": -0.08,
            "vega": 0.15,
            "implied_volatility": 0.35,
            "open_interest": 500,
            "volume": 200,
            "dte": 37,
        }
        with patch.object(engine, "_fetch_watchlist_csp_moomoo") as mock_fetch:
            mock_fetch.return_value = [
                {
                    "ticker": "AAPL",
                    "stock_price": 150.0,
                    "option_type": "PUT",
                    "max_contracts": 1,
                    "existing_position": 0,
                    "from_watchlist": True,
                    "strike": 140.0,
                    "expiration": future_date.replace("-", ""),
                    "dte": 37,
                    "mid_price": 2.75,
                    "premium_per_contract": 275.0,
                    "bid": 2.50,
                    "ask": 3.00,
                    "annualized_return": 18.0,
                    "iv_adjusted_return": 50.0,
                    "otm_pct": 6.67,
                    "delta": -0.20,
                    "implied_volatility": 0.35,
                    "open_interest": 500,
                    "volume": 200,
                    "score": 75.0,
                    "iv_rank": 0.65,
                    "iv_status": "above_avg",
                    "iv_env_adjustment": 5,
                    "size_fit": 85.0,
                    "expected_move_buffer": 3.5,
                    "breakeven": 137.5,
                    "breakeven_buffer_pct": 8.33,
                    "cash_required": 14000.0,
                    "warnings": [],
                    "wheel_decision": {
                        "iv_rank": 0.65,
                        "iv_env_adjustment": 5,
                        "iv_status": "above_avg",
                        "contract_score": 75.0,
                    },
                    "score_details": {},
                    "cash_reserve_enabled": True,
                    "profile_type": "monthly",
                }
            ]
            result = engine.get_top_recommendations(limit=5)

        self.assertIsNotNone(result)
        self.assertTrue(result.get("success"))
        signals = result.get("signals", [])
        self.assertGreater(len(signals), 0)
        sig = signals[0]
        self.assertEqual(sig.get("iv_rank"), 0.65)
        self.assertEqual(sig.get("iv_status"), "above_avg")
        wd = sig.get("wheel_decision", {})
        self.assertEqual(wd.get("iv_rank"), 0.65)

    def test_cash_prefilter_runs_in_research_only_mode(self):
        """With low cash, research_only_mode still avoids impossible chain fetches."""
        self.mock_iv_earnings.get_iv_environment_score.return_value = (0, 0.5, "neutral")

        from api.services.recommendations import RecommendationEngine

        engine = RecommendationEngine(
            self.mock_connection_provider,
            self.mock_config_provider,
            self.mock_db,
            self.mock_iv_earnings,
            self.mock_portfolio_context_provider,
            self.mock_portfolio_service_provider,
            self.mock_watchlist_manager,
            self.mock_options_data,
            self.mock_cash_calculator,
        )

        # Set very low buying power: $200 -> even deep OTM put on $5 stock needs $250
        portfolio = dict(
            self.mock_portfolio_context, cash_available_for_csp=200.0, available_cash=200.0, broker_buying_power=200.0
        )

        # Mock cached stock price to return None so it falls through to live price check
        self.mock_conn.get_cached_stock_price.return_value = None

        with patch("api.services.recommendations.is_market_open", return_value=True):
            result = engine._fetch_watchlist_csp_moomoo("CHEAP", portfolio)

        self.assertEqual(result[0]["reason_code"], "no_cash_fit")
        self.mock_conn.get_option_expiration_dates.assert_not_called()
        self.mock_conn.get_option_chain.assert_not_called()

    def setUp(self):
        self.mock_connection_provider = MagicMock()
        self.mock_config_provider = MagicMock()
        self.mock_config_provider.config = {"cash_reserve_enabled": True}
        self.mock_db = MagicMock()
        self.mock_iv_earnings = MagicMock()
        self.mock_portfolio_context_provider = MagicMock()
        self.mock_portfolio_service_provider = MagicMock()
        self.mock_watchlist_manager = MagicMock()
        self.mock_options_data = MagicMock()
        self.mock_cash_calculator = MagicMock()

        self.mock_conn = MagicMock()
        self.mock_conn.get_stock_price.return_value = 100.0
        self.mock_connection_provider._ensure_connection.return_value = self.mock_conn

        self.mock_portfolio_context = {
            "positions": {},
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "broker_buying_power": 50000.0,
            "broker_buying_power_source": "available_cash",
            "cash_available_for_csp": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "excess_liquidity": 50000.0,
            "short_calls": {},
            "short_puts": {},
        }
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = self.mock_portfolio_context
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["ASKONLY"]

        self.mock_watchlist_manager.get_screening_profile.return_value = {
            "min_mid_price": 0.05,
            "max_spread_pct": 60,
            "min_premium_per_contract": 5,
            "min_open_interest": 10,
            "min_volume": 1,
            "target_iv_adjusted": 50,
            "target_theta_delta_ratio": 0.005,
            "preferred_dte": 37,
            "target_delta": 0.30,
            "delta_tolerance": 0.12,
            "ideal_open_interest": 500,
            "ideal_volume": 100,
            "ideal_spread_pct": 12,
            "liquidity_weight_multiplier": 1.0,
            "profile_type": "monthly",
            "min_dte": 30,
            "max_dte": 45,
            "min_otm_pct": 5,
            "max_otm_pct": 15,
        }

    def _import_engine(self):
        from api.services.recommendations import RecommendationEngine

        engine = RecommendationEngine(
            self.mock_connection_provider,
            self.mock_config_provider,
            self.mock_db,
            self.mock_iv_earnings,
            self.mock_portfolio_context_provider,
            self.mock_portfolio_service_provider,
            self.mock_watchlist_manager,
            self.mock_options_data,
            self.mock_cash_calculator,
        )
        engine._preset_profile = {
            "screener_profile": {
                "csp_default_otm_pct": 10,
                "csp_min_otm_pct": 5,
                "csp_max_otm_pct": 15,
                "csp_min_dte": 30,
                "csp_max_dte": 45,
                "csp_preferred_dte": 37,
            }
        }
        return engine

    def test_has_any_affordable_otm_strike_10pct_not_fit_but_15pct_fits(self):
        """10% OTM strike ($90) needs $9000 but only $8500 available, but 15% OTM ($85) needs $8500."""
        engine = self._import_engine()
        portfolio = {
            "cash_available_for_csp": 8500.0,
            "broker_buying_power": 8500.0,
        }
        # Stock at $100: 10% OTM = $90 strike (needs $9000), 15% OTM = $85 strike (needs $8500)
        result = engine._has_any_affordable_otm_strike(100.0, portfolio)
        self.assertTrue(result)

    def test_no_affordable_otm_strike_at_all(self):
        """Even 15% OTM strike exceeds buying power."""
        engine = self._import_engine()
        portfolio = {
            "cash_available_for_csp": 5000.0,
            "broker_buying_power": 5000.0,
        }
        # Stock at $100: 15% OTM = $85 strike (needs $8500 > $5000)
        result = engine._has_any_affordable_otm_strike(100.0, portfolio)
        self.assertFalse(result)

    def test_find_affordable_csp_strike_returns_highest_affordable(self):
        """Should return the highest strike that fits buying power."""
        engine = self._import_engine()
        portfolio = {
            "cash_available_for_csp": 8500.0,
            "broker_buying_power": 8500.0,
        }
        strikes = [80.0, 85.0, 88.0, 90.0, 95.0]
        result = engine._find_affordable_csp_strike(100.0, portfolio, strikes)
        self.assertEqual(result[0], 85.0)

    def test_watchlist_csp_scoring_applies_earnings_and_vix_context(self):
        """Headline CSP scoring should apply earnings risk and pass VIX into profile selection."""
        engine = self._import_engine()
        self.mock_iv_earnings.get_earnings_score_impact.return_value = (-30, "earnings soon")
        self.mock_iv_earnings.get_earnings_info.return_value = {
            "earnings_date": "2026-07-15",
            "days_to_earnings": 5,
            "warning_level": "soon",
        }
        expiration = (datetime.now() + timedelta(days=37)).strftime("%Y%m%d")
        contract = {
            "strike": 90.0,
            "expiration": expiration,
            "option_type": "PUT",
            "bid": 2.0,
            "ask": 2.10,
            "last": 2.05,
            "dte": 37,
            "implied_volatility": 0.35,
            "open_interest": 500,
            "volume": 200,
            "delta": -0.30,
            "theta": -0.06,
            "gamma": 0.04,
            "vega": 0.15,
        }
        portfolio = {
            "positions": {},
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "broker_buying_power": 50000.0,
            "cash_available_for_csp": 50000.0,
            "account_value": 100000.0,
            "vix_regime": {"regime": "fear", "vix": 32, "delta_adjustment": -0.03},
        }

        decision = engine._score_csp_contract(contract, "AAPL", 100.0, 37, portfolio, {})

        self.assertFalse(decision.hard_blockers)
        self.assertEqual(decision.earnings_adjustment, -30)
        self.assertEqual(decision.earnings_date, "2026-07-15")
        self.assertEqual(decision.days_to_earnings, 5)
        self.assertEqual(decision.vix_regime, "fear")
        self.mock_watchlist_manager.get_screening_profile.assert_called_with(
            "PUT",
            dte=37,
            vix_regime=portfolio["vix_regime"],
            growth_mode_config=engine._preset_profile,
        )

    def test_watchlist_csp_scoring_blocks_missing_iv(self):
        """Headline CSP scoring should not rank contracts that still lack IV after enrichment."""
        engine = self._import_engine()
        expiration = (datetime.now() + timedelta(days=37)).strftime("%Y%m%d")
        contract = {
            "strike": 90.0,
            "expiration": expiration,
            "option_type": "PUT",
            "bid": 2.0,
            "ask": 2.10,
            "last": 2.05,
            "dte": 37,
            "implied_volatility": 0,
            "open_interest": 500,
            "volume": 200,
            "delta": -0.30,
        }

        decision = engine._score_csp_contract(
            contract,
            "AAPL",
            100.0,
            37,
            {"cash_available_for_csp": 50000.0, "broker_buying_power": 50000.0},
            {},
        )

        self.assertTrue(decision.hard_blockers)
        self.assertIn("missing_iv", decision.blocked_reason_codes)


if __name__ == "__main__":
    unittest.main()


class TestRiskTierRanking(unittest.TestCase):
    """Unknown optional risk metadata ranks below known-risk candidates."""

    def setUp(self):
        self.mock_connection_provider = MagicMock()
        self.mock_config_provider = MagicMock()
        self.mock_config_provider.config = {"cash_reserve_enabled": True}
        self.mock_db = MagicMock()
        self.mock_iv_earnings = MagicMock()
        self.mock_portfolio_context_provider = MagicMock()
        self.mock_portfolio_service_provider = MagicMock()
        self.mock_watchlist_manager = MagicMock()
        self.mock_options_data = MagicMock()
        self.mock_cash_calculator = MagicMock()
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ["AAA"]
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            "positions": {},
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "broker_buying_power": 50000.0,
            "cash_available_for_csp": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "excess_liquidity": 50000.0,
            "short_calls": {},
            "short_puts": {},
        }

    def _import_engine(self):
        from api.services.recommendations import RecommendationEngine

        return RecommendationEngine(
            self.mock_connection_provider,
            self.mock_config_provider,
            self.mock_db,
            self.mock_iv_earnings,
            self.mock_portfolio_context_provider,
            self.mock_portfolio_service_provider,
            self.mock_watchlist_manager,
            self.mock_options_data,
            self.mock_cash_calculator,
        )

    def test_unknown_earnings_metadata_ranks_below_known_risk(self):
        engine = self._import_engine()
        # Unknown-risk candidate has HIGHER premium velocity.
        unknown_risk = {
            "ticker": "AAA",
            "stock_price": 100.0,
            "option_type": "PUT",
            "strike": 90.0,
            "expiration": "20240315",
            "dte": 10,
            "mid_price": 1.00,
            "premium_per_contract": 110.0,
            "bid": 1.10,
            "ask": 1.05,
            "annualized_return": 18.0,
            "iv_adjusted_return": 50.0,
            "otm_pct": 10.0,
            "delta": -0.25,
            "implied_volatility": 0.35,
            "open_interest": 500,
            "volume": 200,
            "score": 75.0,
            "profile_type": "monthly",
            "warnings": [],
            "wheel_decision": {"contract_score": 75.0, "confidence_score": 100},
            # earnings metadata missing -> unknown
        }
        known_risk = dict(unknown_risk)
        known_risk["premium_per_contract"] = 50.0  # bid velocity 5 < 11
        known_risk["earnings_date"] = "2026-07-15"
        known_risk["days_to_earnings"] = 12

        with patch.object(engine, "_fetch_watchlist_ticker_csp", return_value=[known_risk, unknown_risk]):
            result = engine.get_top_recommendations(limit=3)

        signals = result.get("signals", [])
        self.assertGreaterEqual(len(signals), 1)
        # The known-risk candidate must rank first despite lower velocity.
        self.assertEqual(signals[0]["premium_per_contract"], 50.0)
        self.assertEqual(signals[0]["days_to_earnings"], 12)
        if len(signals) > 1:
            self.assertEqual(signals[1]["premium_per_contract"], 110.0)


if __name__ == "__main__":
    unittest.main()
