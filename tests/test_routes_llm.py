import os
import unittest
from unittest.mock import patch

from flask import Flask

from api.routes.llm import bp as llm_bp


def _make_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(llm_bp)
    return app


class TestLLMRoutes(unittest.TestCase):
    def test_status_reports_unconfigured_without_503(self):
        app = _make_app()

        with patch.dict(os.environ, {"LLM_ENABLED": "true", "LLM_API_KEY": ""}, clear=False):
            with app.test_client() as client:
                resp = client.get("/api/llm/status")
                data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["success"])
        self.assertTrue(data["enabled"])
        self.assertFalse(data["configured"])
        self.assertFalse(data["available"])

    def test_status_reports_disabled(self):
        app = _make_app()

        with patch.dict(os.environ, {"LLM_ENABLED": "false"}, clear=False):
            with app.test_client() as client:
                resp = client.get("/api/llm/status")
                data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["success"])
        self.assertFalse(data["enabled"])
        self.assertFalse(data["available"])


if __name__ == "__main__":
    unittest.main()
