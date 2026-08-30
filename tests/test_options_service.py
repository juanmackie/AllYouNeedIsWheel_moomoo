"""
Tests for api/services/options_service.py — pure functions and watchlist logic
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.options_service import OptionsService


class TestStripTickerPrefix(unittest.TestCase):
    """Test clean_yfinance_ticker from shared utils."""

    def test_removes_us_prefix(self):
        from api.services.utils import clean_yfinance_ticker

        result = clean_yfinance_ticker("US.AAPL")
        self.assertEqual(result, "AAPL")

    def test_preserves_ticker_without_prefix(self):
        from api.services.utils import clean_yfinance_ticker

        result = clean_yfinance_ticker("AAPL")
        self.assertEqual(result, "AAPL")

    def test_handles_none(self):
        from api.services.utils import clean_yfinance_ticker

        result = clean_yfinance_ticker(None)
        self.assertEqual(result, "")


class TestYFinanceTicker(unittest.TestCase):
    """Test the yfinance ticker helper (session management removed — yfinance handles its own)."""

    def test_get_yfinance_ticker_returns_ticker(self):
        from unittest.mock import patch

        from api.services.utils import get_yfinance_ticker

        with patch("yfinance.Ticker") as mock_ticker:
            result = get_yfinance_ticker("AAPL")
        self.assertIs(result, mock_ticker.return_value)
        self.assertEqual(mock_ticker.call_args.args[0], "AAPL")
        self.assertIn("session", mock_ticker.call_args.kwargs)


class TestGetEffectiveWatchlist(unittest.TestCase):
    """Test watchlist mode logic."""

    @patch("api.services.watchlist_manager.WatchlistManager._fetch_moomoo_watchlist", return_value=[])
    @patch("api.services.config.get_config")
    def test_static_mode_returns_static(self, mock_get_config, _mock_fetch_moomoo_watchlist):
        """Static mode should return the static watchlist."""
        mock_config = MagicMock()
        # Pin an isolated temp-file DB so the repo's local options.db (which
        # may hold app-managed symbols from real runs) never leaks into this
        # test. ":memory:" is unusable here because the pool opens one file
        # path per pooled connection.
        temp_db = os.path.join(tempfile.mkdtemp(), "test_watchlist.db")
        mock_config.get.side_effect = lambda key, default=None: {
            "watchlist": ["AAPL", "TSLA", "NVDA"],
            "watchlist_mode": "static",
            "db_path": temp_db,
        }.get(key, default)
        mock_get_config.return_value = mock_config

        service = OptionsService()
        result = service.get_effective_watchlist()

        self.assertEqual(result, ["AAPL", "NVDA", "TSLA"])

    @patch("api.services.config.get_config")
    @patch("db.database.OptionsDatabase")
    @patch("api.services.portfolio_service.PortfolioService")
    def test_portfolio_context_provider_contract_is_present(
        self, mock_portfolio_service, mock_options_db, mock_get_config
    ):
        """OptionsService must expose the provider attribute used by PortfolioContext."""
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: {
            "db_path": ":memory:",
        }.get(key, default)
        mock_get_config.return_value = mock_config

        service = OptionsService()
        portfolio_service = service.portfolio_context_helper._get_portfolio_service()

        self.assertIs(portfolio_service, mock_portfolio_service.return_value)
        mock_portfolio_service.assert_called_once_with()
        mock_options_db.assert_called_once_with(":memory:")


class TestOptionsServiceConnectionConfig(unittest.TestCase):
    def test_ensure_connection_propagates_portfolio_env(self):
        with (
            patch("api.services.config.get_config") as mock_get_config,
            patch("core.connection.MoomooConnection") as mock_moomoo,
            patch("db.database.OptionsDatabase") as mock_options_db,
        ):
            mock_config = MagicMock()
            mock_config.get.side_effect = lambda key, default=None: {
                "host": "127.0.0.1",
                "port": 11111,
                "readonly": True,
                "portfolio_env": "REAL",
                "security_firm": "FUTUAU",
                "db_path": ":memory:",
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
                host="127.0.0.1",
                port=11111,
                readonly=True,
                account_id=None,
                portfolio_env="REAL",
                security_firm="FUTUAU",
                broker_cache_after_hours=True,
                chain_rate_limit_max_requests=10,
                chain_rate_limit_window_sec=30,
                chain_min_request_spacing_sec=3.0,
            )


if __name__ == "__main__":
    unittest.main()
