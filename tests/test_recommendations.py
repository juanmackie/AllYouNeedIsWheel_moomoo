"""
Tests for api/services/recommendations.py — RecommendationEngine class
"""

import unittest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRecommendationEngine(unittest.TestCase):
    """Test RecommendationEngine with fully mocked context."""

    def setUp(self):
        self.mock_connection_provider = MagicMock()
        self.mock_config_provider = MagicMock()
        self.mock_config_provider.config = {'cash_reserve_enabled': True}
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
            'positions': {
                'AAPL': {'position': 200, 'market_price': 150.0, 'avg_cost': 145.0},
            },
            'cash_balance': 50000.0,
            'short_calls': {},
            'short_puts': {},
        }
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = self.mock_portfolio_context

        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

        self.mock_options_data._process_ticker_for_otm.return_value = {
            'calls': [
                {'strike': 160, 'expiration': '20240315', 'bid': 2.0, 'ask': 2.10,
                 'last': 2.05, 'delta': 0.20, 'implied_volatility': 0.30,
                 'open_interest': 500, 'volume': 100, 'dte': 21},
            ],
            'puts': [
                {'strike': 140, 'expiration': '20240315', 'bid': 1.50, 'ask': 1.60,
                 'last': 1.55, 'delta': 0.18, 'implied_volatility': 0.35,
                 'open_interest': 300, 'volume': 200, 'dte': 21},
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

        self.assertIn('error', result)

    def test_get_top_recommendations_empty_portfolio(self):
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            'positions': {},
            'cash_balance': 0,
            'short_calls': {},
            'short_puts': {},
        }
        engine = self._import_engine()

        result = engine.get_top_recommendations(limit=5)

        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 0)

    def test_get_top_recommendations_processes_positions(self):
        engine = self._import_engine()

        with patch('api.services.recommendations.score_contract') as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = '20240315'
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
            mock_decision.iv_status = 'normal'
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = 'monthly'
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ['Good premium']
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {'score': 85.0}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        self.assertTrue(result['success'])
        self.assertGreater(result['total_scored'], 0)
        self.assertIn('recommendations', result)

    def test_get_top_recommendations_respects_limit(self):
        engine = self._import_engine()

        with patch('api.services.recommendations.score_contract') as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = '20240315'
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
            mock_decision.iv_status = 'normal'
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = 'monthly'
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ['Good premium']
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=1)

        self.assertTrue(result['success'])
        self.assertLessEqual(result['count'], 1)


class TestRecommendationEngineStripPrefix(unittest.TestCase):
    """Test ticker prefix stripping delegates to utils."""

    def test_strip_ticker_prefix_delegates(self):
        with patch('api.services.recommendations.clean_yfinance_ticker') as mock_clean:
            mock_clean.return_value = 'AAPL'
            from api.services.recommendations import RecommendationEngine

            engine = RecommendationEngine(MagicMock(), MagicMock(), MagicMock(),
                                           MagicMock(), MagicMock(), MagicMock(),
                                           MagicMock(), MagicMock(), MagicMock())
            result = engine._strip_ticker_prefix('US.AAPL')

            self.assertEqual(result, 'AAPL')
            mock_clean.assert_called_once_with('US.AAPL')



class TestRecommendationEngineLanes(unittest.TestCase):
    """Test the new lane-based recommendation structure."""

    def setUp(self):
        self.mock_connection_provider = MagicMock()
        self.mock_config_provider = MagicMock()
        self.mock_config_provider.config = {'cash_reserve_enabled': True}
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
            'positions': {
                'AAPL': {'position': 200, 'market_price': 150.0, 'avg_cost': 145.0},
            },
            'cash_balance': 50000.0,
            'available_cash': 50000.0,
            'cash_available_for_csp': 50000.0,
            'cash_reserved_for_csp': 0.0,
            'excess_liquidity': 50000.0,
            'short_calls': {},
            'short_puts': {},
        }
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = self.mock_portfolio_context
        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

        self.mock_options_data._process_ticker_for_otm.return_value = {
            'calls': [
                {'strike': 160, 'expiration': '20240315', 'bid': 2.0, 'ask': 2.10,
                 'last': 2.05, 'delta': 0.20, 'implied_volatility': 0.30,
                 'open_interest': 500, 'volume': 100, 'dte': 21},
            ],
            'puts': [
                {'strike': 140, 'expiration': '20240315', 'bid': 1.50, 'ask': 1.60,
                 'last': 1.55, 'delta': 0.18, 'implied_volatility': 0.35,
                 'open_interest': 300, 'volume': 200, 'dte': 21},
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

    def test_lanes_present_in_response(self):
        """Response should contain lanes dict with covered_calls and watchlist_csp."""
        engine = self._import_engine()
        with patch('api.services.recommendations.score_contract') as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = '20240315'
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
            mock_decision.iv_status = 'normal'
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = 'monthly'
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ['Good premium']
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {'score': 85.0}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        self.assertIn('lanes', result)
        self.assertIn('covered_calls', result['lanes'])
        self.assertIn('watchlist_csp', result['lanes'])
        self.assertIn('cash_available_for_csp', result)
        self.assertIn('cash_reserved_for_csp', result)

    def test_covered_calls_lane_contains_only_calls(self):
        """The covered_calls lane should only contain CALL options."""
        engine = self._import_engine()
        with patch('api.services.recommendations.score_contract') as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = '20240315'
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
            mock_decision.iv_status = 'normal'
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = 'monthly'
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ['Good premium']
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {'score': 85.0}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        for rec in result['lanes']['covered_calls']['recommendations']:
            self.assertEqual(rec['option_type'], 'CALL')

    def test_held_position_flag_in_watchlist_csp(self):
        """Watchlist CSP recommendations should have held_position flag."""
        engine = self._import_engine()

        # Set up watchlist with AAPL (which is already in positions)
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ['AAPL']

        with patch('api.services.recommendations.score_contract') as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = '20240315'
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
            mock_decision.iv_status = 'normal'
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = 'monthly'
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ['Good premium']
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {'score': 85.0}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        # Check that held_position appears in the watchlist CSP lane
        for rec in result['lanes']['watchlist_csp']['recommendations']:
            self.assertIn('held_position', rec)

    def test_legacy_recommendations_still_present(self):
        """Legacy recommendations list should still be present for backwards compatibility."""
        engine = self._import_engine()
        with patch('api.services.recommendations.score_contract') as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 160
            mock_decision.expiration = '20240315'
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
            mock_decision.iv_status = 'normal'
            mock_decision.iv_env_adjustment = 0
            mock_decision.profile_type = 'monthly'
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 158.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 16000.0
            mock_decision.score_details = {}
            mock_decision.rationale = ['Good premium']
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {'score': 85.0}
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        self.assertIn('recommendations', result)
        self.assertGreaterEqual(len(result['recommendations']), 0)


class TestRecommendationEngineCashFields(unittest.TestCase):
    """Test cash field consistency in recommendations."""

    def setUp(self):
        self.mock_connection_provider = MagicMock()
        self.mock_config_provider = MagicMock()
        self.mock_config_provider.config = {'cash_reserve_enabled': True}
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

    def test_cash_available_for_csp_in_response(self):
        """Response should include cash_available_for_csp and cash_reserved_for_csp."""
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            'positions': {},
            'cash_balance': 50000.0,
            'available_cash': 50000.0,
            'cash_available_for_csp': 35000.0,
            'cash_reserved_for_csp': 15000.0,
            'excess_liquidity': 50000.0,
            'short_calls': {},
            'short_puts': {},
        }
        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

        engine = self._import_engine()
        result = engine.get_top_recommendations(limit=5)

        self.assertEqual(result.get('cash_available_for_csp'), 35000.0)
        self.assertEqual(result.get('cash_reserved_for_csp'), 15000.0)


if __name__ == '__main__':
    unittest.main()
