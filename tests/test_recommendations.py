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
            'available_cash': 50000.0,
            'broker_buying_power': 50000.0,
            'broker_buying_power_source': 'available_cash',
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
        self.assertIn('signals', result)
        self.assertIn('blocked_signals', result)
        self.assertNotIn('recommendations', result)
        self.assertNotIn('best_plays', result)
        self.assertNotIn('lanes', result)
        self.assertNotIn('blocked_candidates', result)

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



class TestRecommendationEngineSignals(unittest.TestCase):
    """Test the unified signal recommendation structure."""

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
            'broker_buying_power': 50000.0,
            'broker_buying_power_source': 'available_cash',
            'cash_available_for_csp': 50000.0,
            'cash_reserved_for_csp': 0.0,
            'open_short_put_collateral': 0.0,
            'excess_liquidity': 50000.0,
            'short_calls': {},
            'short_puts': {},
            'vix_regime': {'regime': 'normal', 'vix': 18.0},
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

    def test_signals_present_in_response(self):
        """Response should contain a unified signals list and no legacy wrappers."""
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

        self.assertIn('signals', result)
        self.assertIn('blocked_signals', result)
        self.assertNotIn('lanes', result)
        self.assertNotIn('best_plays', result)
        self.assertNotIn('recommendations', result)
        self.assertNotIn('blocked_candidates', result)
        self.assertIn('broker_buying_power', result)
        self.assertIn('cash_available_for_csp', result)
        self.assertIn('cash_reserved_for_csp', result)

    def test_signals_contains_only_calls_for_covered_call_signals(self):
        """Covered call signals should only contain CALL options."""
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

        for rec in result['signals']:
            if rec['signal_type'] != 'covered_call':
                continue
            self.assertEqual(rec['option_type'], 'CALL')

    def test_held_position_flag_in_watchlist_csp(self):
        """Watchlist CSP signals should have held_position flag."""
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

        # Check that held_position appears in the CSP signal subset
        for rec in result['signals']:
            if rec['signal_type'] != 'csp':
                continue
            self.assertIn('held_position', rec)

    def test_legacy_recommendation_fields_removed(self):
        """Legacy top-level recommendation fields should no longer be present."""
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

        self.assertIn('signals', result)
        self.assertNotIn('recommendations', result)
        self.assertNotIn('best_plays', result)
        self.assertNotIn('lanes', result)



class TestRecommendationEngineDedup(unittest.TestCase):
    """Test symbol deduplication by canonical underlying."""

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
        self.mock_conn.get_stock_price.return_value = 70.0
        self.mock_connection_provider._ensure_connection.return_value = self.mock_conn

        self.mock_portfolio_context = {
            'positions': {
                'UBER': {'position': 200, 'market_price': 70.0, 'avg_cost': 65.0},
                'XPEV': {'position': 100, 'market_price': 30.0, 'avg_cost': 28.0},
            },
            'cash_balance': 50000.0,
            'available_cash': 50000.0,
            'broker_buying_power': 50000.0,
            'broker_buying_power_source': 'available_cash',
            'cash_available_for_csp': 50000.0,
            'cash_reserved_for_csp': 0.0,
            'open_short_put_collateral': 0.0,
            'excess_liquidity': 50000.0,
            'short_calls': {},
            'short_puts': {},
            'vix_regime': {'regime': 'normal', 'vix': 18.0},
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
        return [{
            'strike': 60.0,
            'expiration': '20240315',
            'option_type': 'PUT',
            'bid': 1.50,
            'ask': 1.60,
            'last': 1.55,
            'dte': 21,
            'implied_volatility': 0.35,
            'open_interest': 300,
            'volume': 200,
            'mid_price': 1.55,
            'premium_per_contract': 155.0,
            'annualized_return': 35.0,
            'score': 85.0,
            'ticker': ticker,
            'delta': 0.18,
            'iv_rank': 0.6,
            'otm_pct': 14.29,
            'breakeven': 58.45,
            'breakeven_buffer_pct': 0.0258,
            'cash_required': 6000.0,
            'rationale': ['Good premium'],
            'warnings': [],
            'score_details': {},
        }]

    def test_watchlist_dedup_drops_duplicate_underlying(self):
        """Watchlist with US.UBER and UBER should deduplicate to one entry."""
        self.mock_watchlist_manager.get_effective_watchlist.return_value = [
            'UBER', 'US.UBER',
        ]
        engine = self._import_engine()

        with patch.object(engine, '_fetch_watchlist_ticker_csp') as mock_fetch:
            mock_fetch.side_effect = lambda t, pc: self._mock_csp_return(t)

            result = engine.get_top_recommendations(limit=5)

        # _fetch_watchlist_ticker_csp should be called once after dedup
        self.assertEqual(mock_fetch.call_count, 1)
        called_ticker = mock_fetch.call_args[0][0]
        from core.ticker_utils import canonical_underlying
        self.assertEqual(canonical_underlying(called_ticker), 'UBER')

    def test_positions_dedup_by_canonical_underlying(self):
        """Positions with UBER and US.UBER should dedup to one covered call row."""
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            'positions': {
                'UBER': {'position': 200, 'market_price': 70.0, 'avg_cost': 65.0},
                'US.UBER': {'position': 200, 'market_price': 70.0, 'avg_cost': 65.0},
            },
            'cash_balance': 50000.0,
            'available_cash': 50000.0,
            'cash_available_for_csp': 50000.0,
            'cash_reserved_for_csp': 0.0,
            'excess_liquidity': 50000.0,
            'short_calls': {},
            'short_puts': {},
            'vix_regime': {'regime': 'normal', 'vix': 18.0},
        }
        engine = self._import_engine()

        # Need to avoid the CSP path since it's not relevant for this test
        self.mock_portfolio_context['cash_available_for_csp'] = 0

        with patch('api.services.recommendations.score_contract') as mock_score:
            mock_decision = MagicMock()
            mock_decision.contract_score = 85.0
            mock_decision.strike = 75
            mock_decision.expiration = '20240315'
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
            mock_decision.iv_status = 'normal'
            mock_decision.profile_type = 'monthly'
            mock_decision.size_fit = 1.0
            mock_decision.expected_move_buffer = 0.05
            mock_decision.breakeven = 73.0
            mock_decision.breakeven_buffer_pct = 0.05
            mock_decision.cash_required = 7500.0
            mock_decision.score_details = {}
            mock_decision.rationale = ['Good premium']
            mock_decision.warnings = []
            mock_decision.to_dict.return_value = {'score': 85.0,
                'covered_call_intent': 'income',
                'score_rationale': '',
                'stress_loss': 0,
                'risk_budget_used_pct': 0,
            }
            mock_score.return_value = mock_decision

            result = engine.get_top_recommendations(limit=5)

        # get_stock_price should be called once per canonical underlying
        uber_calls = [c for c in self.mock_conn.get_stock_price.call_args_list if c[0][0] in ('UBER', 'US.UBER')]
        self.assertEqual(len(uber_calls), 1)

    def test_select_top_deduplicates_by_canonical(self):
        """_select_top should not include multiple candidates for same underlying."""
        engine = self._import_engine()

        with patch.object(engine, '_fetch_watchlist_ticker_csp') as mock_fetch:
            mock_fetch.side_effect = lambda t, pc: self._mock_csp_return(t)
            self.mock_watchlist_manager.get_effective_watchlist.return_value = [
                'UBER', 'XPEV',
            ]

            result = engine.get_top_recommendations(limit=5)

        from core.ticker_utils import canonical_underlying
        seen = set()
        for rec in result.get('signals', []):
            cu = canonical_underlying(rec['ticker'])
            self.assertNotIn(cu, seen, f"Duplicate underlying {cu} found")
            seen.add(cu)


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

    def test_cash_fields_in_response(self):
        """Response should include broker_buying_power, cash_available_for_csp, cash_reserved_for_csp."""
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            'positions': {},
            'cash_balance': 50000.0,
            'available_cash': 50000.0,
            'broker_buying_power': 50000.0,
            'broker_buying_power_source': 'available_cash',
            'cash_available_for_csp': 50000.0,
            'cash_reserved_for_csp': 15000.0,
            'open_short_put_collateral': 15000.0,
            'excess_liquidity': 50000.0,
            'short_calls': {},
            'short_puts': {},
        }
        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

        engine = self._import_engine()
        result = engine.get_top_recommendations(limit=5)

        self.assertEqual(result.get('broker_buying_power'), 50000.0)
        self.assertEqual(result.get('cash_available_for_csp'), 50000.0)
        self.assertEqual(result.get('cash_reserved_for_csp'), 15000.0)



class TestRecommendationEngineSignalFields(unittest.TestCase):
    """Test signal-only fields in recommendations."""

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

    def test_signals_contains_signal_fields(self):
        """signals items should include signal-specific fields."""
        from core.wheel_decision import WheelDecision
        mock_decision = MagicMock(spec=WheelDecision)
        mock_decision.hard_blockers = []
        mock_decision.strike = 145.0
        mock_decision.expiration = '20260510'
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
        mock_decision.iv_status = 'normal'
        mock_decision.iv_env_adjustment = 0
        mock_decision.profile_type = 'standard'
        mock_decision.size_fit = 1.0
        mock_decision.expected_move_buffer = 0.05
        mock_decision.breakeven = 73.0
        mock_decision.breakeven_buffer_pct = 0.05
        mock_decision.cash_required = 7500.0
        mock_decision.score_details = {}
        mock_decision.rationale = ['Good premium']
        mock_decision.warnings = []
        mock_decision.to_dict.return_value = {
            'score': 85.0,
            'covered_call_intent': 'income',
            'score_rationale': 'Strong growth candidate',
            'stress_loss': 500,
            'risk_budget_used_pct': 0.15,
            'price_source': 'moomoo',
            'chain_source': 'moomoo',
            'confidence_score': 95,
            'hard_blockers': [],
        }

        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            'positions': {},
            'available_cash': 50000.0,
            'broker_buying_power': 50000.0,
            'broker_buying_power_source': 'available_cash',
            'cash_available_for_csp': 50000.0,
            'cash_reserved_for_csp': 0.0,
            'short_calls': {},
            'short_puts': {},
        }
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ['AAPL']

        with patch('api.services.recommendations.score_contract') as mock_score:
            mock_score.return_value = mock_decision
            engine = self._import_engine()
            result = engine.get_top_recommendations(limit=5)

        # Verify signals contains signal fields
        for rec in result.get('signals', []):
            self.assertIn('signal_type', rec)
            self.assertIn('strategy', rec)
            self.assertEqual(rec['strategy'], 'wheel')
            self.assertIn('broker_feasible', rec)
            self.assertIn('capital_required', rec)
            self.assertIn('risk_budget_used', rec)
            self.assertIn('data_source', rec)
            self.assertIn('confidence', rec)
            self.assertIn('blocked_reason_codes', rec)
            self.assertIn('research_only', rec)
            # CSP signals should have signal_type 'csp'
            if rec.get('option_type') == 'PUT':
                self.assertEqual(rec['signal_type'], 'csp')

    def test_signals_has_no_execution_cta_fields(self):
        """signals items should NOT contain execution-oriented CTA fields."""
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            'positions': {},
            'available_cash': 50000.0,
            'broker_buying_power': 50000.0,
            'broker_buying_power_source': 'available_cash',
            'cash_available_for_csp': 50000.0,
            'cash_reserved_for_csp': 0.0,
            'short_calls': {},
            'short_puts': {},
        }
        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

        engine = self._import_engine()
        result = engine.get_top_recommendations(limit=5)

        for rec in result.get('signals', []):
            self.assertNotIn('execution_blocked', rec,
                             msg="execution_blocked should not appear in signal-only recommendations")
            self.assertNotIn('execution_blocked_reason', rec,
                             msg="execution_blocked_reason should not appear in signal-only recommendations")

    def test_blocked_reason_counts_in_response(self):
        """Response should include blocked_reason_counts dict."""
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            'positions': {},
            'available_cash': 50000.0,
            'broker_buying_power': 50000.0,
            'broker_buying_power_source': 'available_cash',
            'cash_available_for_csp': 50000.0,
            'cash_reserved_for_csp': 0.0,
            'short_calls': {},
            'short_puts': {},
        }
        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

        engine = self._import_engine()
        result = engine.get_top_recommendations(limit=5)

        self.assertIn('blocked_reason_counts', result)

    def test_covered_calls_are_actionable_signals(self):
        """Covered call signals should have research_only=False."""
        from core.wheel_decision import WheelDecision
        mock_decision = MagicMock(spec=WheelDecision)
        mock_decision.hard_blockers = []
        mock_decision.strike = 155.0
        mock_decision.expiration = '20260510'
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
        mock_decision.iv_status = 'normal'
        mock_decision.iv_env_adjustment = 0
        mock_decision.profile_type = 'standard'
        mock_decision.size_fit = 1.0
        mock_decision.expected_move_buffer = 0.05
        mock_decision.breakeven = 73.0
        mock_decision.breakeven_buffer_pct = 0.05
        mock_decision.cash_required = 0
        mock_decision.score_details = {}
        mock_decision.rationale = ['Good call premium']
        mock_decision.warnings = []
        mock_decision.to_dict.return_value = {
            'score': 85.0,
            'covered_call_intent': 'income',
            'score_rationale': 'Strong growth candidate',
            'stress_loss': 300,
            'risk_budget_used_pct': 0.10,
            'price_source': 'moomoo',
            'chain_source': 'moomoo',
            'confidence_score': 95,
            'hard_blockers': [],
        }

        self.mock_portfolio_context_provider.get_portfolio_context.return_value = {
            'positions': {'AAPL': {'position': 300, 'market_price': 148.0, 'avg_cost': 140.0}},
            'available_cash': 50000.0,
            'broker_buying_power': 50000.0,
            'broker_buying_power_source': 'available_cash',
            'cash_available_for_csp': 50000.0,
            'cash_reserved_for_csp': 0.0,
            'short_calls': {},
            'short_puts': {},
        }
        self.mock_watchlist_manager.get_effective_watchlist.return_value = []

        # Mock the options data provider to return CALL data
        self.mock_options_data._process_ticker_for_otm.return_value = {
            'calls': [{
                'strike': 155.0, 'expiration': '20260510', 'dte': 14,
                'bid': 1.15, 'ask': 1.25, 'last': 1.20,
                'mid_price': 1.20, 'premium_per_contract': 120.0,
                'annualized_return': 18.0, 'otm_pct': 5.0, 'delta': 0.30,
                'implied_volatility': 0.35, 'open_interest': 2000, 'volume': 1000,
                'score': 85.0, 'contract_score': 85.0,
                'wheel_decision': mock_decision.to_dict(),
                'cash_required': 0, 'breakeven': 155.0, 'breakeven_buffer_pct': 0.05,
            }],
            'puts': [],
        }
        self.mock_conn.get_stock_price.return_value = 148.0

        with patch('api.services.recommendations.score_contract') as mock_score:
            mock_score.return_value = mock_decision
            engine = self._import_engine()
            result = engine.get_top_recommendations(limit=5)

        for rec in result.get('signals', []):
            if rec.get('option_type') == 'CALL':
                self.assertEqual(rec['signal_type'], 'covered_call')
                self.assertFalse(rec['research_only'],
                                 msg="Covered calls should have research_only=False")


class TestRecommendationNonDuplication(unittest.TestCase):
    """Test that the unified signal list does not duplicate underlyings."""

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
            'broker_buying_power': 50000.0,
            'broker_buying_power_source': 'available_cash',
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
            'puts': [],
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
        d.expiration = '20240315'
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
        d.iv_status = 'normal'
        d.iv_env_adjustment = 0
        d.profile_type = 'monthly'
        d.size_fit = 1.0
        d.expected_move_buffer = 0.05
        d.breakeven = 158.0
        d.breakeven_buffer_pct = 0.05
        d.cash_required = 16000.0
        d.score_details = {}
        d.rationale = ['Good premium']
        d.warnings = []
        d.quote_quality = 'tradable'
        d.blocked_reason_codes = []
        d.to_dict.return_value = {'score': 85.0, 'quote_quality': 'tradable', 'blocked_reason_codes': []}
        return d

    def test_signals_present_then_legacy_fields_absent(self):
        """The unified signal payload should not expose legacy top-level fields."""
        engine = self._import_engine()
        mock_decision = self._make_mock_decision()

        with patch('api.services.recommendations.score_contract') as mock_score:
            mock_score.return_value = mock_decision
            result = engine.get_top_recommendations(limit=5)

        self.assertGreater(len(result.get('signals', [])), 0)
        self.assertNotIn('best_plays', result)
        self.assertNotIn('recommendations', result)
        self.assertNotIn('lanes', result)

    def test_no_duplicate_tickers_across_signals(self):
        """Tickers in signals should not duplicate underlyings."""
        engine = self._import_engine()
        from core.ticker_utils import canonical_underlying

        # Set up watchlist with AAPL + additional tickers
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ['MSFT']
        self.mock_portfolio_context['positions']['MSFT'] = {'position': 100, 'market_price': 300.0, 'avg_cost': 290.0}

        # Set up OTM data for both tickers
        def process_side_effect(conn, ticker, otm_percentage, portfolio_context, expiration=None, option_type=None):
            if ticker == 'MSFT':
                return {
                    'calls': [], 'puts': [
                        {'strike': 280, 'expiration': '20240315', 'bid': 3.0, 'ask': 3.20,
                         'last': 3.10, 'delta': 0.18, 'implied_volatility': 0.30,
                         'open_interest': 500, 'volume': 100, 'dte': 21},
                    ]
                }
            return {'calls': [{'strike': 160, 'expiration': '20240315', 'bid': 2.0, 'ask': 2.10,
                               'last': 2.05, 'delta': 0.20, 'implied_volatility': 0.30,
                               'open_interest': 500, 'volume': 100, 'dte': 21}], 'puts': []}

        self.mock_options_data._process_ticker_for_otm.side_effect = process_side_effect

        mock_decision = self._make_mock_decision()

        with patch('api.services.recommendations.score_contract') as mock_score:
            mock_score.return_value = mock_decision
            with patch.object(engine, '_fetch_watchlist_ticker_csp') as mock_fetch:
                mock_fetch.return_value = [{
                    'strike': 280, 'expiration': '20240315', 'option_type': 'PUT',
                    'bid': 3.0, 'ask': 3.20, 'last': 3.10, 'dte': 21,
                    'implied_volatility': 0.30, 'open_interest': 500, 'volume': 100,
                    'mid_price': 3.10, 'premium_per_contract': 310.0,
                    'annualized_return': 30.0, 'score': 80.0, 'ticker': 'MSFT',
                    'delta': 0.18, 'iv_rank': 0.6, 'otm_pct': 6.67,
                    'breakeven': 276.9, 'breakeven_buffer_pct': 0.03,
                    'cash_required': 28000.0, 'rationale': ['Good premium'],
                    'warnings': [], 'score_details': {},
                    'wheel_decision': {'score': 80.0, 'quote_quality': 'tradable', 'blocked_reason_codes': []},
                }]
                result = engine.get_top_recommendations(limit=5)

        # signals should have items and stay deduplicated by underlying
        self.assertGreater(len(result.get('signals', [])), 0)
        seen = set()
        for rec in result.get('signals', []):
            cu = canonical_underlying(rec['ticker'])
            self.assertNotIn(cu, seen, f"Duplicate underlying {cu} found")
            seen.add(cu)


class TestRecommendationBlockedCandidatesDiagnostics(unittest.TestCase):
    """Test that blocked signals surface with readable diagnostics."""

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
            'positions': {},
            'cash_balance': 50000.0,
            'available_cash': 50000.0,
            'broker_buying_power': 50000.0,
            'broker_buying_power_source': 'available_cash',
            'cash_available_for_csp': 50000.0,
            'cash_reserved_for_csp': 0.0,
            'excess_liquidity': 50000.0,
            'short_calls': {},
            'short_puts': {},
        }
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = self.mock_portfolio_context
        self.mock_watchlist_manager.get_effective_watchlist.return_value = ['ASKONLY']

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

        with patch.object(engine, '_fetch_watchlist_ticker_csp') as mock_fetch:
            mock_fetch.return_value = [engine._make_skip_diagnostic(
                'ASKONLY', 'no_bid', 'No executable bid - ask-only quote'
            )]
            result = engine.get_top_recommendations(limit=5)

        self.assertIn('blocked_signals', result)
        blocked = result['blocked_signals']
        self.assertGreater(len(blocked), 0)
        askonly_blocked = [b for b in blocked if b.get('ticker') == 'ASKONLY']
        self.assertEqual(len(askonly_blocked), 1)
        self.assertEqual(askonly_blocked[0]['reason_code'], 'no_bid')
        self.assertIn('No executable bid', askonly_blocked[0]['reason_text'])
        self.assertIn('blocked_reason_counts', result)
        self.assertIn('no_bid', result['blocked_reason_counts'])

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

        from core.wheel_decision import WheelDecision, _create_failed_decision

        with patch.object(engine, '_fetch_watchlist_csp_moomoo') as mock_fetch:
            # Return a candidate that score_contract would normally block for no_bid
            mock_fetch.side_effect = lambda t, pc: None  # Trigger yfinance fallback
            with patch.object(engine, '_fetch_yfinance_csp_candidates') as mock_yf:
                mock_yf.return_value = [engine._make_skip_diagnostic(
                    'BLOCKED', 'no_bid', 'No executable bid - ask-only quote'
                )]
                result = engine.get_top_recommendations(limit=5)

        self.assertIn('blocked_signals', result)
        if result.get('blocked_signals'):
            has_quote_quality = any(
                b.get('reason_code') in ('no_bid', 'no_ask', 'no_market', 'wide_spread', 'zero_mark', 'low_liquidity')
                for b in result['blocked_signals']
            )
            self.assertTrue(has_quote_quality,
                            msg="Blocked candidates should include quote-quality reason codes")


if __name__ == '__main__':
    unittest.main()
