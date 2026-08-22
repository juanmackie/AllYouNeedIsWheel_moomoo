"""
Tests for api/routes/portfolio.py — portfolio API endpoints.
"""

import json
import unittest
from unittest.mock import MagicMock, patch


class TestPortfolioRoutes(unittest.TestCase):
    """Portfolio route handler tests using Flask test client."""

    def setUp(self):
        from api import create_app

        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def _mock_connection_available(self):
        mock_ps = MagicMock()
        mock_ps._ensure_connection.return_value = MagicMock()
        mock_ps.get_portfolio_summary.return_value = {
            "cash_balance": 50000,
            "account_value": 100000,
            "available_cash": 35000,
        }
        mock_ps.get_positions.return_value = []
        mock_ps.last_error = None
        return mock_ps

    @patch("api.routes.portfolio.probe_opend_status")
    def test_get_portfolio_opend_unavailable(self, mock_probe):
        mock_probe.return_value = {"status": "unavailable", "message": "Not connected"}
        response = self.client.get("/api/portfolio/")
        self.assertEqual(response.status_code, 503)
        data = json.loads(response.data)
        self.assertFalse(data["success"])
        self.assertIn("error_code", data)
        self.assertEqual(data["error_code"], "opend_unavailable")
        self.assertIn("opend_status", data)
        self.assertEqual(data["opend_status"]["status"], "unavailable")

    @patch("api.routes.portfolio.probe_opend_status")
    @patch("api.routes.portfolio.get_portfolio_service")
    def test_get_portfolio_success(self, mock_get_ps, mock_probe):
        mock_probe.return_value = {"status": "connected"}
        mock_ps = self._mock_connection_available()
        mock_get_ps.return_value = mock_ps

        response = self.client.get("/api/portfolio/")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn("cash_balance", data)
        self.assertEqual(data["source_policy"]["mode"], "broker_only")
        self.assertEqual(data["source_policy"]["source_of_truth"], "opend")

    @patch("api.routes.portfolio.probe_opend_status")
    @patch("api.routes.portfolio.get_portfolio_service")
    def test_get_positions_opend_unavailable(self, mock_get_ps, mock_probe):
        mock_probe.return_value = {"status": "unavailable", "message": "Not connected"}
        response = self.client.get("/api/portfolio/positions")
        self.assertEqual(response.status_code, 503)
        data = json.loads(response.data)
        self.assertFalse(data["success"])
        self.assertIn("error_code", data)
        self.assertEqual(data["error_code"], "opend_unavailable")

    @patch("api.routes.portfolio.probe_opend_status")
    @patch("api.routes.portfolio.get_portfolio_service")
    def test_get_positions_success(self, mock_get_ps, mock_probe):
        mock_probe.return_value = {"status": "connected"}
        mock_ps = self._mock_connection_available()
        mock_ps.get_positions.return_value = []
        mock_get_ps.return_value = mock_ps
        response = self.client.get("/api/portfolio/positions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Source-Policy"), "broker_only")
        self.assertEqual(response.headers.get("X-Source-Truth"), "opend")

    @patch("api.routes.portfolio.probe_opend_status")
    @patch("api.routes.portfolio.get_portfolio_service")
    def test_get_positions_invalid_type(self, mock_get_ps, mock_probe):
        mock_probe.return_value = {"status": "connected"}
        response = self.client.get("/api/portfolio/positions?type=INVALID")
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn("error", data)

    @patch("api.routes.portfolio.probe_opend_status")
    @patch("api.routes.portfolio.get_portfolio_service")
    def test_get_weekly_income_success(self, mock_get_ps, mock_probe):
        mock_probe.return_value = {"status": "connected"}
        mock_ps = self._mock_connection_available()
        mock_ps.get_weekly_option_income.return_value = {
            "positions": [],
            "total_income": 0,
            "positions_count": 0,
            "this_friday": "2026-05-15",
            "open_short_positions_count": 3,
            "open_short_contracts_count": 13,
            "open_short_total_income": 2450.0,
        }
        mock_get_ps.return_value = mock_ps
        response = self.client.get("/api/portfolio/weekly-income")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn("open_short_positions_count", data)
        self.assertIn("open_short_contracts_count", data)
        self.assertIn("open_short_total_income", data)

    @patch("api.routes.portfolio.probe_opend_status")
    @patch("api.routes.portfolio.get_portfolio_service")
    def test_get_weekly_income_error(self, mock_get_ps, mock_probe):
        mock_probe.return_value = {"status": "connected"}
        mock_ps = self._mock_connection_available()
        mock_ps.get_weekly_option_income.return_value = {"error": "Something failed"}
        mock_get_ps.return_value = mock_ps
        response = self.client.get("/api/portfolio/weekly-income")
        self.assertEqual(response.status_code, 500)


class TestRollPressureRoutes(unittest.TestCase):
    """Roll-pressure route tests (extracted module, F008)."""

    def setUp(self):
        from api import create_app

        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def test_roll_pressure_opend_unavailable(self):
        with patch("core.connection.probe_opend_status", return_value={"status": "unavailable"}):
            response = self.client.get("/api/portfolio/roll-pressure")
            self.assertEqual(response.status_code, 503)


class TestAlertsRoutes(unittest.TestCase):
    """Alerts route tests (extracted module, F008)."""

    def setUp(self):
        from api import create_app

        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def test_alerts_opend_unavailable(self):
        with patch("core.connection.probe_opend_status", return_value={"status": "unavailable"}):
            response = self.client.get("/api/portfolio/alerts")
            self.assertEqual(response.status_code, 503)


class TestPortfolioHistoryRoute(unittest.TestCase):
    """GET /api/portfolio/history — local snapshot store, no OpenD gate."""

    def setUp(self):
        from api import create_app

        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def test_history_database_unavailable(self):
        self.app.config["database"] = None
        response = self.client.get("/api/portfolio/history")
        self.assertEqual(response.status_code, 503)

    def test_history_success_and_source_policy(self):
        mock_db = MagicMock()
        mock_db.get_portfolio_history.return_value = [
            {
                "run_id": "r1",
                "captured_at": "2026-08-20T15:00:00+00:00",
                "env": "SIMULATE",
                "account_id": "h1",
                "net_liquidation": 1000.0,
                "cash_available": 400.0,
                "cash_reserved_for_csp": 600.0,
                "cash_available_for_csp": 400.0,
                "broker_buying_power": 1000.0,
                "positions": [],
            },
            {
                "run_id": "r2",
                "captured_at": "2026-08-21T15:00:00+00:00",
                "env": "SIMULATE",
                "account_id": "h1",
                "net_liquidation": 1100.0,
                "cash_available": 440.0,
                "cash_reserved_for_csp": 660.0,
                "cash_available_for_csp": 440.0,
                "broker_buying_power": 1100.0,
                "positions": [],
            },
        ]
        self.app.config["database"] = mock_db

        response = self.client.get("/api/portfolio/history?limit=10")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["count"], 2)
        self.assertEqual(len(data["series"]), 2)
        self.assertEqual(data["series"][0]["net_liquidation"], 1000.0)  # oldest first
        self.assertEqual(data["source_policy"]["mode"], "broker_only")
        mock_db.get_portfolio_history.assert_called_once_with(limit=10)

    def test_history_invalid_limit(self):
        mock_db = MagicMock()
        self.app.config["database"] = mock_db
        response = self.client.get("/api/portfolio/history?limit=abc")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
