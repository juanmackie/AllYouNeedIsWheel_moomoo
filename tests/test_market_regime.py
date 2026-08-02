"""
Tests for api/services/vix_regime_service.py - VixRegimeService class
"""

import unittest
from unittest.mock import MagicMock, Mock, patch


class TestVixRegimeServiceInit(unittest.TestCase):
    """Test VixRegimeService initialization"""

    def test_init(self):
        from api.services.vix_regime_service import VixRegimeService

        config_provider = Mock()
        config_provider.config = {"some_key": "some_value"}
        service = VixRegimeService(config_provider)
        self.assertEqual(service.config, {"some_key": "some_value"})


class TestVixRegimeServiceGetVixRegime(unittest.TestCase):
    """Test get_vix_regime method"""

    def setUp(self):
        from api.services.vix_regime_service import VixRegimeService

        config_provider = Mock()
        config_provider.config = {}
        self.service = VixRegimeService(config_provider)

    def test_complacency_regime(self):
        with patch("yfinance.Ticker") as mock_ticker:
            mock_hist = MagicMock()
            mock_hist.empty = False
            mock_hist.__getitem__("Close").iloc.__getitem__.return_value = 12.5
            mock_ticker.return_value.history.return_value = mock_hist

            result = self.service.get_vix_regime()

            self.assertEqual(result["regime"], "complacency")
            self.assertEqual(result["vix"], 12.5)
            self.assertEqual(result["delta_adjustment"], 0.10)
            self.assertEqual(result["exposure_multiplier"], 0.7)

    def test_normal_regime(self):
        with patch("yfinance.Ticker") as mock_ticker:
            mock_hist = MagicMock()
            mock_hist.empty = False
            mock_hist.__getitem__("Close").iloc.__getitem__.return_value = 20.0
            mock_ticker.return_value.history.return_value = mock_hist

            result = self.service.get_vix_regime()

            self.assertEqual(result["regime"], "normal")
            self.assertEqual(result["delta_adjustment"], 0.0)
            self.assertEqual(result["exposure_multiplier"], 1.0)

    def test_fear_regime(self):
        with patch("yfinance.Ticker") as mock_ticker:
            mock_hist = MagicMock()
            mock_hist.empty = False
            mock_hist.__getitem__("Close").iloc.__getitem__.return_value = 35.0
            mock_ticker.return_value.history.return_value = mock_hist

            result = self.service.get_vix_regime()

            self.assertEqual(result["regime"], "fear")
            self.assertEqual(result["delta_adjustment"], -0.05)
            self.assertEqual(result["exposure_multiplier"], 0.5)

    def test_fallback_to_default_when_yfinance_fails(self):
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.side_effect = Exception("API failure")

            result = self.service.get_vix_regime()

            self.assertEqual(result["regime"], "normal")
            self.assertEqual(result["vix"], 20.0)

    def test_cache_hit_returns_cached(self):
        from datetime import datetime

        cached_entry = {
            "data": {
                "vix": 25.0,
                "regime": "normal",
                "delta_adjustment": 0.0,
                "exposure_multiplier": 1.0,
                "description": "test",
            },
            "timestamp": datetime.now(),
        }
        setattr(self.service, "_vix_regime_cache", cached_entry)

        with patch("yfinance.Ticker") as mock_ticker:
            result = self.service.get_vix_regime()
            self.assertEqual(result["vix"], 25.0)
            mock_ticker.assert_not_called()

    def test_cache_expired_fetches_fresh(self):
        from datetime import datetime, timedelta

        old_entry = {
            "data": {
                "vix": 25.0,
                "regime": "normal",
                "delta_adjustment": 0.0,
                "exposure_multiplier": 1.0,
                "description": "test",
            },
            "timestamp": datetime.now() - timedelta(minutes=10),
        }
        setattr(self.service, "_vix_regime_cache", old_entry)

        with patch("yfinance.Ticker") as mock_ticker:
            mock_hist = MagicMock()
            mock_hist.empty = False
            mock_hist.__getitem__("Close").iloc.__getitem__.return_value = 30.0
            mock_ticker.return_value.history.return_value = mock_hist

            result = self.service.get_vix_regime()
            self.assertEqual(result["vix"], 30.0)

    def test_fetch_failure_uses_stale_cache_before_default(self):
        from datetime import datetime, timedelta

        old_entry = {
            "data": {
                "vix": 31.0,
                "regime": "fear",
                "delta_adjustment": -0.05,
                "exposure_multiplier": 0.5,
                "description": "stale fear",
            },
            "timestamp": datetime.now() - timedelta(minutes=10),
        }
        setattr(self.service, "_vix_regime_cache", old_entry)

        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.side_effect = Exception("API failure")

            result = self.service.get_vix_regime()
            self.assertEqual(result["vix"], 31.0)
            self.assertEqual(result["regime"], "fear")


if __name__ == "__main__":
    unittest.main()
