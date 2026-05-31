"""
Tests for api/services/market_regime.py - MarketRegime class
"""

import unittest
from unittest.mock import Mock, patch, MagicMock


class TestMarketRegimeInit(unittest.TestCase):
    """Test MarketRegime initialization"""

    def test_init(self):
        from api.services.market_regime import MarketRegime
        config_provider = Mock()
        config_provider.config = {'some_key': 'some_value'}
        regime = MarketRegime(config_provider)
        self.assertEqual(regime.config, {'some_key': 'some_value'})

    def test_init_with_openbb_provider(self):
        from api.services.market_regime import MarketRegime
        config = Mock()
        config.config = {}
        openbb_provider = Mock()
        regime = MarketRegime(config, openbb_service_provider=openbb_provider)
        self.assertIsNotNone(regime._openbb_service_provider)


class TestMarketRegimeGetVixRegime(unittest.TestCase):
    """Test get_vix_regime method"""

    def setUp(self):
        from api.services.market_regime import MarketRegime
        config_provider = Mock()
        config_provider.config = {}
        self.regime = MarketRegime(config_provider)
        self.regime._vix_cache = None

    def test_complacency_regime(self):
        with patch.object(self.regime, '_get_openbb_service', return_value=None):
            with patch('yfinance.Ticker') as mock_ticker:
                mock_hist = MagicMock()
                mock_hist.empty = False
                mock_hist.__getitem__('Close').iloc.__getitem__.return_value = 12.5
                mock_ticker.return_value.history.return_value = mock_hist

                result = self.regime.get_vix_regime()

                self.assertEqual(result['regime'], 'complacency')
                self.assertEqual(result['vix'], 12.5)
                self.assertEqual(result['delta_adjustment'], 0.10)
                self.assertEqual(result['exposure_multiplier'], 0.7)

    def test_normal_regime(self):
        with patch.object(self.regime, '_get_openbb_service', return_value=None):
            with patch('yfinance.Ticker') as mock_ticker:
                mock_hist = MagicMock()
                mock_hist.empty = False
                mock_hist.__getitem__('Close').iloc.__getitem__.return_value = 20.0
                mock_ticker.return_value.history.return_value = mock_hist

                result = self.regime.get_vix_regime()

                self.assertEqual(result['regime'], 'normal')
                self.assertEqual(result['delta_adjustment'], 0.0)
                self.assertEqual(result['exposure_multiplier'], 1.0)

    def test_fear_regime(self):
        with patch.object(self.regime, '_get_openbb_service', return_value=None):
            with patch('yfinance.Ticker') as mock_ticker:
                mock_hist = MagicMock()
                mock_hist.empty = False
                mock_hist.__getitem__('Close').iloc.__getitem__.return_value = 35.0
                mock_ticker.return_value.history.return_value = mock_hist

                result = self.regime.get_vix_regime()

                self.assertEqual(result['regime'], 'fear')
                self.assertEqual(result['delta_adjustment'], -0.05)
                self.assertEqual(result['exposure_multiplier'], 0.5)

    def test_fallback_to_default_when_all_sources_fail(self):
        with patch.object(self.regime, '_get_openbb_service', return_value=None):
            with patch('yfinance.Ticker') as mock_ticker:
                mock_ticker.return_value.history.side_effect = Exception("API failure")

                result = self.regime.get_vix_regime()

                self.assertEqual(result['regime'], 'normal')
                self.assertEqual(result['vix'], 20.0)

    def test_openbb_disabled_skips_openbb_path(self):
        self.regime.config['openbb_enabled'] = False
        with patch.object(self.regime, '_get_openbb_service') as mock_get_openbb:
            with patch('yfinance.Ticker') as mock_ticker:
                mock_hist = MagicMock()
                mock_hist.empty = False
                mock_hist.__getitem__('Close').iloc.__getitem__.return_value = 21.0
                mock_ticker.return_value.history.return_value = mock_hist

                result = self.regime.get_vix_regime()

                self.assertEqual(result['regime'], 'normal')
                mock_get_openbb.assert_not_called()

    def test_cache_hit_returns_cached(self):
        from datetime import datetime
        cached_entry = {
            'data': {'vix': 25.0, 'regime': 'normal', 'delta_adjustment': 0.0, 'exposure_multiplier': 1.0, 'description': 'test'},
            'timestamp': datetime.now()
        }
        setattr(self.regime, '_vix_regime_cache', cached_entry)
        self.regime._get_openbb_service = Mock()

        result = self.regime.get_vix_regime()
        self.assertEqual(result['vix'], 25.0)
        self.regime._get_openbb_service.assert_not_called()

    def test_cache_expired_fetches_fresh(self):
        from datetime import datetime, timedelta
        old_entry = {
            'data': {'vix': 25.0, 'regime': 'normal', 'delta_adjustment': 0.0, 'exposure_multiplier': 1.0, 'description': 'test'},
            'timestamp': datetime.now() - timedelta(minutes=10)
        }
        setattr(self.regime, '_vix_regime_cache', old_entry)

        with patch.object(self.regime, '_get_openbb_service', return_value=None):
            with patch('yfinance.Ticker') as mock_ticker:
                mock_hist = MagicMock()
                mock_hist.empty = False
                mock_hist.__getitem__('Close').iloc.__getitem__.return_value = 30.0
                mock_ticker.return_value.history.return_value = mock_hist

                result = self.regime.get_vix_regime()
                self.assertEqual(result['vix'], 30.0)


if __name__ == '__main__':
    unittest.main()
