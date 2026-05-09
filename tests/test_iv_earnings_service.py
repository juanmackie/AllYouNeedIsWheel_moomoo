"""
Tests for api/services/iv_earnings_service.py — IV tracking and earnings.
"""

import unittest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime, timedelta


class TestIVEarningsService(unittest.TestCase):
    """Core IV tracking and earnings service tests."""

    def setUp(self):
        from api.services.iv_earnings_service import IVEarningsService
        self.service = IVEarningsService(database=MagicMock())

    def test_cache_invalid_when_empty(self):
        self.assertFalse(self.service._is_cache_valid(None, 4))

    def test_cache_valid_when_recent(self):
        entry = {'timestamp': datetime.now()}
        self.assertTrue(self.service._is_cache_valid(entry, 4))

    def test_cache_invalid_when_expired(self):
        entry = {'timestamp': datetime.now() - timedelta(hours=5)}
        self.assertFalse(self.service._is_cache_valid(entry, 4))

    def test_iv_rank_returns_neutral_without_db(self):
        self.service.db = None
        rank = self.service._calculate_iv_rank('AAPL', 0.30)
        self.assertEqual(rank, 0.5)

    def test_iv_rank_with_historical_data(self):
        mock_db = MagicMock()
        mock_db.get_iv_history.return_value = [
            {'implied_volatility': 0.20},
            {'implied_volatility': 0.25},
            {'implied_volatility': 0.30},
            {'implied_volatility': 0.35},
            {'implied_volatility': 0.40},
        ]
        self.service.db = mock_db
        rank = self.service._calculate_iv_rank('AAPL', 0.30)
        self.assertAlmostEqual(rank, 0.5, places=2)

    def test_get_iv_environment_score_extreme_low(self):
        self.service.db.get_iv_history.return_value = [
            {'implied_volatility': 0.20 + i * 0.05} for i in range(10)
        ]
        score, rank, status = self.service.get_iv_environment_score('AAPL', 0.1)
        self.assertEqual(status, 'extreme_low')
        self.assertEqual(score, -20)

    def test_get_iv_environment_score_extreme_high(self):
        self.service._iv_cache['TEST'] = {
            'iv': 0.80, 'iv_rank': 0.85, 'timestamp': datetime.now()
        }
        score, rank, status = self.service.get_iv_environment_score('TEST', 0.80)
        self.assertEqual(status, 'extreme_high')
        self.assertEqual(score, 20)

    def test_record_iv_data_calls_db(self):
        mock_db = MagicMock()
        self.service.db = mock_db
        self.service.record_iv_data('AAPL', 0.30, stock_price=150.0)
        mock_db.save_iv_data.assert_called_once()

    def test_fetch_earnings_date_handles_no_earnings(self):
        with patch('yfinance.Ticker') as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.earnings_dates = None
            mock_ticker_cls.return_value = mock_ticker
            result = self.service.fetch_earnings_date('AAPL')
            self.assertIn('success', result)

    def test_strip_moomoo_prefix_delegates(self):
        with patch('api.services.iv_earnings_service.clean_yfinance_ticker') as mock_clean:
            mock_clean.return_value = 'AAPL'
            result = self.service._strip_moomoo_prefix('US.AAPL')
            self.assertEqual(result, 'AAPL')
            mock_clean.assert_called_once_with('US.AAPL')


if __name__ == '__main__':
    unittest.main()
