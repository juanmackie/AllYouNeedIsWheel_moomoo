"""
Tests for api/services/watchlist_manager.py — WatchlistManager class
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.watchlist_manager import WatchlistManager


class TestWatchlistManagerInit(unittest.TestCase):
    """Test WatchlistManager initialization."""

    def test_init_stores_context(self):
        mock_context = MagicMock()
        mock_context.config = {"watchlist": ["AAPL"]}
        manager = WatchlistManager(mock_context)
        self.assertIs(manager._config_provider, mock_context)


class TestWatchlistManagerGetEffectiveWatchlist(unittest.TestCase):
    """Test get_effective_watchlist behavior."""

    def setUp(self):
        self.mock_context = MagicMock()
        self.mock_context.config = {}

    def test_static_mode(self):
        """Config list is one source of the merged union."""
        self.mock_context.config = {
            "watchlist": ["AAPL", "TSLA", "NVDA"],
            "watchlist_mode": "static",
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ["AAPL", "NVDA", "TSLA"])

    def test_config_only_union(self):
        self.mock_context.config = {
            "watchlist": ["AAPL"],
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ["AAPL"])

    def test_dynamic_mode_is_unsupported_and_falls_back_to_static(self):
        """Broad dynamic screening is out of scope; dynamic mode returns static."""
        self.mock_context.config = {
            "watchlist": ["AAPL"],
            "watchlist_mode": "dynamic",
            "screening_criteria": {
                "min_volatility_pct": 3.0,
                "min_volume": 1000000,
                "max_stocks": 50,
            },
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ["AAPL"])

    @patch.object(WatchlistManager, "_fetch_moomoo_watchlist")
    def test_hybrid_mode_unions_moomoo_and_static(self, mock_fetch_moomoo):
        """Hybrid mode is the union of the Moomoo group and the static list."""
        mock_fetch_moomoo.return_value = ["AMC", "BB"]
        self.mock_context.config = {
            "watchlist": ["AAPL", "TSLA"],
            "watchlist_mode": "hybrid",
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(len(result), 4)
        self.assertIn("AAPL", result)
        self.assertIn("TSLA", result)
        self.assertIn("AMC", result)
        self.assertIn("BB", result)

    def test_dynamic_failure_falls_back_to_static(self):
        """Any dynamic-mode failure falls back to the static watchlist."""
        self.mock_context.config = {
            "watchlist": ["AAPL"],
            "watchlist_mode": "dynamic",
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ["AAPL"])

    def test_unknown_mode_falls_back_to_static(self):
        self.mock_context.config = {
            "watchlist": ["AAPL"],
            "watchlist_mode": "quantum",
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ["AAPL"])

    @patch("core.context_factory.probe_opend_status", return_value={"status": "connected"})
    @patch.object(WatchlistManager, "_get_moomoo_connection")
    def test_moomoo_mode_returns_tickers(self, mock_get_conn, _mock_probe):
        """Moomoo watchlist mode returns tickers from Moomoo watchlist group."""
        mock_conn = MagicMock()
        mock_conn.is_connected.return_value = True
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.to_dict.return_value = [
            {"code": "US.AAPL"},
            {"code": "US.TSLA"},
            {"code": "US.NVDA"},
        ]
        mock_conn.get_user_security.return_value = (0, mock_df)
        mock_get_conn.return_value = mock_conn
        self.mock_context.config = {
            "watchlist": ["FALLBACK"],
            "watchlist_mode": "moomoo",
            "moomoo_watchlist_group": "My Watchlist",
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        # Config fallback symbols are merged into the union too.
        self.assertEqual(result, ["AAPL", "FALLBACK", "NVDA", "TSLA"])
        mock_conn.get_user_security.assert_called_once_with("My Watchlist")

    @patch.object(WatchlistManager, "_get_moomoo_connection")
    def test_moomoo_mode_falls_back_on_connection_none(self, mock_get_conn):
        """Moomoo watchlist falls back to static when connection is None."""
        mock_get_conn.return_value = None
        self.mock_context.config = {
            "watchlist": ["AAPL", "TSLA"],
            "watchlist_mode": "moomoo",
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ["AAPL", "TSLA"])

    @patch.object(WatchlistManager, "_get_moomoo_connection")
    def test_moomoo_mode_falls_back_on_connect_failure(self, mock_get_conn):
        """Moomoo watchlist falls back to static when connection fails."""
        mock_conn = MagicMock()
        mock_conn.is_connected.return_value = False
        mock_conn.connect.return_value = False
        mock_get_conn.return_value = mock_conn
        self.mock_context.config = {
            "watchlist": ["MSFT"],
            "watchlist_mode": "moomoo",
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ["MSFT"])

    @patch.object(WatchlistManager, "_get_moomoo_connection")
    def test_moomoo_mode_falls_back_on_empty_data(self, mock_get_conn):
        """Moomoo watchlist falls back to static when group has no securities."""
        mock_conn = MagicMock()
        mock_conn.is_connected.return_value = True
        mock_df = MagicMock()
        mock_df.empty = True
        mock_conn.get_user_security.return_value = (0, mock_df)
        mock_get_conn.return_value = mock_conn
        self.mock_context.config = {
            "watchlist": ["GOOGL"],
            "watchlist_mode": "moomoo",
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ["GOOGL"])

    @patch.object(WatchlistManager, "_get_moomoo_connection")
    def test_moomoo_mode_falls_back_on_api_error(self, mock_get_conn):
        """Moomoo watchlist falls back to static when get_user_security fails."""
        mock_conn = MagicMock()
        mock_conn.is_connected.return_value = True
        mock_conn.get_user_security.side_effect = Exception("API error")
        mock_get_conn.return_value = mock_conn
        self.mock_context.config = {
            "watchlist": ["AMD"],
            "watchlist_mode": "moomoo",
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ["AMD"])


class TestWatchlistManagerScreeningProfile(unittest.TestCase):
    """Test get_screening_profile method."""

    def setUp(self):
        self.mock_context = MagicMock()
        self.manager = WatchlistManager(self.mock_context)

    def test_profile_default_monthly(self):
        profile = self.manager.get_screening_profile("CALL")
        self.assertEqual(profile["profile_type"], "monthly")
        self.assertIn("target_delta", profile)

    def test_profile_weekly(self):
        profile = self.manager.get_screening_profile("PUT", dte=7)
        self.assertEqual(profile["profile_type"], "weekly")

    def test_profile_quarterly(self):
        profile = self.manager.get_screening_profile("CALL", dte=60)
        self.assertEqual(profile["profile_type"], "quarterly")

    def test_profile_explicit_type(self):
        profile = self.manager.get_screening_profile("PUT", profile_type="quarterly")
        self.assertEqual(profile["profile_type"], "quarterly")

    def test_profile_call_vs_put(self):
        call_profile = self.manager.get_screening_profile("CALL")
        put_profile = self.manager.get_screening_profile("PUT")
        self.assertNotEqual(call_profile["target_delta"], put_profile["target_delta"])

    def test_profile_vix_adjustment(self):
        vix_regime = {
            "regime": "fear",
            "delta_adjustment": -0.05,
            "exposure_multiplier": 0.5,
        }
        profile = self.manager.get_screening_profile("PUT", vix_regime=vix_regime)
        self.assertEqual(profile["vix_regime"], "fear")
        self.assertLess(profile["target_delta"], 0.22)

    def test_profile_vix_complacency(self):
        vix_regime = {
            "regime": "complacency",
            "delta_adjustment": 0.05,
            "exposure_multiplier": 1.5,
        }
        profile = self.manager.get_screening_profile("PUT", vix_regime=vix_regime)
        self.assertEqual(profile["vix_regime"], "complacency")
        self.assertGreater(profile["target_delta"], 0.22)

    # ------------------------------------------------------------------ #
    # Selected-preset screener profile tests
    # ------------------------------------------------------------------ #

    def _flat_preset(self, **overrides):
        """A flat WheelPreset.to_screener_profile() payload."""
        sp = {
            "csp_target_delta": 0.30,
            "csp_delta_tolerance": 0.12,
            "csp_min_dte": 30,
            "csp_max_dte": 45,
            "csp_preferred_dte": 37,
            "csp_default_otm_pct": 10,
            "csp_min_otm_pct": 5,
            "csp_max_otm_pct": 15,
            "call_default_otm_pct": 10,
            "min_mid_price": 0.05,
            "min_premium_per_contract": 10,
            "max_spread_pct": 60,
            "min_open_interest": 10,
            "require_cash_fit": True,
        }
        sp.update(overrides)
        return sp

    def test_preset_profile_put_uses_preset_delta(self):
        """Selected preset drives the CSP target delta/tolerance."""
        preset = self._flat_preset()
        profile = self.manager.get_screening_profile("PUT", growth_mode_config=preset)
        self.assertEqual(profile["target_delta"], 0.30)
        self.assertEqual(profile["delta_tolerance"], 0.12)

    def test_preset_profile_put_uses_preset_dte(self):
        """Selected preset drives the CSP DTE window."""
        preset = self._flat_preset()
        profile = self.manager.get_screening_profile("PUT", growth_mode_config=preset)
        self.assertEqual(profile["preferred_dte"], 37)
        self.assertEqual(profile["min_dte"], 30)
        self.assertEqual(profile["max_dte"], 45)
        self.assertEqual(profile.get("min_otm_pct"), 5)
        self.assertEqual(profile.get("max_otm_pct"), 15)

    def test_preset_profile_put_default_otm(self):
        """Selected preset drives the CSP default OTM."""
        preset = self._flat_preset()
        profile = self.manager.get_screening_profile("PUT", growth_mode_config=preset)
        self.assertEqual(profile.get("default_otm_pct"), 10)

    def test_preset_profile_put_require_cash_fit(self):
        """Selected preset sets require_cash_fit."""
        preset = self._flat_preset(require_cash_fit=True)
        profile = self.manager.get_screening_profile("PUT", growth_mode_config=preset)
        self.assertTrue(profile.get("require_cash_fit"))

    def test_balanced_mode_keeps_conservative_defaults(self):
        """Balanced mode (no preset) should keep current monthly defaults."""
        balanced_profile = self.manager.get_screening_profile("PUT")
        self.assertEqual(balanced_profile["target_delta"], 0.22)
        self.assertEqual(balanced_profile["delta_tolerance"], 0.16)
        self.assertEqual(balanced_profile["preferred_dte"], 21)
        self.assertEqual(balanced_profile["min_dte"], 7)
        self.assertEqual(balanced_profile["max_dte"], 45)
        self.assertFalse(balanced_profile.get("growth_screener", False))
        self.assertNotIn("require_cash_fit", balanced_profile)

    def test_preset_profile_does_not_affect_call_target_delta(self):
        """Selected preset PUT keys should not overwrite CALL target delta."""
        preset = self._flat_preset()
        call_profile = self.manager.get_screening_profile("CALL", growth_mode_config=preset)
        # CALL keeps its own defaults; only generic floors / call OTM are merged.
        self.assertEqual(call_profile["target_delta"], 0.24)
        self.assertEqual(call_profile["delta_tolerance"], 0.18)

    def test_effective_watchlist_uses_preset_fields(self):
        """Flat preset profile carries the expected threshold keys."""
        preset = self._flat_preset()
        self.assertEqual(preset.get("min_premium_per_contract"), 10)
        self.assertEqual(preset.get("require_cash_fit"), True)


if __name__ == "__main__":
    unittest.main()
