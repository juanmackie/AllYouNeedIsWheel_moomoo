"""Tests for the persisted wheel preset route."""

import unittest
from unittest.mock import MagicMock, patch


class TestSettingsRoutes(unittest.TestCase):
    def test_preset_propagation_failure_rolls_back_database(self):
        from api import create_app

        app = create_app({"TESTING": True})
        db = MagicMock()
        db.get_setting.return_value = "balanced"
        app.config["database"] = db
        client = app.test_client()

        service = MagicMock()
        service.recommendation_engine.set_active_preset.side_effect = RuntimeError("engine unavailable")
        with patch("api.routes.settings._get_options_service", return_value=service):
            response = client.post("/api/settings/preset", json={"preset": "aggressive"})

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()["success"])
        db.set_setting.assert_any_call("wheel_preset", "aggressive")
        db.set_setting.assert_any_call("wheel_preset", "balanced")


if __name__ == "__main__":
    unittest.main()
