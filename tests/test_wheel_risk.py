"""
Tests for api/services/wheel_risk_service.py — Wheel Risk Panel
"""

import unittest
from unittest.mock import MagicMock
from datetime import datetime
from api.services.wheel_risk_service import (
    compute_wheel_risk,
    _classify_vix_pressure,
)


class TestClassifyVixPressure(unittest.TestCase):

    def test_low_vix(self):
        self.assertEqual(_classify_vix_pressure(10.0), "low")

    def test_normal_vix(self):
        self.assertEqual(_classify_vix_pressure(15.0), "normal")

    def test_elevated_vix(self):
        self.assertEqual(_classify_vix_pressure(25.0), "elevated")

    def test_high_vix(self):
        self.assertEqual(_classify_vix_pressure(35.0), "high")

    def test_boundary_low(self):
        self.assertEqual(_classify_vix_pressure(11.99), "low")

    def test_boundary_normal_upper(self):
        self.assertEqual(_classify_vix_pressure(19.99), "normal")

    def test_boundary_elevated_upper(self):
        self.assertEqual(_classify_vix_pressure(29.99), "elevated")


class TestComputeWheelRisk(unittest.TestCase):

    def test_empty_portfolio(self):
        result = compute_wheel_risk({})
        self.assertIn("concentration", result)
        self.assertIn("csp_exposure", result)
        self.assertIn("covered_call_exposure", result)
        self.assertIn("earnings_exposure", result)
        self.assertIn("macro_pressure", result)
        self.assertIn("warnings", result)
        self.assertIn("generated_at", result)

    def test_generated_at_timestamp(self):
        result = compute_wheel_risk({})
        try:
            datetime.fromisoformat(result["generated_at"])
        except (ValueError, TypeError):
            self.fail("generated_at is not ISO format")

    def test_concentration_empty_when_no_positions(self):
        result = compute_wheel_risk({})
        self.assertEqual(result["concentration"]["by_ticker"], {})

    def test_concentration_single_position(self):
        ctx = {
            "positions": {"AAPL": {"position": 100, "market_price": 150.0}},
            "account_value": 100000.0,
            "cash_balance": 50000.0,
        }
        result = compute_wheel_risk(ctx)
        conc = result["concentration"]["by_ticker"]
        self.assertIn("AAPL", conc)
        self.assertAlmostEqual(conc["AAPL"]["pct_of_account"], 15.0, delta=0.1)

    def test_concentration_warning_above_20pct(self):
        ctx = {
            "positions": {"AAPL": {"position": 1000, "market_price": 150.0}},
            "account_value": 100000.0,
            "cash_balance": 50000.0,
        }
        result = compute_wheel_risk(ctx)
        conc = result["concentration"]["by_ticker"]
        self.assertTrue(conc["AAPL"]["warning"])
        self.assertGreater(conc["AAPL"]["pct_of_account"], 20.0)

    def test_concentration_no_warning_below_20pct(self):
        ctx = {
            "positions": {"AAPL": {"position": 10, "market_price": 150.0}},
            "account_value": 200000.0,
            "cash_balance": 100000.0,
        }
        result = compute_wheel_risk(ctx)
        conc = result["concentration"]["by_ticker"]
        self.assertFalse(conc["AAPL"]["warning"])

    def test_top_3_concentration(self):
        ctx = {
            "positions": {
                "AAPL": {"position": 100, "market_price": 150.0},
                "MSFT": {"position": 50, "market_price": 300.0},
                "GOOGL": {"position": 20, "market_price": 200.0},
                "TSLA": {"position": 10, "market_price": 100.0},
            },
            "account_value": 100000.0,
            "cash_balance": 50000.0,
        }
        result = compute_wheel_risk(ctx)
        top3 = result["concentration"]["top_3_concentration_pct"]
        self.assertGreater(top3, 0.0)
        self.assertLess(top3, 100.0)

    def test_csp_exposure_present(self):
        result = compute_wheel_risk({})
        csp = result["csp_exposure"]
        self.assertIn("cash_balance", csp)
        self.assertIn("cash_reserved_for_csp", csp)
        self.assertIn("cash_available_for_csp", csp)
        self.assertIn("csp_cash_ratio", csp)
        self.assertIn("free_cash_ratio", csp)

    def test_csp_overallocation_warning(self):
        ctx = {
            "cash_balance": 100000.0,
            "account_value": 100000.0,
            "cash_reserved_for_csp": 60000.0,
            "cash_available_for_csp": 40000.0,
        }
        result = compute_wheel_risk(ctx)
        self.assertTrue(result["csp_exposure"]["warning_overallocated"])
        self.assertIn("account value reserved for", " ".join(result["warnings"]).lower())

    def test_csp_not_overallocated(self):
        ctx = {
            "cash_balance": 100000.0,
            "account_value": 100000.0,
            "cash_reserved_for_csp": 30000.0,
            "cash_available_for_csp": 70000.0,
        }
        result = compute_wheel_risk(ctx)
        self.assertFalse(result["csp_exposure"]["warning_overallocated"])

    def test_cash_low_warning(self):
        ctx = {
            "cash_balance": 100000.0,
            "account_value": 100000.0,
            "cash_available_for_csp": 2000.0,
        }
        result = compute_wheel_risk(ctx)
        self.assertTrue(result["csp_exposure"]["warning_cash_low"])
        self.assertIn("free cash", " ".join(result["warnings"]).lower())

    def test_csp_cash_ratio_calculated(self):
        ctx = {
            "cash_balance": 100000.0,
            "account_value": 100000.0,
            "cash_reserved_for_csp": 25000.0,
        }
        result = compute_wheel_risk(ctx)
        self.assertAlmostEqual(result["csp_exposure"]["csp_cash_ratio"], 25.0, delta=0.1)

    def test_covered_call_exposure_empty(self):
        result = compute_wheel_risk({})
        self.assertEqual(result["covered_call_exposure"], [])

    def test_covered_call_exposure_with_calls(self):
        ctx = {
            "positions": {"AAPL": {"position": 200, "market_price": 150.0, "avg_cost": 140.0}},
            "short_calls": {"AAPL": 1},
            "account_value": 100000.0,
            "cash_balance": 50000.0,
        }
        result = compute_wheel_risk(ctx)
        cc = result["covered_call_exposure"]
        self.assertEqual(len(cc), 1)
        self.assertEqual(cc[0]["ticker"], "AAPL")
        self.assertEqual(cc[0]["contracts"], 1)
        self.assertEqual(cc[0]["shares_owned"], 200)
        self.assertEqual(cc[0]["coverage_ratio"], 50.0)

    def test_covered_call_full_coverage_warning(self):
        ctx = {
            "positions": {"AAPL": {"position": 100, "market_price": 150.0}},
            "short_calls": {"AAPL": 1},
            "account_value": 100000.0,
            "cash_balance": 50000.0,
        }
        result = compute_wheel_risk(ctx)
        cc = result["covered_call_exposure"]
        self.assertEqual(cc[0]["coverage_ratio"], 100.0)

    def test_covered_call_upside_capped(self):
        ctx = {
            "positions": {"AAPL": {"position": 100, "market_price": 150.0}},
            "short_calls": {"AAPL": 1},
            "account_value": 100000.0,
            "cash_balance": 50000.0,
        }
        result = compute_wheel_risk(ctx)
        cc = result["covered_call_exposure"]
        self.assertGreater(cc[0]["upside_capped_value"], 0.0)
        self.assertGreater(cc[0]["upside_capped_pct"], 0.0)

    def test_macro_pressure_vix_normal(self):
        ctx = {
            "vix_regime": {"vix": 15.0, "regime": "normal"},
        }
        result = compute_wheel_risk(ctx)
        mp = result["macro_pressure"]
        self.assertEqual(mp["vix_regime"], "normal")
        self.assertEqual(mp["vix_pressure"], "normal")

    def test_macro_pressure_vix_high(self):
        ctx = {
            "vix_regime": {"vix": 32.0, "regime": "fear"},
        }
        result = compute_wheel_risk(ctx)
        mp = result["macro_pressure"]
        self.assertEqual(mp["vix_pressure"], "high")

    def test_macro_pressure_vix_low(self):
        ctx = {
            "vix_regime": {"vix": 10.0, "regime": "complacency"},
        }
        result = compute_wheel_risk(ctx)
        mp = result["macro_pressure"]
        self.assertEqual(mp["vix_pressure"], "low")

    def test_macro_pressure_with_macro_regime(self):
        ctx = {
            "vix_regime": {"vix": 20.0, "regime": "normal"},
            "macro_regime": {
                "macro_multiplier": 0.85,
                "credit_stress": "elevated",
                "rate_regime": "tightening",
            },
        }
        result = compute_wheel_risk(ctx)
        mp = result["macro_pressure"]
        self.assertEqual(mp["macro_multiplier"], 0.85)
        self.assertEqual(mp["credit_stress"], "elevated")
        self.assertEqual(mp["rate_regime"], "tightening")

    def test_macro_pressure_no_macro_regime_has_defaults(self):
        ctx = {"vix_regime": {"vix": 20.0, "regime": "normal"}}
        result = compute_wheel_risk(ctx)
        self.assertIn("macro_multiplier", result["macro_pressure"])
        self.assertEqual(result["macro_pressure"]["macro_multiplier"], 1.0)
        self.assertEqual(result["macro_pressure"]["credit_stress"], "unknown")

    def test_vix_pressure_warning_generated(self):
        ctx = {
            "vix_regime": {"vix": 35.0, "regime": "fear"},
            "account_value": 100000.0,
            "cash_balance": 50000.0,
        }
        result = compute_wheel_risk(ctx)
        self.assertIn("VIX is elevated", " ".join(result["warnings"]))

    def test_short_options_count(self):
        ctx = {
            "short_calls": {"AAPL": 1, "MSFT": 2},
            "short_puts": {"GOOGL": 1},
            "account_value": 100000.0,
            "cash_balance": 50000.0,
        }
        result = compute_wheel_risk(ctx)
        self.assertEqual(result["macro_pressure"]["total_short_options"], 4)

    def test_warnings_multiple_risks(self):
        ctx = {
            "positions": {"AAPL": {"position": 500, "market_price": 150.0}},
            "short_calls": {"AAPL": 1},
            "account_value": 100000.0,
            "cash_balance": 100000.0,
            "cash_reserved_for_csp": 60000.0,
            "cash_available_for_csp": 40000.0,
            "vix_regime": {"vix": 35.0, "regime": "fear"},
        }
        result = compute_wheel_risk(ctx)
        self.assertGreaterEqual(len(result["warnings"]), 2)

    def test_broker_buying_power_in_csp(self):
        ctx = {
            "cash_balance": 50000.0,
            "account_value": 100000.0,
            "broker_buying_power": 45000.0,
            "cash_available_for_csp": 50000.0,
        }
        result = compute_wheel_risk(ctx)
        self.assertEqual(result["csp_exposure"]["broker_buying_power"], 45000.0)

    def test_total_stock_value(self):
        ctx = {
            "positions": {
                "AAPL": {"position": 100, "market_price": 150.0},
                "MSFT": {"position": 50, "market_price": 300.0},
            },
            "account_value": 100000.0,
            "cash_balance": 10000.0,
        }
        result = compute_wheel_risk(ctx)
        self.assertAlmostEqual(result["concentration"]["total_stock_value"], 30000.0, delta=0.1)

    def test_multiple_tickers_sorted_by_value(self):
        ctx = {
            "positions": {
                "AAPL": {"position": 10, "market_price": 150.0},
                "MSFT": {"position": 50, "market_price": 300.0},
                "GOOGL": {"position": 5, "market_price": 200.0},
            },
            "account_value": 100000.0,
            "cash_balance": 50000.0,
        }
        result = compute_wheel_risk(ctx)
        tickers = list(result["concentration"]["by_ticker"].keys())
        self.assertEqual(tickers, ["MSFT", "AAPL", "GOOGL"])

    def test_regression_high_concentration_warning(self):
        """High single-ticker concentration must trigger warning — regression guard."""
        ctx = {
            "positions": {"AAPL": {"position": 1000, "market_price": 200.0}},
            "account_value": 200000.0,
            "cash_balance": 10000.0,
        }
        result = compute_wheel_risk(ctx)
        conc = result["concentration"]["by_ticker"]["AAPL"]
        self.assertGreater(conc["pct_of_account"], 20.0)
        self.assertTrue(conc["warning"])
        self.assertTrue(any("AAPL" in w and "concentration" in w for w in result["warnings"]))

    def test_earnings_exposure_empty_when_no_options(self):
        result = compute_wheel_risk({})
        self.assertEqual(result["earnings_exposure"]["count"], 0)
        self.assertEqual(result["earnings_exposure"]["tickers_at_risk"], [])


class TestBuildEarningsExposure(unittest.TestCase):
    """Tests for _build_earnings_exposure window filter."""

    def test_no_short_options_returns_empty(self):
        from api.routes.wheel_risk import _build_earnings_exposure
        result = _build_earnings_exposure({}, db=MagicMock())
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["tickers_at_risk"], [])

    def test_no_db_returns_empty(self):
        from api.routes.wheel_risk import _build_earnings_exposure
        ctx = {"short_puts": {"AAPL": 1}}
        result = _build_earnings_exposure(ctx, db=None)
        self.assertEqual(result["count"], 0)

    def test_earnings_within_window_included(self):
        from api.routes.wheel_risk import _build_earnings_exposure
        from datetime import datetime, timedelta
        mock_db = MagicMock()
        future_date = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        mock_db.get_earnings_date.return_value = {"earnings_date": future_date}
        ctx = {"short_puts": {"AAPL": 1}}
        result = _build_earnings_exposure(ctx, db=mock_db)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["tickers_at_risk"][0]["ticker"], "AAPL")

    def test_earnings_beyond_window_excluded(self):
        from api.routes.wheel_risk import _build_earnings_exposure
        from datetime import datetime, timedelta
        mock_db = MagicMock()
        far_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        mock_db.get_earnings_date.return_value = {"earnings_date": far_date}
        ctx = {"short_puts": {"AAPL": 1}}
        result = _build_earnings_exposure(ctx, db=mock_db)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["tickers_at_risk"], [])

    def test_no_earnings_data_returns_empty(self):
        from api.routes.wheel_risk import _build_earnings_exposure
        mock_db = MagicMock()
        mock_db.get_earnings_date.return_value = None
        ctx = {"short_puts": {"AAPL": 1}}
        result = _build_earnings_exposure(ctx, db=mock_db)
        self.assertEqual(result["count"], 0)

    def test_mixed_window_and_beyond(self):
        from api.routes.wheel_risk import _build_earnings_exposure
        from datetime import datetime, timedelta
        mock_db = MagicMock()
        soon = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        far = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        mock_db.get_earnings_date.side_effect = lambda t: (
            {"earnings_date": soon} if t == "AAPL" else {"earnings_date": far}
        )
        ctx = {"short_puts": {"AAPL": 1, "MSFT": 1}}
        result = _build_earnings_exposure(ctx, db=mock_db)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["tickers_at_risk"][0]["ticker"], "AAPL")


if __name__ == "__main__":
    unittest.main()
