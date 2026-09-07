"""Wheel run model + runner tests.

Covers: opaque identity, account resolution rules (explicit REAL identity,
ambiguity hard-fail, never first-account), snapshot tradeability gates,
failed-refresh preservation, and snapshot persistence.
"""

import unittest
from unittest.mock import MagicMock

from core.run_model import (
    RefreshAttempt,
    RunMetadata,
    WheelRunSnapshot,
    recompute_effective_snapshot,
    utc_now_iso,
)
from core.wheel_runner import WheelRunner, opaque_account_id, resolve_account


def _make_snapshot(status="ready", coverage_complete=True, quote_age_sec=10, errors=()):
    fetched = utc_now_iso()
    run = RunMetadata(
        run_id="run1",
        generated_at=fetched,
        published_at=fetched,
        env="REAL",
        account_id="abc123",
        preset_key="balanced",
        preset_version=1,
        market_state="open",
        status=status,
        errors=tuple(errors),
        quote_fetched_at={"AAPL": fetched},
        max_tradeable_age_sec=120,
        coverage_scanned=1 if coverage_complete else 0,
        coverage_total=1,
    )
    return WheelRunSnapshot(
        run=run,
        portfolio={},
        csp_picks=(),
        cc_decisions=(),
        roll_decisions=(),
        rejected=(),
        preset={},
        watchlist_origins={},
    )


class TestOpaqueIdentity(unittest.TestCase):
    def test_opaque_id_is_short_hash(self):
        self.assertEqual(len(opaque_account_id("123456789")), 12)
        self.assertEqual(opaque_account_id("123456789"), opaque_account_id("123456789"))
        self.assertNotEqual(opaque_account_id("123456789"), "123456789")


class TestAccountResolution(unittest.TestCase):
    def _conn(self, accounts):
        conn = MagicMock()
        conn._get_available_accounts.return_value = accounts
        return conn

    def _acc(self, acc_id, trd_env):
        return {"acc_id": acc_id, "trd_env": trd_env}

    def test_real_requires_explicit_identity(self):
        conn = self._conn([self._acc("ACC1", "REAL")])
        with self.assertRaises(ValueError):
            resolve_account(conn, {"portfolio_env": "REAL", "account_id": ""})

    def test_real_mismatch_fails(self):
        conn = self._conn([self._acc("ACC1", "REAL")])
        with self.assertRaises(ValueError):
            resolve_account(conn, {"portfolio_env": "REAL", "account_id": "ACC9"})

    def test_real_explicit_match(self):
        conn = self._conn([self._acc("ACC1", "REAL"), self._acc("ACC2", "REAL")])
        self.assertEqual(resolve_account(conn, {"portfolio_env": "REAL", "account_id": "ACC2"}), "ACC2")

    def test_simulate_multiple_ambiguous_fails(self):
        conn = self._conn([self._acc("P1", "SIMULATE"), self._acc("P2", "SIMULATE")])
        with self.assertRaises(ValueError):
            resolve_account(conn, {"portfolio_env": "SIMULATE", "account_id": ""})

    def test_simulate_single_auto_resolve(self):
        conn = self._conn([self._acc("P1", "SIMULATE")])
        self.assertEqual(resolve_account(conn, {"portfolio_env": "SIMULATE", "account_id": ""}), "P1")

    # S02: account-resolution errors must never leak raw account ids into
    # persisted/public error strings (the runner persists str(exc) to /api/run).
    def _assert_redacted(self, msg):
        for raw in ("ACC1", "ACC2", "ACC9", "P1", "P2"):
            self.assertNotIn(raw, msg, f"raw account id {raw!r} leaked into error")

    def test_real_mismatch_error_is_redacted(self):
        conn = self._conn([self._acc("ACC1", "REAL"), self._acc("ACC2", "REAL")])
        with self.assertRaises(ValueError) as ctx:
            resolve_account(conn, {"portfolio_env": "REAL", "account_id": "ACC9"})
        self._assert_redacted(str(ctx.exception))
        self.assertIn("2 available REAL account", str(ctx.exception))

    def test_real_missing_configured_error_is_redacted(self):
        conn = self._conn([self._acc("ACC1", "REAL")])
        with self.assertRaises(ValueError) as ctx:
            resolve_account(conn, {"portfolio_env": "REAL", "account_id": ""})
        self.assertNotIn("ACC1", str(ctx.exception))

    def test_simulate_configured_mismatch_error_is_redacted(self):
        conn = self._conn([self._acc("P1", "SIMULATE")])
        with self.assertRaises(ValueError) as ctx:
            resolve_account(conn, {"portfolio_env": "SIMULATE", "account_id": "P9"})
        self._assert_redacted(str(ctx.exception))
        self.assertIn("1 available SIMULATE account", str(ctx.exception))

    def test_simulate_ambiguous_error_is_redacted(self):
        conn = self._conn([self._acc("P1", "SIMULATE"), self._acc("P2", "SIMULATE")])
        with self.assertRaises(ValueError) as ctx:
            resolve_account(conn, {"portfolio_env": "SIMULATE", "account_id": ""})
        self._assert_redacted(str(ctx.exception))


class TestSnapshotTradeability(unittest.TestCase):
    def test_ready_fresh_complete_is_tradeable(self):
        self.assertTrue(_make_snapshot().tradeable)

    def test_not_ready_is_not_tradeable(self):
        for status in ("partial", "planning", "stale"):
            self.assertFalse(_make_snapshot(status=status).tradeable, status)

    def test_incomplete_coverage_not_tradeable(self):
        self.assertFalse(_make_snapshot(coverage_complete=False).tradeable)

    def test_errors_not_tradeable(self):
        self.assertFalse(_make_snapshot(errors=("boom",)).tradeable)

    def test_read_time_staleness_does_not_mutate_snapshot_payload(self):
        from datetime import datetime, timedelta, timezone

        fetched = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
        snapshot = _make_snapshot().to_dict()
        snapshot["run"]["quote_fetched_at"] = {"AAPL": fetched}
        effective = recompute_effective_snapshot(snapshot, datetime.now(timezone.utc))
        self.assertFalse(effective["tradeable"])
        self.assertEqual(effective["effective_status"], "stale")
        self.assertEqual(snapshot["run"]["status"], "ready")
        self.assertEqual(snapshot["tradeable"], True)

    def test_stale_quotes_not_tradeable(self):
        from datetime import datetime, timezone

        old_ts = datetime.now(timezone.utc).timestamp() - 300
        fetched = datetime.fromtimestamp(old_ts, tz=timezone.utc).isoformat()
        run = RunMetadata(
            run_id="r",
            generated_at=fetched,
            published_at=fetched,
            env="REAL",
            account_id="a",
            preset_key="balanced",
            preset_version=1,
            market_state="open",
            status="ready",
            quote_fetched_at={"AAPL": fetched},
            max_tradeable_age_sec=120,
            coverage_scanned=1,
            coverage_total=1,
        )
        snap = WheelRunSnapshot(
            run=run,
            portfolio={},
            csp_picks=(),
            cc_decisions=(),
            roll_decisions=(),
            rejected=(),
            preset={},
            watchlist_origins={},
        )
        self.assertFalse(snap.tradeable)


class TestRunnerFailurePreservesLastSnapshot(unittest.TestCase):
    def test_failed_refresh_keeps_previous_snapshot(self):
        db = MagicMock()
        service = MagicMock()
        runner = WheelRunner(db, service, {"portfolio_env": "SIMULATE", "account_id": ""}, max_tradeable_age_sec=120)

        conn = MagicMock()
        conn._get_available_accounts.return_value = [{"acc_id": "P1", "trd_env": "SIMULATE"}]
        service._ensure_connection.return_value = conn
        service._get_portfolio_context.return_value = {}

        engine = MagicMock()
        engine.get_top_recommendations.return_value = {
            "error": "OpenD disconnected mid-scan",
        }
        service.recommendation_engine = engine

        with self.assertRaises(RuntimeError):
            runner.refresh()

        # Last snapshot must remain None; failed attempt recorded with no run_id.
        self.assertIsNone(runner.latest())
        self.assertTrue(db.save_refresh_attempt.called)
        saved = [c.args[0] for c in db.save_refresh_attempt.call_args_list]
        self.assertEqual(saved[-1].state, "failed")
        self.assertIsNone(saved[-1].run_id)


class TestRunnerRollDiagnosticsInjection(unittest.TestCase):
    """F-H1 regression: core must not import api; roll diagnostics arrive via
    an injected provider callable."""

    def test_wheel_runner_source_has_no_api_import(self):
        import inspect

        from core import wheel_runner

        source = inspect.getsource(wheel_runner)
        self.assertNotIn("from api", source)
        self.assertNotRegex(source, r"\bimport api\b")

    def test_default_provider_returns_empty(self):
        db = MagicMock()
        service = MagicMock()
        runner = WheelRunner(db, service, {"portfolio_env": "SIMULATE", "account_id": ""})
        self.assertEqual(runner._build_roll_decisions({"positions": {}}, MagicMock()), [])

    def test_injected_provider_is_used(self):
        db = MagicMock()
        service = MagicMock()
        provider = MagicMock(return_value=[{"ticker": "AAPL"}])
        runner = WheelRunner(
            db,
            service,
            {"portfolio_env": "SIMULATE", "account_id": ""},
            roll_diagnostics_provider=provider,
        )
        ctx = {"positions": {"AAPL": {"security_type": "OPT"}}}
        conn = MagicMock()
        result = runner._build_roll_decisions(ctx, conn)
        provider.assert_called_once_with(ctx, conn)
        self.assertEqual(result, [{"ticker": "AAPL"}])


class TestAttemptModel(unittest.TestCase):
    def test_attempt_roundtrip_dict(self):
        a = RefreshAttempt(attempt_id="a1", run_id=None, state="queued")
        d = a.to_dict()
        self.assertEqual(d["state"], "queued")
        self.assertEqual(d["progress"], 0.0)


if __name__ == "__main__":
    unittest.main()
