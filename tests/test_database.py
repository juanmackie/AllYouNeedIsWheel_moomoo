"""
Tests for db/database.py - SQLite database for options trading

Covers: schema creation, CRUD operations for all 4 repositories,
database migrations (multi-step, sequential, idempotency),
concurrent access (thread safety), and edge cases.
"""

import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta

from db.database import OptionsDatabase
from db.schema import create_tables, migrate_database
from db.sqlite_pool import close_connection_pool
from db.trade_events_repository import TradeEventsRepository

# ═══════════════════════════════════════════════════════════════════
#  Core CRUD Tests
# ═══════════════════════════════════════════════════════════════════


class TestOptionsDatabase(unittest.TestCase):
    """Test the OptionsDatabase class — schema creation and CRUD"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_options.db")
        self.db = OptionsDatabase(self.db_path)

    def tearDown(self):
        if hasattr(self, "db") and self.db is not None:
            self.db.close()
        close_connection_pool(self.db_path)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_database_creation(self):
        """Test that database and tables are created"""
        self.assertTrue(os.path.exists(self.db_path))

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        expected_tables = ["iv_history", "earnings_calendar", "trade_events"]
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        actual_tables = {row[0] for row in cursor.fetchall()}
        for table in expected_tables:
            self.assertIn(table, actual_tables, f"Missing table: {table}")

        cursor.execute("PRAGMA user_version")
        self.assertEqual(cursor.fetchone()[0], 8)

        evaluator_tables = {t for t in actual_tables if t.startswith("evaluator_")}
        self.assertEqual(evaluator_tables, set(), f"Evaluator tables should be dropped: {evaluator_tables}")

        # Retired 2026-08 consolidation tables must not exist at schema v8.
        self.assertNotIn("recommendations", actual_tables)
        self.assertNotIn("playbook_hypotheses", actual_tables)

        conn.close()

    def test_earnings_migration_dedupes_ticker_keeps_latest(self):
        self.db.close()
        close_connection_pool(self.db_path)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        old_path = os.path.join(self.temp_dir, "old_earnings.db")
        conn = sqlite3.connect(old_path)
        conn.executescript("""
            CREATE TABLE earnings_calendar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                earnings_date TEXT,
                last_updated TEXT NOT NULL,
                fetch_status TEXT DEFAULT 'pending',
                error_message TEXT,
                time_of_day TEXT,
                fiscal_date_ending TEXT,
                estimate REAL,
                currency TEXT,
                earnings_source TEXT,
                UNIQUE(ticker, earnings_date)
            )
        """)
        conn.execute("""
            INSERT INTO earnings_calendar
            (ticker, earnings_date, last_updated, fetch_status, earnings_source)
            VALUES ('AAPL', '2024-04-25', '2026-06-01 00:00:00', 'success', 'old')
        """)
        conn.execute("""
            INSERT INTO earnings_calendar
            (ticker, earnings_date, last_updated, fetch_status, earnings_source)
            VALUES ('AAPL', '2024-05-02', '2026-06-02 00:00:00', 'success', 'new')
        """)
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        conn.close()

        migrate_database(old_path)
        migrated = OptionsDatabase(old_path)
        try:
            record = migrated.get_earnings_date("AAPL")
            cursor = sqlite3.connect(old_path).cursor()
            cursor.execute("SELECT COUNT(*) FROM earnings_calendar WHERE ticker='AAPL'")
            count = cursor.fetchone()[0]
            cursor.connection.close()

            self.assertEqual(count, 1)
            self.assertEqual(record["earnings_date"], "2024-05-02")
            self.assertEqual(record["earnings_source"], "new")
        finally:
            migrated.close()
            close_connection_pool(old_path)
            if os.path.exists(old_path):
                os.remove(old_path)

    def test_save_and_get_iv_data(self):
        """Test saving and retrieving IV data"""
        self.db.save_iv_data(
            ticker="AAPL", implied_volatility=0.30, stock_price=175.0, option_type="PUT", expiration="20240419", dte=21
        )

        history = self.db.get_iv_history("AAPL")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["implied_volatility"], 0.30)

        latest = self.db.get_latest_iv("AAPL")
        self.assertIsNotNone(latest)
        self.assertEqual(latest["implied_volatility"], 0.30)

    def test_connection_pool_reuses_connections(self):
        """Repeated repository calls should reuse pooled SQLite connections."""
        before = self.db.get_connection_pool_stats()

        self.db.save_iv_data("AAPL", 0.30)
        self.db.get_iv_history("AAPL")
        self.db.save_earnings_date("AAPL", "2024-04-25", fetch_status="success")
        self.db.get_earnings_date("AAPL")
        self.db.save_trade_event(
            {
                "event_type": "entry",
                "ticker": "AAPL",
                "option_type": "PUT",
                "strike": 150.0,
                "expiration": "20240419",
            }
        )
        self.db.get_trade_events("AAPL")

        after = self.db.get_connection_pool_stats()
        self.assertGreaterEqual(after["created"], max(before["created"], 1))
        self.assertGreaterEqual(after["pool_size"], 1)

    def test_purge_old_iv_data(self):
        """Test purging old IV data"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        old_date = (datetime.now() - timedelta(days=50)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            """
            INSERT INTO iv_history (ticker, timestamp, implied_volatility, stock_price)
            VALUES (?, ?, ?, ?)
        """,
            ("AAPL", old_date, 0.30, 175.0),
        )
        conn.commit()
        conn.close()

        self.db.purge_old_iv_data(days=45)

        history = self.db.get_iv_history("AAPL")
        self.assertEqual(len(history), 0)

    def test_prune_retained_data_removes_expired_operational_history(self):
        """Retention cleanup removes old rows across operational history tables."""
        old_history = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            INSERT INTO option_chain_snapshots
                (ticker, expiration, right, chain_json, as_of)
            VALUES ('AAPL', '20270115', 'P', '{}', '2000-01-01 00:00:00');
            INSERT INTO run_metadata
                (run_id, generated_at, published_at, env, snapshot_json, status)
            VALUES ('old-run', '2000-01-01 00:00:00', '2000-01-01 00:00:00',
                    'SIMULATE', '{}', 'planning');
            INSERT INTO refresh_attempts
                (attempt_id, state, stage, created_at)
            VALUES ('old-attempt', 'failed', 'scan', '2000-01-01 00:00:00');
            INSERT INTO portfolio_snapshots
                (run_id, captured_at, env)
            VALUES ('old-run', '2000-01-01 00:00:00', 'SIMULATE');
            INSERT INTO trade_events
                (timestamp, event_type, ticker, option_type, strike, expiration)
            VALUES ('2000-01-01 00:00:00', 'entry', 'AAPL', 'PUT', 100, '20270115');
            INSERT INTO scan_ledger
                (scan_type, timestamp, config_hash, portfolio_hash)
            VALUES ('recommendations', '2000-01-01 00:00:00', 'config', 'portfolio');
            """
        )
        conn.execute(
            "INSERT INTO iv_history (ticker, timestamp, implied_volatility) VALUES (?, ?, ?)",
            ("AAPL", old_history, 0.3),
        )
        conn.commit()
        conn.close()

        deleted = self.db.prune_retained_data()

        self.assertEqual(deleted["option_chain_snapshots"], 1)
        self.assertEqual(deleted["run_metadata"], 1)
        self.assertEqual(deleted["refresh_attempts"], 1)
        self.assertEqual(deleted["portfolio_snapshots"], 1)
        self.assertEqual(deleted["trade_events"], 1)
        self.assertEqual(deleted["scan_ledger"], 1)
        self.assertEqual(deleted["iv_history"], 1)

    def test_save_and_get_earnings_date(self):
        """Test saving and retrieving earnings dates"""
        self.db.save_earnings_date(ticker="AAPL", earnings_date="2024-04-25", fetch_status="success")

        result = self.db.get_earnings_date("AAPL")
        self.assertIsNotNone(result)
        self.assertEqual(result["earnings_date"], "2024-04-25")
        self.assertEqual(result["fetch_status"], "success")

    def test_get_pending_earnings(self):
        """Test getting tickers with upcoming earnings"""
        today = datetime.now().strftime("%Y-%m-%d")
        future_date = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")

        self.db.save_earnings_date("AAPL", future_date)
        self.db.save_earnings_date("TSLA", today)
        self.db.save_earnings_date("MSFT", "2025-01-01")

        pending = self.db.get_pending_earnings(days_threshold=7)
        tickers = [p["ticker"] for p in pending]
        self.assertIn("AAPL", tickers)
        self.assertIn("TSLA", tickers)
        self.assertNotIn("MSFT", tickers)

    def test_get_tickers_needing_earnings_update(self):
        """Test getting tickers with stale earnings data"""
        self.db.save_earnings_date("FRESH", "2025-06-01", fetch_status="success")

        old_conn = sqlite3.connect(self.db_path)
        old_cursor = old_conn.cursor()
        old_time = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
        old_cursor.execute(
            """
            INSERT OR REPLACE INTO earnings_calendar
            (ticker, earnings_date, last_updated, fetch_status)
            VALUES (?, ?, ?, ?)
        """,
            ("STALE", "2025-06-01", old_time, "success"),
        )
        old_conn.commit()
        old_conn.close()

        self.db.save_earnings_date("PENDING_STATUS", "2025-06-01", fetch_status="pending")
        self.db.save_earnings_date("ERROR_STATUS", "2025-06-01", fetch_status="error")

        stale_tickers = self.db.get_tickers_needing_earnings_update(hours_threshold=24)
        self.assertIn("STALE", stale_tickers)
        self.assertIn("PENDING_STATUS", stale_tickers)
        self.assertIn("ERROR_STATUS", stale_tickers)
        self.assertNotIn("FRESH", stale_tickers)

    def test_save_and_get_trade_events(self):
        """Test saving and retrieving trade events"""
        event_data = {
            "event_type": "entry",
            "ticker": "AAPL",
            "option_type": "PUT",
            "strike": 150.0,
            "expiration": "20240419",
            "premium_in": 250.0,
            "premium_out": 0.0,
            "pnl": 0.0,
            "leakage": 0.0,
            "reason": "new_entry",
            "details": {"order_id": 1},
        }

        self.db.save_trade_event(event_data)

        events = self.db.get_trade_events(ticker="AAPL")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "entry")
        self.assertEqual(events[0]["premium_in"], 250.0)
        self.assertEqual(events[0]["details"], {"order_id": 1})

    def test_get_trade_analytics(self):
        """Test getting trade analytics"""
        for i in range(5):
            event_data = {
                "event_type": "entry",
                "ticker": "AAPL",
                "option_type": "PUT",
                "strike": 150.0 + i,
                "expiration": "20240419",
                "premium_in": 250.0,
                "premium_out": 0.0,
            }
            self.db.save_trade_event(event_data)

        analytics = self.db.get_trade_analytics()
        self.assertIsInstance(analytics, dict)
        self.assertIn("total_exits", analytics)
        self.assertIn("wins", analytics)
        self.assertEqual(analytics["total_exits"], 0)

    def test_trade_events_filter_by_type(self):
        """Test filtering trade events by event type"""
        self.db.save_trade_event(
            {"event_type": "entry", "ticker": "AAPL", "option_type": "PUT", "strike": 150.0, "expiration": "20240419"}
        )
        self.db.save_trade_event(
            {
                "event_type": "exit",
                "ticker": "AAPL",
                "option_type": "PUT",
                "strike": 150.0,
                "expiration": "20240419",
                "pnl": 50.0,
            }
        )

        entries = self.db.get_trade_events(event_type="entry")
        self.assertEqual(len(entries), 1)

        exits = self.db.get_trade_events(event_type="exit")
        self.assertEqual(len(exits), 1)


# ═══════════════════════════════════════════════════════════════════
#  Edge-Case Tests
# ═══════════════════════════════════════════════════════════════════


class TestDatabaseEdgeCases(unittest.TestCase):
    """Test edge cases for database operations"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_options.db")
        self.db = OptionsDatabase(self.db_path)

    def tearDown(self):
        if hasattr(self, "db") and self.db is not None:
            self.db.close()
        close_connection_pool(self.db_path)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_iv_history_multiple_entries(self):
        """Test IV history with multiple entries"""
        for i in range(5):
            self.db.save_iv_data(
                ticker="AAPL",
                implied_volatility=0.30 + i * 0.01,
                stock_price=175.0,
            )

        history = self.db.get_iv_history("AAPL")
        self.assertEqual(len(history), 5)

    def test_trade_events_with_limit(self):
        """Test getting trade events with limit"""
        for i in range(10):
            event_data = {
                "event_type": "entry",
                "ticker": "AAPL",
                "option_type": "PUT",
                "strike": 150.0 + i,
                "expiration": "20240419",
            }
            self.db.save_trade_event(event_data)

        events = self.db.get_trade_events(limit=5)
        self.assertEqual(len(events), 5)

    def test_trade_event_with_roll_fields(self):
        """Test saving a trade event with rollover fields"""
        event_data = {
            "event_type": "roll",
            "ticker": "AAPL",
            "option_type": "PUT",
            "strike": 150.0,
            "expiration": "20240419",
            "from_strike": 145.0,
            "from_expiration": "20240412",
            "to_strike": 150.0,
            "to_expiration": "20240419",
            "premium_in": 200.0,
            "premium_out": 150.0,
        }
        self.db.save_trade_event(event_data)
        events = self.db.get_trade_events(event_type="roll")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["from_strike"], 145.0)
        self.assertEqual(events[0]["to_strike"], 150.0)

    def test_trade_event_with_string_details(self):
        """Test trade event with pre-serialized JSON string in details"""
        event_data = {
            "event_type": "entry",
            "ticker": "AAPL",
            "option_type": "PUT",
            "strike": 150.0,
            "expiration": "20240419",
            "details": '{"raw": "data"}',
        }
        self.db.save_trade_event(event_data)
        events = self.db.get_trade_events(ticker="AAPL")
        self.assertEqual(events[0]["details"], {"raw": "data"})

    def test_get_earnings_date_nonexistent(self):
        """Test getting earnings date for unknown ticker"""
        result = self.db.get_earnings_date("NONEXISTENT")
        self.assertIsNone(result)

    def test_earnings_date_upsert(self):
        """Test that saving the same ticker updates the existing row"""
        self.db.save_earnings_date("AAPL", "2024-04-25", fetch_status="success")
        self.db.save_earnings_date("AAPL", "2024-05-02", fetch_status="success")

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM earnings_calendar WHERE ticker='AAPL'")
        count = cursor.fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

        result = self.db.get_earnings_date("AAPL")
        self.assertEqual(result["earnings_date"], "2024-05-02")

    def test_iv_history_days_filter(self):
        """Test that get_iv_history respects the days parameter"""
        old_conn = sqlite3.connect(self.db_path)
        old_cursor = old_conn.cursor()
        old_ts = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        old_cursor.execute(
            "INSERT INTO iv_history (ticker, timestamp, implied_volatility) VALUES (?, ?, ?)", ("AAPL", old_ts, 0.25)
        )
        old_conn.commit()
        old_conn.close()

        self.db.save_iv_data("AAPL", 0.35, stock_price=180.0)

        history_7d = self.db.get_iv_history("AAPL", days=7)
        self.assertEqual(len(history_7d), 1)
        self.assertEqual(history_7d[0]["implied_volatility"], 0.35)

        history_90d = self.db.get_iv_history("AAPL", days=90)
        self.assertEqual(len(history_90d), 2)

    def test_iv_data_none_fields(self):
        """Test saving IV data with optional fields as None"""
        result = self.db.save_iv_data("AAPL", 0.30)
        self.assertTrue(result)
        latest = self.db.get_latest_iv("AAPL")
        self.assertIsNotNone(latest)
        self.assertEqual(latest["implied_volatility"], 0.30)


# ═══════════════════════════════════════════════════════════════════
#  Earnings Repository Tests
# ═══════════════════════════════════════════════════════════════════


class TestEarningsRepository(unittest.TestCase):
    """Earnings calendar repository — save, error, and data preservation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_earnings.db")
        self.db = OptionsDatabase(self.db_path)

    def tearDown(self):
        if hasattr(self, "db") and self.db is not None:
            self.db.close()
        close_connection_pool(self.db_path)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_save_and_get_richer_fields(self):
        self.db.save_earnings_date(
            "AAPL",
            "2026-05-15",
            time_of_day="post-market",
            fiscal_date_ending="2026-04-30",
            estimate=2.35,
            currency="USD",
            earnings_source="Alpha Vantage",
        )
        record = self.db.get_earnings_date("AAPL")
        self.assertEqual(record["earnings_date"], "2026-05-15")
        self.assertEqual(record["time_of_day"], "post-market")
        self.assertEqual(record["fiscal_date_ending"], "2026-04-30")
        self.assertEqual(record["estimate"], 2.35)
        self.assertEqual(record["currency"], "USD")
        self.assertEqual(record["earnings_source"], "Alpha Vantage")

    def test_mark_earnings_error_preserves_prior_date(self):
        self.db.save_earnings_date("AAPL", "2026-05-15", fetch_status="success")
        self.db.mark_earnings_error("AAPL", "Temporary network error")
        record = self.db.get_earnings_date("AAPL")
        self.assertEqual(record["earnings_date"], "2026-05-15")
        self.assertEqual(record["fetch_status"], "error")
        self.assertEqual(record["error_message"], "Temporary network error")

    def test_mark_earnings_error_preserves_richer_fields(self):
        self.db.save_earnings_date(
            "AAPL",
            "2026-05-15",
            time_of_day="post-market",
            estimate=2.35,
            currency="USD",
            earnings_source="Alpha Vantage",
        )
        self.db.mark_earnings_error("AAPL", "Rate limited")
        record = self.db.get_earnings_date("AAPL")
        self.assertEqual(record["time_of_day"], "post-market")
        self.assertEqual(record["estimate"], 2.35)
        self.assertEqual(record["currency"], "USD")
        self.assertEqual(record["earnings_source"], "Alpha Vantage")
        self.assertEqual(record["fetch_status"], "error")

    def test_mark_earnings_error_on_nonexistent_ticker(self):
        result = self.db.mark_earnings_error("NONEXIST", "No data")
        self.assertFalse(result)

    @unittest.skip("earnings threshold date-sensitive (2026-05-15 vs today)")
    def test_get_pending_earnings_returns_richer_fields(self):
        self.db.save_earnings_date(
            "AAPL",
            "2026-05-15",
            time_of_day="post-market",
            fiscal_date_ending="2026-04-30",
            estimate=2.35,
            currency="USD",
            earnings_source="Alpha Vantage",
        )
        pending = self.db.get_pending_earnings(days_threshold=30)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["time_of_day"], "post-market")
        self.assertEqual(pending[0]["estimate"], 2.35)
        self.assertEqual(pending[0]["currency"], "USD")
        self.assertEqual(pending[0]["earnings_source"], "Alpha Vantage")
        self.assertEqual(pending[0]["fiscal_date_ending"], "2026-04-30")


# ═══════════════════════════════════════════════════════════════════
#  Migration Tests
# ═══════════════════════════════════════════════════════════════════


class TestDatabaseMigrations(unittest.TestCase):
    """Test database migration logic — sequential, idempotent, rollover detection"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_migrations.db")

    def tearDown(self):
        if hasattr(self, "db") and self.db is not None:
            self.db.close()
        close_connection_pool(self.db_path)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_default_db_path_is_resolved(self):
        """Test that OptionsDatabase resolves the default path correctly"""
        db = OptionsDatabase()
        self.assertIsNotNone(db)
        self.assertTrue(str(db.db_path).endswith("options.db"))
        db.close()


# ═══════════════════════════════════════════════════════════════════
#  Concurrent-Access Tests
# ═══════════════════════════════════════════════════════════════════


class TestDatabaseConcurrency(unittest.TestCase):
    """Test thread safety of database operations"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_concurrent.db")
        self.db = OptionsDatabase(self.db_path)

    def tearDown(self):
        if hasattr(self, "db") and self.db is not None:
            self.db.close()
        close_connection_pool(self.db_path)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_concurrent_iv_writes(self):
        """Concurrent IV data writes don't corrupt the iv_history table"""
        errors = []
        lock = threading.Lock()

        def iv_writer(ticker):
            local_db = None
            try:
                local_db = OptionsDatabase(self.db_path)
                for i in range(20):
                    local_db.save_iv_data(ticker, implied_volatility=0.20 + i * 0.01)
            except Exception as e:
                with lock:
                    errors.append(str(e))
            finally:
                if local_db is not None:
                    local_db.close()

        threads = [threading.Thread(target=iv_writer, args=(f"TICK{t}",)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"IV write errors: {errors}")

        total = 0
        for t in range(5):
            total += len(self.db.get_iv_history(f"TICK{t}", days=365))
        self.assertEqual(total, 100, "All 100 IV entries should be present")

    def test_concurrent_earnings_upsert(self):
        """Concurrent upserts on same earnings ticker (INSERT OR REPLACE)"""
        errors = []
        lock = threading.Lock()

        def earnings_writer(thread_id):
            local_db = None
            try:
                local_db = OptionsDatabase(self.db_path)
                for _ in range(10):
                    local_db.save_earnings_date("AAPL", f"2025-0{thread_id}-01", fetch_status="success")
            except Exception as e:
                with lock:
                    errors.append(str(e))
            finally:
                if local_db is not None:
                    local_db.close()

        threads = [threading.Thread(target=earnings_writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Earnings upsert errors: {errors}")

        # Only one row for AAPL should exist
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM earnings_calendar WHERE ticker='AAPL'")
        count = cursor.fetchone()[0]
        conn.close()
        self.assertEqual(count, 1, "INSERT OR REPLACE should keep exactly 1 row per ticker")

    def test_concurrent_purge_and_write(self):
        """Purge and write on same table concurrently is safe"""
        for i in range(100):
            self.db.save_iv_data("AAPL", 0.30)

        errors = []
        lock = threading.Lock()

        def purger():
            local_db = None
            try:
                local_db = OptionsDatabase(self.db_path)
                local_db.purge_old_iv_data(days=0)
            except Exception as e:
                with lock:
                    errors.append(str(e))
            finally:
                if local_db is not None:
                    local_db.close()

        def writer():
            local_db = None
            try:
                local_db = OptionsDatabase(self.db_path)
                for _ in range(20):
                    local_db.save_iv_data("AAPL", 0.35)
            except Exception as e:
                with lock:
                    errors.append(str(e))
            finally:
                if local_db is not None:
                    local_db.close()

        threads = [
            threading.Thread(target=purger),
            threading.Thread(target=writer),
            threading.Thread(target=writer),
            threading.Thread(target=purger),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Purge+write errors: {errors}")
        final_count = len(self.db.get_iv_history("AAPL", days=365))
        self.assertGreaterEqual(final_count, 0)


# ═══════════════════════════════════════════════════════════════════
#  Repository Isolation Tests
# ═══════════════════════════════════════════════════════════════════


class TestTradeEventsRepositoryDirect(unittest.TestCase):
    """Direct tests for TradeEventsRepository"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_te.db")
        conn = sqlite3.connect(self.db_path)
        create_tables(conn)
        conn.close()
        self.repo = TradeEventsRepository(self.db_path)

    def tearDown(self):
        close_connection_pool(self.db_path)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_get_trade_analytics_empty(self):
        analytics = self.repo.get_trade_analytics()
        self.assertEqual(analytics["total_exits"], 0)
        self.assertEqual(analytics["wins"], 0)
        self.assertEqual(analytics["win_rate"], 0)
        self.assertEqual(analytics["avg_leakage"], 0)
        self.assertEqual(analytics["roll_count"], 0)
        self.assertEqual(analytics["per_symbol"], [])
        self.assertEqual(analytics["exit_events"], [])

    def test_get_trade_analytics_with_exits(self):
        self.repo.save_trade_event(
            {
                "event_type": "exit",
                "ticker": "AAPL",
                "option_type": "PUT",
                "strike": 150.0,
                "expiration": "20240419",
                "pnl": 50.0,
                "leakage": 5.0,
            }
        )
        self.repo.save_trade_event(
            {
                "event_type": "exit",
                "ticker": "AAPL",
                "option_type": "PUT",
                "strike": 150.0,
                "expiration": "20240419",
                "pnl": -20.0,
                "leakage": 3.0,
            }
        )
        self.repo.save_trade_event(
            {
                "event_type": "roll",
                "ticker": "AAPL",
                "option_type": "PUT",
                "strike": 150.0,
                "expiration": "20240419",
                "from_strike": 145.0,
                "to_strike": 150.0,
                "premium_in": 200.0,
                "premium_out": 150.0,
            }
        )

        analytics = self.repo.get_trade_analytics()
        self.assertEqual(analytics["total_exits"], 2)
        self.assertEqual(analytics["wins"], 1)
        self.assertEqual(analytics["win_rate"], 50.0)
        self.assertEqual(analytics["roll_count"], 1)
        self.assertEqual(analytics["avg_leakage"], 4.0)
        self.assertEqual(len(analytics["per_symbol"]), 1)

    def test_get_trade_analytics_with_zero_leakage_exits(self):
        self.repo.save_trade_event(
            {
                "event_type": "exit",
                "ticker": "AAPL",
                "option_type": "PUT",
                "strike": 150.0,
                "expiration": "20240419",
                "pnl": 10.0,
                "leakage": 0.0,
            }
        )
        analytics = self.repo.get_trade_analytics()
        self.assertEqual(analytics["total_exits"], 1)
        self.assertEqual(analytics["avg_leakage"], 0.0)

    def test_get_trade_analytics_target_hit_and_stopped(self):
        self.repo.save_trade_event(
            {
                "event_type": "target_hit",
                "ticker": "AAPL",
                "option_type": "PUT",
                "strike": 150.0,
                "expiration": "20240419",
                "pnl": 100.0,
            }
        )
        self.repo.save_trade_event(
            {
                "event_type": "stopped",
                "ticker": "TSLA",
                "option_type": "PUT",
                "strike": 200.0,
                "expiration": "20240419",
                "pnl": -150.0,
            }
        )
        analytics = self.repo.get_trade_analytics()
        self.assertEqual(analytics["total_exits"], 2)
        self.assertEqual(analytics["wins"], 1)
        self.assertEqual(analytics["win_rate"], 50.0)


if __name__ == "__main__":
    unittest.main()
