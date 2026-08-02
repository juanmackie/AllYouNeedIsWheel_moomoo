"""Tests for api/routes/earnings.py — earnings API endpoints."""

import unittest
from unittest.mock import MagicMock, patch


class TestEarningsRoutes(unittest.TestCase):
    def setUp(self):
        from api import create_app

        self.app = create_app({"TESTING": True})
        self.client = self.app.test_client()

    @patch("api.routes.earnings.get_scheduler_info")
    @patch("api.routes.earnings.IVEarningsService")
    @patch("api.routes.earnings.OptionsDatabase")
    def test_get_earnings_status(self, mock_db, mock_service_cls, mock_scheduler):
        mock_scheduler.return_value = {"running": True, "workers": 1}
        mock_service = MagicMock()
        mock_service.get_cache_stats.return_value = {"hits": 3, "misses": 1}
        mock_service_cls.return_value = mock_service

        response = self.client.get("/api/earnings/status")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["scheduler"]["workers"], 1)
        self.assertEqual(data["cache_stats"]["hits"], 3)
        mock_db.assert_called_once()

    @patch("api.routes.earnings.IVEarningsService")
    @patch("api.routes.earnings.OptionsDatabase")
    def test_update_single_earnings(self, mock_db, mock_service_cls):
        mock_service = MagicMock()
        mock_service.update_earnings_data.return_value = True
        mock_service.get_earnings_info.return_value = {"ticker": "AAPL"}
        mock_service_cls.return_value = mock_service

        response = self.client.get("/api/earnings/update/AAPL")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["ticker"], "AAPL")
        self.assertEqual(data["earnings_info"]["ticker"], "AAPL")
        mock_db.assert_called_once()

    @patch("api.routes.earnings.WatchlistManager")
    @patch("api.routes.earnings.PortfolioService")
    @patch("api.routes.earnings.IVEarningsService")
    @patch("api.routes.earnings.OptionsDatabase")
    def test_refresh_all_earnings(self, mock_db, mock_service_cls, mock_portfolio_cls, mock_watchlist_cls):
        mock_service = MagicMock()
        mock_service.batch_update_earnings.return_value = {"successful": 2, "failed": 1}
        mock_service_cls.return_value = mock_service

        mock_portfolio = MagicMock()
        mock_portfolio.get_positions.return_value = [{"symbol": "AAPL"}]
        mock_portfolio_cls.return_value = mock_portfolio

        mock_watchlist = MagicMock()
        mock_watchlist.get_effective_watchlist.return_value = ["MSFT"]
        mock_watchlist_cls.return_value = mock_watchlist

        response = self.client.post("/api/earnings/refresh")
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["updated_count"], 2)
        self.assertEqual(data["failed_count"], 1)
        self.assertEqual(data["total_attempted"], 2)
        mock_db.assert_called_once()

    @patch("api.routes.earnings.OptionsDatabase")
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
