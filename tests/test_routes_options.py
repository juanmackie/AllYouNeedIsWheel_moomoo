"""
Tests for api/routes/options.py — Flask route endpoints.

All endpoints are tested via Flask test client with mocked services,
database, and OpenD connection to avoid requiring live infrastructure.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask

from api.routes.options import bp
from api.routes.utils import _RATE_LIMIT_BUCKETS, enforce_route_rate_limit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(**overrides):
    """Create a minimal Flask app with the options blueprint registered."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config.update(
        {
            "connection_config": {
                "host": "127.0.0.1",
                "port": 11111,
            },
            **overrides,
        }
    )
    app.register_blueprint(bp)
    return app


def _reset_service_global():
    """Reset the module-level service singleton between tests."""
    import api.routes.options as mod

    mod._options_service_instance = None


def _patch_service(mock_options_service=None):
    """Build a standard OptionsService mock with sensible defaults."""
    if mock_options_service is None:
        mock_options_service = MagicMock()
    return mock_options_service


# ---------------------------------------------------------------------------
# Connection-status
# ---------------------------------------------------------------------------


class TestConnectionStatus(unittest.TestCase):
    """GET /api/options/connection-status"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service.connection = None

    @patch("api.routes.options.get_options_service")
    @patch("core.connection.MoomooConnection")
    def test_returns_pool_stats_and_service_info(self, mock_moomoo_cls, mock_get_svc):
        """Should return connection pool stats and service connection info."""
        mock_get_svc.return_value = self.mock_service
        mock_moomoo_cls.get_connection_pool_stats.return_value = {"pool_size": 1, "active_connections": 0}

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/connection-status")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["connection_pool"]["pool_size"], 1)
        self.assertFalse(data["service_initialized"])

    @patch("api.routes.options.get_options_service")
    @patch("core.connection.MoomooConnection")
    def test_includes_conn_info_when_initialized(self, mock_moomoo_cls, mock_get_svc):
        """Should include connection info when service connection exists."""
        mock_conn = MagicMock()
        mock_conn.get_connection_info.return_value = {"host": "127.0.0.1", "port": 11111, "connected": True}
        self.mock_service.connection = mock_conn
        mock_get_svc.return_value = self.mock_service
        mock_moomoo_cls.get_connection_pool_stats.return_value = {}

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/connection-status")
            data = resp.get_json()

        self.assertTrue(data["success"])
        self.assertTrue(data["service_initialized"])
        self.assertEqual(data["service_connection"]["host"], "127.0.0.1")

    @patch("api.routes.options.get_options_service")
    @patch("core.connection.MoomooConnection")
    def test_handles_exception_gracefully(self, mock_moomoo_cls, mock_get_svc):
        """Should return error response on exception."""
        mock_get_svc.side_effect = RuntimeError("boom")

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/connection-status")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 500)
        self.assertFalse(data["success"])


# ---------------------------------------------------------------------------
# OTM options
# ---------------------------------------------------------------------------


class TestOtmOptions(unittest.TestCase):
    """GET /api/options/otm"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service.get_otm_options.return_value = {"status": "success", "options": []}

    def _make_request(self, client, **params):
        return client.get("/api/options/otm", query_string=params)

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_returns_options_successfully(self, mock_probe, mock_get_svc):
        """Should return OTM options for valid parameters."""
        mock_probe.return_value = {"status": "connected"}
        self.mock_service.get_otm_options.return_value = {
            "data": {
                "AAPL": {
                    "calls": [{"price_source": "yfinance", "chain_source": "yfinance"}],
                    "puts": [],
                }
            }
        }
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = self._make_request(client, tickers="AAPL", otm="10")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["source_policy"]["mode"], "research_with_fallbacks")
        self.assertIn("yfinance", data["source_policy"]["external_fallback_sources_used"])
        self.mock_service.get_otm_options.assert_called_once_with(
            ticker="AAPL", otm_percentage=10.0, option_type=None, expiration=None
        )

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_passes_option_type_and_expiration(self, mock_probe, mock_get_svc):
        """Should forward optional option_type and expiration parameters."""
        mock_probe.return_value = {"status": "connected"}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = self._make_request(client, tickers="AAPL", otm="10", optionType="PUT", expiration="20240510")
            resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.mock_service.get_otm_options.assert_called_once_with(
            ticker="AAPL", otm_percentage=10.0, option_type="PUT", expiration="20240510"
        )

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_validates_invalid_option_type(self, mock_probe, mock_get_svc):
        """Should reject invalid option_type."""
        mock_probe.return_value = {"status": "connected"}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = self._make_request(client, tickers="AAPL", optionType="INVALID")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(data["success"])

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_returns_503_when_opend_unavailable(self, mock_probe, mock_get_svc):
        """Should return 503 when OpenD is unavailable."""
        mock_probe.return_value = {"status": "unavailable", "message": "OpenD is not responding."}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = self._make_request(client, tickers="AAPL")

        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertIn("error_code", data)
        self.assertEqual(data["error_code"], "opend_unavailable")
        self.assertIn("opend_status", data)
        self.assertEqual(data["opend_status"]["status"], "unavailable")
        self.mock_service.get_otm_options.assert_not_called()

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_rejects_put_otm_outside_growth_range(self, mock_probe, mock_get_svc):
        """Should reject PUT OTM requests outside the Growth Mode CSP range."""
        mock_probe.return_value = {"status": "connected"}
        mock_get_svc.return_value = self.mock_service

        app = _make_app(
            connection_config={
                "host": "127.0.0.1",
                "port": 11111,
                "growth_mode": {
                    "enabled": True,
                    "screener_profile": {
                        "csp_min_otm_pct": 5,
                        "csp_max_otm_pct": 15,
                    },
                },
            }
        )
        with app.test_client() as client:
            resp = self._make_request(client, tickers="AAPL", otm="4", optionType="PUT")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(data["success"])


class TestRouteRateLimitHelpers(unittest.TestCase):
    def setUp(self):
        _RATE_LIMIT_BUCKETS.clear()

    def test_helper_enforces_limit(self):
        allowed, retry_after = enforce_route_rate_limit(
            "top-recommendations",
            "127.0.0.1",
            max_requests=1,
            window_seconds=60,
        )
        self.assertTrue(allowed)
        self.assertEqual(retry_after, 0)

        allowed, retry_after = enforce_route_rate_limit(
            "top-recommendations",
            "127.0.0.1",
            max_requests=1,
            window_seconds=60,
        )
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 1)


# ---------------------------------------------------------------------------
# Stock price
# ---------------------------------------------------------------------------


class TestStockPrice(unittest.TestCase):
    """GET /api/options/stock-price"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service.get_stock_price.side_effect = lambda t: {"AAPL": 150.0, "TSLA": 200.0}.get(t, 0)

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_single_ticker(self, mock_probe, mock_get_svc):
        """Should return price for a single ticker."""
        mock_probe.return_value = {"status": "connected"}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/stock-price", query_string={"tickers": "AAPL"})
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["data"]["AAPL"], 150.0)

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_multiple_tickers(self, mock_probe, mock_get_svc):
        """Should return prices for comma-separated tickers."""
        mock_probe.return_value = {"status": "connected"}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/stock-price", query_string={"tickers": "AAPL,TSLA"})
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["data"]["AAPL"], 150.0)
        self.assertEqual(data["data"]["TSLA"], 200.0)

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_missing_tickers_returns_400(self, mock_probe, mock_get_svc):
        """Should return 400 when no tickers provided."""
        mock_probe.return_value = {"status": "connected"}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/stock-price", query_string={})
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(data["success"])

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_rejects_invalid_tickers(self, mock_probe, mock_get_svc):
        """Should reject ticker values with unsafe characters."""
        mock_probe.return_value = {"status": "connected"}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/stock-price", query_string={"tickers": "AAPL,MS/FT"})
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(data["success"])
        self.assertIn("Invalid ticker", data["error"])

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_opend_unavailable_503(self, mock_probe, mock_get_svc):
        """Should return 503 when OpenD is unavailable."""
        mock_probe.return_value = {"status": "unavailable", "message": "OpenD is not responding."}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/stock-price", query_string={"tickers": "AAPL"})

        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertIn("error_code", data)
        self.assertEqual(data["error_code"], "opend_unavailable")


# ---------------------------------------------------------------------------
# Expirations
# ---------------------------------------------------------------------------


class TestExpirations(unittest.TestCase):
    """GET /api/options/expirations"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service.get_option_expirations.return_value = {
            "ticker": "AAPL",
            "expirations": ["20240510", "20240610"],
        }

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_returns_expirations(self, mock_probe, mock_get_svc):
        """Should return option expirations for a ticker."""
        mock_probe.return_value = {"status": "connected"}
        self.mock_service.get_option_expirations.return_value = {
            "ticker": "AAPL",
            "expiration_source": "yfinance",
            "expirations": ["20240510", "20240610"],
        }
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/expirations", query_string={"ticker": "AAPL"})
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertIn("expirations", data)
        self.assertEqual(len(data["expirations"]), 2)
        self.assertIn("yfinance", data["source_policy"]["external_fallback_sources_used"])
        self.mock_service.get_option_expirations.assert_called_once_with("AAPL", None)

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_forwards_option_type(self, mock_probe, mock_get_svc):
        """Should forward option_type parameter."""
        mock_probe.return_value = {"status": "connected"}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            client.get("/api/options/expirations", query_string={"ticker": "AAPL", "option_type": "CALL"})

        self.mock_service.get_option_expirations.assert_called_once_with("AAPL", "CALL")

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_rejects_missing_ticker(self, mock_probe, mock_get_svc):
        """Should return 400 when ticker not provided."""
        mock_probe.return_value = {"status": "connected"}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/expirations")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", data)

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_rejects_invalid_ticker(self, mock_probe, mock_get_svc):
        """Should return 400 for unsafe ticker input."""
        mock_probe.return_value = {"status": "connected"}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/expirations", query_string={"ticker": "BRK.B/../etc"})
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(data["success"])

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_rejects_invalid_option_type(self, mock_probe, mock_get_svc):
        """Should return 400 for invalid option_type."""
        mock_probe.return_value = {"status": "connected"}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/expirations", query_string={"ticker": "AAPL", "option_type": "INVALID"})
            resp.get_json()

        self.assertEqual(resp.status_code, 400)

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_returns_404_on_service_error(self, mock_probe, mock_get_svc):
        """Should return 404 when service reports error."""
        mock_probe.return_value = {"status": "connected"}
        self.mock_service.get_option_expirations.return_value = {"error": "No options found"}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/expirations", query_string={"ticker": "INVALID"})
            data = resp.get_json()

        self.assertEqual(resp.status_code, 404)
        self.assertIn("error", data)

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_opend_unavailable_503(self, mock_probe, mock_get_svc):
        """Should return 503 when OpenD is unavailable."""
        mock_probe.return_value = {"status": "unavailable", "message": "OpenD down."}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/expirations", query_string={"ticker": "AAPL"})

        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertIn("error_code", data)
        self.assertEqual(data["error_code"], "opend_unavailable")


# ---------------------------------------------------------------------------
# Cash status
# ---------------------------------------------------------------------------


class TestCashStatus(unittest.TestCase):
    """GET /api/options/cash-status"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service.config = {"cash_reserve_enabled": True}

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    @patch("api.services.portfolio_service.PortfolioService")
    def test_returns_cash_status(self, mock_portfolio_cls, mock_probe, mock_get_svc):
        """Should return cash balance and reserve info."""
        mock_probe.return_value = {"status": "connected"}
        mock_get_svc.return_value = self.mock_service

        mock_portfolio = MagicMock()
        mock_portfolio.get_portfolio_summary.return_value = {"cash_balance": 50000}
        mock_portfolio.get_positions.return_value = []
        mock_portfolio_cls.return_value = mock_portfolio
        self.mock_service._get_portfolio_context.return_value = {
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "cash_available_for_csp": 50000.0,
            "broker_buying_power": 0.0,
            "broker_buying_power_source": "none",
            "excess_liquidity": 0.0,
        }

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/cash-status")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["cash_balance"], 50000.0)
        self.assertEqual(data["cash_reserved"], 0.0)
        self.assertEqual(data["cash_available"], 50000.0)

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    @patch("api.services.portfolio_service.PortfolioService")
    def test_calculates_cash_reserved_for_short_puts(self, mock_portfolio_cls, mock_probe, mock_get_svc):
        """Should calculate cash reserved for short put positions."""
        mock_probe.return_value = {"status": "connected"}
        mock_get_svc.return_value = self.mock_service

        mock_portfolio = MagicMock()
        mock_portfolio.get_portfolio_summary.return_value = {"cash_balance": 50000}
        mock_portfolio.get_positions.return_value = [
            {
                "symbol": "US.AAPL",
                "position": -2,
                "option_type": "PUT",
                "strike": 150.0,
                "expiration": "20240510",
            }
        ]
        mock_portfolio_cls.return_value = mock_portfolio
        self.mock_service._get_portfolio_context.return_value = {
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "cash_reserved_for_csp": 30000.0,
            "cash_available_for_csp": 20000.0,
            "broker_buying_power": 0.0,
            "broker_buying_power_source": "none",
            "excess_liquidity": 0.0,
        }

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/cash-status")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        # cash_reserved = 2 contracts * 150 strike * 100 = 30000
        self.assertEqual(data["cash_reserved"], 30000.0)
        # cash_available is true available cash; cash_available_for_csp is deployable (minus reserved).
        self.assertEqual(data["cash_available"], 50000.0)
        self.assertEqual(data["cash_available_for_csp"], 20000.0)
        self.assertEqual(len(data["open_puts"]), 1)

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    def test_opend_unavailable_503(self, mock_probe, mock_get_svc):
        """Should return 503 when OpenD is unavailable."""
        mock_probe.return_value = {"status": "unavailable", "message": "OpenD down."}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/cash-status")

        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertFalse(data["success"])
        self.assertIn("error_code", data)
        self.assertEqual(data["error_code"], "opend_unavailable")


# ---------------------------------------------------------------------------
# VIX regime
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Analytics: lifecycle
# ---------------------------------------------------------------------------


class TestTradeLifecycle(unittest.TestCase):
    """GET /api/options/analytics/lifecycle"""

    @patch("db.database.OptionsDatabase")
    @patch("api.services.config.get_config")
    def test_returns_lifecycle_events(self, mock_get_config, mock_db_cls):
        """Should return trade events and analytics."""
        mock_db = MagicMock()
        mock_db.get_trade_events.return_value = [{"id": 1, "ticker": "AAPL", "event_type": "entry"}]
        mock_db.get_trade_analytics.return_value = {"win_rate": 0.6, "total_exits": 10}
        mock_db_cls.return_value = mock_db
        mock_get_config.return_value = {"db_path": ":memory:"}

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/analytics/lifecycle")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(len(data["events"]), 1)
        self.assertIn("analytics", data)

    @patch("db.database.OptionsDatabase")
    @patch("api.services.config.get_config")
    def test_forwards_query_params(self, mock_get_config, mock_db_cls):
        """Should forward ticker, event_type, limit params."""
        mock_db = MagicMock()
        mock_db.get_trade_events.return_value = []
        mock_db.get_trade_analytics.return_value = {}
        mock_db_cls.return_value = mock_db
        mock_get_config.return_value = {"db_path": ":memory:"}

        app = _make_app()
        with app.test_client() as client:
            client.get(
                "/api/options/analytics/lifecycle", query_string={"ticker": "AAPL", "event_type": "roll", "limit": "50"}
            )

        mock_db.get_trade_events.assert_called_once_with(ticker="AAPL", event_type="roll", limit=50)


# ---------------------------------------------------------------------------
# Analytics: leakage
# ---------------------------------------------------------------------------


class TestLeakageAnalytics(unittest.TestCase):
    """GET /api/options/analytics/leakage"""

    @patch("db.database.OptionsDatabase")
    @patch("api.services.config.get_config")
    def test_returns_leakage_metrics(self, mock_get_config, mock_db_cls):
        """Should return leakage analytics."""
        mock_db = MagicMock()
        mock_db.get_trade_analytics.return_value = {
            "win_rate": 0.65,
            "avg_leakage": 0.02,
            "total_exits": 20,
            "wins": 13,
            "roll_count": 5,
            "per_symbol": [{"ticker": "AAPL", "leakage": 0.01}],
        }
        mock_db_cls.return_value = mock_db
        mock_get_config.return_value = {"db_path": ":memory:"}

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/analytics/leakage")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["analytics"]["win_rate"], 0.65)
        self.assertEqual(data["analytics"]["total_exits"], 20)
        self.assertEqual(len(data["analytics"]["per_symbol"]), 1)


# ---------------------------------------------------------------------------
# Watchlist tickers
# ---------------------------------------------------------------------------


class TestWatchlistTickers(unittest.TestCase):
    """GET /api/options/watchlist-tickers"""

    @patch("api.services.watchlist_manager.WatchlistManager")
    @patch("api.services.config.get_config")
    def test_returns_effective_watchlist(self, mock_get_config, mock_wl_cls):
        """Should return effective watchlist with mode."""
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda k, d=None: {
            "watchlist": ["AAPL", "MSFT"],
            "watchlist_mode": "static",
            "growth_mode": {"enabled": True, "screener_profile": {"min_volatility_pct": 4.5}},
        }.get(k, d)
        mock_get_config.return_value = mock_config

        mock_wl = MagicMock()
        mock_wl.get_effective_watchlist.return_value = ["AAPL", "MSFT", "GOOGL"]
        mock_wl_cls.return_value = mock_wl

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/watchlist-tickers")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["count"], 3)
        self.assertIn("mode", data)
        self.assertTrue(data["growth_mode_enabled"])
        mock_wl.get_effective_watchlist.assert_called_once_with(
            growth_mode_config={"enabled": True, "screener_profile": {"min_volatility_pct": 4.5}}
        )

    @patch("api.services.watchlist_manager.WatchlistManager")
    @patch("api.services.config.get_config")
    def test_fallback_on_exception(self, mock_get_config, mock_wl_cls):
        """Should fall back to static config on exception."""
        mock_wl = MagicMock()
        mock_wl.get_effective_watchlist.side_effect = Exception("Boom")
        mock_wl_cls.return_value = mock_wl

        app = _make_app(connection_config={"host": "127.0.0.1", "port": 11111, "watchlist": ["AAPL"]})
        with app.test_client() as client:
            resp = client.get("/api/options/watchlist-tickers")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["tickers"], ["AAPL"])


# ---------------------------------------------------------------------------
# Screening config
# ---------------------------------------------------------------------------


class TestScreeningConfig(unittest.TestCase):
    """GET /api/options/screening-config"""

    @patch("api.services.config.get_config")
    def test_returns_growth_csp_bounds(self, mock_get_config):
        """Should expose the Growth Mode CSP defaults and bounds."""
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda k, d=None: {
            "growth_mode": {
                "enabled": True,
                "screener_profile": {
                    "csp_target_delta": 0.30,
                    "csp_delta_tolerance": 0.12,
                    "csp_min_dte": 30,
                    "csp_max_dte": 45,
                    "csp_preferred_dte": 37,
                    "csp_default_otm_pct": 10,
                    "csp_min_otm_pct": 5,
                    "csp_max_otm_pct": 15,
                    "min_volatility_pct": 4.5,
                },
            },
        }.get(k, d)
        mock_get_config.return_value = mock_config

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/screening-config")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["success"])
        self.assertTrue(data["growth_mode_enabled"])
        self.assertEqual(data["csp_default_otm_pct"], 10)
        self.assertEqual(data["call_default_otm_pct"], 10)
        self.assertEqual(data["csp_min_dte"], 30)
        self.assertEqual(data["csp_max_dte"], 45)
        self.assertEqual(data["csp_preferred_dte"], 37)
        self.assertEqual(data["csp_min_otm_pct"], 5)
        self.assertEqual(data["csp_max_otm_pct"], 15)
        self.assertEqual(data["csp_profile_summary"]["dte_range"], "30-45")
        self.assertEqual(data["csp_profile_summary"]["otm_range"], "5-15")


# ---------------------------------------------------------------------------
# Cash status CSP fields
# ---------------------------------------------------------------------------


class TestCashStatusCSPFields(unittest.TestCase):
    """GET /api/options/cash-status — CSP-specific fields"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service.config = {"cash_reserve_enabled": True}

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    @patch("api.services.portfolio_service.PortfolioService")
    def test_returns_csp_fields(self, mock_portfolio_cls, mock_probe, mock_get_svc):
        """Should include cash_available_for_csp and cash_reserved_for_csp."""
        mock_probe.return_value = {"status": "connected"}
        mock_get_svc.return_value = self.mock_service

        mock_portfolio = MagicMock()
        mock_portfolio.get_portfolio_summary.return_value = {
            "cash_balance": 50000,
            "excess_liquidity": 55000,
        }
        mock_portfolio.get_positions.return_value = [
            {
                "symbol": "US.AAPL",
                "position": -1,
                "option_type": "PUT",
                "strike": 150.0,
                "expiration": "20240510",
            }
        ]
        mock_portfolio_cls.return_value = mock_portfolio
        self.mock_service._get_portfolio_context.return_value = {
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "cash_reserved_for_csp": 15000.0,
            "cash_available_for_csp": 40000.0,
            "broker_buying_power": 55000.0,
            "broker_buying_power_source": "excess_liquidity",
            "excess_liquidity": 55000.0,
        }

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/cash-status")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertIn("cash_available_for_csp", data)
        self.assertIn("cash_reserved_for_csp", data)
        self.assertIn("available_cash", data)
        self.assertIn("broker_buying_power", data)
        self.assertIn("broker_buying_power_source", data)
        # available_cash is true cash; excess_liquidity remains broker buying power.
        self.assertEqual(data["available_cash"], 50000.0)
        # cash_reserved = 1 * 150 * 100 = 15000
        self.assertEqual(data["cash_reserved_for_csp"], 15000.0)
        self.assertEqual(data["broker_buying_power"], 55000.0)
        self.assertEqual(data["broker_buying_power_source"], "excess_liquidity")
        self.assertEqual(data["cash_available_for_csp"], 40000.0)

    @patch("api.routes.options.get_options_service")
    @patch("api.routes.options.probe_opend_status")
    @patch("api.services.portfolio_service.PortfolioService")
    def test_preserves_existing_aliases(self, mock_portfolio_cls, mock_probe, mock_get_svc):
        """Should preserve existing cash_balance, cash_reserved, cash_available aliases."""
        mock_probe.return_value = {"status": "connected"}
        mock_get_svc.return_value = self.mock_service

        mock_portfolio = MagicMock()
        mock_portfolio.get_portfolio_summary.return_value = {
            "cash_balance": 50000,
        }
        mock_portfolio.get_positions.return_value = []
        mock_portfolio_cls.return_value = mock_portfolio
        self.mock_service._get_portfolio_context.return_value = {
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "cash_available_for_csp": 50000.0,
            "broker_buying_power": 50000.0,
            "broker_buying_power_source": "available_cash",
            "excess_liquidity": 50000.0,
        }

        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/options/cash-status")
            data = resp.get_json()

        self.assertIn("cash_balance", data)
        self.assertIn("cash_reserved", data)
        self.assertIn("cash_available", data)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
