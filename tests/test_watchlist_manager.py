"""
Tests for api/services/watchlist_manager.py — WatchlistManager class
"""

import unittest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.watchlist_manager import WatchlistManager


class TestWatchlistManagerInit(unittest.TestCase):
    """Test WatchlistManager initialization."""

    def test_init_stores_context(self):
        mock_context = MagicMock()
        mock_context.config = {'watchlist': ['AAPL']}
        manager = WatchlistManager(mock_context)
        self.assertIs(manager._config_provider, mock_context)


class TestWatchlistManagerGetEffectiveWatchlist(unittest.TestCase):
    """Test get_effective_watchlist behavior."""

    def setUp(self):
        self.mock_context = MagicMock()
        self.mock_context.config = {}

    def test_static_mode(self):
        self.mock_context.config = {
            'watchlist': ['AAPL', 'TSLA', 'NVDA'],
            'watchlist_mode': 'static',
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ['AAPL', 'TSLA', 'NVDA'])

    def test_static_mode_is_default(self):
        self.mock_context.config = {
            'watchlist': ['AAPL'],
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ['AAPL'])

    @patch.object(WatchlistManager, '_get_tvscreener_service')
    def test_dynamic_mode(self, mock_get_tvscreener):
        mock_tvscreener = MagicMock()
        mock_tvscreener.get_wheel_candidates.return_value = ['GME', 'AMC']
        mock_get_tvscreener.return_value = mock_tvscreener
        self.mock_context.config = {
            'watchlist': ['AAPL'],
            'watchlist_mode': 'dynamic',
            'screening_criteria': {
                'min_iv_rank': 30,
                'min_volume': 1000000,
                'max_stocks': 50,
            },
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ['GME', 'AMC'])
        mock_tvscreener.get_wheel_candidates.assert_called_once_with(
            min_iv_rank=30, min_volume=1000000, limit=50
        )

    @patch.object(WatchlistManager, '_get_tvscreener_service')
    def test_hybrid_mode(self, mock_get_tvscreener):
        mock_tvscreener = MagicMock()
        mock_tvscreener.get_wheel_candidates.return_value = ['AMC', 'BB']
        mock_get_tvscreener.return_value = mock_tvscreener
        self.mock_context.config = {
            'watchlist': ['AAPL', 'TSLA'],
            'watchlist_mode': 'hybrid',
            'screening_criteria': {
                'min_iv_rank': 20,
                'min_volume': 500000,
                'max_stocks': 30,
            },
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(len(result), 4)
        self.assertIn('AAPL', result)
        self.assertIn('TSLA', result)
        self.assertIn('AMC', result)
        self.assertIn('BB', result)

    @patch.object(WatchlistManager, '_get_tvscreener_service')
    def test_dynamic_failure_falls_back_to_static(self, mock_get_tvscreener):
        mock_tvscreener = MagicMock()
        mock_tvscreener.get_wheel_candidates.side_effect = Exception('Rate limited')
        mock_get_tvscreener.return_value = mock_tvscreener
        self.mock_context.config = {
            'watchlist': ['AAPL'],
            'watchlist_mode': 'dynamic',
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ['AAPL'])

    @patch.object(WatchlistManager, '_get_tvscreener_service')
    def test_dynamic_no_service_falls_back(self, mock_get_tvscreener):
        mock_get_tvscreener.return_value = None
        self.mock_context.config = {
            'watchlist': ['AAPL'],
            'watchlist_mode': 'dynamic',
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ['AAPL'])


class TestWatchlistManagerScreeningProfile(unittest.TestCase):
    """Test get_screening_profile method."""

    def setUp(self):
        self.mock_context = MagicMock()
        self.manager = WatchlistManager(self.mock_context)

    def test_profile_default_monthly(self):
        profile = self.manager.get_screening_profile('CALL')
        self.assertEqual(profile['profile_type'], 'monthly')
        self.assertIn('target_delta', profile)

    def test_profile_weekly(self):
        profile = self.manager.get_screening_profile('PUT', dte=7)
        self.assertEqual(profile['profile_type'], 'weekly')

    def test_profile_quarterly(self):
        profile = self.manager.get_screening_profile('CALL', dte=60)
        self.assertEqual(profile['profile_type'], 'quarterly')

    def test_profile_explicit_type(self):
        profile = self.manager.get_screening_profile('PUT', profile_type='quarterly')
        self.assertEqual(profile['profile_type'], 'quarterly')

    def test_profile_call_vs_put(self):
        call_profile = self.manager.get_screening_profile('CALL')
        put_profile = self.manager.get_screening_profile('PUT')
        self.assertNotEqual(call_profile['target_delta'], put_profile['target_delta'])

    def test_profile_vix_adjustment(self):
        vix_regime = {
            'regime': 'fear',
            'delta_adjustment': -0.05,
            'exposure_multiplier': 0.5,
        }
        profile = self.manager.get_screening_profile('PUT', vix_regime=vix_regime)
        self.assertEqual(profile['vix_regime'], 'fear')
        self.assertLess(profile['target_delta'], 0.22)

    def test_profile_vix_complacency(self):
        vix_regime = {
            'regime': 'complacency',
            'delta_adjustment': 0.05,
            'exposure_multiplier': 1.5,
        }
        profile = self.manager.get_screening_profile('PUT', vix_regime=vix_regime)
        self.assertEqual(profile['vix_regime'], 'complacency')
        self.assertGreater(profile['target_delta'], 0.22)


if __name__ == '__main__':
    unittest.main()
