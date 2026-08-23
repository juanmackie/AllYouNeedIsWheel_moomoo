"""
Tests for api/routes/alerts.py — position alerts endpoints.

All endpoints are tested via Flask test client with mocked services and OpenD
probe to avoid requiring live infrastructure.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask

from api.routes.alerts import bp


def _make_app(**overrides):
    """Create a minimal Flask app with the alerts blueprint registered."""
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


class TestAlertsOpenDGate(unittest.TestCase):
    """Regression guard: the 503 gate must expose the documented contract."""

    @patch("api.routes.utils.probe_opend_status")
    def test_unavailable_503_has_top_level_contract_fields(self, mock_probe):
        """F-H2 regression: error_code/opend_status must be top-level, not
        nested under 'extra'."""
        mock_probe.return_value = {"status": "unavailable", "message": "OpenD is down."}
        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/portfolio/alerts")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 503)
        self.assertFalse(data["success"])
        self.assertEqual(data["error_code"], "opend_unavailable")
        self.assertEqual(data["opend_status"]["status"], "unavailable")
        self.assertNotIn("extra", data)

    @patch("api.routes.utils.probe_opend_status")
    def test_login_required_maps_error_code(self, mock_probe):
        mock_probe.return_value = {"status": "login_required", "message": "Login required."}
        app = _make_app()
        with app.test_client() as client:
            resp = client.get("/api/portfolio/alerts")
            data = resp.get_json()

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(data["error_code"], "opend_login_required")


if __name__ == "__main__":
    unittest.main()
