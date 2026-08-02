import os
import unittest
from unittest.mock import patch

from api import _resolve_secret_key, create_app


class TestApiConfig(unittest.TestCase):
    def test_create_app_uses_non_static_secret_key(self):
        with patch.dict(os.environ, {}, clear=True), patch("api._register_services"):
            app = create_app({"TESTING": True})

        self.assertNotEqual(app.config["SECRET_KEY"], "dev")
        self.assertTrue(app.config["SECRET_KEY"])

    def test_resolve_secret_key_preserves_explicit_secret(self):
        with patch.dict(os.environ, {"SECRET_KEY": "explicit-secret"}, clear=True):
            self.assertEqual(_resolve_secret_key(), "explicit-secret")


if __name__ == "__main__":
    unittest.main()
