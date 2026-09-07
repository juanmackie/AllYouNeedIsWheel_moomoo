"""
Tests for api/routes/portfolio.py — portfolio API endpoints.
"""

import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


class TestPortfolioRoutes(unittest.TestCase):
    """Portfolio route handler tests using Flask test client."""

    def setUp(self):
        from api import create_app

        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def _mock_connection_available(self):
        mock_ps = MagicMock()
        mock_ps._ensure_connection.return_value = MagicMock()
        mock_ps.get_portfolio_summary.return_value = {
            "cash_balance": 50000,
            "account_value": 100000,
            "available_cash": 35000,
        }
        mock_ps.get_positions.return_value = []
        mock_ps.last_error = None
        return mock_ps

    @patch("api.routes.utils.probe_opend_status")
    def test_get_portfolio_opend_unavailable(self, mock_probe):
        mock_probe.return_value = {"status": "unavailable", "message": "Not connected"}
        response = self.client.get("/api/portfolio/")
        self.assertEqual(response.status_code, 503)
        data = json.loads(response.data)
        self.assertFalse(data["success"])
        self.assertIn("error_code", data)
        self.assertEqual(data["error_code"], "opend_unavailable")
        self.assertIn("opend_status", data)
        self.assertEqual(data["opend_status"]["status"], "unavailable")

    @patch("api.routes.utils.probe_opend_status")
    @patch("api.routes.portfolio.get_portfolio_service")
    def test_get_portfolio_success(self, mock_get_ps, mock_probe):
        mock_probe.return_value = {"status": "connected"}
        mock_ps = self._mock_connection_available()
        mock_get_ps.return_value = mock_ps

        response = self.client.get("/api/portfolio/")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn("cash_balance", data)
        self.assertEqual(data["source_policy"]["mode"], "broker_only")
        self.assertEqual(data["source_policy"]["source_of_truth"], "opend")

    @patch("api.routes.utils.probe_opend_status")
    @patch("api.routes.portfolio.get_portfolio_service")
    def test_get_positions_opend_unavailable(self, mock_get_ps, mock_probe):
        mock_probe.return_value = {"status": "unavailable", "message": "Not connected"}
        response = self.client.get("/api/portfolio/positions")
        self.assertEqual(response.status_code, 503)
        data = json.loads(response.data)
        self.assertFalse(data["success"])
        self.assertIn("error_code", data)
        self.assertEqual(data["error_code"], "opend_unavailable")

    @patch("api.routes.utils.probe_opend_status")
    @patch("api.routes.portfolio.get_portfolio_service")
    def test_get_positions_success(self, mock_get_ps, mock_probe):
        mock_probe.return_value = {"status": "connected"}
        mock_ps = self._mock_connection_available()
        mock_ps.get_positions.return_value = []
        mock_get_ps.return_value = mock_ps
        response = self.client.get("/api/portfolio/positions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Source-Policy"), "broker_only")
        self.assertEqual(response.headers.get("X-Source-Truth"), "opend")

    @patch("api.routes.utils.probe_opend_status")
    @patch("api.routes.portfolio.get_portfolio_service")
    def test_get_positions_invalid_type(self, mock_get_ps, mock_probe):
        mock_probe.return_value = {"status": "connected"}
        response = self.client.get("/api/portfolio/positions?type=INVALID")
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn("error", data)

    @patch("api.routes.utils.probe_opend_status")
    @patch("api.routes.portfolio.get_portfolio_service")
    def test_get_weekly_income_success(self, mock_get_ps, mock_probe):
        mock_probe.return_value = {"status": "connected"}
        mock_ps = self._mock_connection_available()
        mock_ps.get_weekly_option_income.return_value = {
            "positions": [],
            "total_income": 0,
            "positions_count": 0,
            "this_friday": "2026-05-15",
            "open_short_positions_count": 3,
            "open_short_contracts_count": 13,
            "open_short_total_income": 2450.0,
        }
        mock_get_ps.return_value = mock_ps
        response = self.client.get("/api/portfolio/weekly-income")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn("open_short_positions_count", data)
        self.assertIn("open_short_contracts_count", data)
        self.assertIn("open_short_total_income", data)

    @patch("api.routes.utils.probe_opend_status")
    @patch("api.routes.portfolio.get_portfolio_service")
    def test_get_weekly_income_error(self, mock_get_ps, mock_probe):
        mock_probe.return_value = {"status": "connected"}
        mock_ps = self._mock_connection_available()
        mock_ps.get_weekly_option_income.return_value = {"error": "Something failed"}
        mock_get_ps.return_value = mock_ps
        response = self.client.get("/api/portfolio/weekly-income")
        self.assertEqual(response.status_code, 500)


class TestRollPressureRoutes(unittest.TestCase):
    """Roll-pressure route tests (extracted module, F008)."""

    def setUp(self):
        from api import create_app

        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def test_roll_pressure_opend_unavailable(self):
        with patch("api.routes.utils.probe_opend_status", return_value={"status": "unavailable"}):
            response = self.client.get("/api/portfolio/roll-pressure")
            self.assertEqual(response.status_code, 503)


class TestAlertsRoutes(unittest.TestCase):
    """Alerts route tests (extracted module, F008)."""

    def setUp(self):
        from api import create_app

        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def test_alerts_opend_unavailable(self):
        with patch("api.routes.utils.probe_opend_status", return_value={"status": "unavailable"}):
            response = self.client.get("/api/portfolio/alerts")
            self.assertEqual(response.status_code, 503)


class TestPortfolioHistoryRoute(unittest.TestCase):
    """GET /api/portfolio/history — local snapshot store, no OpenD gate."""

    def setUp(self):
        from api import create_app

        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def test_history_database_unavailable(self):
        self.app.config["database"] = None
        response = self.client.get("/api/portfolio/history")
        self.assertEqual(response.status_code, 503)

    @patch("api.services.config.get_config", return_value={"portfolio_env": "SIMULATE", "account_id": ""})
    def test_history_success_and_source_policy(self, mock_get_config):
        mock_db = MagicMock()
        mock_db.get_portfolio_history.return_value = [
            {
                "run_id": "r1",
                "captured_at": "2026-08-20T15:00:00+00:00",
                "env": "SIMULATE",
                "account_id": "h1",
                "net_liquidation": 1000.0,
                "cash_available": 400.0,
                "cash_reserved_for_csp": 600.0,
                "cash_available_for_csp": 400.0,
                "broker_buying_power": 1000.0,
                "positions": [],
            },
            {
                "run_id": "r2",
                "captured_at": "2026-08-21T15:00:00+00:00",
                "env": "SIMULATE",
                "account_id": "h1",
                "net_liquidation": 1100.0,
                "cash_available": 440.0,
                "cash_reserved_for_csp": 660.0,
                "cash_available_for_csp": 440.0,
                "broker_buying_power": 1100.0,
                "positions": [],
            },
        ]
        self.app.config["database"] = mock_db

        response = self.client.get("/api/portfolio/history?limit=10")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["count"], 2)
        self.assertEqual(len(data["series"]), 2)
        self.assertEqual(data["series"][0]["net_liquidation"], 1000.0)  # oldest first
        self.assertEqual(data["source_policy"]["mode"], "broker_only")
        # Chart series uses the limited history; pace uses the full unbounded
        # history so the durable baseline never shifts with the chart limit (C07).
        self.assertEqual(mock_db.get_portfolio_history.call_count, 2)
        chunk = [dict(c.kwargs) for c in mock_db.get_portfolio_history.call_args_list]
        self.assertIn({"limit": 10, "env": "SIMULATE", "account_id": ""}, chunk)
        self.assertIn({"unbounded": True, "env": "SIMULATE", "account_id": ""}, chunk)

    def test_history_invalid_limit(self):
        mock_db = MagicMock()
        self.app.config["database"] = mock_db
        response = self.client.get("/api/portfolio/history?limit=abc")
        self.assertEqual(response.status_code, 400)

    @patch("api.services.config.get_config", return_value={"portfolio_env": "SIMULATE", "account_id": ""})
    def test_pace_goal_pinned_across_chart_limits(self, mock_get_config):
        """C07: the growth goal (baseline/target) must not move with the chart
        ``limit``. The chart series truncates, but pace is derived from the full
        unbounded history, so a small limit still reports the true baseline."""
        # 5 snapshots; only the first (oldest) is the true baseline.
        snaps = [
            {
                "run_id": f"r{i}",
                "captured_at": f"2026-07-2{i}T15:00:00+00:00",
                "env": "SIMULATE",
                "account_id": "h1",
                "net_liquidation": 1000.0 + 100 * i,
                "cash_available": 0.0,
                "cash_reserved_for_csp": 0.0,
                "cash_available_for_csp": 0.0,
                "broker_buying_power": 0.0,
                "positions": [],
            }
            for i in range(5)
        ]
        mock_db = MagicMock()

        # Limited call returns only the last ``limit`` snapshots (oldest-first slice).
        def fake_history(**kwargs):
            if kwargs.get("unbounded"):
                return snaps
            lim = kwargs.get("limit", 180)
            return snaps[-lim:]

        mock_db.get_portfolio_history.side_effect = fake_history
        self.app.config["database"] = mock_db

        small = json.loads(self.client.get("/api/portfolio/history?limit=2").data)
        large = json.loads(self.client.get("/api/portfolio/history?limit=100").data)
        self.assertEqual(small["count"], 2)
        self.assertEqual(large["count"], 5)
        self.assertEqual(
            small["pace"],
            large["pace"],
            "C07: pace/baseline must be independent of the chart limit",
        )
        self.assertEqual(small["pace"]["baseline_nav"], 1000.0)
        self.assertEqual(small["pace"]["target_nav"], 5000.0)


class TestScorePositionExitInputs(unittest.TestCase):
    """C02 — adapter-level proof that entry credit and earnings reach exit rules.

    These exercise api.services.portfolio_scoring.score_position directly so we
    can assert the produced exit verdict reacts to inputs that the old adapter
    dropped (avg_cost and earnings_info).
    """

    def _pos(self, **overrides):
        base = {
            "symbol": "AAPL231215P90",
            "option_type": "P",
            "strike": 90.0,
            "expiration": (datetime.now() + timedelta(days=30)).strftime("%Y%m%d"),
            "position": -1,
            "bid": 0.30,
            "ask": 0.40,
            "avg_cost": 2.00,
            "delta": -0.10,
            "theta": -0.01,
            "implied_volatility": 0.25,
        }
        base.update(overrides)
        return base

    def _score(self, pos, earnings_info=None):
        from api.services.portfolio_scoring import score_position

        conn = MagicMock()
        conn.get_stock_price.return_value = 100.0
        iv = MagicMock()
        iv.record_iv_data.return_value = None
        iv.get_iv_environment_score.return_value = (0.0, 50.0, "normal")
        iv.get_earnings_score_impact.return_value = (0.0, 0)
        iv.get_earnings_info.return_value = earnings_info or {}
        portfolio_context = {
            "positions": {},
            "cash_balance": 50000,
            "account_value": 100000,
            "short_puts": {"AAPL": 1},
            "short_calls": {},
            "vix_regime": {"regime": "normal", "vix": 20.0},
        }
        return score_position(pos, conn, portfolio_context, iv)

    def test_entry_credit_reaches_profit_take_rule(self):
        # Short PUT sold for $2.00 credit; mark now ~$0.35 -> ~82% captured.
        decision = self._score(self._pos())
        self.assertIsNotNone(decision)
        self.assertEqual(decision.exit_verdict, "TAKE_PROFIT")
        self.assertTrue(any("credit captured" in r for r in decision.exit_reasons))

    def test_entry_credit_unknown_does_not_take_profit(self):
        # Without avg_cost the profit-take rule cannot fire -> fall through to HOLD.
        decision = self._score(self._pos(avg_cost=0))
        self.assertIsNotNone(decision)
        self.assertNotEqual(decision.exit_verdict, "TAKE_PROFIT")
        self.assertEqual(decision.exit_verdict, "HOLD")

    def test_earnings_info_reaches_exit_rules(self):
        # Strike 97 -> only 3% OTM, with earnings 3 days out and 30 DTE.
        pos = self._pos(strike=97.0, avg_cost=0)
        decision = self._score(pos, earnings_info={"days_to_earnings": 3})
        self.assertIsNotNone(decision)
        self.assertEqual(decision.exit_verdict, "CLOSE")
        self.assertTrue(any("Earnings in 3d" in r for r in decision.exit_reasons))

    def test_missing_earnings_info_falls_through_to_hold(self):
        # Same 3% OTM position but no earnings signal -> earnings rule cannot fire.
        pos = self._pos(strike=97.0, avg_cost=0)
        decision = self._score(pos, earnings_info={})
        self.assertIsNotNone(decision)
        self.assertEqual(decision.exit_verdict, "HOLD")

    def test_dte_uses_us_market_clock_not_host_date(self):
        # C13: position DTE must be computed from the US market clock. Here the
        # mocked US market date is 2026-06-01 while the host could be a day ahead;
        # the DTE must be measured against the US date, not datetime.now().date().
        from datetime import date
        from unittest.mock import patch

        us_market_date = date(2026, 6, 1)
        pos = self._pos(
            expiration="20260701",  # 30 days after the US market date above
            avg_cost=0,
            strike=97.0,
        )
        with patch(
            "api.services.portfolio_scoring.market_now",
            return_value=datetime(us_market_date.year, us_market_date.month, us_market_date.day, 12, 0, 0),
        ):
            decision = self._score(pos, earnings_info={})
        self.assertIsNotNone(decision)
        self.assertEqual(decision.dte, 30)


if __name__ == "__main__":
    unittest.main()
