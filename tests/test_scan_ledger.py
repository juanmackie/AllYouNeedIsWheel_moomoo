"""
Tests for core/scan_ledger.py — Wheel Scan Ledger
"""

import unittest
import json
import hashlib
from unittest.mock import MagicMock, patch
from datetime import datetime

from core.scan_ledger import (
    ScanLedgerEntry,
    ScanLedger,
    compute_config_hash,
    compute_portfolio_hash,
    extract_data_sources,
    _stable_json,
)


class TestScanLedgerHashes(unittest.TestCase):
    def test_compute_config_hash_is_deterministic(self):
        config = {"scoring": {"version": "1.0"}, "filters": {"min_dte": 30}}
        h1 = compute_config_hash(config)
        h2 = compute_config_hash(config)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)

    def test_compute_config_hash_changes_with_config(self):
        config_a = {"scoring": {"version": "1.0"}}
        config_b = {"scoring": {"version": "2.0"}}
        self.assertNotEqual(compute_config_hash(config_a), compute_config_hash(config_b))

    def test_compute_portfolio_hash_is_deterministic(self):
        ctx = {
            "positions": {"AAPL": {"position": 200}, "MSFT": {"position": 100}},
            "cash_balance": 50000.0,
            "account_value": 150000.0,
        }
        h1 = compute_portfolio_hash(ctx)
        h2 = compute_portfolio_hash(ctx)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)

    def test_compute_portfolio_hash_empty(self):
        ctx = {"positions": {}, "cash_balance": 0, "account_value": 0}
        h = compute_portfolio_hash(ctx)
        self.assertEqual(len(h), 16)

    def test_compute_portfolio_hash_unknown_on_error(self):
        h = compute_portfolio_hash({"positions": "not_a_dict"})
        self.assertEqual(h, "unknown")


class TestScanLedgerSources(unittest.TestCase):
    def test_extract_data_sources_empty(self):
        sources = extract_data_sources({})
        self.assertEqual(sources, [])

    def test_extract_data_sources_from_portfolio(self):
        ctx = {"vix_regime": {"vix": 18}, "broker_buying_power": 50000}
        sources = extract_data_sources(ctx)
        names = [s["name"] for s in sources]
        self.assertIn("vix_regime", names)
        self.assertIn("moomoo_portfolio", names)

    def test_extract_data_sources_from_decisions(self):
        from core.wheel_decision import WheelDecision
        d = WheelDecision(
            ticker="AAPL",
            price_source="moomoo",
            chain_source="moomoo",
            greeks_source="Black-Scholes computed",
            iv_source="broker",
            earnings_source="provider/cache/manual",
            macro_source="FRED/cache/disabled",
        )
        sources = extract_data_sources({}, [d])
        names = [s["name"] for s in sources]
        for expected in ("moomoo", "Black-Scholes computed", "broker", "provider/cache/manual"):
            self.assertIn(expected, names)

    def test_extract_data_sources_ignores_missing(self):
        from core.wheel_decision import WheelDecision
        d = WheelDecision(ticker="AAPL", price_source="missing")
        sources = extract_data_sources({}, [d])
        self.assertEqual(sources, [])


class TestScanLedgerEntry(unittest.TestCase):
    def test_default_creation(self):
        entry = ScanLedgerEntry()
        self.assertEqual(entry.scan_type, "recommendations")
        self.assertEqual(entry.scoring_version, "1.0")
        self.assertEqual(entry.data_sources, [])
        self.assertEqual(entry.warnings, [])
        self.assertEqual(entry.top_signals, [])
        self.assertEqual(entry.blocked_candidates, [])
        self.assertEqual(entry.total_candidates, 0)
        self.assertEqual(entry.passed_count, 0)
        self.assertEqual(entry.blocked_count, 0)

    def test_to_dict_roundtrip(self):
        entry = ScanLedgerEntry(
            scan_type="recommendations",
            timestamp=datetime.now().isoformat(),
            config_hash="abc123",
            portfolio_hash="def456",
            data_sources=[{"name": "moomoo", "status": "used"}],
            warnings=["Low IV warning"],
            top_signals=[{"ticker": "AAPL", "score": 85}],
            blocked_candidates=[{"ticker": "MSFT", "reason": "no cash"}],
            total_candidates=10,
            passed_count=3,
            blocked_count=7,
            elapsed_seconds=12.5,
        )
        d = entry.to_dict()
        self.assertEqual(d["scan_type"], "recommendations")
        self.assertEqual(d["total_candidates"], 10)
        self.assertEqual(d["passed_count"], 3)
        self.assertEqual(d["blocked_count"], 7)

    def test_backward_compatible_empty_warnings(self):
        entry = ScanLedgerEntry()
        self.assertEqual(entry.warnings, [])


class TestScanLedgerDB(unittest.TestCase):
    def setUp(self):
        self.mock_db = MagicMock()
        self.mock_db.db_path = ":memory:"
        self.ledger = ScanLedger(self.mock_db)

    def tearDown(self):
        self.ledger.close()

    @patch("db.sqlite_pool.pooled_connection")
    def test_record_entry(self, mock_pool):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_cursor.fetchone.return_value = [1]
        mock_pool.return_value.__enter__.return_value = mock_conn

        entry = ScanLedgerEntry(
            scan_type="recommendations",
            timestamp="2026-01-01T00:00:00",
            config_hash="abc",
            portfolio_hash="def",
            data_sources=[{"name": "moomoo", "status": "used"}],
            warnings=["test warning"],
            total_candidates=5,
            passed_count=2,
            blocked_count=3,
            elapsed_seconds=1.5,
        )
        entry_id = self.ledger.record(entry)
        self.assertEqual(entry_id, 1)
        mock_conn.execute.assert_called()

    @patch("db.sqlite_pool.pooled_connection")
    def test_record_with_error_message(self, mock_pool):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_cursor.fetchone.return_value = [1]
        mock_pool.return_value.__enter__.return_value = mock_conn

        entry = ScanLedgerEntry(
            scan_type="recommendations",
            timestamp="2026-01-01T00:00:00",
            config_hash="abc",
            portfolio_hash="def",
            error_message="Connection timed out",
        )
        entry_id = self.ledger.record(entry)
        self.assertIsNotNone(entry_id)

    @patch("db.sqlite_pool.pooled_connection")
    def test_get_recent_empty(self, mock_pool):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_pool.return_value.__enter__.return_value = mock_conn

        entries = self.ledger.get_recent(limit=10)
        self.assertEqual(entries, [])

    @patch("db.sqlite_pool.pooled_connection")
    def test_get_stats(self, mock_pool):
        mock_conn = MagicMock()
        mock_row = {
            "total": 10, "errors": 2, "avg_elapsed": 1.5,
            "avg_candidates": 5, "avg_passed": 3, "avg_blocked": 2,
        }
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_pool.return_value.__enter__.return_value = mock_conn

        stats = self.ledger.get_stats()
        self.assertEqual(stats.get("total"), 10)
        self.assertEqual(stats.get("errors"), 2)


class TestStableJson(unittest.TestCase):
    def test_stable_json_sorts_keys(self):
        a = _stable_json({"b": 2, "a": 1})
        b = _stable_json({"a": 1, "b": 2})
        self.assertEqual(a, b)

    def test_stable_json_handles_nested(self):
        result = _stable_json({"x": {"z": 3, "y": 2}})
        self.assertIn('"y"', result)
        self.assertIn('"z"', result)


class TestScanLedgerWritesFromRecommendations(unittest.TestCase):
    """Verify scan ledger entry construction matches what the recommendation engine produces."""

    def test_entry_fields_for_empty_scan(self):
        """An empty scan should produce a valid ledger entry with zero counts."""
        from core.scan_ledger import ScanLedgerEntry, compute_config_hash, compute_portfolio_hash, extract_data_sources

        portfolio_context = {
            "positions": {}, "available_cash": 0, "broker_buying_power": 0,
        }
        entry = ScanLedgerEntry(
            scan_type="recommendations",
            config_hash=compute_config_hash({"growth_mode": {}}),
            portfolio_hash=compute_portfolio_hash(portfolio_context),
            elapsed_seconds=0.5,
            total_candidates=0,
            passed_count=0,
            blocked_count=0,
            data_sources=extract_data_sources(portfolio_context),
        )
        self.assertEqual(entry.scan_type, "recommendations")
        self.assertEqual(entry.total_candidates, 0)
        self.assertEqual(entry.passed_count, 0)
        self.assertEqual(entry.blocked_count, 0)
        self.assertEqual(len(entry.config_hash), 16)
        self.assertEqual(len(entry.portfolio_hash), 16)

    def test_entry_fields_for_error(self):
        """An error scan should record the error message."""
        from core.scan_ledger import ScanLedgerEntry

        entry = ScanLedgerEntry(
            scan_type="recommendations",
            error_message="Failed to establish connection to moomoo",
        )
        self.assertEqual(entry.error_message, "Failed to establish connection to moomoo")
        self.assertEqual(entry.total_candidates, 0)

    def test_entry_signals_and_blocked(self):
        """Top signals and blocked candidates should be captured."""
        from core.scan_ledger import ScanLedgerEntry

        entry = ScanLedgerEntry(
            scan_type="recommendations",
            top_signals=[
                {"ticker": "AAPL", "option_type": "PUT", "strike": 145.0, "score": 85.0},
            ],
            blocked_candidates=[
                {"ticker": "MSFT", "reason": "no cash"},
            ],
            total_candidates=10,
            passed_count=1,
            blocked_count=9,
        )
        self.assertEqual(len(entry.top_signals), 1)
        self.assertEqual(len(entry.blocked_candidates), 1)
        self.assertEqual(entry.passed_count, 1)
        self.assertEqual(entry.blocked_count, 9)


class TestScanLedgerIntegrationWrite(unittest.TestCase):
    """Integration test: verify a real ledger record call writes one row."""

    @patch("db.sqlite_pool.pooled_connection")
    def test_record_writes_one_row(self, mock_pool):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_cursor.fetchone.return_value = [42]
        mock_pool.return_value.__enter__.return_value = mock_conn

        mock_db = MagicMock()
        mock_db.db_path = ":memory:"
        ledger = ScanLedger(mock_db)

        entry = ScanLedgerEntry(
            scan_type="recommendations",
            timestamp="2026-05-29T12:00:00",
            config_hash="abc123",
            portfolio_hash="def456",
            elapsed_seconds=2.3,
            total_candidates=8,
            passed_count=3,
            blocked_count=5,
            data_sources=[{"name": "moomoo", "status": "used"}],
            top_signals=[{"ticker": "AAPL", "score": 90}],
            blocked_candidates=[{"ticker": "MSFT", "reason": "no cash"}],
        )
        row_id = ledger.record(entry)
        self.assertEqual(row_id, 42)

        calls = mock_conn.execute.call_args_list
        insert_call = calls[0]
        self.assertIn("INSERT INTO scan_ledger", insert_call[0][0])
        params = insert_call[0][1]
        self.assertEqual(params[0], "recommendations")
        self.assertEqual(params[2], "abc123")
        self.assertEqual(params[3], "def456")
        self.assertEqual(params[9], 8)
        self.assertEqual(params[10], 3)
        self.assertEqual(params[11], 5)
        ledger.close()


if __name__ == "__main__":
    unittest.main()
