import importlib
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask


class TestApplicationStartup(unittest.TestCase):
    def test_explicit_startup_recovers_interrupted_refresh_after_database_initialization(self):
        app_module = importlib.import_module("app")
        database = MagicMock()
        api_app = Flask("startup-test")

        with (
            patch.object(app_module, "create_app", return_value=api_app),
            patch.object(app_module, "OptionsDatabase", return_value=database),
            patch("app.os.path.exists", return_value=False),
            patch("core.wheel_runner.recover_interrupted_refresh") as recover,
        ):
            created_app = app_module.create_application()

        self.assertIs(created_app, api_app)
        self.assertIs(created_app.config["database"], database)
        recover.assert_called_once_with(database)


if __name__ == "__main__":
    unittest.main()
