import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from flask import Flask

from api.routes.run import bp


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
