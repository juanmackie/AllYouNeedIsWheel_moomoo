"""Integration: ingestion → persistence → attribution → outcome summary route.

Wires the real SQLite database (schema v10), the real ``FillsService`` /
``OutcomeService``, and the real route layer against a query-only fake broker
connection. No OpenD, no network, no mock portfolio data in production
behavior — the fake connection exists only inside this test.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.fills_service import FillsService
from api.services.outcome_service import OutcomeService
from core.outcome_attribution import is_capital_movement_cash_flow
from core.run_model import RunMetadata, WheelRunSnapshot
from core.wheel_runner import opaque_account_id
from db.database import OptionsDatabase
from db.sqlite_pool import close_connection_pool

BROKER_ACCOUNT = "BROKER-ACC-42"
OPAQUE = opaque_account_id(BROKER_ACCOUNT)
ENV = "REAL"

# OpenD-style option code: US.AAPL260220P00070000 → AAPL 2026-02-20 PUT 70.0
AAPL_PUT_CODE = "US.AAPL260220P00070000"
MSFT_PUT_CODE = "US.MSFT260320P00040000"


def _signal(**extra):
    row = {
        "ticker": "AAPL",
        "option_type": "PUT",
        "expiration": "20260220",
        "strike": 70.0,
        "dte": 30,
        "bid_premium_per_contract": 110.0,
        "stock_price": 80.0,
        "event_tier": "earnings_week",
        "quality_tier": "A",
        "signal_type": "csp",
    }
    row.update(extra)
    return row


def _deal(deal_id, order_id, code, side, qty, price, create_time, status="OK"):
    """Raw OpenD history-deal row shape (pandas to_dict('records'))."""
    return {
        "code": code,
        "stock_name": "dummy",
        "deal_market": "US",
        "deal_id": deal_id,
        "order_id": order_id,
        "qty": qty,
        "price": price,
        "trd_side": side,
        "create_time": create_time,
        "counter_broker_id": "",
        "counter_broker_name": "",
        "status": status,
        "jp_acc_type": "",
    }


class _FakeBrokerConnection:
    """Query-only stand-in for ``MoomooConnection`` (no broker, no network)."""

    def __init__(self, deals, fees_by_order, cash_flows_by_date):
        self.deals = deals
        self.fees_by_order = fees_by_order
        self.cash_flows_by_date = cash_flows_by_date
        self.deal_calls = []
        self.fee_calls = []
        self.cash_calls = []

    def resolve_portfolio_identity(self):
        return ENV, OPAQUE

    def get_history_deals(self, start="", end=""):
        self.deal_calls.append((start, end))
        return list(self.deals)

    def get_order_fees(self, order_ids):
        self.fee_calls.append(list(order_ids))
        return [
            {"order_id": oid, "fee_amount": self.fees_by_order[oid], "fee_details": []}
            for oid in order_ids
            if oid in self.fees_by_order
        ]

    def get_cash_flow(self, clearing_date):
        self.cash_calls.append(clearing_date)
        return list(self.cash_flows_by_date.get(clearing_date, []))


class _OutcomeIntegrationBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "outcome_integration.db")
        self.db = OptionsDatabase(self.db_path)

    def tearDown(self):
        self.db.close()
        close_connection_pool(self.db_path)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def _publish_signal_run(self, generated_at="2026-01-02T15:00:00", run_id="run-1", signals=None):
        snapshot = WheelRunSnapshot(
            run=RunMetadata(
                run_id=run_id,
                generated_at=generated_at,
                published_at=generated_at,
                env=ENV,
                account_id=OPAQUE,
                preset_key="balanced",
                preset_version=1,
                market_state="closed",
                status="ready",
            ),
            portfolio=None,
            csp_picks=(),
            cc_decisions=(),
            roll_decisions=(),
            rejected=(),
            preset={},
            watchlist_origins={},
            signals=tuple(signals if signals is not None else [_signal()]),
        )
        self.db.save_run_snapshot(snapshot)


class TestIngestToAttributionToSummary(_OutcomeIntegrationBase):
    """Fake broker deals → FillsService → SQLite → OutcomeService summary."""

    def _fake_connection(self):
        deals = [
            _deal("D1", "O1", AAPL_PUT_CODE, "SELL", 1.0, 1.05, "2026-01-05 14:30:00"),
            _deal("D2", "O1", AAPL_PUT_CODE, "BUY", 1.0, 0.30, "2026-02-05 14:30:00"),
            # Cancelled deals are not trading evidence.
            _deal("D3", "O2", AAPL_PUT_CODE, "SELL", 1.0, 0.90, "2026-01-06 14:30:00", status="CANCELLED"),
            # A share fill is stored but never option-attributed.
            _deal("D4", "O3", "US.AAPL", "BUY", 100.0, 69.50, "2026-02-05 14:31:00"),
            # An option fill with no stored signal → unmatched evidence.
            _deal("D5", "O4", MSFT_PUT_CODE, "SELL", 1.0, 0.80, "2026-01-07 14:30:00"),
        ]
        fees = {"O1": 2.0, "O4": 1.0}
        cash = {
            "2026-01-10": [
                {
                    "cashflow_id": "CF1",
                    "clearing_date": "2026-01-10",
                    "settlement_date": "2026-01-10",
                    "currency": "USD",
                    "cashflow_type": "Deposit",
                    "cashflow_direction": "IN",
                    "cashflow_amount": 5000.0,
                    "cashflow_remark": "",
                    "create_time": "2026-01-10 09:00:00",
                }
            ]
        }
        return _FakeBrokerConnection(deals, fees, cash)

    def test_end_to_end_ingest_then_summary(self):
        self._publish_signal_run()
        conn = self._fake_connection()

        result = FillsService(conn, self.db).ingest_history_fills(days=30)
        self.assertTrue(result["ok"])
        self.assertEqual(result["seen"], 5)
        self.assertEqual(result["ingested"], 4)  # cancelled deal skipped
        self.assertEqual(result["fees_updated"], 3)  # D1, D2 (O1) + D5 (O4)

        fills = self.db.get_fills(env=ENV, account_id=OPAQUE)
        self.assertEqual(len(fills), 4)
        opt_fills = {f["fill_id"]: f for f in fills if f["security_type"] == "OPT"}
        # Fees allocated across O1's fills proportionally to gross value.
        self.assertAlmostEqual(opt_fills["D1"]["fees"] + opt_fills["D2"]["fees"], 2.0)
        self.assertGreater(opt_fills["D1"]["fees"], opt_fills["D2"]["fees"])
        self.assertAlmostEqual(opt_fills["D5"]["fees"], 1.0)

        # Idempotent re-ingestion: same deal ids insert nothing new.
        again = FillsService(conn, self.db).ingest_history_fills(days=30)
        self.assertEqual(again["ingested"], 0)
        self.assertEqual(len(self.db.get_fills(env=ENV, account_id=OPAQUE)), 4)

        summary = OutcomeService(self.db).get_outcome_summary(ENV, OPAQUE)

        self.assertEqual(summary["totals"]["sample_size"], 2)  # AAPL signal + MSFT unmatched
        # Any fill-backed contract is evidenced; only closed (realized) legs
        # count as measured. Open obligations report net=None and land in
        # unknown_count — collected premium is unrealized context, never profit.
        self.assertEqual(summary["totals"]["measured_count"], 1)
        self.assertEqual(summary["totals"]["unknown_count"], 1)
        self.assertAlmostEqual(summary["totals"]["net_dollars"], 73.0)  # only realized AAPL leg

        aapl = next(r for r in summary["outcomes"] if r["ticker"] == "AAPL")
        self.assertEqual(aapl["outcome_status"], "measured")
        self.assertEqual(aapl["attribution"], "inferred")
        self.assertAlmostEqual(aapl["net_pnl"], 73.0)
        self.assertAlmostEqual(aapl["quoted_credit_per_contract"], 110.0)
        self.assertAlmostEqual(aapl["filled_credit_per_contract"], 105.0)  # 1.05/share × 100
        self.assertAlmostEqual(aapl["slippage_per_contract"], -5.0)
        # CSP capital-days: strike×100 = 7000 held 31 days (closed tranche).
        self.assertAlmostEqual(aapl["capital_days"], 7000.0 * 31)
        self.assertAlmostEqual(aapl["owner_efficiency"], 73.0 / (7000.0 * 31))
        self.assertFalse(aapl["open"])
        self.assertEqual(len(aapl["fills"]), 2)

        msft = next(r for r in summary["outcomes"] if r["ticker"] == "MSFT")
        self.assertEqual(msft["signal_type"], "unmatched")
        self.assertEqual(msft["attribution"], "unattributed")
        self.assertIsNone(msft["quoted_credit_per_contract"])
        self.assertEqual(msft["outcome_status"], "open")  # open short: premium is unrealized
        self.assertTrue(msft["open"])
        self.assertIsNone(msft["net_pnl"])
        self.assertIsNone(msft["gross_premium_pnl"])
        self.assertAlmostEqual(msft["unrealized_premium_dollars"], 80.0)  # 0.80/share × 100, qty 1
        self.assertAlmostEqual(msft["collected_premium_dollars"], 80.0)

        # Identity scoping: a different account sees nothing.
        other = OutcomeService(self.db).get_outcome_summary("REAL", opaque_account_id("other"))
        self.assertEqual(other["totals"]["sample_size"], 0)

    def test_unknown_fees_yield_unknown_outcome(self):
        self._publish_signal_run()
        conn = _FakeBrokerConnection(
            [_deal("D1", "O1", AAPL_PUT_CODE, "SELL", 1.0, 1.05, "2026-01-05 14:30:00")],
            {},  # fee query returns nothing: fees stay NULL, never zero
            {},
        )
        result = FillsService(conn, self.db).ingest_history_fills(days=30)
        self.assertTrue(result["ok"])
        self.assertEqual(result["fees_updated"], 0)

        summary = OutcomeService(self.db).get_outcome_summary(ENV, OPAQUE)
        self.assertEqual(summary["totals"]["sample_size"], 1)
        self.assertEqual(summary["totals"]["unknown_count"], 1)
        self.assertEqual(summary["totals"]["measured_count"], 0)
        self.assertIsNone(summary["outcomes"][0]["net_pnl"])
        self.assertTrue(summary["outcomes"][0]["open"])

    def test_signal_without_fills_is_pending(self):
        self._publish_signal_run()
        summary = OutcomeService(self.db).get_outcome_summary(ENV, OPAQUE)
        self.assertEqual(summary["totals"]["sample_size"], 1)
        self.assertEqual(summary["totals"]["pending_count"], 1)
        self.assertEqual(summary["totals"]["coverage_pct"], 0.0)

    def test_cash_flows_persisted_and_classified(self):
        conn = self._fake_connection()
        service = FillsService(conn, self.db)
        result = service.ingest_cash_flows(["2026-01-10"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["ingested"], 1)

        flows = self.db.get_cash_flows(env=ENV, account_id=OPAQUE)
        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0]["flow_type"], "Deposit")
        self.assertAlmostEqual(flows[0]["amount"], 5000.0)
        self.assertTrue(is_capital_movement_cash_flow(flows[0]["flow_type"], flows[0]["remark"]))
        # Cash movements never enter trading attribution: no fills → no outcomes.
        summary = OutcomeService(self.db).get_outcome_summary(ENV, OPAQUE)
        self.assertEqual(summary["totals"]["sample_size"], 0)
        self.assertEqual(summary["totals"]["net_dollars"], 0.0)


class TestRouteIntegration(TestIngestToAttributionToSummary):
    """Full route → OutcomeService → SQLite → fake broker (ingest) path."""

    def setUp(self):
        from api.routes.utils import _RATE_LIMIT_BUCKETS

        _RATE_LIMIT_BUCKETS.clear()  # clears the persistent route_rate_limits store too
        super().setUp()

    def _app(self, conn):
        from flask import Flask

        import api.routes.options as options_routes
        from api.routes.options import bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(bp)
        service = OutcomeService(self.db, connection_provider=lambda: conn)
        self.patchers = [
            patch.object(options_routes, "get_outcome_service", return_value=service),
            patch("api.services.config.get_current_identity", return_value=(ENV, OPAQUE)),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.addCleanup(lambda: [p.stop() for p in self.patchers])
        return app

    @patch("api.routes.utils.probe_opend_status", return_value={"status": "connected"})
    def test_ingest_route_then_summary_route(self, _mock_probe):
        self._publish_signal_run()
        conn = self._fake_connection()
        app = self._app(conn)

        with app.test_client() as client:
            resp = client.post("/api/options/analytics/outcomes/ingest?days=30&cash_flow_days=1")
            self.assertEqual(resp.status_code, 200)
            body = resp.get_json()
            self.assertTrue(body["success"])
            self.assertEqual(body["fills"]["ingested"], 4)

            resp = client.get("/api/options/analytics/outcomes")
            self.assertEqual(resp.status_code, 200)
            payload = resp.get_json()
            self.assertTrue(payload["success"])
            self.assertIn("totals", payload)  # dict payloads merge into the body
            data = payload
            self.assertEqual(data["totals"]["sample_size"], 2)
            self.assertEqual(data["totals"]["measured_count"], 1)
            self.assertAlmostEqual(data["totals"]["net_dollars"], 73.0)

            # Drill-down by ticker reaches the service through the route.
            resp = client.get("/api/options/analytics/outcomes?ticker=MSFT")
            data = resp.get_json()
            self.assertEqual(data["totals"]["sample_size"], 1)
            self.assertEqual(data["outcomes"][0]["signal_type"], "unmatched")

            # Rate limiter: 6/min on ingest, 7th call is rejected.
            for _ in range(5):
                client.post("/api/options/analytics/outcomes/ingest")
            resp = client.post("/api/options/analytics/outcomes/ingest")
            self.assertEqual(resp.status_code, 429)

        # 6 accepted ingests: 1 with cash_flow_days=1, then 5 × default 7
        # clearing dates. Fee backfill re-queries every accepted ingest
        # because the share fill's order (O3) has no fee entry — its fills
        # legitimately stay fee-NULL (unknown ≠ zero).
        self.assertEqual(len(conn.deal_calls), 6)
        self.assertEqual(len(conn.fee_calls), 6)
        self.assertEqual(len(conn.cash_calls), 36)


if __name__ == "__main__":
    unittest.main()
