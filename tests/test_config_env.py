"""
Tests for env-based config overrides.
"""

import importlib
import os
import unittest
from unittest.mock import patch

import config as config_module
from config import apply_env_overrides


class TestWatchlistEnvOverride(unittest.TestCase):
    """WATCHLIST should override the static watchlist from config."""

    def test_default_watchlist_is_not_hardcoded_in_code(self):
        with patch.dict(os.environ, {"WATCHLIST": ""}, clear=False):
            reloaded = importlib.reload(config_module)

        self.assertEqual(reloaded.DEFAULT_CONNECTION_CONFIG["watchlist"], [])

    def test_watchlist_env_override_is_parsed_and_normalized(self):
        config = {"watchlist": ["DEFAULT"]}

        with patch.dict(os.environ, {"WATCHLIST": "aapl, msft, nvda"}, clear=False):
            result = apply_env_overrides(config)

        self.assertEqual(result["watchlist"], ["AAPL", "MSFT", "NVDA"])

    def test_watchlist_env_empty_keeps_existing_config(self):
        config = {"watchlist": ["AAPL"]}

        with patch.dict(os.environ, {"WATCHLIST": ""}, clear=False):
            result = apply_env_overrides(config)

        self.assertEqual(result["watchlist"], ["AAPL"])


if __name__ == "__main__":
    unittest.main()
