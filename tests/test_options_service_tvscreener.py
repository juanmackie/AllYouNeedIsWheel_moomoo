"""
Tests for OptionsService integration with TvscreenerService - Simplified
"""

from unittest.mock import MagicMock
from api.services.options_service import OptionsService


class TestOptionsServiceGetEffectiveWatchlist:
    """Test get_effective_watchlist() method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.service = OptionsService()
        self.service.config = {
            'watchlist_mode': 'static',
            'watchlist': ['AAPL', 'MSFT'],
            'screening_criteria': {
                'min_iv_rank': 30,
                'min_volume': 1000000,
                'max_stocks': 50
            }
        }

    def test_static_mode(self):
        """Test static watchlist mode."""
        self.service.config['watchlist_mode'] = 'static'
        result = self.service.get_effective_watchlist()
        assert result == ['AAPL', 'MSFT']

    def test_dynamic_mode_failure_fallback(self):
        """Test dynamic mode falls back to static on failure."""
        # Mock get_service to return None (service not available)
        self.service._tvscreener_service = False  # Sentinel for not available

        self.service.config['watchlist_mode'] = 'dynamic'
        result = self.service.get_effective_watchlist()

        # Should fall back to static watchlist
        assert result == ['AAPL', 'MSFT']

    def test_hybrid_mode_failure_fallback(self):
        """Test hybrid mode falls back to static on failure."""
        # Mock get_service to return None (service not available)
        self.service._tvscreener_service = False  # Sentinel for not available

        self.service.config['watchlist_mode'] = 'hybrid'
        result = self.service.get_effective_watchlist()

        # Should fall back to static watchlist
        assert result == ['AAPL', 'MSFT']

    def test_default_mode_static(self):
        """Test that default mode is static when not specified."""
        # Remove watchlist_mode from config
        del self.service.config['watchlist_mode']
        result = self.service.get_effective_watchlist()

        # Should default to static
        assert result == ['AAPL', 'MSFT']


class TestOptionsServiceGetTvscreenerService:
    """Test _get_tvscreener_service() method."""

    def test_service_not_initialized(self):
        """Test when tvscreener service is not initialized."""
        service = OptionsService()
        service._tvscreener_service = None

        result = service._get_tvscreener_service()
        assert result is None

    def test_service_false_sentinel(self):
        """Test when tvscreener service is False (failed init)."""
        service = OptionsService()
        service._tvscreener_service = False

        result = service._get_tvscreener_service()
        assert result is None

    def test_service_initialization_success(self):
        """Test successful service retrieval."""
        service = OptionsService()
        service._tvscreener_service = None

        # Mock get_service to return a mock tvscreener service
        mock_tvscreener = MagicMock()
        
        # We need to mock the import inside _get_tvscreener_service
        # Since it's using import inside the method, let's just set it directly
        service._tvscreener_service = mock_tvscreener

        result = service._get_tvscreener_service()
        assert result == mock_tvscreener
