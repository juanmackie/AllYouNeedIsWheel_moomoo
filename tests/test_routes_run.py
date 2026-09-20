import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from flask import Flask

from api.routes.run import bp


def _taken_row(run_id, link_key="link-1"):
    """Stored ``trade_events`` row shape for an owner-recorded taken link."""
    return {
        "event_type": "taken",
        "ticker": "SOXL",
        "option_type": "PUT",
        "strike": 100.0,
        "expiration": "20260918",
        "env": "REAL",
        "account_id": "acct-1",
        "provenance": "owner_recorded",
        "timestamp": "2026-09-20T00:00:00+00:00",
        "details": {
            "run_id": run_id,
            "link_key": link_key,
            "lane": "csp_picks",
            "recommendation": {"ticker": "SOXL", "option_type": "PUT", "expiration": "20260918", "strike": 100.0},
            "traded": None,
            "recorded_at": "2026-09-20T00:00:00+00:00",
        },
    }


class TestRunRoute(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.app = Flask(__name__)
        self.app.config["database"] = self.db
        self.app.register_blueprint(bp)

    def test_get_run_recomputes_stale_without_mutating_snapshot(self):
        fetched = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
        snapshot = {
            "run": {
                "status": "ready",
                "errors": [],
                "coverage_complete": True,
                "quote_fetched_at": {"AAPL": fetched},
                "max_tradeable_age_sec": 120,
            },
            "tradeable": True,
            "signals": [{"ticker": "AAPL"}],
        }
        self.db.get_latest_attempt.return_value = None
        self.db.get_latest_snapshot.return_value = snapshot

        with self.app.test_client() as client:
            response = client.get("/api/run")

        payload = response.get_json()
        self.assertFalse(payload["snapshot"]["tradeable"])
        self.assertEqual(payload["snapshot"]["effective_status"], "stale")
        self.assertEqual(snapshot["run"]["status"], "ready")
        self.assertTrue(snapshot["tradeable"])

    def test_get_run_exposes_only_the_current_runs_taken_links(self):
        snapshot = {
            "run": {
                "run_id": "run-a",
                "status": "ready",
                "errors": [],
                "coverage_complete": True,
                "quote_fetched_at": {},
                "max_tradeable_age_sec": 120,
            },
            "signals": [{"ticker": "SOXL", "option_type": "PUT", "expiration": "20260918", "strike": 100.0}],
        }
        self.db.get_latest_attempt.return_value = None
        self.db.get_latest_snapshot.return_value = snapshot
        self.db.get_trade_events.return_value = [
            _taken_row("run-a", link_key="link-a"),
            _taken_row("run-b", link_key="link-b"),
        ]

        with patch("api.services.config.get_current_identity", return_value=("REAL", "acct-1")):
            with self.app.test_client() as client:
                response = client.get("/api/run")

        payload = response.get_json()
        # Only the run on screen may claim a taken link; another run's link is
        # never attached to this card surface.
        self.assertEqual([link["run_id"] for link in payload["taken_links"]], ["run-a"])
        self.assertEqual(payload["taken_links"][0]["recommendation"]["ticker"], "SOXL")
        self.assertEqual(payload["snapshot"]["run"]["run_id"], "run-a")

    @patch("api.routes.run._get_runner")
    @patch("api.routes.run.start_background_refresh", return_value=True)
    def test_refresh_starts_one_runner_attempt(self, mock_refresh, mock_get_runner):
        self.db.get_latest_attempt.return_value = {"state": "refreshing"}
        mock_get_runner.return_value = MagicMock()

        with self.app.test_client() as client:
            response = client.post("/api/run/refresh")

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.get_json()["started"])
        mock_refresh.assert_called_once()

    def test_get_run_exposes_lanes_rejected_portfolio_and_combined_signals(self):
        fetched = datetime.now(timezone.utc).isoformat()
        snapshot = {
            "run": {
                "run_id": "run-lanes",
                "status": "ready",
                "errors": [],
                "coverage_complete": True,
                "quote_fetched_at": {"AAPL": fetched},
                "max_tradeable_age_sec": 120,
                "coverage_scanned": 1,
                "coverage_total": 1,
            },
            "tradeable": True,
            "portfolio": {"account_value": 10000},
            "signals": [{"rank": 1, "ticker": "MSFT", "option_type": "CALL"}],
            "csp_picks": [{"ticker": "AAPL", "option_type": "PUT", "expiration": "20260619", "strike": 140.0}],
            "cc_decisions": [{"ticker": "NVDA", "option_type": "CALL"}],
            "rejected": [{"ticker": "AMZN", "reason_code": "no_cash_fit", "reason_text": "no fit"}],
        }
        self.db.get_latest_attempt.return_value = None
        self.db.get_latest_snapshot.return_value = snapshot

        with self.app.test_client() as client:
            response = client.get("/api/run")

        payload = response.get_json()["snapshot"]
        # Every persisted lane is exposed alongside the intact combined shortlist.
        self.assertEqual(payload["signals"][0]["ticker"], "MSFT")
        self.assertEqual(len(payload["csp_picks"]), 1)
        self.assertEqual(len(payload["cc_decisions"]), 1)
        self.assertEqual(payload["rejected"][0]["reason_code"], "no_cash_fit")
        self.assertEqual(payload["portfolio"]["account_value"], 10000)
        # Lane candidates get the same read-time eligibility + capital view.
        self.assertIn("eligibility", payload["csp_picks"][0])
        self.assertIn("quote_age_sec", payload["cc_decisions"][0])


def _run_dict(now_utc, **overrides):
    run = {
        "run_id": "run-check-1",
        "market_state": "closed",
        "status": "planning",
        "errors": [],
        "coverage_scanned": 2,
        "coverage_total": 2,
        "coverage_complete": True,
        "quote_fetched_at": {
            "AAPL": (now_utc - timedelta(seconds=30)).isoformat(),
            "TSLA": (now_utc - timedelta(seconds=40)).isoformat(),
        },
        "max_tradeable_age_sec": 300,
    }
    run.update(overrides)
    return run


def _signal_dict(**overrides):
    sig = {
        "rank": 1,
        "ticker": "AAPL",
        "option_type": "PUT",
        "expiration": "20260619",
        "strike": 140.0,
        "recommended_contracts": 1,
        "copy_eligible": True,
        "chain_source": "broker",
        "bid": 2.5,
        "ask": 3.0,
        "bid_premium_per_contract": 2.5,
        "limit_target_per_contract": 2.75,
        "mid_price": 2.75,
        "wheel_decision": {},
    }
    sig.update(overrides)
    return sig


class TestRunCopyCheck(unittest.TestCase):
    """P1a: read-only copy revalidation before a clipboard write."""

    def setUp(self):
        self.db = MagicMock()
        self.app = Flask(__name__)
        self.app.config["database"] = self.db
        self.app.register_blueprint(bp)

    def _stub_snapshot(self, run_overrides=None, signal_overrides=None, now_utc=None):
        now_utc = now_utc or datetime.now(timezone.utc)
        snapshot = {
            "run": _run_dict(now_utc, **(run_overrides or {})),
            "tradeable": False,
            "signals": [_signal_dict(**(signal_overrides or {}))],
        }
        self.db.get_latest_snapshot.return_value = snapshot
        return snapshot

    def _weekday_noon_et(self, year=2026, month=5, day=25):
        from zoneinfo import ZoneInfo

        return datetime(year, month, day, 10, 0, tzinfo=ZoneInfo("America/New_York"))

    def _url(self, run_id="run-check-1", ticker="AAPL", option_type="PUT", expiration="20260619", strike="140"):
        return (
            f"/api/run/copy-check?run_id={run_id}&ticker={ticker}"
            f"&option_type={option_type}&expiration={expiration}&strike={strike}"
        )

    @patch("core.run_model.market_now")
    @patch("api.routes.run.market_now")
    def test_copy_check_live_mode(self, mock_run_market_now, mock_model_market_now):
        now_et = self._weekday_noon_et()
        mock_run_market_now.return_value = now_et
        mock_model_market_now.return_value = now_et
        self._stub_snapshot(
            run_overrides={"market_state": "open", "status": "ready"},
            now_utc=now_et.astimezone(timezone.utc),
        )
        fetch = MagicMock()
        with self.app.test_client() as client:
            with patch("api.routes.run._options_service_fetch_live_chain", fetch):
                response = client.get(self._url())
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["matched_run"])
        self.assertTrue(payload["matched_contract"])
        self.assertEqual(payload["mode"], "live")
        self.assertEqual(payload["contract"]["ticker"], "AAPL")
        fetch.assert_not_called()  # live never hits OpenD at copy time

    @patch("core.run_model.market_now")
    @patch("api.routes.run.market_now")
    def test_copy_check_staged_with_broker_evidence(self, mock_run_market_now, mock_model_market_now):
        now_et = self._weekday_noon_et(year=2026, month=5, day=23)  # Saturday
        mock_run_market_now.return_value = now_et
        mock_model_market_now.return_value = now_et
        now_utc = now_et.astimezone(timezone.utc)
        self._stub_snapshot(now_utc=now_utc)  # planning-status closed-market run, complete coverage
        old_evidence = {
            "source": "broker",
            "option": {
                "strike": 140.0,
                "bid": 2.7,
                "ask": 3.2,
                "quote_fetched_at_utc": (now_utc - timedelta(hours=3)).isoformat(),
            },
        }
        with self.app.test_client() as client:
            with patch("api.routes.run._options_service_fetch_live_chain", return_value=old_evidence):
                response = client.get(self._url())
        payload = response.get_json()
        self.assertEqual(payload["mode"], "staged")
        self.assertEqual(payload["contract"]["bid"], 2.7)  # merged live quote
        self.assertEqual(payload["contract"]["limit_target_per_contract"], 2.75)  # kept from snapshot

    @patch("core.run_model.market_now")
    @patch("api.routes.run.market_now")
    def test_copy_check_staged_rejected_on_persisted_fallback(self, mock_run_market_now, mock_model_market_now):
        now_et = self._weekday_noon_et(year=2026, month=5, day=23)
        mock_run_market_now.return_value = now_et
        mock_model_market_now.return_value = now_et
        self._stub_snapshot(now_utc=now_et.astimezone(timezone.utc))
        fallback = {"source": "persisted-broker", "option": {"strike": 140.0, "bid": 2.7}}
        with self.app.test_client() as client:
            with patch("api.routes.run._options_service_fetch_live_chain", return_value=fallback):
                response = client.get(self._url())
        payload = response.get_json()
        self.assertEqual(payload["mode"], "review_only")
        self.assertTrue(any("persisted" in r for r in payload["reasons"]))

    @patch("core.run_model.market_now")
    @patch("api.routes.run.market_now")
    def test_copy_check_staged_rejected_when_market_open_now(self, mock_run_market_now, mock_model_market_now):
        now_et = self._weekday_noon_et(year=2026, month=5, day=23)
        mock_run_market_now.return_value = now_et
        mock_model_market_now.return_value = now_et
        now_utc = now_et.astimezone(timezone.utc)
        self._stub_snapshot(now_utc=now_utc)
        fresh_now = {
            "source": "broker",
            "option": {
                "strike": 140.0,
                "bid": 2.7,
                "quote_fetched_at_utc": now_utc.isoformat(),
            },
        }
        with self.app.test_client() as client:
            with patch("api.routes.run._options_service_fetch_live_chain", return_value=fresh_now):
                response = client.get(self._url())
        payload = response.get_json()
        self.assertEqual(payload["mode"], "review_only")
        self.assertTrue(any("open now" in r for r in payload["reasons"]))

    @patch("core.run_model.market_now")
    @patch("api.routes.run.market_now")
    def test_copy_check_run_mismatch(self, mock_run_market_now, mock_model_market_now):
        now_et = self._weekday_noon_et()
        mock_run_market_now.return_value = now_et
        mock_model_market_now.return_value = now_et
        self._stub_snapshot(
            run_overrides={"market_state": "open", "status": "ready"},
            now_utc=now_et.astimezone(timezone.utc),
        )
        with self.app.test_client() as client:
            response = client.get(self._url(run_id="different-run"))
        payload = response.get_json()
        self.assertFalse(payload["matched_run"])
        self.assertEqual(payload["mode"], "live")

    @patch("core.run_model.market_now")
    @patch("api.routes.run.market_now")
    def test_copy_check_contract_no_longer_present(self, mock_run_market_now, mock_model_market_now):
        now_et = self._weekday_noon_et()
        mock_run_market_now.return_value = now_et
        mock_model_market_now.return_value = now_et
        self._stub_snapshot(
            run_overrides={"market_state": "open", "status": "ready"},
            now_utc=now_et.astimezone(timezone.utc),
        )
        with self.app.test_client() as client:
            response = client.get(self._url(ticker="MSFT", strike="200"))
        payload = response.get_json()
        self.assertFalse(payload["matched_contract"])
        self.assertEqual(payload["mode"], "review_only")

    def test_copy_check_matches_candidate_outside_shortlist(self):
        """A candidate present only in csp_picks is copy-addressable too."""
        now_et = self._weekday_noon_et()
        now_utc = now_et.astimezone(timezone.utc)
        snapshot = {
            "run": _run_dict(now_utc, market_state="open", status="ready"),
            "tradeable": True,
            "signals": [{"ticker": "OTHER", "option_type": "CALL"}],
            "csp_picks": [_signal_dict()],
            "cc_decisions": [],
        }
        self.db.get_latest_snapshot.return_value = snapshot
        fetch = MagicMock()
        with self.app.test_client() as client:
            with patch("api.routes.run._options_service_fetch_live_chain", fetch):
                with patch("api.routes.run.market_now", return_value=now_et):
                    with patch("core.run_model.market_now", return_value=now_et):
                        response = client.get(self._url())
        payload = response.get_json()
        self.assertTrue(payload["matched_contract"])
        self.assertEqual(payload["signal_lane"], "csp_picks")
        self.assertEqual(payload["mode"], "live")
        fetch.assert_not_called()

    def test_copy_check_invalid_params_400(self):
        with self.app.test_client() as client:
            response = client.get("/api/run/copy-check?ticker=AAPL")
        self.assertEqual(response.status_code, 400)

    def test_copy_check_no_snapshot_review_only(self):
        self.db.get_latest_snapshot.return_value = None
        with self.app.test_client() as client:
            response = client.get(self._url())
        payload = response.get_json()
        self.assertEqual(payload["mode"], "review_only")
        self.assertFalse(payload["matched_run"])
        self.assertFalse(payload["matched_contract"])


if __name__ == "__main__":
    unittest.main()
