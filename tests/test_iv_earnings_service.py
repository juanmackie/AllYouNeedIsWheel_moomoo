"""
Tests for api/services/iv_earnings_service.py — IV tracking and earnings.
"""

import unittest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime, timedelta


def _reset_av_globals():
    import api.services.alpha_vantage_provider as av
    av._cache = None
    av._cache_timestamp = None
    av._last_request_time = 0


class TestIVEarningsService(unittest.TestCase):
    """Core IV tracking and earnings service tests."""

    def setUp(self):
        from api.services.iv_earnings_service import IVEarningsService
        self.service = IVEarningsService(database=MagicMock())
        _reset_av_globals()

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



class TestAlphaVantageProvider(unittest.TestCase):
    """Alpha Vantage CSV parsing, caching, and availability."""

    def setUp(self):
        from api.services.alpha_vantage_provider import AlphaVantageEarningsProvider
        self.provider = AlphaVantageEarningsProvider(api_key='test_key')
        _reset_av_globals()

    def test_unavailable_without_key(self):
        provider = __import__('api.services.alpha_vantage_provider', fromlist=['AlphaVantageEarningsProvider']).AlphaVantageEarningsProvider(api_key='')
        self.assertFalse(provider.available)

    def test_unavailable_with_none_key(self):
        provider = __import__('api.services.alpha_vantage_provider', fromlist=['AlphaVantageEarningsProvider']).AlphaVantageEarningsProvider(api_key=None)
        self.assertFalse(provider.available)

    def test_parse_csv_recognises_timeOfTheDay(self):
        csv_text = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
            "AAPL,Apple Inc,2026-05-15,2026-04-30,2.35,USD,post-market\n"
            "MSFT,Microsoft Corp,2026-05-20,2026-05-10,3.10,USD,pre-market\n"
        )
        result = self.provider._parse_csv(csv_text)
        self.assertEqual(result['AAPL']['timeOfDay'], 'post-market')
        self.assertEqual(result['AAPL']['reportDate'], '2026-05-15')
        self.assertEqual(result['AAPL']['fiscalDateEnding'], '2026-04-30')
        self.assertEqual(result['AAPL']['estimate'], 2.35)
        self.assertEqual(result['AAPL']['currency'], 'USD')
        self.assertEqual(result['MSFT']['timeOfDay'], 'pre-market')

    def test_parse_csv_fallback_timeOfDay(self):
        csv_text = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfDay\n"
            "AAPL,Apple Inc,2026-05-15,2026-04-30,,USD,after-market\n"
        )
        result = self.provider._parse_csv(csv_text)
        self.assertEqual(result['AAPL']['timeOfDay'], 'post-market')
        self.assertIsNone(result['AAPL']['estimate'])

    def test_parse_csv_blank_timing(self):
        csv_text = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
            "AAPL,Apple Inc,2026-05-15,2026-04-30,,USD,\n"
        )
        result = self.provider._parse_csv(csv_text)
        self.assertEqual(result['AAPL']['timeOfDay'], '')

    def test_parse_csv_malformed_skips_bad_rows(self):
        csv_text = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
            "AAPL,Apple Inc,2026-05-15,2026-04-30,2.35,USD,post-market\n"
            ",Bad Inc,,2026-04-30,,USD,\n"
            "NVDA,NVIDIA Corp,2026-06-01,2026-05-20,4.50,USD,pre-market\n"
        )
        result = self.provider._parse_csv(csv_text)
        self.assertIn('AAPL', result)
        self.assertIn('NVDA', result)
        self.assertNotIn('', result)

    def test_get_earnings_returns_none_without_key(self):
        provider = __import__('api.services.alpha_vantage_provider', fromlist=['AlphaVantageEarningsProvider']).AlphaVantageEarningsProvider(api_key='')
        self.assertIsNone(provider.get_earnings('AAPL'))

    def test_cache_reused_on_second_call(self):
        csv_text = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
            "AAPL,Apple Inc,2026-05-15,2026-04-30,2.35,USD,post-market\n"
        )
        with patch.object(self.provider, '_fetch_csv', return_value=csv_text) as mock_fetch:
            r1 = self.provider.get_earnings('AAPL')
            r2 = self.provider.get_earnings('AAPL')
            self.assertEqual(r1, r2)
            mock_fetch.assert_called_once()

    def test_provider_priority_alpha_vantage_success_skips_fallbacks(self):
        """Alpha Vantage success should skip OpenBB/yfinance."""
        from api.services.iv_earnings_service import IVEarningsService
        from api.services.alpha_vantage_provider import AlphaVantageEarningsProvider
        svc = IVEarningsService(database=MagicMock())
        csv_text = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
            "AAPL,Apple Inc,2026-05-15,2026-04-30,2.35,USD,post-market\n"
        )
        with (
            patch.object(svc._alpha_vantage, '_fetch_csv', return_value=csv_text),
            patch.object(AlphaVantageEarningsProvider, 'available', new_callable=PropertyMock(return_value=True)),
        ):
            result = svc.fetch_earnings_date('AAPL')
            self.assertTrue(result['success'])
            self.assertEqual(result['earnings_date'], '2026-05-15')
            self.assertEqual(result['time_of_day'], 'post-market')
            self.assertEqual(result['earnings_source'], 'Alpha Vantage')
            self.assertEqual(result['estimate'], 2.35)
            self.assertEqual(result['currency'], 'USD')

    def test_get_earnings_info_includes_richer_fields(self):
        """get_earnings_info returns time_of_day and earnings_source when available."""
        from api.services.iv_earnings_service import IVEarningsService
        mock_db = MagicMock()
        svc = IVEarningsService(database=mock_db)
        mock_db.get_earnings_date.return_value = {
            'ticker': 'AAPL',
            'earnings_date': '2026-05-15',
            'time_of_day': 'post-market',
            'fiscal_date_ending': '2026-04-30',
            'estimate': 2.35,
            'currency': 'USD',
            'earnings_source': 'Alpha Vantage',
            'fetch_status': 'success',
            'error_message': None,
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        info = svc.get_earnings_info('AAPL')
        self.assertEqual(info['time_of_day'], 'post-market')
        self.assertEqual(info['earnings_source'], 'Alpha Vantage')
        self.assertEqual(info['estimate'], 2.35)
        self.assertEqual(info['currency'], 'USD')
        self.assertEqual(info['fiscal_date_ending'], '2026-04-30')

    def test_get_earnings_info_returns_none_when_missing(self):
        """get_earnings_info returns None for newer fields when no data."""
        from api.services.iv_earnings_service import IVEarningsService
        mock_db = MagicMock()
        svc = IVEarningsService(database=mock_db)
        mock_db.get_earnings_date.return_value = None
        info = svc.get_earnings_info('UNKNOWN')
        self.assertIsNone(info['time_of_day'])
        self.assertIsNone(info['earnings_source'])
        self.assertIsNone(info['estimate'])
        self.assertIsNone(info['currency'])


if __name__ == '__main__':
    unittest.main()
