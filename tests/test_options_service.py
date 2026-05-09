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
                'min_iv_rank': 30,
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
                'min_iv_rank': 30,
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


class TestLazyServiceInitialization(unittest.TestCase):
    """Test lazy initialization of optional services."""

    def test_openbb_service_initially_none(self):
        """_openbb_service should be None initially."""
        service = OptionsService()
        self.assertIsNone(service._openbb_service)

    def test_tvscreener_service_initially_none(self):
        """_tvscreener_service should be None initially."""
        service = OptionsService()
        self.assertIsNone(service._tvscreener_service)

    @patch('api.get_service')
    def test_tvscreener_service_lazy_init(self, mock_get_service):
        """_get_tvscreener_service should cache the result."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        service = OptionsService()
        result1 = service._get_tvscreener_service()
        result2 = service._get_tvscreener_service()

        self.assertIs(result1, mock_service)
        self.assertIs(result2, mock_service)
        mock_get_service.assert_called_once()  # Only called once due to caching


if __name__ == '__main__':
    unittest.main()
