"""Owner-recorded "taken" link tests (``api/services/taken_links.py``).

A taken link is owner-asserted journal evidence, not broker evidence: it states
which recommendation from a published run the owner acted on, because the traded
contract may differ from the suggested strike or expiry. These tests pin the
honesty rules — canonical identity, idempotency, env/account scoping, "unstated
stays unknown", and unusable rows skipped rather than reinterpreted.
"""

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from flask import Flask

from api.routes.run import bp
from api.services.taken_links import (
    PROVENANCE_OWNER_RECORDED,
    TAKEN_EVENT_TYPE,
    TakenLinkError,
    build_link_row,
    normalize_contract,
    normalize_traded,
    read_taken_links,
    record_taken_link,
    traded_differs_from_recommendation,
)
from core.wheel_runner import opaque_account_id
from db.database import OptionsDatabase
from db.sqlite_pool import close_connection_pool

ENV = "REAL"
ACCOUNT = opaque_account_id("BROKER-ACC-42")
OTHER_ACCOUNT = opaque_account_id("SOMEONE-ELSE")
RUN_ID = "run-a1"

# The recommendation the app published: SOXL 20260918 P100.
RECOMMENDATION = {"ticker": "SOXL", "option_type": "PUT", "expiration": "20260918", "strike": 100.0}
# What the owner actually traded: SOXL 20260918 P106 (observed divergence).
# The traded block carries a full identity when the contract was stated; the
# strike-only form is the "partial" case whose divergence is unknown, not false.
TRADED = {"strike": 106.0, "qty": 1, "price": 6.1}
TRADED_IDENTITY = {"ticker": "SOXL", "option_type": "PUT", "expiration": "20260918", **TRADED}


class TestContractNormalization(unittest.TestCase):
    def test_normalizes_case_spacing_and_expiration(self):
        self.assertEqual(
            normalize_contract(" soxl ", "put", "2026-09-18", "100"),
            {"ticker": "SOXL", "option_type": "PUT", "expiration": "20260918", "strike": 100.0},
        )

    def test_rejects_incomplete_or_nonsense_contracts(self):
        cases = [
            ("", "PUT", "20260918", 100.0),
            ("SOXL", "STRANGLE", "20260918", 100.0),
            ("SOXL", "PUT", "not-a-date", 100.0),
            ("SOXL", "PUT", "20260918", 0),
            ("SOXL", "PUT", "20260918", "abc"),
        ]
        for ticker, option_type, expiration, strike in cases:
            with self.subTest(ticker=ticker, option_type=option_type, expiration=expiration, strike=strike):
                with self.assertRaises(TakenLinkError):
                    normalize_contract(ticker, option_type, expiration, strike)


class TestTradedContract(unittest.TestCase):
    def test_only_stated_fields_survive(self):
        self.assertEqual(normalize_traded({"strike": "106", "qty": "1", "price": 6.1}), TRADED)
        self.assertIsNone(normalize_traded(None))
        self.assertIsNone(normalize_traded({}))
        self.assertIsNone(normalize_traded(""))

    def test_rejects_non_numeric_and_negative_values(self):
        for block in ({"strike": "abc"}, {"qty": "lots"}, {"price": -1}, {"option_type": "STRANGLE"}):
            with self.subTest(block=block):
                with self.assertRaises(TakenLinkError):
                    normalize_traded(block)

    def test_differs_flag_is_unknown_when_identity_is_partial(self):
        recommendation = normalize_contract(**RECOMMENDATION)
        # A differing strike with full identity is a stated divergence.
        self.assertTrue(
            traded_differs_from_recommendation(
                recommendation,
                {"ticker": "SOXL", "option_type": "PUT", "expiration": "20260918", "strike": 106.0},
            )
        )
        # The same contract is not a divergence.
        self.assertFalse(
            traded_differs_from_recommendation(
                recommendation,
                {"ticker": "SOXL", "option_type": "PUT", "expiration": "20260918", "strike": 100.0},
            )
        )
        # Partial identity (or nothing) is unknown — never reported as a match.
        self.assertIsNone(traded_differs_from_recommendation(recommendation, {"strike": 100.0}))
        self.assertIsNone(traded_differs_from_recommendation(recommendation, None))


class TestLinkRow(unittest.TestCase):
    def test_row_carries_recommendation_in_columns_and_trade_in_details(self):
        recommendation = normalize_contract(**RECOMMENDATION)
        row = build_link_row(
            run_id=RUN_ID,
            lane="csp_picks",
            recommendation=recommendation,
            traded=dict(TRADED_IDENTITY),
            env=ENV,
            account_id=ACCOUNT,
            now_iso="2026-09-20T00:00:00+00:00",
        )
        self.assertEqual(row["event_type"], TAKEN_EVENT_TYPE)
        self.assertEqual(
            (row["ticker"], row["option_type"], row["expiration"], row["strike"]),
            ("SOXL", "PUT", "20260918", 100.0),
        )
        self.assertIsNone(row["pnl"])  # a link is never income
        self.assertEqual(row["provenance"], PROVENANCE_OWNER_RECORDED)
        self.assertEqual((row["env"], row["account_id"]), (ENV, ACCOUNT))
        self.assertEqual(row["details"]["traded"], TRADED_IDENTITY)
        self.assertEqual(row["details"]["lane"], "csp_picks")
        self.assertTrue(row["details"]["traded_differs_from_recommendation"])
        self.assertIn(RUN_ID, row["details"]["link_key"])

    def test_unstated_trade_stays_null(self):
        recommendation = normalize_contract(**RECOMMENDATION)
        row = build_link_row(
            run_id=RUN_ID,
            lane="",
            recommendation=recommendation,
            traded=None,
            env=ENV,
            account_id=ACCOUNT,
            now_iso="2026-09-20T00:00:00+00:00",
        )
        self.assertIsNone(row["details"]["traded"])
        self.assertIsNone(row["details"]["traded_differs_from_recommendation"])


class TestTakenLinkPersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "taken_links.db")
        self.db = OptionsDatabase(self.db_path)

    def tearDown(self):
        self.db.close()
        close_connection_pool(self.db_path)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _record(self, run_id=RUN_ID, **overrides):
        kwargs = {"lane": "csp_picks", "traded": dict(TRADED_IDENTITY), "env": ENV, "account_id": ACCOUNT}
        kwargs.update(overrides)
        return record_taken_link(self.db, run_id=run_id, recommendation=RECOMMENDATION, **kwargs)

    def test_records_and_is_idempotent(self):
        first = self._record()
        self.assertFalse(first["idempotent"])
        rows = self.db.get_trade_events(event_type=TAKEN_EVENT_TYPE, env=ENV, account_id=ACCOUNT)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provenance"], PROVENANCE_OWNER_RECORDED)
        self.assertIsNone(rows[0]["pnl"])
        self.assertEqual(rows[0]["details"]["traded"], TRADED_IDENTITY)

        again = self._record()
        self.assertTrue(again["idempotent"])
        self.assertEqual(again["link"]["link_key"], first["link"]["link_key"])
        self.assertEqual(len(self.db.get_trade_events(event_type=TAKEN_EVENT_TYPE, env=ENV, account_id=ACCOUNT)), 1)

    def test_a_different_run_is_a_separate_link(self):
        self._record()
        second = self._record(run_id="run-b2")
        self.assertFalse(second["idempotent"])
        self.assertEqual(len(read_taken_links(self.db, ENV, ACCOUNT)), 2)

    def test_links_are_identity_scoped(self):
        self._record()
        self.assertEqual(len(read_taken_links(self.db, ENV, ACCOUNT)), 1)
        self.assertEqual(read_taken_links(self.db, ENV, OTHER_ACCOUNT), [])
        self.assertEqual(read_taken_links(self.db, "SIMULATE", ACCOUNT), [])

    def test_read_shape_round_trips(self):
        self._record()
        link = read_taken_links(self.db, ENV, ACCOUNT)[0]
        self.assertEqual(link["run_id"], RUN_ID)
        self.assertEqual(link["lane"], "csp_picks")
        self.assertEqual(
            link["recommendation"], {"ticker": "SOXL", "option_type": "PUT", "expiration": "20260918", "strike": 100.0}
        )
        self.assertEqual(link["traded"], TRADED_IDENTITY)
        self.assertTrue(link["traded_differs_from_recommendation"])
        self.assertEqual(link["provenance"], PROVENANCE_OWNER_RECORDED)
        self.assertEqual(link["env"], ENV)
        self.assertTrue(link["recorded_at"])

    def test_a_partial_traded_block_reports_divergence_as_unknown(self):
        """Strike-only input must not be read as "same contract as recommended"."""
        link = self._record(traded=dict(TRADED))["link"]
        self.assertEqual(link["traded"], TRADED)
        self.assertIsNone(link["traded_differs_from_recommendation"])

    def test_unusable_rows_are_skipped_not_reinterpreted(self):
        # No details at all: nothing linking has been stated.
        self.db.save_trade_event(
            {
                "event_type": TAKEN_EVENT_TYPE,
                "ticker": "SOXL",
                "option_type": "PUT",
                "strike": 100.0,
                "expiration": "20260918",
                "env": ENV,
                "account_id": ACCOUNT,
                "provenance": PROVENANCE_OWNER_RECORDED,
                "details": {},
            }
        )
        # A run id with an undecodable contract identity.
        self.db.save_trade_event(
            {
                "event_type": TAKEN_EVENT_TYPE,
                "ticker": "",
                "option_type": "PUT",
                "strike": 0,
                "expiration": "",
                "env": ENV,
                "account_id": ACCOUNT,
                "provenance": PROVENANCE_OWNER_RECORDED,
                "details": {"run_id": RUN_ID},
            }
        )
        self.assertEqual(read_taken_links(self.db, ENV, ACCOUNT), [])

    def test_other_event_types_are_not_links(self):
        self.db.save_trade_event(
            {
                "event_type": "entry",
                "ticker": "SOXL",
                "option_type": "PUT",
                "strike": 100.0,
                "expiration": "20260918",
                "env": ENV,
                "account_id": ACCOUNT,
                "details": {"run_id": RUN_ID},
            }
        )
        self.assertEqual(read_taken_links(self.db, ENV, ACCOUNT), [])

    def test_requires_a_database_and_a_run_id(self):
        with self.assertRaises(TakenLinkError):
            record_taken_link(None, run_id=RUN_ID, recommendation=RECOMMENDATION)
        with self.assertRaises(TakenLinkError):
            record_taken_link(self.db, run_id="  ", recommendation=RECOMMENDATION)
        with self.assertRaises(TakenLinkError):
            record_taken_link(self.db, run_id=RUN_ID, recommendation="SOXL 100P")


class TestTakenLinkRoute(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.db.get_trade_events.return_value = []
        self.app = Flask(__name__)
        self.app.config["database"] = self.db
        self.app.register_blueprint(bp)

        rate_limit = patch("api.routes.utils.enforce_route_rate_limit", return_value=(True, 0))
        rate_limit.start()
        self.addCleanup(rate_limit.stop)
        identity = patch("api.services.config.get_current_identity", return_value=(ENV, ACCOUNT))
        identity.start()
        self.addCleanup(identity.stop)

    def _snapshot(self, run_id=RUN_ID):
        fetched = datetime.now(timezone.utc).isoformat()
        return {
            "run": {
                "run_id": run_id,
                "status": "ready",
                "errors": [],
                "coverage_complete": True,
                "quote_fetched_at": {"SOXL": fetched},
                "max_tradeable_age_sec": 300,
                "coverage_scanned": 1,
                "coverage_total": 1,
            },
            "tradeable": True,
            "csp_picks": [
                {
                    "rank": 1,
                    "ticker": "SOXL",
                    "option_type": "PUT",
                    "expiration": "20260918",
                    "strike": 100.0,
                    "dte": 19,
                    "bid": 6.2,
                    "premium_velocity_per_day": 32.6,
                    "copy_eligible": True,
                }
            ],
        }

    def _body(self, **overrides):
        body = {
            "run_id": RUN_ID,
            "ticker": "SOXL",
            "option_type": "PUT",
            "expiration": "20260918",
            "strike": 100.0,
            "traded": dict(TRADED_IDENTITY),
        }
        body.update(overrides)
        return body

    def test_records_taken_link_for_published_contract(self):
        self.db.get_run_snapshots.return_value = [self._snapshot()]
        with self.app.test_client() as client:
            response = client.post("/api/run/taken", json=self._body())

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["idempotent"])
        self.assertEqual(payload["link"]["run_id"], RUN_ID)
        self.assertEqual(payload["link"]["recommendation"]["strike"], 100.0)
        self.assertEqual(payload["link"]["traded"]["strike"], 106.0)
        self.assertTrue(payload["link"]["traded_differs_from_recommendation"])
        self.assertEqual(payload["link"]["provenance"], PROVENANCE_OWNER_RECORDED)

        self.db.save_trade_event.assert_called_once()
        saved = self.db.save_trade_event.call_args.args[0]
        self.assertEqual(saved["event_type"], TAKEN_EVENT_TYPE)
        self.assertIsNone(saved["pnl"])
        self.assertEqual(saved["account_id"], ACCOUNT)

    def test_reports_idempotent_when_the_link_already_exists(self):
        self.db.get_run_snapshots.return_value = [self._snapshot()]
        stored = build_link_row(
            run_id=RUN_ID,
            lane="csp_picks",
            recommendation=normalize_contract(**RECOMMENDATION),
            traded=dict(TRADED_IDENTITY),
            env=ENV,
            account_id=ACCOUNT,
            now_iso="2026-09-20T00:00:00+00:00",
        )
        self.db.get_trade_events.return_value = [stored]

        with self.app.test_client() as client:
            response = client.post("/api/run/taken", json=self._body())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["idempotent"])
        self.db.save_trade_event.assert_not_called()

    def test_rejects_run_id_that_is_not_published(self):
        # Only runs in stored history can be linked; an unknown id is refused.
        self.db.get_run_snapshots.return_value = [self._snapshot(run_id="run-newer")]
        with self.app.test_client() as client:
            response = client.post("/api/run/taken", json=self._body())

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.get_json()["success"])
        self.db.save_trade_event.assert_not_called()

    def test_links_a_recommendation_from_an_older_published_run(self):
        """The owner trades the evening run at the next open and marks it later.

        Requiring the *current* run would refuse exactly the workflow the link
        exists for; history lookup keeps it evidence-backed instead.
        """
        self.db.get_run_snapshots.return_value = [
            self._snapshot(run_id="run-newer"),
            self._snapshot(run_id=RUN_ID),
        ]
        with self.app.test_client() as client:
            response = client.post("/api/run/taken", json=self._body())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["link"]["run_id"], RUN_ID)
        self.db.save_trade_event.assert_called_once()

    def test_rejects_contract_outside_the_shortlist(self):
        self.db.get_run_snapshots.return_value = [self._snapshot()]
        with self.app.test_client() as client:
            response = client.post("/api/run/taken", json=self._body(strike=999.0))

        self.assertEqual(response.status_code, 404)
        self.db.save_trade_event.assert_not_called()

    def test_rejects_invalid_parameters(self):
        self.db.get_run_snapshots.return_value = [self._snapshot()]
        cases = [
            self._body(run_id=""),
            self._body(ticker=""),
            self._body(option_type="STRANGLE"),
            self._body(expiration=""),
            self._body(strike=0),
            "not-a-dict",
        ]
        for case in cases:
            with self.subTest(case=case):
                with self.app.test_client() as client:
                    response = client.post("/api/run/taken", json=case)
                self.assertEqual(response.status_code, 400)
        self.db.save_trade_event.assert_not_called()

    def test_reports_missing_run_and_missing_database(self):
        self.db.get_run_snapshots.return_value = []
        with self.app.test_client() as client:
            response = client.post("/api/run/taken", json=self._body())
        self.assertEqual(response.status_code, 404)

        self.db.get_run_snapshots.return_value = [self._snapshot()]
        self.app.config["database"] = None
        with self.app.test_client() as client:
            response = client.post("/api/run/taken", json=self._body())
        self.assertEqual(response.status_code, 503)

    def test_reports_rate_limiting(self):
        with patch("api.routes.utils.enforce_route_rate_limit", return_value=(False, 42)):
            with self.app.test_client() as client:
                response = client.post("/api/run/taken", json=self._body())
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()["retry_after"], 42)


if __name__ == "__main__":
    unittest.main()
