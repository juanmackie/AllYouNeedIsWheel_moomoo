"""
Tests for api/services/options_service.py — pure functions and watchlist logic
"""

import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.options_service import OptionsService


class TestStripTickerPrefix(unittest.TestCase):
    """Test clean_yfinance_ticker from shared utils."""

    def test_removes_us_prefix(self):
        from api.services.utils import clean_yfinance_ticker
        result = clean_yfinance_ticker('US.AAPL')
        self.assertEqual(result, 'AAPL')

    def test_preserves_ticker_without_prefix(self):
        from api.services.utils import clean_yfinance_ticker
        result = clean_yfinance_ticker('AAPL')
        self.assertEqual(result, 'AAPL')

    def test_handles_none(self):
        from api.services.utils import clean_yfinance_ticker
        result = clean_yfinance_ticker(None)
        self.assertEqual(result, '')


class TestYFinanceTicker(unittest.TestCase):
    """Test the yfinance ticker helper (session management removed — yfinance handles its own)."""

    def test_get_yfinance_ticker_returns_ticker(self):
        from api.services.utils import get_yfinance_ticker
        from unittest.mock import patch

        with patch('yfinance.Ticker') as mock_ticker:
            result = get_yfinance_ticker('AAPL')
        self.assertIs(result, mock_ticker.return_value)
        self.assertEqual(mock_ticker.call_args.args[0], 'AAPL')
        self.assertIn('session', mock_ticker.call_args.kwargs)


class TestGetEffectiveWatchlist(unittest.TestCase):
    """Test watchlist mode logic."""

    @patch('api.get_service')
    @patch('api.services.config.get_config')
    def test_static_mode_returns_static(self, mock_get_config, mock_get_service):
        """Static mode should return the static watchlist."""
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: {
            'watchlist': ['AAPL', 'TSLA', 'NVDA'],
            'watchlist_mode': 'static',
        }.get(key, default)
        mock_get_config.return_value = mock_config
        mock_get_service.return_value = None  # No tvscreener service

        service = OptionsService()
        result = service.get_effective_watchlist()

        self.assertEqual(result, ['AAPL', 'TSLA', 'NVDA'])

    @patch('api.get_service')
    @patch('api.services.config.get_config')
    def test_dynamic_mode_returns_dynamic(self, mock_get_config, mock_get_service):
        """Dynamic mode should return tvscreener results."""
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: {
            'watchlist': ['AAPL'],
            'watchlist_mode': 'dynamic',
            'screening_criteria': {
                'min_volatility_pct': 3.0,
                'min_volume': 1000000,
                'max_stocks': 50,
            },
        }.get(key, default)
        mock_tvscreener = MagicMock()
        mock_tvscreener.get_wheel_candidates.return_value = ['GME', 'AMC', 'BB']
        mock_get_service.return_value = mock_tvscreener
        mock_get_config.return_value = mock_config

        service = OptionsService()
        result = service.get_effective_watchlist()

        self.assertEqual(result, ['GME', 'AMC', 'BB'])

    @patch('api.get_service')
    @patch('api.services.config.get_config')
    def test_hybrid_mode_combines(self, mock_get_config, mock_get_service):
        """Hybrid mode should combine static and dynamic."""
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: {
            'watchlist': ['AAPL', 'TSLA'],
            'watchlist_mode': 'hybrid',
            'screening_criteria': {
                'min_volatility_pct': 3.0,
                'min_volume': 1000000,
                'max_stocks': 50,
            },
        }.get(key, default)
        mock_tvscreener = MagicMock()
        mock_tvscreener.get_wheel_candidates.return_value = ['GME', 'AMC']
        mock_get_service.return_value = mock_tvscreener
        mock_get_config.return_value = mock_config

        service = OptionsService()
        result = service.get_effective_watchlist()

        # Should have AAPL, TSLA, GME, AMC (no duplicates)
        self.assertEqual(len(result), 4)
        self.assertIn('AAPL', result)
        self.assertIn('TSLA', result)
        self.assertIn('GME', result)
        self.assertIn('AMC', result)

    @patch('api.get_service')
    @patch('api.services.config.get_config')
    def test_dynamic_failure_falls_back(self, mock_get_config, mock_get_service):
        """Dynamic mode should fall back to static on failure."""
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: {
            'watchlist': ['AAPL', 'TSLA'],
            'watchlist_mode': 'dynamic',
        }.get(key, default)
        mock_tvscreener = MagicMock()
        mock_tvscreener.get_wheel_candidates.side_effect = Exception('API down')
        mock_get_service.return_value = mock_tvscreener
        mock_get_config.return_value = mock_config

        service = OptionsService()
        result = service.get_effective_watchlist()

        # Should fall back to static
        self.assertEqual(result, ['AAPL', 'TSLA'])


class TestOptionsServiceConnectionConfig(unittest.TestCase):
    def test_ensure_connection_propagates_portfolio_env(self):
        with patch('api.services.config.get_config') as mock_get_config, \
             patch('core.connection.MoomooConnection') as mock_moomoo, \
             patch('db.database.OptionsDatabase') as mock_options_db:
            mock_config = MagicMock()
            mock_config.get.side_effect = lambda key, default=None: {
                'host': '127.0.0.1',
                'port': 11111,
                'readonly': True,
                'portfolio_env': 'REAL',
                'security_firm': 'FUTUAU',
                'db_path': ':memory:',
            }.get(key, default)
            mock_get_config.return_value = mock_config

            fresh = MagicMock()
            fresh.is_connected.return_value = True
            fresh.connect.return_value = True
            mock_moomoo.return_value = fresh
            mock_options_db.return_value = MagicMock()

            service = OptionsService()
            result = service._ensure_connection()

            self.assertIs(result, fresh)
            mock_moomoo.assert_called_once_with(
                host='127.0.0.1',
                port=11111,
                readonly=True,
                account_id=None,
                portfolio_env='REAL',
                security_firm='FUTUAU',
                broker_cache_after_hours=True,
            )


if __name__ == '__main__':
    unittest.main()
