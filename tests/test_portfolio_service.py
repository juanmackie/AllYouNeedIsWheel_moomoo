"""
Tests for api/services/portfolio_service.py - PortfolioService class
"""

import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta

from api.services.config import get_config as _get_config


class TestPortfolioServiceInit(unittest.TestCase):
    """Test PortfolioService initialization"""

    def setUp(self):
        self._orig_config = _get_config()
        self._orig_config.config = {'db_path': ':memory:'}

    def test_init_defaults(self):
        from api.services.portfolio_service import PortfolioService
        svc = PortfolioService()
        self.assertIsNotNone(svc.config)
        self.assertIsNone(svc.connection)
        self.assertIsNone(svc.last_error)
        self.assertIsNone(svc._portfolio_cache)
        self.assertIsNone(svc._portfolio_cache_time)
        self.assertEqual(svc._portfolio_cache_ttl, 30)

    def test_init_with_connection(self):
        from api.services.portfolio_service import PortfolioService
        mock_conn = Mock()
        svc = PortfolioService(connection=mock_conn)
        self.assertIs(svc.connection, mock_conn)


class TestPortfolioServiceEnsureConnection(unittest.TestCase):
    """Test connection management"""

    def setUp(self):
        c = _get_config()
        c.config = {
            'host': '127.0.0.1', 'port': 11111,
            'readonly': True, 'db_path': ':memory:'
        }
        from api.services.portfolio_service import PortfolioService
        self.svc = PortfolioService()

    def test_uses_shared_connection_when_connected(self):
        mock_conn = Mock()
        mock_conn.is_connected.return_value = True
        self.svc.connection = mock_conn

        result = self.svc._ensure_connection()
        self.assertIs(result, mock_conn)
        mock_conn.connect.assert_not_called()

    def test_reconnects_disconnected_shared(self):
        mock_conn = Mock()
        mock_conn.is_connected.return_value = False
        mock_conn.connect.return_value = True
        self.svc.connection = mock_conn

        result = self.svc._ensure_connection()
        self.assertIs(result, mock_conn)
        mock_conn.connect.assert_called_once()

    @patch('core.connection.MoomooConnection')
    def test_creates_new_when_reconnect_fails(self, mock_moomoo_class):
        shared = Mock()
        shared.is_connected.return_value = False
        shared.connect.return_value = False
        self.svc.connection = shared

        fresh = Mock()
        fresh.is_connected.return_value = True
        fresh.connect.return_value = True
        mock_moomoo_class.return_value = fresh

        result = self.svc._ensure_connection()
        self.assertIs(result, fresh)
        mock_moomoo_class.assert_called_once()

    @patch('core.connection.MoomooConnection')
    def test_creates_new_when_no_shared(self, mock_moomoo_class):
        self.svc.connection = None
        fresh = Mock()
        fresh.connect.return_value = True
        mock_moomoo_class.return_value = fresh

        result = self.svc._ensure_connection()
        self.assertIsNotNone(result)
        mock_moomoo_class.assert_called_once()
        fresh.connect.assert_called_once()

    @patch('core.connection.MoomooConnection')
    def test_new_connection_failure_sets_error(self, mock_moomoo_class):
        self.svc.connection = None
        fresh = Mock()
        fresh.connect.return_value = False
        fresh.last_error = 'Connection refused'
        mock_moomoo_class.return_value = fresh

        result = self.svc._ensure_connection()
        self.assertIs(result, fresh)
        self.assertEqual(self.svc.last_error, 'Connection refused')


class TestPortfolioServiceCaching(unittest.TestCase):
    """Test in-memory portfolio caching"""

    def setUp(self):
        c = _get_config()
        c.config = {'db_path': ':memory:'}
        from api.services.portfolio_service import PortfolioService
        self.svc = PortfolioService()

    def test_cache_hit_returns_cached(self):
        self.svc._portfolio_cache = {'account_value': 10000}
        self.svc._portfolio_cache_time = datetime.now()
        result = self.svc._get_cached_portfolio()
        self.assertEqual(result, {'account_value': 10000})

    def test_cache_expiry_fetches_fresh(self):
        self.svc._portfolio_cache = {'account_value': 10000}
        self.svc._portfolio_cache_time = datetime.now() - timedelta(seconds=60)
        self.svc._fetch_portfolio = Mock(return_value={'account_value': 20000})

        result = self.svc._get_cached_portfolio()
        self.assertEqual(result, {'account_value': 20000})
        self.svc._fetch_portfolio.assert_called_once()

    def test_peek_cached_portfolio_returns_without_refresh(self):
        self.svc._portfolio_cache = {'account_value': 10000}
        self.svc._portfolio_cache_time = datetime.now() - timedelta(seconds=60)
        self.svc._fetch_portfolio = Mock(side_effect=AssertionError('should not refresh'))

        result = self.svc.peek_cached_portfolio()
        self.assertEqual(result, {'account_value': 10000})
        self.svc._fetch_portfolio.assert_not_called()

    def test_invalidate_cache_clears(self):
        self.svc._portfolio_cache = {'account_value': 10000}
        self.svc._portfolio_cache_time = datetime.now()
        self.svc.invalidate_cache()
        self.assertIsNone(self.svc._portfolio_cache)
        self.assertIsNone(self.svc._portfolio_cache_time)


class TestPortfolioServicePositions(unittest.TestCase):
    """Test position retrieval"""

    def setUp(self):
        c = _get_config()
        c.config = {'db_path': ':memory:'}
        from api.services.portfolio_service import PortfolioService
        self.svc = PortfolioService()
        self.svc._fetch_portfolio = Mock()

    def test_get_portfolio_summary(self):
        self.svc._fetch_portfolio.return_value = {
            'account_value': 50000,
            'available_cash': 10000,
            'positions': {'AAPL': {'shares': 10}}
        }
        result = self.svc.get_portfolio_summary()
        self.assertEqual(result['account_value'], 50000)
        self.assertNotIn('positions', result)

    def test_get_portfolio_summary_failure(self):
        self.svc._fetch_portfolio.return_value = None
        result = self.svc.get_portfolio_summary()
        self.assertIn('error', result)

    def test_get_positions_stk(self):
        self.svc._fetch_portfolio.return_value = {
            'positions': {
                'US.AAPL': {'shares': 10, 'security_type': 'STK', 'market_price': 150, 'avg_cost': 140, 'market_value': 1500, 'unrealized_pnl': 100},
                'US.TSLA240315C00200000': {'shares': -1, 'security_type': 'OPT', 'market_price': 2.5, 'avg_cost': 2.0, 'market_value': 250, 'unrealized_pnl': 50, 'expiration': '20240315', 'strike': 200, 'option_type': 'CALL'},
            }
        }
        positions = self.svc.get_positions('STK')
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]['symbol'], 'US.AAPL')

    def test_get_positions_opt(self):
        self.svc._fetch_portfolio.return_value = {
            'positions': {
                'US.AAPL': {'shares': 10, 'security_type': 'STK', 'market_price': 150, 'avg_cost': 140, 'market_value': 1500, 'unrealized_pnl': 100},
                'US.TSLA240315C00200000': {'shares': -1, 'security_type': 'OPT', 'market_price': 2.5, 'avg_cost': 2.0, 'market_value': 250, 'unrealized_pnl': 50, 'expiration': '20240315', 'strike': 200, 'option_type': 'CALL'},
            }
        }
        positions = self.svc.get_positions('OPT')
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]['expiration'], '20240315')

    def test_get_weekly_option_income(self):
        today = datetime.now()
        days_until_friday = (4 - today.weekday()) % 7
        this_friday = today + timedelta(days=days_until_friday)
        expiry_str = this_friday.strftime('%Y%m%d')

        mock_portfolio = {
            'positions': {
                'US.AAPL' + expiry_str + 'P00150000': {
                    'shares': -2, 'security_type': 'OPT', 'option_type': 'PUT',
                    'expiration': expiry_str, 'strike': 150,
                    'avg_cost': 3.0, 'market_price': 0.5,
                }
            }
        }
        self.svc._fetch_portfolio.return_value = mock_portfolio
        result = self.svc.get_weekly_option_income()
        self.assertEqual(result['positions_count'], 1)
        self.assertEqual(result['total_income'], 600.0)
        self.assertEqual(result['open_short_positions_count'], 1)
        self.assertEqual(result['open_short_contracts_count'], 2)
        self.assertEqual(result['open_short_total_income'], 600.0)

    def test_get_weekly_option_income_no_match(self):
        old_expiry = '20990101'
        mock_portfolio = {
            'positions': {
                'US.AAPL' + old_expiry + 'P00150000': {
                    'shares': -2, 'security_type': 'OPT', 'option_type': 'PUT',
                    'expiration': old_expiry, 'strike': 150,
                    'avg_cost': 3.0, 'market_price': 0.5,
                }
            }
        }
        self.svc._fetch_portfolio.return_value = mock_portfolio
        result = self.svc.get_weekly_option_income()
        self.assertEqual(result['positions_count'], 0)
        self.assertEqual(result['total_income'], 0)
        self.assertEqual(result['open_short_positions_count'], 1)
        self.assertEqual(result['open_short_contracts_count'], 2)
        self.assertEqual(result['open_short_total_income'], 600.0)

    def test_get_weekly_option_income_mixed_open_shorts(self):
        today = datetime.now()
        days_until_friday = (4 - today.weekday()) % 7
        this_friday = today + timedelta(days=days_until_friday)
        next_friday = this_friday + timedelta(days=7)
        weekly_expiry = this_friday.strftime('%Y%m%d')
        later_expiry = next_friday.strftime('%Y%m%d')

        self.svc.get_positions = Mock(return_value=[
            {
                'symbol': 'US.AAPL' + weekly_expiry + 'P00150000',
                'position': -2,
                'security_type': 'OPT',
                'option_type': 'PUT',
                'expiration': weekly_expiry,
                'strike': 150,
                'avg_cost': 3.0,
                'market_price': 0.5,
            },
            {
                'symbol': 'US.AAPL' + later_expiry + 'C00160000',
                'position': -1,
                'security_type': 'OPT',
                'option_type': 'CALL',
                'expiration': later_expiry,
                'strike': 160,
                'avg_cost': 2.5,
                'market_price': 0.4,
            },
            {
                'symbol': 'US.AAPL' + later_expiry + 'P00140000',
                'position': 1,
                'security_type': 'OPT',
                'option_type': 'PUT',
                'expiration': later_expiry,
                'strike': 140,
                'avg_cost': 1.0,
                'market_price': 1.2,
            },
        ])

        result = self.svc.get_weekly_option_income()
        self.assertEqual(result['positions_count'], 1)
        self.assertEqual(result['total_income'], 600.0)
        self.assertEqual(result['open_short_positions_count'], 2)
        self.assertEqual(result['open_short_contracts_count'], 3)
        self.assertEqual(result['open_short_total_income'], 850.0)

    def test_get_weekly_option_income_long_only_returns_zeroes(self):
        self.svc.get_positions = Mock(return_value=[
            {
                'symbol': 'US.AAPL',
                'position': 10,
                'security_type': 'OPT',
                'option_type': 'CALL',
                'expiration': '20990101',
                'strike': 200,
                'avg_cost': 4.0,
                'market_price': 5.0,
            }
        ])

        result = self.svc.get_weekly_option_income()
        self.assertEqual(result['positions_count'], 0)
        self.assertEqual(result['total_income'], 0)
        self.assertEqual(result['open_short_positions_count'], 0)
        self.assertEqual(result['open_short_contracts_count'], 0)
        self.assertEqual(result['open_short_total_income'], 0)


if __name__ == '__main__':
    unittest.main()
