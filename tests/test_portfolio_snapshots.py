"""Tests for core/portfolio_snapshot.py + db/portfolio_snapshots_repository.py.

Covers: pure snapshot building from a portfolio context, repository
save/latest/history round-trip, idempotency per run_id, and migration to
schema version 7.
"""

import os
import sqlite3
import tempfile
import unittest

from core.portfolio_snapshot import build_portfolio_snapshot
from db.database import OptionsDatabase
from db.portfolio_snapshots_repository import PortfolioSnapshotsRepository
from db.sqlite_pool import close_connection_pool


def _context():
    return {
        "account_value": 25_000.0,
        "available_cash": 10_000.0,
        "cash_reserved_for_csp": 6_000.0,
        "cash_available_for_csp": 4_000.0,
        "broker_buying_power": 12_000.0,
        "positions": {
            "US.AAPL": {"symbol": "US.AAPL", "security_type": "STK", "position": 100, "avg_cost": 210.5},
            # Duplicated under both raw and canonical key (context behavior)
            "AAPL": {"symbol": "AAPL", "security_type": "STK", "position": 100, "avg_cost": 210.5},
            "US.TSLA260904P00300000": {
                "symbol": "US.TSLA260904P00300000",
                "security_type": "OPT",
                "position": -1,
                "option_type": "PUT",
                "strike": 300,
                "expiration": "20260904",
                "market_price": 2.4,
            },
            # Long options and unknown types are excluded
            "US.SPY260904C00600000": {
                "symbol": "US.SPY260904C00600000",
                "security_type": "OPT",
                "position": 1,
                "option_type": "CALL",
                "strike": 600,
                "expiration": "20260904",
            },
            "US.BAD": {"symbol": "US.BAD", "security_type": "BOND", "position": 5},
        },
    }


class TestBuildPortfolioSnapshot(unittest.TestCase):
    def test_builds_deduped_sorted_positions(self):
        snap = build_portfolio_snapshot(_context(), run_id="r1", env="REAL", opaque_account="abc123", captured_at="t1")
        self.assertEqual(snap["run_id"], "r1")
        self.assertEqual(snap["net_liquidation"], 25_000.0)
        self.assertEqual(snap["cash_available"], 10_000.0)
        self.assertEqual(snap["cash_reserved_for_csp"], 6_000.0)
        self.assertEqual(snap["cash_available_for_csp"], 4_000.0)
        self.assertEqual(snap["broker_buying_power"], 12_000.0)

        symbols = [p["symbol"] for p in snap["positions"]]
        # parse_moomoo_symbol strips only the "US." prefix; OCC option keys
        # stay intact. Deduped (raw + canonical AAPL) and sorted.
        self.assertEqual(symbols, ["AAPL", "SPY260904C00600000", "TSLA260904P00300000"])

        tsla = snap["positions"][2]
        self.assertEqual(tsla["qty"], -1)
        self.assertEqual(tsla["contract_key"], "TSLA260904P00300000 20260904 P300")

    def test_none_and_empty_contexts(self):
        snap = build_portfolio_snapshot(None, "r", "SIMULATE", "", "t")
        self.assertEqual(snap["net_liquidation"], 0.0)
        self.assertEqual(snap["positions"], [])
        snap = build_portfolio_snapshot({}, "r", "SIMULATE", "", "t")
        self.assertEqual(snap["positions"], [])


class TestPortfolioSnapshotsRepository(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_snapshots.db")
        self.db = OptionsDatabase(self.db_path)
        self.repo = PortfolioSnapshotsRepository(self.db_path)

    def tearDown(self):
        if hasattr(self, "db") and self.db is not None:
            self.db.close()
        close_connection_pool(self.db_path)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def _snap(self, run_id, captured_at, nav=1000.0):
        return {
            "run_id": run_id,
            "captured_at": captured_at,
            "env": "SIMULATE",
            "account_id": "hash1",
            "net_liquidation": nav,
            "cash_available": nav * 0.4,
            "cash_reserved_for_csp": nav * 0.6,
            "cash_available_for_csp": nav * 0.4,
            "broker_buying_power": nav,
            "positions": [{"symbol": "AAPL", "security_type": "STK", "qty": 10}],
        }

    def test_save_latest_history_roundtrip(self):
        self.assertTrue(self.repo.save_portfolio_snapshot(self._snap("r1", "2026-08-20T15:00:00")))
        self.assertTrue(self.repo.save_portfolio_snapshot(self._snap("r2", "2026-08-21T15:00:00", nav=1100.0)))

        latest = self.repo.get_latest_portfolio_snapshot()
        self.assertEqual(latest["run_id"], "r2")
        self.assertEqual(latest["net_liquidation"], 1100.0)
        self.assertEqual(latest["positions"][0]["symbol"], "AAPL")

        history = self.repo.get_portfolio_history()
        self.assertEqual([s["run_id"] for s in history], ["r1", "r2"])  # oldest first

    def test_idempotent_per_run_id(self):
        self.repo.save_portfolio_snapshot(self._snap("r1", "2026-08-20T15:00:00"))
        self.repo.save_portfolio_snapshot(self._snap("r1", "2026-08-20T16:00:00", nav=9999.0))
        history = self.repo.get_portfolio_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["net_liquidation"], 1000.0)  # first write wins

    def test_limit_clamps(self):
        for i in range(5):
            self.repo.save_portfolio_snapshot(self._snap(f"r{i}", f"2026-08-2{i}T15:00:00"))
        self.assertEqual(len(self.repo.get_portfolio_history(limit=3)), 3)
        self.assertEqual(self.repo.get_portfolio_history(limit=0), [])

    def test_migration_sets_user_version_7(self):
        conn = sqlite3.connect(self.db_path)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        self.assertGreaterEqual(version, 7)


if __name__ == "__main__":
    unittest.main()
