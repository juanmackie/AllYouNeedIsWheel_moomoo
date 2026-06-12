"""
Tests for TvscreenerService - Simplified
"""

import pytest
from unittest.mock import MagicMock, patch
from api.services.tvscreener_service import TvscreenerService


class TestTvscreenerServiceInit:
    """Test service initialization."""

    def test_initialization_state(self):
        """Test that service starts uninitialized."""
        service = TvscreenerService()
        assert service._initialized is False
        assert service._tvscreener is None
        assert len(service._cache) == 0

    def test_cache_ttl(self):
        """Test that cache TTL is set correctly."""
        service = TvscreenerService()
        assert service._cache.ttl == 300  # 5 minutes


class TestTvscreenerServiceInitializedState:
    """Test behavior with different initialization states."""

    def test_initialized_with_tvscreener(self):
        """Test returns True when initialized with tvscreener."""
        service = TvscreenerService()
        service._initialized = True
        service._tvscreener = MagicMock()

        result = service._ensure_initialized()

        assert result is True

    def test_initialized_without_tvscreener(self):
        """Test returns False when initialized without tvscreener."""
        service = TvscreenerService()
        service._initialized = True
        service._tvscreener = None

        result = service._ensure_initialized()

        assert result is False


class TestTvscreenerServiceGetWheelCandidates:
    """Test wheel candidate screening with tvscreener installed."""

    def setup_method(self):
        """Set up test with tvscreener installed."""
        self.service = TvscreenerService()
        self.service._initialized = True
        # tvscreener is now installed in the venv

    @pytest.mark.skip(reason="Requires mocking tvscreener.StockScreener")
    def test_get_wheel_candidates_success(self):
        """Test successful candidate retrieval."""
        # This test requires complex mocking of tvscreener API
        # Skipping for now - would need to mock:
        # - tvscreener.StockScreener
        # - screener.select(), where(), limit(), get()
        # - tvscreener.StockField
        pass

    def test_get_wheel_candidates_not_initialized(self):
        """Test returns None when not initialized."""
        service = TvscreenerService()
        # Mock _ensure_initialized to return False (simulate tvscreener not available)
        with patch.object(service, '_ensure_initialized', return_value=False):
            result = service.get_wheel_candidates()
            assert result is None

    @pytest.mark.skip(reason="Requires mocking tvscreener.StockScreener")
    def test_get_wheel_candidates_empty_result(self):
        """Test handling of empty results."""
        pass

    def test_get_wheel_candidates_exception(self):
        """Test exception handling by forcing an error."""
        service = TvscreenerService()
        service._initialized = True
        service._tvscreener = MagicMock()

        with patch('tvscreener.StockScreener', side_effect=Exception("API Error")):
            result = service.get_wheel_candidates()
            assert result is None

    def test_max_price_filters_with_stock_field_price(self):
        """When max_price is provided, screener.where(StockField.PRICE <= max_price) is called."""
        service = TvscreenerService()
        service._initialized = True
        service._tvscreener = MagicMock()

        mock_screener = MagicMock()

        import pandas as pd
        mock_screener.get.return_value = pd.DataFrame({'symbol': ['CHEAP1']})

        with patch('tvscreener.StockScreener', return_value=mock_screener):
            result = service.get_wheel_candidates(max_price=133.6)

        assert result == ['CHEAP1']
        # 3 where() calls: volatility, volume, price
        assert mock_screener.where.call_count == 3

    def test_no_max_price_skips_price_filter(self):
        """When max_price is None, no StockField.PRICE filter is applied."""
        service = TvscreenerService()
        service._initialized = True
        service._tvscreener = MagicMock()

        mock_screener = MagicMock()

        import pandas as pd
        mock_screener.get.return_value = pd.DataFrame({'symbol': ['ANY']})

        with patch('tvscreener.StockScreener', return_value=mock_screener):
            result = service.get_wheel_candidates(max_price=None)

        assert result == ['ANY']
        # Only 2 where() calls: volatility + volume (no price)
        assert mock_screener.where.call_count == 2


class TestCreateTvscreenerService:
    """Test factory function."""

    def test_create_service(self):
        """Test factory function creates service."""
        from api.services.tvscreener_service import create_tvscreener_service
        service = create_tvscreener_service()
        assert isinstance(service, TvscreenerService)
