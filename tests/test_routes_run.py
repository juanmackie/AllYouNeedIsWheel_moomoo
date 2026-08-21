import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from flask import Flask

from api.routes.run import bp


class TestRunRoute(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.app = Flask(__name__)
        self.app.config["database"] = self.db
        self.app.register_blueprint(bp)

    def test_get_run_recomputes_stale_without_mutating_snapshot(self):
        fetched = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
        snapshot = {
            "run": {
                "status": "ready",
                "errors": [],
                "coverage_complete": True,
                "quote_fetched_at": {"AAPL": fetched},
                "max_tradeable_age_sec": 120,
            },
            "tradeable": True,
            "signals": [{"ticker": "AAPL"}],
        }
        self.db.get_latest_attempt.return_value = None
        self.db.get_latest_snapshot.return_value = snapshot

        with self.app.test_client() as client:
            response = client.get("/api/run")

        payload = response.get_json()
        self.assertFalse(payload["snapshot"]["tradeable"])
        self.assertEqual(payload["snapshot"]["effective_status"], "stale")
        self.assertEqual(snapshot["run"]["status"], "ready")
        self.assertTrue(snapshot["tradeable"])

    @patch("api.routes.run._get_runner")
    @patch("api.routes.run.start_background_refresh", return_value=True)
    def test_refresh_starts_one_runner_attempt(self, mock_refresh, mock_get_runner):
        self.db.get_latest_attempt.return_value = {"state": "refreshing"}
        mock_get_runner.return_value = MagicMock()

        with self.app.test_client() as client:
            response = client.post("/api/run/refresh")

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.get_json()["started"])
        mock_refresh.assert_called_once()


if __name__ == "__main__":
    unittest.main()
