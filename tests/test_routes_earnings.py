"""Tests for api/routes/earnings.py — earnings API endpoints."""

import unittest
from unittest.mock import MagicMock, patch


class TestEarningsRoutes(unittest.TestCase):
    def setUp(self):
        from api import create_app

        self.app = create_app({"TESTING": True})
        self.client = self.app.test_client()

    @patch("api.routes.earnings.get_service")
    def test_get_earnings_status(self, mock_get_service):
        mock_service = MagicMock()
        mock_service.get_cache_stats.return_value = {"hits": 3, "misses": 1}
        mock_get_service.return_value = mock_service

        response = self.client.get("/api/earnings/status")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "manual")
        self.assertFalse(data["scheduler"]["running"])
        self.assertEqual(data["cache_stats"]["hits"], 3)
        mock_get_service.assert_called_once_with("ivearnings")

    @patch("api.routes.earnings.get_service")
    def test_update_single_earnings(self, mock_get_service):
        mock_service = MagicMock()
        mock_service.update_earnings_data.return_value = True
        mock_service.get_earnings_info.return_value = {"ticker": "AAPL"}
        mock_get_service.return_value = mock_service

        response = self.client.post("/api/earnings/update/AAPL")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["ticker"], "AAPL")
        self.assertEqual(data["earnings_info"]["ticker"], "AAPL")

    @patch("api.routes.earnings.get_service")
    def test_update_single_earnings_rejects_invalid_ticker(self, mock_get_service):
        response = self.client.post("/api/earnings/update/AAPL$bad")

        self.assertEqual(response.status_code, 400)
        mock_get_service.assert_not_called()

    @patch("api.routes.earnings.get_service")
    def test_refresh_all_earnings(self, mock_get_service):
        def _service(name):
            if name == "ivearnings":
                service = MagicMock()
                service.batch_update_earnings.return_value = {"successful": 2, "failed": 1}
                return service
            if name == "portfolio":
                portfolio = MagicMock()
                portfolio.get_positions.return_value = [{"symbol": "AAPL"}]
                return portfolio
            if name == "options":
                options = MagicMock()
                options.watchlist_manager.get_effective_watchlist.return_value = ["MSFT"]
                return options
            raise AssertionError(f"unexpected service: {name}")

        mock_get_service.side_effect = _service

        response = self.client.post("/api/earnings/refresh")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["updated_count"], 2)
        self.assertEqual(data["failed_count"], 1)
        self.assertEqual(data["total_attempted"], 2)

    @patch("db.database.OptionsDatabase")
    def test_get_pending_earnings(self, mock_db):
        mock_db_instance = MagicMock()
        mock_db_instance.get_pending_earnings.return_value = [{"ticker": "AAPL"}]
        mock_db.return_value = mock_db_instance

        response = self.client.get("/api/earnings/pending")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["tickers"][0]["ticker"], "AAPL")
        mock_db.assert_called_once()


if __name__ == "__main__":
    unittest.main()
