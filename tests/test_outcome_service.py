"""Tests for api/services/outcome_service.py — outcome summary service layer.

Covers the pure matching/aggregation helpers (signal↔fill join, quoted vs
filled credit, net-of-fee outcomes, capital-days, groups with evidence
metadata) plus OutcomeService against a fake database. No broker, no Flask.
"""

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.outcome_service import (
    aggregate_group,
    build_outcome_records,
    dte_bucket,
    fill_identity,
    filter_records,
    quoted_credit_per_contract,
    signal_identity,
)


def _signal(ticker="AAPL", expiration="20260220", option_type="PUT", strike=70.0, **extra):
    base = {
        "ticker": ticker,
        "option_type": option_type,
        "expiration": expiration,
        "strike": strike,
        "dte": 30,
        "bid_premium_per_contract": 1.10,
        "event_tier": "earnings_week",
        "quality_tier": "A",
        "stock_price": 80.0,
        "signal_type": "csp" if option_type == "PUT" else "covered_call",
    }
    base.update(extra)
    return base


def _fill(ticker="AAPL", expiration="20260220", option_type="PUT", strike=70.0, side="SELL", qty=1, price=1.05, fees=0.0, captured_at="2026-01-05T14:30:00", **extra):
    row = {
        "fill_id": f"deal-{side}-{captured_at}-{price}",
        "order_id": "ord-1",
        "ticker": ticker,
        "security_type": "OPT",
        "option_type": option_type,
        "expiration": expiration,
        "strike": strike,
        "side": side,
        "qty": qty,
        "price": price,
        "fees": fees,
        "captured_at": captured_at,
    }
    row.update(extra)
    return row


def _snapshot(signals, generated_at="2026-01-02T15:00:00", preset_key="balanced", run_id="run-1"):
    return {
        "run": {"generated_at": generated_at, "preset_key": preset_key, "run_id": run_id},
        "signals": signals,
        "csp_picks": [],
        "cc_decisions": [],
    }


NOW = datetime(2026, 2, 20, 15, 0, 0)


class TestIdentities(unittest.TestCase):
    def test_signal_identity_normalizes_strike_and_expiration(self):
        self.assertEqual(signal_identity(_signal(strike=70, expiration="2026-02-20")), ("AAPL", "20260220", "PUT", 70.0))

    def test_signal_identity_rejects_incomplete(self):
        self.assertIsNone(signal_identity({"ticker": "AAPL"}))
        self.assertIsNone(signal_identity(_signal(strike=None)))
        self.assertIsNone(signal_identity(_signal(option_type="BANANA")))

    def test_fill_identity_ignores_stock_fills(self):
        self.assertIsNone(fill_identity({"security_type": "STK", "ticker": "AAPL"}))
        self.assertEqual(fill_identity(_fill()), ("AAPL", "20260220", "PUT", 70.0))

    def test_quoted_credit_fallback_chain(self):
        self.assertEqual(quoted_credit_per_contract(_signal()), 1.10)
        self.assertEqual(quoted_credit_per_contract(_signal(bid_premium_per_contract=None, limit_target_per_contract=0.95)), 0.95)
        self.assertIsNone(quoted_credit_per_contract(_signal(bid_premium_per_contract=None, limit_target_per_contract=None, premium_per_contract=None)))


class TestDteBucket(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(dte_bucket(3), "0-7")
        self.assertEqual(dte_bucket(14), "8-14")
        self.assertEqual(dte_bucket(31), "31-45")
        self.assertEqual(dte_bucket(120), "90+")
        self.assertEqual(dte_bucket(None), "unknown")
        self.assertEqual(dte_bucket("nope"), "unknown")


class TestBuildOutcomeRecords(unittest.TestCase):
    def test_measured_outcome_with_fees(self):
        records = build_outcome_records(
            [_snapshot([_signal()])],
            [
                _fill(side="SELL", price=1.05, fees=1.0, captured_at="2026-01-05T14:30:00"),
                _fill(side="BUY", price=0.30, fees=1.0, captured_at="2026-02-05T14:30:00"),
            ],
            now=NOW,
        )
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["outcome_status"], "measured")
        # net = (1.05 - 0.30) * 100 - 2.0 fees
        self.assertAlmostEqual(rec["net_pnl"], 73.0)
        self.assertAlmostEqual(rec["filled_credit_per_contract"], 1.05)
        self.assertAlmostEqual(rec["quoted_credit_per_contract"], 1.10)
        self.assertAlmostEqual(rec["slippage_per_contract"], -0.05)
        self.assertAlmostEqual(rec["slippage_dollars"], -5.0)
        self.assertAlmostEqual(rec["fees_total"], 2.0)
        self.assertFalse(rec["open"])
        self.assertEqual(len(rec["fills"]), 2)

    def test_unknown_fees_never_become_zero(self):
        records = build_outcome_records(
            [_snapshot([_signal()])],
            [_fill(side="SELL", price=1.05, fees=None)],
            now=NOW,
        )
        rec = records[0]
        self.assertEqual(rec["outcome_status"], "unknown")
        self.assertIsNone(rec["net_pnl"])
        self.assertTrue(rec["fees_known"] is False)
        self.assertAlmostEqual(rec["gross_premium_pnl"], 105.0)

    def test_pending_when_no_fills(self):
        records = build_outcome_records([_snapshot([_signal()])], [], now=NOW)
        self.assertEqual(records[0]["outcome_status"], "pending")
        self.assertIsNone(records[0]["net_pnl"])

    def test_open_csp_capital_days_accrue_to_now(self):
        records = build_outcome_records(
            [_snapshot([_signal()])],
            [_fill(side="SELL", price=1.05, fees=1.0, captured_at="2026-01-05T14:30:00")],
            now=NOW,
        )
        rec = records[0]
        self.assertTrue(rec["open"])
        # Jan 5 14:30 → Feb 20 15:00 = 46 days + 0.5h; capital = strike*100 = 7000
        expected_days = 7000 * 46.0208333333
        self.assertAlmostEqual(rec["capital_days"], expected_days, places=2)
        self.assertAlmostEqual(rec["owner_efficiency"], 104.0 / expected_days)

    def test_covered_call_capital_uses_signal_stock_price(self):
        records = build_outcome_records(
            [_snapshot([_signal(option_type="CALL", strike=85.0, signal_type="covered_call")])],
            [_fill(option_type="CALL", strike=85.0, side="SELL", price=1.00, fees=1.0, captured_at="2026-02-19T14:30:00")],
            now=NOW,
        )
        rec = records[0]
        self.assertAlmostEqual(rec["capital_days"], 8000 * 1.0208333333, places=2)

    def test_covered_call_without_stock_price_has_unknown_capital(self):
        records = build_outcome_records(
            [_snapshot([_signal(option_type="CALL", strike=85.0, stock_price=None)])],
            [_fill(option_type="CALL", strike=85.0, side="SELL", price=1.00, fees=1.0, captured_at="2026-02-19T14:30:00")],
            now=NOW,
        )
        self.assertIsNone(records[0]["capital_days"])
        self.assertIsNone(records[0]["owner_efficiency"])

    def test_partial_close_per_tranche_capital_days(self):
        records = build_outcome_records(
            [_snapshot([_signal()])],
            [
                _fill(side="SELL", qty=2, price=1.05, fees=2.0, captured_at="2026-01-05T14:30:00"),
                _fill(side="BUY", qty=1, price=0.50, fees=1.0, captured_at="2026-01-20T14:30:00"),
            ],
            now=NOW,
        )
        rec = records[0]
        self.assertAlmostEqual(rec["open_contracts"], 1)
        # closed tranche: 15 days × 7000; open remainder keeps accruing from
        # its original entry (Jan 5 → Feb 20 = 46.0208 days) × 7000
        expected = 7000 * 15 + 7000 * 46.0208333333
        self.assertAlmostEqual(rec["capital_days"], expected, places=2)

    def test_earliest_signal_wins_for_quote(self):
        snaps = [
            _snapshot([_signal(bid_premium_per_contract=0.90)], generated_at="2026-01-10T15:00:00", run_id="run-2"),
            _snapshot([_signal(bid_premium_per_contract=1.10)], generated_at="2026-01-02T15:00:00", run_id="run-1"),
        ]
        records = build_outcome_records(snaps, [_fill(side="SELL", price=1.05, fees=1.0)], now=NOW)
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["quoted_credit_per_contract"], 1.10)
        self.assertEqual(records[0]["run_id"], "run-1")

    def test_unmatched_fills_reported_without_fabricated_quote(self):
        records = build_outcome_records([], [_fill(ticker="MSFT", strike=300.0, expiration="20260320")], now=NOW)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["signal_type"], "unmatched")
        # No stored signal ⇒ no quoted credit; the fill's own net result is
        # still measurable but never given a fabricated quote.
        self.assertIsNone(rec["quoted_credit_per_contract"])
        self.assertIsNone(rec["slippage_per_contract"])
        self.assertEqual(rec["outcome_status"], "measured")
        self.assertAlmostEqual(rec["net_pnl"], 1.05 * 100)

    def test_event_tier_carried_display_only(self):
        records = build_outcome_records([_snapshot([_signal()])], [_fill(side="SELL", price=1.05, fees=0.0)], now=NOW)
        self.assertEqual(records[0]["event_tier"], "earnings_week")
        self.assertEqual(records[0]["quality_tier"], "A")


class TestAggregateGroup(unittest.TestCase):
    def _records(self):
        measured = {
            "outcome_status": "measured",
            "net_pnl": 100.0,
            "capital_days": 7000.0,
            "open": False,
            "quoted_credit_per_contract": 1.10,
            "filled_credit_per_contract": 1.05,
            "slippage_per_contract": -0.05,
            "fees_known": True,
            "fees_total": 1.0,
            "ticker": "AAPL",
        }
        unknown = dict(measured, outcome_status="unknown", net_pnl=None, fees_known=False, fees_total=None)
        pending = dict(measured, outcome_status="pending", net_pnl=None, filled_credit_per_contract=None)
        return [measured, unknown, pending]

    def test_mandatory_evidence_metadata(self):
        agg = aggregate_group(self._records())
        self.assertEqual(agg["sample_size"], 3)
        self.assertEqual(agg["matched_count"], 2)
        self.assertEqual(agg["coverage_pct"], 66.7)
        self.assertEqual(agg["measured_count"], 1)
        self.assertEqual(agg["unknown_count"], 1)
        self.assertEqual(agg["pending_count"], 1)
        self.assertAlmostEqual(agg["net_dollars"], 100.0)
        self.assertAlmostEqual(agg["owner_efficiency"], 100.0 / 7000.0)
        self.assertAlmostEqual(agg["quoted_credit_avg_per_contract"], 1.10)
        self.assertAlmostEqual(agg["filled_credit_avg_per_contract"], 1.05)
        self.assertEqual(agg["fees_unknown_count"], 1)

    def test_empty_group_has_zero_coverage(self):
        agg = aggregate_group([])
        self.assertEqual(agg["sample_size"], 0)
        self.assertEqual(agg["coverage_pct"], 0.0)
        self.assertIsNone(agg["owner_efficiency"])


class TestFilterRecords(unittest.TestCase):
    def test_filters_and_passthrough(self):
        a = {"ticker": "AAPL", "preset_key": "balanced", "event_tier": "earnings_week", "dte_bucket": "31-45"}
        b = {"ticker": "MSFT", "preset_key": "balanced", "event_tier": "untiered", "dte_bucket": "0-7"}
        self.assertEqual(filter_records([a, b], ticker="aapl"), [a])
        self.assertEqual(filter_records([a, b], event_tier="untiered"), [b])
        self.assertEqual(len(filter_records([a, b])), 2)


class TestOutcomeServiceWithFakeDb(unittest.TestCase):
    def test_summary_groups_and_payload(self):
        from api.services.outcome_service import OutcomeService

        fills = [
            _fill(side="SELL", price=1.05, fees=1.0, captured_at="2026-01-05T14:30:00"),
            _fill(side="BUY", price=0.30, fees=1.0, captured_at="2026-02-05T14:30:00"),
        ]
        snapshots = [_snapshot([_signal()], preset_key="balanced")]

        class FakeDb:
            def get_fills(self, **kwargs):
                return fills

            def get_run_snapshots(self, **kwargs):
                return snapshots

        service = OutcomeService(FakeDb(), connection_provider=None)
        payload = service.get_outcome_summary(env="REAL", account_id="abc")

        self.assertTrue(payload["totals"]["sample_size"] >= 1)
        self.assertEqual(payload["count"], 1)
        self.assertIn("by_preset", payload["groups"])
        self.assertIn("by_dte_bucket", payload["groups"])
        self.assertIn("by_ticker", payload["groups"])
        self.assertIn("by_event_tier", payload["groups"])
        self.assertEqual(payload["groups"]["by_preset"][0]["key"], "balanced")
        self.assertEqual(payload["outcomes"][0]["fills"][0]["fill_id"], fills[0]["fill_id"])

    def test_ingest_requires_connection(self):
        from api.services.outcome_service import OutcomeService

        service = OutcomeService(object(), connection_provider=None)
        result = service.ingest_broker_evidence(days=30)
        self.assertFalse(result["ok"])
        self.assertIn("unavailable", result["error"].lower())


if __name__ == "__main__":
    unittest.main()
