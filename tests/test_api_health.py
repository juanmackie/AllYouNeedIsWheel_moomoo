import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from api import create_app


class TestApiHealth(unittest.TestCase):
    def test_health_check_reports_dependency_statuses(self):
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "health.db")
        database = MagicMock()
        database.db_path = db_path

        mock_tvscreener = MagicMock()
        mock_tvscreener._ensure_initialized.return_value = True

        with (
            patch("api._register_services"),
            patch("api.get_service", return_value=mock_tvscreener),
            patch("api.probe_opend_status", return_value={"status": "connected"}),
        ):
            app = create_app(
                {
                    "TESTING": True,
                    "connection_config": {"host": "127.0.0.1", "port": 11111},
                    "database": database,
                }
            )

            with app.test_client() as client:
                resp = client.get("/health")
                data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["tvscreener"], "available")
        self.assertEqual(data["database"], "available")
        self.assertEqual(data["opend"], "connected")


if __name__ == "__main__":
    unittest.main()
