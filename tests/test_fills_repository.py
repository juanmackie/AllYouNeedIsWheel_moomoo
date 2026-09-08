"""Focused tests for db/fills_repository.py — schema v10, idempotent
account-scoped ingestion of broker fills/fees/cash flows, and the
OptionsDatabase facade passthroughs.
"""

import os
import sqlite3
import tempfile
import unittest

from db.database import OptionsDatabase
from db.fills_repository import CashFlowRepository, FillsRepository
from db.schema import migrate_database
from db.sqlite_pool import close_connection_pool


def _fill_row(fill_id="D1", env="REAL", account_id="abc123def456", **overrides):
    row = {
        "fill_id": fill_id,
        "order_id": "O1",
        "captured_at": "2026-08-01 14:30:00",
        "ingested_at": "2026-08-01 15:00:00",
        "env": env,
        "account_id": account_id,
        "ticker": "AAPL",
        "security_type": "OPT",
        "contract_key": "AAPL 20260918 P150.0",
        "option_type": "PUT",
        "strike": 150.0,
        "expiration": "20260918",
        "side": "SELL",
        "qty": 1,
        "price": 1.5,
        "fees": None,
        "raw_json": {"deal_id": fill_id},
    }
    row.update(overrides)
    return row


class TestFillsRepository(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_fills.db")
        self.db = OptionsDatabase(self.db_path)
        self.repo = FillsRepository(self.db_path)

    def tearDown(self):
        self.db.close()
        close_connection_pool(self.db_path)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_schema_v10_tables_exist(self):
        conn = sqlite3.connect(self.db_path)
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("option_fills", tables)
            self.assertIn("account_cash_flows", tables)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, 10)
        finally:
            conn.close()

    def test_migration_from_v9_is_idempotent_and_additive(self):
        # Simulate an existing v9 database, then migrate forward.
        self.db.close()
        close_connection_pool(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA user_version = 9")
        conn.commit()
        conn.close()

        migrate_database(self.db_path)

        conn = sqlite3.connect(self.db_path)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, 10)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(option_fills)")}
            self.assertIn("fill_id", cols)
            self.assertIn("fees", cols)
        finally:
            conn.close()

    def test_save_fills_is_idempotent_per_broker_deal_id(self):
        first = self.repo.save_fills([_fill_row("D1"), _fill_row("D2")])
        self.assertEqual(first, 2)
        # Re-ingesting identical rows (e.g. next refresh) adds nothing.
        again = self.repo.save_fills([_fill_row("D1"), _fill_row("D2")])
        self.assertEqual(again, 0)
        rows = self.repo.get_fills(env="REAL", account_id="abc123def456")
        self.assertEqual(len(rows), 2)

    def test_fill_without_fill_id_is_rejected(self):
        inserted = self.repo.save_fills([_fill_row("D1"), _fill_row("")])
        self.assertEqual(inserted, 1)

    def test_identity_scoping(self):
        self.repo.save_fills(
            [_fill_row("D1", env="REAL", account_id="acctA"), _fill_row("D2", env="REAL", account_id="acctB")]
        )
        rows_a = self.repo.get_fills(env="REAL", account_id="acctA")
        self.assertEqual([r["fill_id"] for r in rows_a], ["D1"])
        rows_b = self.repo.get_fills(env="REAL", account_id="acctB")
        self.assertEqual([r["fill_id"] for r in rows_b], ["D2"])

    def test_fees_start_null_and_update_only_once(self):
        self.repo.save_fills([_fill_row("D1", order_id="O1")])
        rows = self.repo.get_fills(env="REAL", account_id="abc123def456", order_id="O1")
        self.assertIsNone(rows[0]["fees"])

        self.assertEqual(self.repo.get_order_ids_missing_fees(env="REAL", account_id="abc123def456"), ["O1"])
        updated = self.repo.update_fill_fees({"D1": 1.5})
        self.assertEqual(updated, 1)
        rows = self.repo.get_fills(env="REAL", account_id="abc123def456", order_id="O1")
        self.assertAlmostEqual(rows[0]["fees"], 1.5)

        # An already-allocated fee is never overwritten by a later backfill.
        updated_again = self.repo.update_fill_fees({"D1": 99.0})
        self.assertEqual(updated_again, 0)

    def test_raw_json_round_trips(self):
        raw = {"deal_id": "D1", "counter_broker_id": "XX"}
        self.repo.save_fills([_fill_row("D1", raw_json=raw)])
        rows = self.repo.get_fills(env="REAL", account_id="abc123def456")
        self.assertEqual(rows[0]["raw_json"], raw)

    def test_facade_passthroughs(self):
        self.assertEqual(self.db.save_fills([_fill_row("D9")]), 1)
        self.assertIsNotNone(self.db.get_latest_fill_captured_at(env="REAL", account_id="abc123def456"))
        self.assertEqual(self.db.get_fills(env="REAL", account_id="abc123def456")[0]["ticker"], "AAPL")
        self.assertEqual(self.db.update_fill_fees({"D9": 2.0}), 1)


class TestCashFlowRepository(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_cashflow.db")
        self.db = OptionsDatabase(self.db_path)
        self.repo = CashFlowRepository(self.db_path)

    def tearDown(self):
        self.db.close()
        close_connection_pool(self.db_path)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def _flow(self, cashflow_id="CF1", account_id="acctA", **overrides):
        row = {
            "cashflow_id": cashflow_id,
            "clearing_date": "2026-08-01",
            "settlement_date": "2026-08-01",
            "currency": "USD",
            "direction": "IN",
            "flow_type": "Deposit",
            "amount": 1000.0,
            "remark": "",
            "env": "REAL",
            "account_id": account_id,
            "raw_json": {"cashflow_id": cashflow_id},
        }
        row.update(overrides)
        return row

    def test_idempotent_ingest_and_identity_scoping(self):
        self.assertEqual(self.repo.save_cash_flows([self._flow("CF1"), self._flow("CF2", account_id="acctB")]), 2)
        self.assertEqual(self.repo.save_cash_flows([self._flow("CF1")]), 0)

        flows_a = self.repo.get_cash_flows(env="REAL", account_id="acctA")
        self.assertEqual(len(flows_a), 1)
        self.assertEqual(flows_a[0]["flow_type"], "Deposit")
        flows_b = self.repo.get_cash_flows(env="REAL", account_id="acctB")
        self.assertEqual(len(flows_b), 1)

        self.assertEqual(self.db.get_cash_flows(env="REAL", account_id="acctA")[0]["amount"], 1000.0)


if __name__ == "__main__":
    unittest.main()
