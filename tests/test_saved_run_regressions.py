"""
Saved-run regressions.

Reproduces the canonical saved run — 3 CSP picks, 4 covered-call decisions, 25
rejection explanations — end-to-end through the real engine, the immutable
snapshot builder, and the /api/run route, then pins the surrounding contracts
those lanes agreed on:

1. Both strategy sections populate and every rejection explanation is
   accessible (blocked_signals is never truncated to the top-3 shortlist).
2. Copy revalidation works for a candidate that is only in csp_picks /
   cc_decisions (outside the combined shortlist).
3. A failed refresh retains the previous snapshot (original generated_at /
   published_at) while the failure reason stays visible on the attempt.
4. The premium headline (total_income) equals the sum of the Friday table
   rows from the same income response.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from flask import Flask

from api.services.recommendations import RecommendationEngine

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

# ─────────────────────────────────────────────────────────────────────────────
# Saved-run payload builders
# ─────────────────────────────────────────────────────────────────────────────


def _csp_candidate(ticker, strike, bid, capital_velocity_per_day):
    """Deterministic qualified CSP candidate shaped like the engine's fetch
    output (recommendations._fetch_watchlist_ticker_csp -> scored candidate)."""
    return {
        "ticker": ticker,
        "stock_price": strike,
        "option_type": "PUT",
        "strike": strike,
        "expiration": "20260619",
        "dte": 21,
        "mid_price": bid + 0.05,
        "premium_per_contract": round(bid * 100, 2),
        "bid": bid,
        "ask": round(bid + 0.10, 2),
        "otm_pct": 5.0,
        "delta": -0.20,
        "implied_volatility": 0.30,
        "open_interest": 500,
        "volume": 100,
        "quality_tier": "qualified",
        "cash_required": strike * 100,
        "recommended_contracts": 1,
        "max_contracts": 1,
        "copy_eligible": True,
        "profile_type": "monthly",
        "research_only": False,
        "warnings": [],
        "quote_fetched_at_utc": "2026-05-25T15:59:00+00:00",
        "wheel_decision": {
            "confidence_score": 100,
            "capital_velocity_per_day": capital_velocity_per_day,
            "copy_eligible": True,
            "quote_fetched_at_utc": "2026-05-25T15:59:00+00:00",
        },
    }


def _cc_call(ticker, strike, capital_velocity_per_day):
    """Deterministic covered-call contract returned by the OTM options fetch."""
    return {
        "strike": strike,
        "expiration": "20260619",
        "bid": 2.0,
        "ask": 2.10,
        "last": 2.05,
        "delta": 0.20,
        "implied_volatility": 0.30,
        "open_interest": 500,
        "volume": 100,
        "dte": 21,
        "option_type": "CALL",
        "bid_premium_per_contract": 200.0,
        "capital_velocity_per_day": capital_velocity_per_day,
        "copy_eligible": True,
        "recommended_contracts": 1,
        "quote_fetched_at_utc": "2026-05-25T15:59:00+00:00",
        "wheel_decision": {
            "confidence_score": 100,
            "capital_velocity_per_day": capital_velocity_per_day,
            "copy_eligible": True,
            "quote_fetched_at_utc": "2026-05-25T15:59:00+00:00",
        },
    }


def _skip_diagnostic(ticker, idx):
    """Distinct rejection explanation per rejected ticker."""
    return {
        "_skip_diagnostic": True,
        "ticker": ticker,
        "reason_code": f"reject_{idx:02d}",
        "reason_text": f"{ticker}: no OTM strike fits the active preset (reason {idx:02d})",
    }


# ─────────────────────────────────────────────────────────────────────────────
# The canonical saved run: 3 CSP + 4 CC + 25 rejected, through the real engine
# ─────────────────────────────────────────────────────────────────────────────


class TestSavedRunReproduction(unittest.TestCase):
    """End-to-end reproduction of the saved run at 3/4/25 counts."""

    PICK_TICKERS = ["CSP-A", "CSP-B", "CSP-C"]
    REJECTED_TICKERS = [f"REJ{i:02d}" for i in range(1, 26)]
    POSITION_TICKERS = ["AAA", "BBB", "CCC", "DDD"]

    def setUp(self):
        self.mock_connection_provider = MagicMock()
        self.mock_config_provider = MagicMock()
        self.mock_config_provider.config = {"cash_reserve_enabled": True}
        self.mock_db = MagicMock()
        self.mock_iv_earnings = MagicMock()
        self.mock_iv_earnings.get_iv_environment_score.return_value = (0, 0.5, "normal")
        self.mock_iv_earnings.get_earnings_score_impact.return_value = (0, None)
        self.mock_iv_earnings.get_earnings_info.return_value = {}

        self.mock_conn = MagicMock()
        self.mock_conn.get_stock_price.return_value = 200.0
        self.mock_connection_provider._ensure_connection.return_value = self.mock_conn

        self.mock_watchlist_manager = MagicMock()
        self.mock_watchlist_manager.get_effective_watchlist.return_value = self.PICK_TICKERS + self.REJECTED_TICKERS

        self.positions = {}
        for ticker in self.POSITION_TICKERS:
            self.positions[ticker] = {"position": 200, "market_price": 200.0, "avg_cost": 180.0}

        self.mock_portfolio_context = {
            "positions": self.positions,
            "cash_balance": 50000.0,
            "available_cash": 50000.0,
            "broker_buying_power": 50000.0,
            "broker_buying_power_source": "available_cash",
            "cash_available_for_csp": 50000.0,
            "cash_reserved_for_csp": 0.0,
            "excess_liquidity": 50000.0,
            "short_calls": {},
            "short_puts": {},
        }
        self.mock_portfolio_context_provider = MagicMock()
        self.mock_portfolio_context_provider.get_portfolio_context.return_value = self.mock_portfolio_context

        self.mock_options_data = MagicMock()
        self.mock_cash_calculator = MagicMock()
        self.mock_portfolio_service_provider = MagicMock()

    def _scan_universe(self, tickers, status="ok"):
        return {
            "status": status,
            "group_name": "My Watchlist",
            "explanation": "" if status == "ok" else f"group status {status}",
            "groups_available": ["My Watchlist"],
            "tickers": tickers,
            "raw_codes": {t: f"US.{t}" for t in tickers},
            "groups_raw": {"My Watchlist": [f"US.{t}" for t in tickers]},
            "fetched_at": "2026-05-25T14:00:00+00:00",
        }

    def _import_engine(self):
        return RecommendationEngine(
            self.mock_connection_provider,
            self.mock_config_provider,
            self.mock_db,
            self.mock_iv_earnings,
            self.mock_portfolio_context_provider,
            self.mock_portfolio_service_provider,
            self.mock_watchlist_manager,
            self.mock_options_data,
            self.mock_cash_calculator,
        )

    def _run_engine(self):
        """Run the real engine against the 3/4/25 scenario; return the payload."""
        universe = self._scan_universe(self.PICK_TICKERS + self.REJECTED_TICKERS)
        self.mock_watchlist_manager.get_scan_universe.return_value = universe

        # 3 scored CSP picks, then 25 distinct rejections, in watchlist order.
        fetch_returns = [
            [_csp_candidate("CSP-A", 180.0, 2.80, 0.014)],
            [_csp_candidate("CSP-B", 120.0, 1.90, 0.013)],
            [_csp_candidate("CSP-C", 90.0, 1.40, 0.012)],
        ] + [[_skip_diagnostic(t, i)] for i, t in enumerate(self.REJECTED_TICKERS, start=1)]
        self.mock_watchlist_manager.get_effective_watchlist.return_value = self.PICK_TICKERS + self.REJECTED_TICKERS
        # Position order (dict insertion) drives CC lane order. Give the four
        # covered calls the four highest capital velocities so the combined
        # top-3 shortlist is 3 of the covered calls and both strategy lanes are
        # fully populated (3 CSP + 4 CC = 7 lane candidates).
        otm_returns = {
            ticker: {"calls": [_cc_call(ticker, 220.0 + i, 0.020 - (i * 0.001))], "puts": []}
            for i, ticker in enumerate(self.POSITION_TICKERS)
        }
        self.mock_options_data._process_ticker_for_otm.side_effect = [otm_returns[t] for t in self.POSITION_TICKERS]
        self.mock_conn.get_stock_price.return_value = 200.0

        engine = self._import_engine()
        with (
            patch("api.services.recommendations.is_market_open", return_value=True),
            patch.object(engine, "_fetch_watchlist_ticker_csp", side_effect=fetch_returns),
        ):
            return engine.get_top_recommendations(limit=3)

    def _build_snapshot(self, result):
        """Persist the engine payload through the immutable snapshot builder.

        ``core*`` modules can be dropped from ``sys.modules`` and re-imported by
        ``test_import_side_effects`` — resolve the module here (not at import
        time) so the class and the ``is_market_open`` global we patch are the
        same module object. Otherwise the patch misses and the real market
        clock decides ``status``.
        """
        import core.wheel_runner as _wheel_runner

        runner = _wheel_runner.WheelRunner(
            db=MagicMock(),
            options_service=MagicMock(),
            config={"portfolio_env": "SIMULATE", "account_id": ""},
            max_tradeable_age_sec=300,
        )
        with patch.object(_wheel_runner, "is_market_open", return_value=True):
            return runner._build_snapshot(
                env="REAL",
                opaque_account="acc-saved-run",
                attempt_started="2026-05-25T14:00:00+00:00",
                result=result,
                portfolio=self.mock_portfolio_context,
                roll_decisions=[],
            )

    def _make_app(self, snapshot_dict):
        db = MagicMock()
        db.get_latest_attempt.return_value = None
        db.get_latest_snapshot.return_value = snapshot_dict
        app = Flask(__name__)
        app.config["database"] = db
        from api.routes.run import bp

        app.register_blueprint(bp)
        return app, db

    def test_engine_reproduces_saved_run_counts(self):
        result = self._run_engine()

        self.assertTrue(result["success"])
        # Strategy lanes fully populated: 3 CSP picks, 4 CC decisions.
        self.assertEqual(len(result["watchlist_csps"]["signals"]), 3)
        self.assertEqual(len(result["covered_calls"]["signals"]), 4)
        # The combined shortlist is limited to 3.
        self.assertEqual(len(result["signals"]), 3)
        # Every one of the 25 rejections is exposed — none truncated by the
        # top-3 shortlist.
        self.assertEqual(len(result["blocked_signals"]), 25)
        for entry in result["blocked_signals"]:
            self.assertTrue(entry["reason_code"].startswith("reject_"))
            self.assertIn("reason_text", entry)
        self.assertEqual(len({e["reason_text"] for e in result["blocked_signals"]}), 25)

        # CSP picks are outside the combined shortlist (the 4 covered calls
        # outrank them), proving lane candidates survive independently.
        shortlist_tickers = {s["ticker"] for s in result["signals"]}
        self.assertIn("AAA", shortlist_tickers)
        self.assertNotIn("CSP-A", shortlist_tickers)

    def test_full_pipeline_serves_saved_run_via_route(self):
        result = self._run_engine()
        snapshot = self._build_snapshot(result)
        snapshot_dict = snapshot.to_dict()
        app, _ = self._make_app(snapshot_dict)

        with (
            patch("core.run_model.market_now") as mock_market_now,
            patch("api.routes.run.market_now", return_value=_weekday_noon_et()),
        ):
            mock_market_now.return_value = _weekday_noon_et()
            with app.test_client() as client:
                response = client.get("/api/run")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["snapshot"]
        self.assertEqual(len(payload["csp_picks"]), 3)
        self.assertEqual(len(payload["cc_decisions"]), 4)
        self.assertEqual(len(payload["signals"]), 3)
        # Both strategy sections populate.
        self.assertTrue(payload["csp_picks"])
        self.assertTrue(payload["cc_decisions"])
        # All 25 rejection explanations remain accessible through the route.
        self.assertEqual(len(payload["rejected"]), 25)
        self.assertEqual(len({e["reason_text"] for e in payload["rejected"]}), 25)
        rejected_codes = {e["reason_code"] for e in payload["rejected"]}
        self.assertIn("reject_01", rejected_codes)
        self.assertIn("reject_25", rejected_codes)

        # Cash fields come from the correct snapshot fields: CSP candidates
        # carry collateral == cash_required; CC candidates carry available
        # shares == max_contracts * 100; the portfolio cash balance is the
        # snapshot's portfolio context.
        for csp in payload["csp_picks"]:
            self.assertEqual(csp["collateral"], round(csp["cash_required"], 2))
            self.assertIsNone(csp["available_shares"])
        for cc in payload["cc_decisions"]:
            self.assertEqual(cc["available_shares"], round(cc["max_contracts"] * 100, 2))
            self.assertIsNone(cc["collateral"])
        self.assertEqual(payload["portfolio"]["cash_available_for_csp"], 50000.0)
        self.assertEqual(payload["portfolio"]["cash_balance"], 50000.0)

        # Read-only service must not mutate the persisted snapshot.
        self.assertEqual(len(snapshot.csp_picks), 3)
        self.assertEqual(len(snapshot.cc_decisions), 4)
        self.assertEqual(len(snapshot.rejected), 25)
        self.assertEqual(snapshot.run.status, "ready")
        self.assertEqual(snapshot.run.run_id, snapshot_dict["run"]["run_id"])

    def test_copy_revalidation_for_candidate_outside_combined_shortlist(self):
        result = self._run_engine()
        snapshot = self._build_snapshot(result)
        snapshot_dict = snapshot.to_dict()
        # Serve the run as a fully fresh, complete snapshot (quotes for every
        # scanned symbol) so the live revalidation path is the one under test.
        now_et = _weekday_noon_et()
        fresh_ts = (now_et.astimezone(timezone.utc) - timedelta(seconds=60)).isoformat()
        snapshot_dict["run"]["quote_fetched_at"] = {sym: fresh_ts for sym in snapshot_dict["run"]["quote_fetched_at"]}
        app, _ = self._make_app(snapshot_dict)
        shortlist_tickers = {s["ticker"] for s in snapshot.signals}
        # CSP-A is in csp_picks only; DDD is in cc_decisions only.
        self.assertNotIn("CSP-A", shortlist_tickers)
        self.assertNotIn("DDD", shortlist_tickers)

        with (
            patch("core.run_model.market_now", return_value=now_et),
            patch("api.routes.run.market_now", return_value=now_et),
        ):
            with app.test_client() as client:
                csp_resp = client.get(
                    "/api/run/copy-check",
                    query_string={
                        "run_id": snapshot.run.run_id,
                        "ticker": "CSP-A",
                        "option_type": "PUT",
                        "expiration": "20260619",
                        "strike": "180",
                    },
                )
                cc_resp = client.get(
                    "/api/run/copy-check",
                    query_string={
                        "run_id": snapshot.run.run_id,
                        "ticker": "DDD",
                        "option_type": "CALL",
                        "expiration": "20260619",
                        "strike": "223",
                    },
                )

        csp_payload = csp_resp.get_json()
        self.assertTrue(csp_payload["matched_run"])
        self.assertTrue(csp_payload["matched_contract"])
        self.assertEqual(csp_payload["signal_lane"], "csp_picks")
        self.assertEqual(csp_payload["mode"], "live")

        cc_payload = cc_resp.get_json()
        self.assertTrue(cc_payload["matched_contract"])
        self.assertEqual(cc_payload["signal_lane"], "cc_decisions")
        self.assertEqual(cc_payload["mode"], "live")


# ─────────────────────────────────────────────────────────────────────────────
# A failed refresh keeps the previous snapshot + a visible failure reason
# ─────────────────────────────────────────────────────────────────────────────


class TestFailedRefreshRetainsPreviousSnapshot(unittest.TestCase):
    def test_route_exposes_failed_attempt_and_untouched_previous_snapshot(self):
        timestamp = _weekday_noon_et().astimezone(timezone.utc).isoformat()
        previous = {
            "run": {
                "run_id": "run-before",
                "generated_at": timestamp,
                "published_at": timestamp,
                "env": "REAL",
                "account_id": "acc-1",
                "preset_key": "balanced",
                "preset_version": 1,
                "market_state": "open",
                "status": "ready",
                "errors": [],
                "partial_symbols": [],
                "stale_symbols": [],
                "quote_fetched_at": {"CSP-A": timestamp},
                "max_tradeable_age_sec": 300,
                "coverage_scanned": 28,
                "coverage_total": 28,
                "coverage_complete": True,
                "schema_version": 1,
            },
            "portfolio": {"cash_available_for_csp": 50000.0, "cash_balance": 50000.0},
            "signals": [{"ticker": "AAA", "option_type": "CALL"}],
            "csp_picks": [{"ticker": "CSP-A", "option_type": "PUT"}],
            "cc_decisions": [],
            "roll_decisions": [],
            "rejected": [],
            "preset": {},
            "watchlist_origins": {},
            "active_watchlist": {},
        }
        failed_attempt = {
            "attempt_id": "a-fail",
            "run_id": None,
            "state": "failed",
            "stage": "scan",
            "progress": 0.0,
            "started_at": timestamp,
            "finished_at": timestamp,
            "latest_error": "OpenD disconnected mid-scan",
            "latest_failure_at": timestamp,
        }
        db = MagicMock()
        db.get_latest_snapshot.return_value = previous
        db.get_latest_attempt.return_value = failed_attempt
        app = Flask(__name__)
        app.config["database"] = db
        from api.routes.run import bp

        app.register_blueprint(bp)

        with app.test_client() as client:
            response = client.get("/api/run")

        payload = response.get_json()
        # The failure reason stays visible on the attempt.
        self.assertEqual(payload["attempt"]["state"], "failed")
        self.assertEqual(payload["attempt"]["latest_error"], "OpenD disconnected mid-scan")
        self.assertIsNone(payload["attempt"]["run_id"])
        # The previous snapshot is retained with its ORIGINAL timestamps and is
        # not relabeled by the failed attempt.
        snap = payload["snapshot"]
        self.assertEqual(snap["run"]["run_id"], "run-before")
        self.assertEqual(snap["run"]["generated_at"], timestamp)
        self.assertEqual(snap["run"]["published_at"], timestamp)
        self.assertEqual(snap["run"]["status"], "ready")
        self.assertEqual(snap["portfolio"]["cash_available_for_csp"], 50000.0)
        self.assertEqual(snap["csp_picks"][0]["ticker"], "CSP-A")


# ─────────────────────────────────────────────────────────────────────────────
# Premium headline == Friday table totals from the same income response
# ─────────────────────────────────────────────────────────────────────────────


class TestWeeklyIncomeHeadlineMatchesFridayTable(unittest.TestCase):
    def test_total_income_equals_sum_of_friday_table_rows(self):
        from api.services.portfolio_service import PortfolioService

        svc = PortfolioService()
        this_friday = "20260529"
        # Four open short puts/calls on/around Friday; one expires after Friday
        # so it appears only in open-short totals, never in the weekly table.
        positions = [
            {
                "symbol": "US.US.AAPL" + this_friday + "P00170000",
                "option_type": "PUT",
                "expiration": this_friday,
                "strike": 170,
                "position": -3,
                "avg_cost": 2.5,
            },
            {
                "symbol": "US.US.MSFT" + this_friday + "P00450000",
                "option_type": "PUT",
                "expiration": this_friday,
                "strike": 450,
                "position": -1,
                "avg_cost": 4.0,
            },
            {
                "symbol": "US.US.AAPL" + this_friday + "C00190000",
                "option_type": "CALL",
                "expiration": this_friday,
                "strike": 190,
                "position": -2,
                "avg_cost": 1.75,
            },
            {
                "symbol": "US.US.TSLA20260717C00200000",
                "option_type": "CALL",
                "expiration": "20260717",
                "strike": 200,
                "position": -1,
                "avg_cost": 8.0,
            },
        ]
        summary = svc._build_short_option_income_summary(positions, this_friday_str=this_friday)

        rows = summary["positions"]
        # The Friday table has exactly the rows that expire <= the Friday cutoff.
        self.assertEqual(summary["positions_count"], len(rows))
        self.assertEqual(summary["positions_count"], 3)
        # Headline total_income is exactly the sum of the same response's rows
        # (a separate recompute path would drift).
        self.assertEqual(summary["total_income"], round(sum(row["income"] for row in rows), 2))
        self.assertEqual(summary["total_income"], round(2.5 * 3 * 100 + 4.0 * 1 * 100 + 1.75 * 2 * 100, 2))
        # Open-short totals include the out-of-Friday contract.
        self.assertEqual(summary["open_short_positions_count"], 4)
        self.assertEqual(
            summary["open_short_total_income"],
            round(summary["total_income"] + 8.0 * 1 * 100, 2),
        )


def _weekday_noon_et(year=2026, month=5, day=25):
    if ZoneInfo is None:  # pragma: no cover
        return datetime(year, month, day, 16, 0, tzinfo=timezone.utc)
    return datetime(year, month, day, 12, 0, tzinfo=ZoneInfo("America/New_York"))


if __name__ == "__main__":
    unittest.main()
