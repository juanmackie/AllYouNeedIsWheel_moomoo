"""Wheel run model + runner tests.

Covers: opaque identity, account resolution rules (explicit REAL identity,
ambiguity hard-fail, never first-account), snapshot tradeability gates,
failed-refresh preservation, and snapshot persistence.
"""

import copy as _copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from core.run_model import (
    RefreshAttempt,
    RunMetadata,
    WheelRunSnapshot,
    compute_signal_eligibility,
    recompute_effective_snapshot,
    resolve_coverage_truth,
    resolve_session_context,
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


class TestReadTimeEligibility(unittest.TestCase):
    """P1a: backend-computed read-time session context, coverage truth, and
    per-candidate copy mode. All pure and deterministic (no broker I/O)."""

    def setUp(self):
        from zoneinfo import ZoneInfo

        self.et = ZoneInfo("America/New_York")
        self.monday_10am_et = datetime(2026, 5, 25, 10, 0, tzinfo=self.et)  # scheduled open
        self.saturday_10am_et = datetime(2026, 5, 23, 10, 0, tzinfo=self.et)  # weekend -> closed
        self.now_utc = self.monday_10am_et.astimezone(timezone.utc)

    def _run(self, **overrides):
        run = {
            "run_id": "run-check-1",
            "market_state": "open",
            "status": "ready",
            "errors": [],
            "coverage_scanned": 2,
            "coverage_total": 2,
            "coverage_complete": True,
            "quote_fetched_at": {
                "AAPL": (self.now_utc - timedelta(seconds=30)).isoformat(),
                "TSLA": (self.now_utc - timedelta(seconds=40)).isoformat(),
            },
            "max_tradeable_age_sec": 300,
        }
        run.update(overrides)
        return run

    def _signal(self, **overrides):
        sig = {
            "ticker": "AAPL",
            "option_type": "PUT",
            "expiration": "20260619",
            "strike": 140.0,
            "recommended_contracts": 1,
            "copy_eligible": True,
            "chain_source": "broker",
        }
        sig.update(overrides)
        return sig

    def test_no_evidence_is_unknown_session(self):
        state, reasons = resolve_session_context(self._run(quote_fetched_at={}), self.monday_10am_et)
        self.assertEqual(state, "unknown")
        self.assertTrue(reasons)

    def test_scheduled_open_and_fresh_is_open(self):
        self.assertEqual(resolve_session_context(self._run(), self.monday_10am_et)[0], "open")

    def test_scheduled_open_but_stale_is_holiday_shortened(self):
        stale = {"AAPL": (self.now_utc - timedelta(hours=3)).isoformat()}
        self.assertEqual(
            resolve_session_context(self._run(quote_fetched_at=stale), self.monday_10am_et)[0], "holiday_shortened"
        )

    def test_weekend_is_closed(self):
        state = resolve_session_context(self._run(), self.saturday_10am_et)[0]
        self.assertEqual(state, "closed")

    def test_coverage_truth_classification(self):
        self.assertEqual(resolve_coverage_truth(self._run(), "closed")[0], "complete")
        partial, reasons = resolve_coverage_truth(
            self._run(coverage_scanned=1, coverage_total=3, coverage_complete=False), "open"
        )
        self.assertEqual(partial, "partial")
        self.assertTrue(reasons)
        self.assertEqual(
            resolve_coverage_truth(
                self._run(status="planning", coverage_scanned=0, coverage_total=5, coverage_complete=False), "closed"
            )[0],
            "planning_quota",
        )
        self.assertEqual(
            resolve_coverage_truth(self._run(coverage_total=0, coverage_complete=False), "open")[0], "unknown"
        )

    def _elig(self, session_state, coverage_truth="complete", quotes_fresh=True, **sig_overrides):
        return compute_signal_eligibility(
            self._signal(**sig_overrides),
            session_state=session_state,
            coverage_truth=coverage_truth,
            quotes_fresh=quotes_fresh,
            coverage_reasons=["coverage incomplete"],
            session_reasons=["no evidence"],
        )

    def test_live_intraday(self):
        mode, reasons = self._elig("open")
        self.assertEqual(mode, "live")
        self.assertEqual(reasons, [])

    def test_open_with_stale_quotes_is_review_only(self):
        mode, reasons = self._elig("open", quotes_fresh=False)
        self.assertEqual(mode, "review_only")
        self.assertTrue(any("stale" in r for r in reasons))

    def test_open_with_partial_coverage_is_review_only(self):
        self.assertEqual(self._elig("open", coverage_truth="partial")[0], "review_only")

    def test_closed_with_complete_coverage_is_staged(self):
        mode, _ = self._elig("closed")
        self.assertEqual(mode, "staged")

    def test_closed_with_quota_truncated_planning_is_review_only(self):
        # quota-truncated planning must demote to review_only even though the
        # market is closed (coverage truth distinguishes it from stageable).
        mode, reasons = self._elig("closed", coverage_truth="planning_quota")
        self.assertEqual(mode, "review_only")
        self.assertTrue(any("coverage" in r for r in reasons))

    def test_closed_with_persisted_fallback_source_is_review_only(self):
        mode, reasons = self._elig("closed", chain_source="persisted-broker")
        self.assertEqual(mode, "review_only")
        self.assertTrue(any("persisted" in r for r in reasons))

    def test_unknown_session_demotes_both_live_and_staged(self):
        self.assertEqual(self._elig("unknown")[0], "review_only")
        self.assertEqual(self._elig("unknown")[0], "review_only")

    def test_candidate_not_copy_eligible_is_review_only(self):
        mode, reasons = self._elig("open", copy_eligible=False)
        self.assertEqual(mode, "review_only")
        self.assertTrue(any("copy eligible" in r for r in reasons))

    def test_no_recommended_quantity_is_review_only(self):
        self.assertEqual(self._elig("open", recommended_contracts=0)[0], "review_only")

    def test_build_eligibility_view_attaches_and_does_not_mutate_persisted(self):
        snapshot = {
            "run": self._run(),
            "tradeable": True,
            "signals": [self._signal(), self._signal(ticker="TSLA", copy_eligible=False)],
        }
        import copy as _copy

        persisted = _copy.deepcopy(snapshot)
        view = recompute_effective_snapshot(snapshot, self.now_utc, self.monday_10am_et)
        self.assertEqual(view["eligibility"]["session"]["state"], "open")
        self.assertEqual(view["eligibility"]["coverage"]["truth"], "complete")
        self.assertEqual(view["signals"][0]["eligibility"]["mode"], "live")
        self.assertEqual(view["signals"][1]["eligibility"]["mode"], "review_only")
        # persisted snapshot untouched
        self.assertEqual(persisted["signals"][0], snapshot["signals"][0])
        self.assertNotIn("eligibility", snapshot["signals"][0])

    def test_build_eligibility_view_closed_session_styles_candidate_staged(self):
        snapshot = {
            "run": self._run(status="planning", market_state="closed"),
            "tradeable": False,
            "signals": [self._signal()],
        }
        view = recompute_effective_snapshot(snapshot, self.now_utc, self.saturday_10am_et)
        self.assertEqual(view["eligibility"]["session"]["state"], "closed")
        self.assertEqual(view["signals"][0]["eligibility"]["mode"], "staged")

    def _candidate(self, option_type="PUT", **overrides):
        cand = {
            "ticker": "AAPL",
            "option_type": option_type,
            "expiration": "20260619",
            "strike": 140.0,
            "recommended_contracts": 1,
            "copy_eligible": True,
            "chain_source": "broker",
            "quote_fetched_at_utc": (self.now_utc - timedelta(seconds=45)).isoformat(),
            "max_contracts": 2,
            "cash_required": 12000.0,
        }
        cand.update(overrides)
        return cand

    def _lanes_snapshot(self, now_et=None):
        return {
            "run": self._run(),
            "tradeable": True,
            "signals": [self._candidate()],
            "csp_picks": [self._candidate(), self._candidate(ticker="MSFT", strike=200.0, cash_required=18000.0)],
            "cc_decisions": [self._candidate(option_type="CALL", strike=160.0, cash_required=None, max_contracts=3)],
            "roll_decisions": [{"ticker": "NVDA"}],
        }

    def test_build_eligibility_view_surfaces_outside_shortlist_lanes(self):
        snapshot = self._lanes_snapshot()
        persisted = _copy.deepcopy(snapshot)
        view = recompute_effective_snapshot(snapshot, self.now_utc, self.monday_10am_et)
        # Every copy-addressable lane carries the same read-time eligibility.
        for lane in ("signals", "csp_picks", "cc_decisions"):
            for candidate in view[lane]:
                self.assertIn("eligibility", candidate)
                self.assertEqual(candidate["eligibility"]["mode"], "live")
                self.assertIn("quote_age_sec", candidate)
        # roll_decisions are a position-management panel, not candidates.
        self.assertNotIn("eligibility", view["roll_decisions"][0])
        # Persisted snapshot stays immutable (no read-time fields leak back).
        for lane in ("signals", "csp_picks", "cc_decisions"):
            self.assertEqual(persisted[lane], snapshot[lane])
            self.assertNotIn("eligibility", snapshot[lane][0])
            self.assertNotIn("quote_age_sec", snapshot[lane][0])

    def test_read_time_capital_view_collateral_and_available_shares(self):
        view = recompute_effective_snapshot(self._lanes_snapshot(), self.now_utc, self.monday_10am_et)
        csp = view["csp_picks"][0]
        self.assertEqual(csp["collateral"], 12000.0)
        self.assertIsNone(csp["available_shares"])
        self.assertAlmostEqual(csp["quote_age_sec"], 45.0, delta=1.0)
        cc = view["cc_decisions"][0]
        self.assertEqual(cc["available_shares"], 300.0)  # 3 contracts x 100
        self.assertIsNone(cc["collateral"])
        # Unknown quote timestamps stay None (em-dash), never 0.
        stale = self._lanes_snapshot()
        stale["csp_picks"][1]["quote_fetched_at_utc"] = ""
        view = recompute_effective_snapshot(stale, self.now_utc, self.monday_10am_et)
        self.assertIsNone(view["csp_picks"][1]["quote_age_sec"])


class TestActiveWatchlistSnapshot(unittest.TestCase):
    """The ACTIVE WATCHLIST foot payload is persisted with the run snapshot:
    the tickers actually evaluated, the group name + status, the last successful
    sync (stamped only on a healthy group), per-ticker scan status, unsupported
    symbols, and the holdings checked for covered calls. Never carries archived
    config/app additions."""

    def _runner(self):
        return WheelRunner(
            MagicMock(), MagicMock(), {"portfolio_env": "SIMULATE", "account_id": ""}, max_tradeable_age_sec=120
        )

    def _build(self, active_watchlist, tradeable_run=True):
        result = {
            "generated_at": utc_now_iso(),
            "scan_coverage": {"scanned": 2, "total": 2, "complete": True},
            "errors": [],
            "state": None if tradeable_run else "planning",
            "watchlist_origins": {"AAPL": ["moomoo"], "MSFT": ["moomoo"]},
            "quote_fetched_at": {"AAPL": utc_now_iso(), "MSFT": utc_now_iso()},
            "signals": [],
            "watchlist_csps": {"signals": []},
            "covered_calls": {"signals": []},
            "active_watchlist": active_watchlist,
        }
        with patch("core.wheel_runner.is_market_open", return_value=True):
            return self._runner()._build_snapshot("REAL", "acc1", utc_now_iso(), result, {"account_value": 10000}, [])

    def _active_watchlist(self, status="ok", ticker_status=None):
        statuses = ticker_status or {"AAPL": "scanned", "MSFT": "scanned"}
        return {
            "group_name": "My Watchlist",
            "group_status": status,
            "explanation": "" if status == "ok" else "group unavailable",
            "groups_available": ["My Watchlist"],
            "fetched_at": "2026-09-10T12:00:00+00:00",
            "last_successful_sync": "",
            "tickers": [
                {"symbol": s, "raw_code": f"US.{s}", "status": st, "quote_fetched_at": ""} for s, st in statuses.items()
            ],
            "unsupported": [],
            "holdings_checked": ["AAPL", "NVDA"],
        }

    def test_snapshot_dict_roundtrip_includes_active_watchlist(self):
        aw = self._active_watchlist()
        snap = self._build(aw)
        payload = snap.to_dict()
        self.assertEqual(payload["active_watchlist"]["group_name"], "My Watchlist")
        self.assertEqual(len(payload["active_watchlist"]["tickers"]), 2)
        self.assertEqual(payload["active_watchlist"]["holdings_checked"], ["AAPL", "NVDA"])

    def test_last_successful_sync_stamped_only_when_group_ok(self):
        healthy = self._build(self._active_watchlist(status="ok"))
        self.assertTrue(healthy.active_watchlist["last_successful_sync"])
        broken = self._build(self._active_watchlist(status="missing_group"))
        self.assertEqual(broken.active_watchlist["last_successful_sync"], "")

    def test_error_tickers_populate_partial_symbols(self):
        snap = self._build(self._active_watchlist(ticker_status={"AAPL": "error", "MSFT": "scanned"}))
        self.assertEqual(snap.run.partial_symbols, ("AAPL",))
        self.assertNotIn("MSFT", snap.run.partial_symbols)

    def test_planning_run_never_looks_like_successful_sync(self):
        snap = self._build(self._active_watchlist(status="empty_group"), tradeable_run=False)
        self.assertEqual(snap.run.status, "planning")
        self.assertEqual(snap.active_watchlist["last_successful_sync"], "")
        self.assertTrue(snap.active_watchlist["explanation"])


if __name__ == "__main__":
    unittest.main()
