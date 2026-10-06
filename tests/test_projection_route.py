"""Tests for GET /api/portfolio/projection — Monte Carlo trajectory route."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


def _history_snaps(navs, step_days=10):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "run_id": f"r{i}",
            "captured_at": (start + timedelta(days=i * step_days)).isoformat(),
            "env": "SIMULATE",
            "account_id": "h1",
            "net_liquidation": nav,
            "cash_available": 0.0,
            "cash_reserved_for_csp": 0.0,
            "cash_available_for_csp": 0.0,
            "broker_buying_power": 0.0,
            "positions": [],
        }
        for i, nav in enumerate(navs)
    ]


class TestProjectionRoute(unittest.TestCase):
    def setUp(self):
        from api import create_app

        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def test_database_unavailable(self):
        self.app.config["database"] = None
        response = self.client.get("/api/portfolio/projection")
        self.assertEqual(response.status_code, 503)

    def test_invalid_seed(self):
        self.app.config["database"] = MagicMock()
        response = self.client.get("/api/portfolio/projection?seed=abc")
        self.assertEqual(response.status_code, 400)

    def test_invalid_horizon(self):
        self.app.config["database"] = MagicMock()
        response = self.client.get("/api/portfolio/projection?horizon_days=abc")
        self.assertEqual(response.status_code, 400)

    @patch("api.services.config.get_config", return_value={"portfolio_env": "SIMULATE", "account_id": ""})
    def test_projection_ok(self, _mock_config):
        mock_db = MagicMock()
        mock_db.get_setting.return_value = None
        mock_db.get_portfolio_history.return_value = _history_snaps(
            [60000.0, 60600.0, 60300.0, 61200.0, 60900.0, 61800.0]
        )
        mock_db.get_fills.return_value = []
        self.app.config["database"] = mock_db

        response = self.client.get("/api/portfolio/projection?horizon_days=365&paths=200")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["source_policy"]["mode"], "broker_only")
        projection = data["projection"]
        self.assertEqual(projection["status"], "ok")
        self.assertEqual(data["horizon_days"], 365)
        self.assertEqual(data["n_paths"], 200)
        self.assertTrue(len(projection["bands"]) >= 2)
        band = projection["bands"][-1]
        self.assertLessEqual(band["p10"], band["p50"])
        self.assertLessEqual(band["p50"], band["p90"])
        self.assertIn("fills", data)

    @patch("api.services.config.get_config", return_value={"portfolio_env": "SIMULATE", "account_id": ""})
    def test_projection_insufficient_passthrough(self, _mock_config):
        mock_db = MagicMock()
        mock_db.get_setting.return_value = None
        mock_db.get_portfolio_history.return_value = _history_snaps([60000.0])
        mock_db.get_fills.return_value = []
        self.app.config["database"] = mock_db

        response = self.client.get("/api/portfolio/projection")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["projection"]["status"], "insufficient")

    @patch("api.services.config.get_config", return_value={"portfolio_env": "SIMULATE", "account_id": ""})
    def test_projection_clamps_ranges(self, _mock_config):
        mock_db = MagicMock()
        mock_db.get_setting.return_value = None
        mock_db.get_portfolio_history.return_value = _history_snaps(
            [60000.0, 60600.0, 60300.0, 61200.0, 60900.0, 61800.0]
        )
        mock_db.get_fills.return_value = []
        self.app.config["database"] = mock_db

        response = self.client.get("/api/portfolio/projection?horizon_days=9999&paths=99999")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["horizon_days"], 1825)
        self.assertEqual(data["n_paths"], 5000)


if __name__ == "__main__":
    unittest.main()
