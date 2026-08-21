"""
Logging coverage tests.

Verifies that every important code path in the application produces
the expected log messages at the appropriate level.

These tests use unittest.TestCase with assertLogs for deterministic log capture
without requiring pytest or its caplog fixture.
"""

import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


class TestScoringFactorsLogging(unittest.TestCase):
    """core/scoring_factors.py — logging for premium velocity and sub-scores."""

    def test_premium_velocity_logs_debug(self):
        from core.scoring_factors import premium_velocity_per_day

        with self.assertLogs("core.scoring_factors", level="DEBUG") as log:
            result = premium_velocity_per_day(500.0, 37)
            self.assertAlmostEqual(result, 500.0 / 37)
            self.assertTrue(any("premium_velocity" in msg for msg in log.output))

    def test_premium_velocity_no_log_on_zero_premium(self):
        from core.scoring_factors import premium_velocity_per_day

        result = premium_velocity_per_day(0, 37)
        self.assertEqual(result, 0.0)


class TestWheelDecisionLogging(unittest.TestCase):
    """core/wheel_decision.py — logging for scoring decisions."""

    def test_create_failed_decision_logs_info(self):
        from core.wheel_decision import _create_failed_decision

        with self.assertLogs("core.wheel_decision", level="INFO") as log:
            decision = _create_failed_decision(
                "TEST",
                "PUT",
                100.0,
                "20261218",
                "Insufficient cash",
                ["no_cash_fit"],
            )
            self.assertEqual(decision.ticker, "TEST")
            self.assertIn("Hard blocker", log.output[0])
            self.assertIn("TEST", log.output[0])

    def test_score_contract_success_logs_info(self):
        from core.wheel_decision import score_contract

        profile = {
            "ideal_spread_pct": 12,
            "ideal_open_interest": 500,
            "ideal_volume": 1000,
            "target_iv_adjusted": 50,
            "target_delta": 0.30,
            "delta_tolerance": 0.12,
            "preferred_dte": 37,
            "target_capital_efficiency": 100,
            "target_theta_delta_ratio": 0.005,
            "min_mid_price": 0.05,
            "max_spread_pct": 60,
            "min_premium_per_contract": 10,
            "min_open_interest": 10,
            "min_volume": 1,
            "default_otm_pct": 10,
            "profile_type": "monthly",
        }
        portfolio = {
            "positions": {},
            "cash_balance": 50000,
            "available_cash": 50000,
            "cash_available_for_csp": 50000,
            "account_value": 100000,
            "vix_regime": {"vix": 15, "regime": "normal"},
        }
        option = {
            "strike": 95.0,
            "expiration": (datetime.now() + timedelta(days=37)).strftime("%Y%m%d"),
            "option_type": "PUT",
            "bid": 1.50,
            "ask": 1.60,
            "delta": -0.30,
            "gamma": 0.02,
            "theta": -0.05,
            "vega": 0.10,
            "implied_volatility": 0.25,
            "open_interest": 500,
            "volume": 200,
        }
        with self.assertLogs("core.wheel_decision", level="INFO") as log:
            result = score_contract(
                "TEST",
                option,
                100.0,
                profile,
                portfolio,
                iv_status_str="normal",
                iv_rank=0.5,
            )
            self.assertIsNotNone(result)
            self.assertIn("score_contract", log.output[-1])
            self.assertIn("TEST", log.output[-1])


class TestUtilsLogging(unittest.TestCase):
    """core/utils.py — logging for market status."""

    @patch("core.utils.datetime")
    def test_is_market_open_logs_on_change(self, mock_dt):
        import core.utils as utils
        from core.utils import is_market_open

        utils._last_market_status = None

        class FakeDatetime:
            @staticmethod
            def now(tz):
                class FakeNow:
                    def weekday(self):
                        return 0

                    def replace(self, **kw):
                        return self

                    def __le__(self, other):
                        return isinstance(other, FakeNow)

                    def __ge__(self, other):
                        return isinstance(other, FakeNow)

                    def strftime(self, fmt):
                        return "09:30"

                return FakeNow()

        mock_dt.now = FakeDatetime.now
        mock_dt.MARKET_TIMEZONE = "America/New_York"
        utils.MARKET_TIMEZONE = "America/New_York"

        with self.assertLogs("autotrader.utils", level="INFO") as log:
            is_market_open()
            self.assertTrue(any("Market" in msg for msg in log.output))


class TestGreeksLogging(unittest.TestCase):
    """core/greeks.py — logging for BS Greeks computation."""

    def test_enrich_option_with_greeks_logs_info(self):
        from core.greeks import enrich_option_with_greeks

        option = {
            "strike": 100.0,
            "expiration": "20260918",
            "option_type": "CALL",
            "implied_volatility": 0.30,
            "delta": 0,
            "gamma": 0,
            "theta": 0,
            "vega": 0,
        }
        with self.assertLogs("core.greeks", level="INFO") as log:
            result = enrich_option_with_greeks(option, stock_price=105.0)
            self.assertIn("delta", result)
            self.assertGreater(abs(result["delta"]), 0.001)
            self.assertTrue(any("Computed BS Greeks" in msg for msg in log.output))


class TestDatabaseLogging(unittest.TestCase):
    """db/database.py — logging for DB initialization and transactions."""

    @patch("db.database.pooled_connection")
    @patch("db.database.create_tables")
    @patch("db.database.migrate_database")
    @patch("db.database.register_pool_handle")
    def test_init_logs_info(self, mock_reg, mock_migrate, mock_create, mock_pool):
        mock_conn = MagicMock()
        mock_pool.return_value.__enter__.return_value = mock_conn
        # Avoid actual repository init side-effects
        with (
            patch("db.database.IVRepository"),
            patch("db.database.EarningsRepository"),
            patch("db.database.TradeEventsRepository"),
            patch("db.database.OptionChainRepository"),
        ):
            with self.assertLogs("db.database", level="INFO") as log:
                from db.database import OptionsDatabase

                OptionsDatabase(db_name=":memory:")
                self.assertTrue(any("OptionsDatabase initialized" in msg for msg in log.output))


class TestApiLogging(unittest.TestCase):
    """api/__init__.py — logging for request lifecycle and health checks."""

    def _build_health_app(self, env=None, config=None):
        env = env or {}
        config = config or {}
        with patch.dict(os.environ, env, clear=True), patch("api._register_services"):
            from api import create_app

            return create_app({"TESTING": True, **config})

    def test_health_request_logs_completion(self):
        mock_tvscreener = MagicMock()
        mock_tvscreener._ensure_initialized.return_value = True
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.execute.return_value = None

        with (
            patch("api.get_service", return_value=mock_tvscreener),
            patch("api.sqlite3.connect", return_value=mock_conn),
            patch("api.probe_opend_status", return_value={"status": "connected"}),
        ):
            app = self._build_health_app()

            with self.assertLogs("autotrader.api", level="INFO") as log:
                with app.test_client() as client:
                    resp = client.get("/health", headers={"X-Request-Id": "req-123"})

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any("request completed method=GET path=/health status=200" in msg for msg in log.output))

    def test_health_probe_failures_log_warnings(self):
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.execute.side_effect = RuntimeError("db down")
        database = MagicMock()
        database.db_path = ":memory:"

        with (
            patch("api.sqlite3.connect", return_value=mock_conn),
            patch("api.probe_opend_status", side_effect=RuntimeError("opend down")),
        ):
            app = self._build_health_app(
                config={"database": database, "connection_config": {"host": "127.0.0.1", "port": 11111}}
            )

            with self.assertLogs("autotrader.api", level="WARNING") as log:
                with app.test_client() as client:
                    resp = client.get("/health")
                    data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["database"], "error")
        self.assertEqual(data["opend"], "error")
        self.assertTrue(any("Health check database probe failed" in msg for msg in log.output))
        self.assertTrue(any("Health check OpenD probe failed" in msg for msg in log.output))


class TestRepositoriesLogging(unittest.TestCase):
    """db/earnings_repository.py, db/iv_repository.py — logging for data persistence."""

    @patch("db.earnings_repository.pooled_connection")
    def test_earnings_save_logs_info(self, mock_pool):
        mock_conn = MagicMock()
        mock_pool.return_value.__enter__.return_value = mock_conn
        from db.earnings_repository import EarningsRepository

        repo = EarningsRepository(":memory:")
        with self.assertLogs("db.earnings", level="INFO") as log:
            result = repo.save_earnings_date("TEST", "2026-07-18")
            self.assertTrue(result)
            self.assertTrue(any("Saved earnings date" in msg for msg in log.output))

    @patch("db.iv_repository.pooled_connection")
    def test_iv_save_logs_debug(self, mock_pool):
        mock_conn = MagicMock()
        mock_pool.return_value.__enter__.return_value = mock_conn
        from db.iv_repository import IVRepository

        repo = IVRepository(":memory:")
        with self.assertLogs("db.iv_history", level="DEBUG") as log:
            result = repo.save_iv_data("TEST", 0.30)
            self.assertTrue(result)
            self.assertTrue(any("Saved IV data" in msg for msg in log.output))


class TestServiceConnectionLogging(unittest.TestCase):
    """api/services/*.py — logging for broker connection and portfolio failures."""

    def test_options_service_reconnect_logs_info(self):
        from api.services.options_service import OptionsService

        service = OptionsService.__new__(OptionsService)
        service.config = {
            "host": "127.0.0.1",
            "port": 11111,
            "readonly": True,
            "portfolio_env": "SIMULATE",
            "security_firm": "FUTUAU",
            "broker_cache_after_hours": True,
        }
        service.connection = MagicMock()
        service.connection.is_connected.return_value = False
        service.connection.connect.return_value = True
        service.portfolio_service = None

        with self.assertLogs("api.services.options", level="INFO") as log:
            result = service._ensure_connection()

        self.assertIs(result, service.connection)
        self.assertTrue(any("Existing connection found but disconnected" in msg for msg in log.output))
        self.assertTrue(any("Successfully reconnected to moomoo OpenD" in msg for msg in log.output))

    def test_options_service_new_connection_logs_success(self):
        from api.services.options_service import OptionsService

        service = OptionsService.__new__(OptionsService)
        service.config = {
            "host": "127.0.0.1",
            "port": 11111,
            "readonly": True,
            "portfolio_env": "SIMULATE",
            "security_firm": "FUTUAU",
            "broker_cache_after_hours": True,
        }
        disconnected = MagicMock()
        disconnected.is_connected.return_value = False
        disconnected.connect.return_value = False
        service.connection = disconnected
        service.portfolio_service = None

        new_conn = MagicMock()
        new_conn.connect.return_value = True

        with patch("core.connection.MoomooConnection", return_value=new_conn) as mock_moomoo:
            with self.assertLogs("api.services.options", level="INFO") as log:
                result = service._ensure_connection()

        self.assertIs(result, new_conn)
        self.assertTrue(any("Failed to reconnect, will create new connection" in msg for msg in log.output))
        self.assertTrue(any("Creating new moomoo connection" in msg for msg in log.output))
        self.assertTrue(any("Successfully connected to moomoo OpenD" in msg for msg in log.output))
        mock_moomoo.assert_called_once_with(
            host="127.0.0.1",
            port=11111,
            readonly=True,
            account_id=None,
            portfolio_env="SIMULATE",
            security_firm="FUTUAU",
            broker_cache_after_hours=True,
        )

    def test_portfolio_service_get_positions_logs_errors(self):
        from api.services.portfolio_service import PortfolioService

        service = PortfolioService.__new__(PortfolioService)
        service.last_error = None
        service._get_cached_portfolio = MagicMock(side_effect=RuntimeError("portfolio down"))

        with self.assertLogs("api.services.portfolio", level="ERROR") as log:
            result = service.get_positions()

        self.assertIsNone(result)
        self.assertEqual(service.last_error, "Error getting positions: portfolio down")
        self.assertTrue(any("Error getting positions: portfolio down" in msg for msg in log.output))


if __name__ == "__main__":
    unittest.main()
