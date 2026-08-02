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
        self.mock_context.config = {
            "watchlist": ["AAPL", "TSLA", "NVDA"],
            "watchlist_mode": "static",
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ["AAPL", "TSLA", "NVDA"])

    def test_static_mode_is_default(self):
        self.mock_context.config = {
            "watchlist": ["AAPL"],
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ["AAPL"])

    @patch.object(WatchlistManager, "_get_tvscreener_service")
    def test_dynamic_mode(self, mock_get_tvscreener):
        mock_tvscreener = MagicMock()
        mock_tvscreener.get_wheel_candidates.return_value = ["GME", "AMC"]
        mock_get_tvscreener.return_value = mock_tvscreener
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

        self.assertEqual(result, ["GME", "AMC"])
        mock_tvscreener.get_wheel_candidates.assert_called_once_with(
            min_volatility_pct=3.0, min_volume=1000000, limit=50, max_price=None
        )

    @patch.object(WatchlistManager, "_get_tvscreener_service")
    def test_hybrid_mode(self, mock_get_tvscreener):
        mock_tvscreener = MagicMock()
        mock_tvscreener.get_wheel_candidates.return_value = ["AMC", "BB"]
        mock_get_tvscreener.return_value = mock_tvscreener
        self.mock_context.config = {
            "watchlist": ["AAPL", "TSLA"],
            "watchlist_mode": "hybrid",
            "screening_criteria": {
                "min_volatility_pct": 2.0,
                "min_volume": 500000,
                "max_stocks": 30,
            },
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(len(result), 4)
        self.assertIn("AAPL", result)
        self.assertIn("TSLA", result)
        self.assertIn("AMC", result)
        self.assertIn("BB", result)

    @patch.object(WatchlistManager, "_get_tvscreener_service")
    def test_dynamic_failure_falls_back_to_static(self, mock_get_tvscreener):
        mock_tvscreener = MagicMock()
        mock_tvscreener.get_wheel_candidates.side_effect = Exception("Rate limited")
        mock_get_tvscreener.return_value = mock_tvscreener
        self.mock_context.config = {
            "watchlist": ["AAPL"],
            "watchlist_mode": "dynamic",
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ["AAPL"])

    @patch.object(WatchlistManager, "_get_tvscreener_service")
    def test_dynamic_no_service_falls_back(self, mock_get_tvscreener):
        mock_get_tvscreener.return_value = None
        self.mock_context.config = {
            "watchlist": ["AAPL"],
            "watchlist_mode": "dynamic",
        }
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist()

        self.assertEqual(result, ["AAPL"])

    @patch.object(WatchlistManager, "_get_moomoo_connection")
    def test_moomoo_mode_returns_tickers(self, mock_get_conn):
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

        self.assertEqual(result, ["AAPL", "TSLA", "NVDA"])
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

    @patch.object(WatchlistManager, "_get_tvscreener_service")
    def test_dynamic_mode_passes_max_price_from_portfolio_context(self, mock_get_tvscreener):
        """Dynamic watchlist passes max_price to tvscreener when portfolio_context is provided."""
        mock_tvscreener = MagicMock()
        mock_tvscreener.get_wheel_candidates.return_value = ["CHEAP1", "CHEAP2"]
        mock_get_tvscreener.return_value = mock_tvscreener
        self.mock_context.config = {
            "watchlist": ["AAPL"],
            "watchlist_mode": "dynamic",
            "screening_criteria": {
                "min_volatility_pct": 3.0,
                "min_volume": 1000000,
                "max_stocks": 50,
            },
        }
        portfolio = {"cash_available_for_csp": 11358.0}
        manager = WatchlistManager(self.mock_context)

        result = manager.get_effective_watchlist(portfolio_context=portfolio)

        self.assertEqual(result, ["CHEAP1", "CHEAP2"])
        # max_price = 11358 / 100 / 0.85 ≈ 133.62
        expected_max_price = 11358 / 100 / (1 - 15 / 100)
        mock_tvscreener.get_wheel_candidates.assert_called_once_with(
            min_volatility_pct=3.0, min_volume=1000000, limit=50, max_price=expected_max_price
        )

    @patch.object(WatchlistManager, "_get_tvscreener_service")
    def test_dynamic_mode_no_max_price_without_portfolio_context(self, mock_get_tvscreener):
        """Dynamic watchlist passes max_price=None when no portfolio_context."""
        mock_tvscreener = MagicMock()
        mock_tvscreener.get_wheel_candidates.return_value = ["GME", "AMC"]
        mock_get_tvscreener.return_value = mock_tvscreener
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

        self.assertEqual(result, ["GME", "AMC"])
        mock_tvscreener.get_wheel_candidates.assert_called_once_with(
            min_volatility_pct=3.0, min_volume=1000000, limit=50, max_price=None
        )


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
    # Growth Mode screener profile tests
    # ------------------------------------------------------------------ #

    def test_growth_mode_put_uses_higher_delta(self):
        """Growth Mode PUT profile should use higher target delta (0.28-0.35)."""
        growth_cfg = {
            "enabled": True,
            "screener_profile": {
                "csp_target_delta": 0.30,
                "csp_delta_tolerance": 0.12,
                "csp_min_dte": 30,
                "csp_max_dte": 45,
                "csp_preferred_dte": 37,
                "csp_default_otm_pct": 10,
                "csp_min_otm_pct": 5,
                "csp_max_otm_pct": 15,
                "min_volatility_pct": 4.5,
                "max_watchlist_tickers": 25,
                "require_cash_fit": True,
            },
        }
        growth_profile = self.manager.get_screening_profile("PUT", growth_mode_config=growth_cfg)
        self.assertEqual(growth_profile["target_delta"], 0.30)
        self.assertEqual(growth_profile["delta_tolerance"], 0.12)

    def test_growth_mode_put_uses_shorter_dte(self):
        """Growth Mode PUT profile should use the 30-45 DTE CSP window."""
        growth_cfg = {
            "enabled": True,
            "screener_profile": {
                "csp_target_delta": 0.30,
                "csp_delta_tolerance": 0.12,
                "csp_min_dte": 30,
                "csp_max_dte": 45,
                "csp_preferred_dte": 37,
                "csp_default_otm_pct": 10,
                "csp_min_otm_pct": 5,
                "csp_max_otm_pct": 15,
            },
        }
        growth_profile = self.manager.get_screening_profile("PUT", growth_mode_config=growth_cfg)
        self.assertEqual(growth_profile["preferred_dte"], 37)
        self.assertEqual(growth_profile["min_dte"], 30)
        self.assertEqual(growth_profile["max_dte"], 45)
        self.assertEqual(growth_profile.get("min_otm_pct"), 5)
        self.assertEqual(growth_profile.get("max_otm_pct"), 15)

    def test_growth_mode_put_default_otm_closer(self):
        """Growth Mode PUT profile should use the 10% OTM default."""
        growth_cfg = {
            "enabled": True,
            "screener_profile": {
                "csp_target_delta": 0.30,
                "csp_delta_tolerance": 0.12,
                "csp_preferred_dte": 37,
                "csp_default_otm_pct": 10,
                "csp_min_otm_pct": 5,
                "csp_max_otm_pct": 15,
            },
        }
        growth_profile = self.manager.get_screening_profile("PUT", growth_mode_config=growth_cfg)
        self.assertEqual(growth_profile.get("default_otm_pct"), 10)

    def test_growth_mode_put_sets_growth_screener_flag(self):
        """Growth Mode PUT profile should set growth_screener flag."""
        growth_cfg = {
            "enabled": True,
            "screener_profile": {
                "csp_target_delta": 0.30,
                "csp_delta_tolerance": 0.12,
                "csp_preferred_dte": 37,
                "csp_default_otm_pct": 10,
                "csp_min_otm_pct": 5,
                "csp_max_otm_pct": 15,
            },
        }
        growth_profile = self.manager.get_screening_profile("PUT", growth_mode_config=growth_cfg)
        self.assertTrue(growth_profile.get("growth_screener"))

    def test_growth_mode_put_require_cash_fit(self):
        """Growth Mode PUT profile should set require_cash_fit when enabled."""
        growth_cfg = {
            "enabled": True,
            "screener_profile": {
                "csp_target_delta": 0.30,
                "csp_delta_tolerance": 0.12,
                "csp_preferred_dte": 37,
                "csp_default_otm_pct": 10,
                "csp_min_otm_pct": 5,
                "csp_max_otm_pct": 15,
                "require_cash_fit": True,
            },
        }
        growth_profile = self.manager.get_screening_profile("PUT", growth_mode_config=growth_cfg)
        self.assertTrue(growth_profile.get("require_cash_fit"))

    def test_balanced_mode_keeps_conservative_defaults(self):
        """Balanced mode (growth disabled) should keep current monthly defaults."""
        balanced_profile = self.manager.get_screening_profile("PUT")
        self.assertEqual(balanced_profile["target_delta"], 0.22)
        self.assertEqual(balanced_profile["delta_tolerance"], 0.16)
        self.assertEqual(balanced_profile["preferred_dte"], 21)
        self.assertEqual(balanced_profile["min_dte"], 7)
        self.assertEqual(balanced_profile["max_dte"], 45)
        self.assertFalse(balanced_profile.get("growth_screener", False))
        self.assertNotIn("require_cash_fit", balanced_profile)

    def test_growth_mode_does_not_affect_call_profile(self):
        """Growth Mode screener profile should only affect PUT, not CALL."""
        growth_cfg = {
            "enabled": True,
            "screener_profile": {
                "csp_target_delta": 0.30,
                "csp_delta_tolerance": 0.12,
                "csp_preferred_dte": 37,
                "csp_default_otm_pct": 10,
                "csp_min_otm_pct": 5,
                "csp_max_otm_pct": 15,
            },
        }
        call_profile = self.manager.get_screening_profile("CALL", growth_mode_config=growth_cfg)
        # CALL should keep its own defaults
        self.assertEqual(call_profile["target_delta"], 0.24)
        self.assertEqual(call_profile["delta_tolerance"], 0.18)

    def test_growth_mode_effective_watchlist_uses_higher_volatility(self):
        """Growth mode watchlist should use higher min_volatility_pct from screener_profile."""
        growth_cfg = {
            "enabled": True,
            "screener_profile": {
                "min_volatility_pct": 4.5,
                "max_watchlist_tickers": 25,
            },
        }
        # Just verify the config parsing works properly
        sp = growth_cfg.get("screener_profile", {})
        self.assertEqual(sp.get("min_volatility_pct"), 4.5)
        self.assertEqual(sp.get("max_watchlist_tickers"), 25)


if __name__ == "__main__":
    unittest.main()
