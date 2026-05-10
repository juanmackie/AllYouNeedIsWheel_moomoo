"""
Tests for db/database.py - SQLite database for options trading

Covers: schema creation, CRUD operations for all 4 repositories,
database migrations (multi-step, sequential, idempotency),
concurrent access (thread safety), and edge cases.
"""

import unittest
import sqlite3
import tempfile
import os
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from db.database import OptionsDatabase
from db.schema import create_tables, migrate_database
from db.orders_repository import OrdersRepository
from db.iv_repository import IVRepository
from db.earnings_repository import EarningsRepository
from db.trade_events_repository import TradeEventsRepository


# ═══════════════════════════════════════════════════════════════════
#  Core CRUD Tests
# ═══════════════════════════════════════════════════════════════════

class TestOptionsDatabase(unittest.TestCase):
    """Test the OptionsDatabase class — schema creation and CRUD"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_options.db')
        self.db = OptionsDatabase(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_database_creation(self):
        """Test that database and tables are created"""
        self.assertTrue(os.path.exists(self.db_path))

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        expected_tables = ['orders', 'recommendations', 'iv_history',
                           'earnings_calendar', 'trade_events']
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        actual_tables = {row[0] for row in cursor.fetchall()}
        for table in expected_tables:
            self.assertIn(table, actual_tables, f"Missing table: {table}")

        conn.close()

    def test_save_and_get_order(self):
        """Test saving and retrieving an order"""
        order_data = {
            'ticker': 'AAPL',
            'option_type': 'PUT',
            'action': 'SELL',
            'strike': 150.0,
            'expiration': '20240419',
            'premium': 2.50,
            'quantity': 1,
            'bid': 2.0,
            'ask': 3.0,
            'last': 2.50,
            'delta': -0.20,
            'gamma': 0.05,
            'theta': -0.05,
            'vega': 0.15,
            'implied_volatility': 0.30,
            'open_interest': 500,
            'volume': 100,
        }

        order_id = self.db.save_order(order_data)
        self.assertGreater(order_id, 0)

        retrieved = self.db.get_order(order_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved['ticker'], 'AAPL')
        self.assertEqual(retrieved['strike'], 150.0)
        self.assertEqual(retrieved['status'], 'pending')
        self.assertFalse(retrieved['executed'])

    def test_get_nonexistent_order(self):
        """Test retrieving an order that doesn't exist"""
        result = self.db.get_order(99999)
        self.assertIsNone(result)

    def test_update_order_status(self):
        """Test updating order status"""
        order_data = {
            'ticker': 'AAPL',
            'option_type': 'PUT',
            'action': 'SELL',
            'strike': 150.0,
            'expiration': '20240419',
            'premium': 2.50,
        }

        order_id = self.db.save_order(order_data)

        execution_details = {
            'moomoo_order_id': 'test_123',
            'moomoo_status': 'Filled',
            'filled': 1,
            'remaining': 0,
            'avg_fill_price': 2.50,
        }
        self.db.update_order_status(
            order_id=order_id,
            status='processing',
            executed=True,
            execution_details=execution_details
        )

        updated = self.db.get_order(order_id)
        self.assertEqual(updated['status'], 'processing')
        self.assertTrue(updated['executed'])
        self.assertEqual(updated['moomoo_order_id'], 'test_123')

    def test_delete_order(self):
        """Test deleting an order"""
        order_data = {
            'ticker': 'AAPL',
            'option_type': 'PUT',
            'action': 'SELL',
            'strike': 150.0,
            'expiration': '20240419',
        }

        order_id = self.db.save_order(order_data)
        self.db.delete_order(order_id)

        result = self.db.get_order(order_id)
        self.assertIsNone(result)

    def test_get_orders_with_filters(self):
        """Test getting orders with various filters"""
        order_ids = []
        for i, ticker in enumerate(['AAPL', 'TSLA', 'AAPL']):
            order_data = {
                'ticker': ticker,
                'option_type': 'PUT',
                'action': 'SELL',
                'strike': 150.0 + i,
                'expiration': '20240419',
            }
            order_id = self.db.save_order(order_data)
            order_ids.append(order_id)

        # Mark the third order as executed (simulate update)
        self.db.update_order_status(order_ids[2], status='executed', executed=True)

        # Get all orders
        all_orders = self.db.get_orders()
        self.assertEqual(len(all_orders), 3)

        # Filter by status
        pending_orders = self.db.get_orders(status='pending')
        self.assertEqual(len(pending_orders), 2)

        # Filter by ticker
        aapl_orders = self.db.get_orders(ticker='AAPL')
        self.assertEqual(len(aapl_orders), 2)

        # Filter by executed
        executed_orders = self.db.get_orders(executed=True)
        self.assertEqual(len(executed_orders), 1)

    def test_get_orders_with_status_filter_list(self):
        """Test get_orders with status_filter as a list"""
        order_ids = []
        for s in ['pending', 'processing', 'cancelled']:
            oid = self.db.save_order({'ticker': 'AAPL', 'option_type': 'PUT',
                                       'action': 'SELL', 'strike': 150.0,
                                       'expiration': '20240419'})
            self.db.update_order_status(oid, status=s)
            order_ids.append(oid)

        result = self.db.get_orders(status_filter=['pending', 'processing'])
        self.assertEqual(len(result), 2)

    def test_get_orders_with_is_rollover_filter(self):
        """Test filtering orders by isRollover flag"""
        oid1 = self.db.save_order({'ticker': 'AAPL', 'option_type': 'PUT',
                                    'action': 'SELL', 'strike': 150.0,
                                    'expiration': '20240419', 'isRollover': False})
        oid2 = self.db.save_order({'ticker': 'AAPL', 'option_type': 'PUT',
                                    'action': 'BUY', 'strike': 155.0,
                                    'expiration': '20240419', 'isRollover': True})

        non_rollover = self.db.get_orders(isRollover=False)
        self.assertEqual(len(non_rollover), 1)
        self.assertEqual(non_rollover[0]['id'], oid1)

        rollover = self.db.get_orders(isRollover=True)
        self.assertEqual(len(rollover), 1)
        self.assertEqual(rollover[0]['id'], oid2)

    def test_save_and_get_iv_data(self):
        """Test saving and retrieving IV data"""
        self.db.save_iv_data(
            ticker='AAPL',
            implied_volatility=0.30,
            stock_price=175.0,
            option_type='PUT',
            expiration='20240419',
            dte=21
        )

        history = self.db.get_iv_history('AAPL')
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['implied_volatility'], 0.30)

        latest = self.db.get_latest_iv('AAPL')
        self.assertIsNotNone(latest)
        self.assertEqual(latest['implied_volatility'], 0.30)

    def test_purge_old_iv_data(self):
        """Test purging old IV data"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        old_date = (datetime.now() - timedelta(days=50)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("""
            INSERT INTO iv_history (ticker, timestamp, implied_volatility, stock_price)
            VALUES (?, ?, ?, ?)
        """, ('AAPL', old_date, 0.30, 175.0))
        conn.commit()
        conn.close()

        self.db.purge_old_iv_data(days=45)

        history = self.db.get_iv_history('AAPL')
        self.assertEqual(len(history), 0)

    def test_save_and_get_earnings_date(self):
        """Test saving and retrieving earnings dates"""
        self.db.save_earnings_date(
            ticker='AAPL',
            earnings_date='2024-04-25',
            fetch_status='success'
        )

        result = self.db.get_earnings_date('AAPL')
        self.assertIsNotNone(result)
        self.assertEqual(result['earnings_date'], '2024-04-25')
        self.assertEqual(result['fetch_status'], 'success')

    def test_get_pending_earnings(self):
        """Test getting tickers with upcoming earnings"""
        today = datetime.now().strftime('%Y-%m-%d')
        future_date = (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%d')

        self.db.save_earnings_date('AAPL', future_date)
        self.db.save_earnings_date('TSLA', today)
        self.db.save_earnings_date('MSFT', '2025-01-01')

        pending = self.db.get_pending_earnings(days_threshold=7)
        tickers = [p['ticker'] for p in pending]
        self.assertIn('AAPL', tickers)
        self.assertIn('TSLA', tickers)
        self.assertNotIn('MSFT', tickers)

    def test_get_tickers_needing_earnings_update(self):
        """Test getting tickers with stale earnings data"""
        self.db.save_earnings_date('FRESH', '2025-06-01', fetch_status='success')

        old_conn = sqlite3.connect(self.db_path)
        old_cursor = old_conn.cursor()
        old_time = (datetime.now() - timedelta(hours=48)).strftime('%Y-%m-%d %H:%M:%S')
        old_cursor.execute("""
            INSERT OR REPLACE INTO earnings_calendar
            (ticker, earnings_date, last_updated, fetch_status)
            VALUES (?, ?, ?, ?)
        """, ('STALE', '2025-06-01', old_time, 'success'))
        old_conn.commit()
        old_conn.close()

        self.db.save_earnings_date('PENDING_STATUS', '2025-06-01', fetch_status='pending')
        self.db.save_earnings_date('ERROR_STATUS', '2025-06-01', fetch_status='error')

        stale_tickers = self.db.get_tickers_needing_earnings_update(hours_threshold=24)
        self.assertIn('STALE', stale_tickers)
        self.assertIn('PENDING_STATUS', stale_tickers)
        self.assertIn('ERROR_STATUS', stale_tickers)
        self.assertNotIn('FRESH', stale_tickers)

    def test_save_and_get_trade_events(self):
        """Test saving and retrieving trade events"""
        event_data = {
            'event_type': 'entry',
            'ticker': 'AAPL',
            'option_type': 'PUT',
            'strike': 150.0,
            'expiration': '20240419',
            'premium_in': 250.0,
            'premium_out': 0.0,
            'pnl': 0.0,
            'leakage': 0.0,
            'reason': 'new_entry',
            'details': {'order_id': 1},
        }

        self.db.save_trade_event(event_data)

        events = self.db.get_trade_events(ticker='AAPL')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['event_type'], 'entry')
        self.assertEqual(events[0]['premium_in'], 250.0)
        self.assertEqual(events[0]['details'], {'order_id': 1})

    def test_get_trade_analytics(self):
        """Test getting trade analytics"""
        for i in range(5):
            event_data = {
                'event_type': 'entry',
                'ticker': 'AAPL',
                'option_type': 'PUT',
                'strike': 150.0 + i,
                'expiration': '20240419',
                'premium_in': 250.0,
                'premium_out': 0.0,
            }
            self.db.save_trade_event(event_data)

        analytics = self.db.get_trade_analytics()
        self.assertIsInstance(analytics, dict)
        self.assertIn('total_exits', analytics)
        self.assertIn('wins', analytics)
        self.assertEqual(analytics['total_exits'], 0)

    def test_trade_events_filter_by_type(self):
        """Test filtering trade events by event type"""
        self.db.save_trade_event({'event_type': 'entry', 'ticker': 'AAPL',
                                   'option_type': 'PUT', 'strike': 150.0,
                                   'expiration': '20240419'})
        self.db.save_trade_event({'event_type': 'exit', 'ticker': 'AAPL',
                                   'option_type': 'PUT', 'strike': 150.0,
                                   'expiration': '20240419', 'pnl': 50.0})

        entries = self.db.get_trade_events(event_type='entry')
        self.assertEqual(len(entries), 1)

        exits = self.db.get_trade_events(event_type='exit')
        self.assertEqual(len(exits), 1)


# ═══════════════════════════════════════════════════════════════════
#  Edge-Case Tests
# ═══════════════════════════════════════════════════════════════════

class TestDatabaseEdgeCases(unittest.TestCase):
    """Test edge cases for database operations"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_options.db')
        self.db = OptionsDatabase(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_update_nonexistent_order(self):
        """Test updating an order that doesn't exist"""
        result = self.db.update_order_status(order_id=99999, status='executed')
        self.assertFalse(result)

    def test_delete_nonexistent_order(self):
        """Test deleting an order that doesn't exist"""
        result = self.db.delete_order(99999)
        self.assertFalse(result)

    def test_get_orders_empty_database(self):
        """Test getting orders from empty database"""
        orders = self.db.get_orders()
        self.assertEqual(len(orders), 0)

    def test_iv_history_multiple_entries(self):
        """Test IV history with multiple entries"""
        for i in range(5):
            self.db.save_iv_data(
                ticker='AAPL',
                implied_volatility=0.30 + i * 0.01,
                stock_price=175.0,
            )

        history = self.db.get_iv_history('AAPL')
        self.assertEqual(len(history), 5)

    def test_trade_events_with_limit(self):
        """Test getting trade events with limit"""
        for i in range(10):
            event_data = {
                'event_type': 'entry',
                'ticker': 'AAPL',
                'option_type': 'PUT',
                'strike': 150.0 + i,
                'expiration': '20240419',
            }
            self.db.save_trade_event(event_data)

        events = self.db.get_trade_events(limit=5)
        self.assertEqual(len(events), 5)

    def test_save_order_minimal_fields(self):
        """Test saving an order with only required fields"""
        order_data = {
            'ticker': 'AAPL',
            'option_type': 'PUT',
            'action': 'SELL',
            'strike': 150.0,
            'expiration': '20240419',
        }
        order_id = self.db.save_order(order_data)
        self.assertGreater(order_id, 0)

        retrieved = self.db.get_order(order_id)
        self.assertEqual(retrieved['ticker'], 'AAPL')
        self.assertEqual(retrieved['quantity'], 1)
        self.assertEqual(retrieved['premium'], 0)
        self.assertFalse(retrieved['is_mock'])

    def test_save_order_empty_ticker(self):
        """Test saving an order with empty ticker string"""
        order_data = {
            'ticker': '',
            'option_type': 'PUT',
            'action': 'SELL',
            'strike': 150.0,
            'expiration': '20240419',
        }
        order_id = self.db.save_order(order_data)
        self.assertIsNotNone(order_id)
        retrieved = self.db.get_order(order_id)
        self.assertEqual(retrieved['ticker'], '')

    def test_save_order_large_values(self):
        """Test saving an order with very large numeric values"""
        order_data = {
            'ticker': 'AAPL',
            'option_type': 'PUT',
            'action': 'SELL',
            'strike': 999999.99,
            'expiration': '20240419',
            'premium': 99999.99,
            'quantity': 9999,
            'open_interest': 999999999,
        }
        order_id = self.db.save_order(order_data)
        self.assertGreater(order_id, 0)
        retrieved = self.db.get_order(order_id)
        self.assertEqual(retrieved['strike'], 999999.99)
        self.assertEqual(retrieved['quantity'], 9999)

    def test_save_order_with_is_mock(self):
        """Test saving an order with is_mock flag"""
        order_data = {
            'ticker': 'AAPL',
            'option_type': 'PUT',
            'action': 'SELL',
            'strike': 150.0,
            'expiration': '20240419',
            'is_mock': True,
        }
        order_id = self.db.save_order(order_data)
        retrieved = self.db.get_order(order_id)
        self.assertTrue(retrieved['is_mock'])

    def test_update_order_quantity_pending_success(self):
        """Test updating quantity on a pending order succeeds"""
        oid = self.db.save_order({'ticker': 'AAPL', 'option_type': 'PUT',
                                   'action': 'SELL', 'strike': 150.0,
                                   'expiration': '20240419'})
        result = self.db.update_order_quantity(oid, 5)
        self.assertTrue(result)
        retrieved = self.db.get_order(oid)
        self.assertEqual(retrieved['quantity'], 5)

    def test_update_order_quantity_nonexistent(self):
        """Test updating quantity on a nonexistent order"""
        result = self.db.update_order_quantity(99999, 5)
        self.assertFalse(result)

    def test_update_order_quantity_non_pending(self):
        """Test updating quantity on a non-pending order is rejected"""
        oid = self.db.save_order({'ticker': 'AAPL', 'option_type': 'PUT',
                                   'action': 'SELL', 'strike': 150.0,
                                   'expiration': '20240419'})
        self.db.update_order_status(oid, status='processing', executed=True)
        result = self.db.update_order_quantity(oid, 5)
        self.assertFalse(result)
        retrieved = self.db.get_order(oid)
        self.assertNotEqual(retrieved['quantity'], 5)

    def test_trade_event_with_roll_fields(self):
        """Test saving a trade event with rollover fields"""
        event_data = {
            'event_type': 'roll',
            'ticker': 'AAPL',
            'option_type': 'PUT',
            'strike': 150.0,
            'expiration': '20240419',
            'from_strike': 145.0,
            'from_expiration': '20240412',
            'to_strike': 150.0,
            'to_expiration': '20240419',
            'premium_in': 200.0,
            'premium_out': 150.0,
        }
        self.db.save_trade_event(event_data)
        events = self.db.get_trade_events(event_type='roll')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['from_strike'], 145.0)
        self.assertEqual(events[0]['to_strike'], 150.0)

    def test_trade_event_with_string_details(self):
        """Test trade event with pre-serialized JSON string in details"""
        event_data = {
            'event_type': 'entry',
            'ticker': 'AAPL',
            'option_type': 'PUT',
            'strike': 150.0,
            'expiration': '20240419',
            'details': '{"raw": "data"}',
        }
        self.db.save_trade_event(event_data)
        events = self.db.get_trade_events(ticker='AAPL')
        self.assertEqual(events[0]['details'], {'raw': 'data'})

    def test_get_earnings_date_nonexistent(self):
        """Test getting earnings date for unknown ticker"""
        result = self.db.get_earnings_date('NONEXISTENT')
        self.assertIsNone(result)

    def test_earnings_date_upsert(self):
        """Test that saving the same ticker updates the existing row"""
        self.db.save_earnings_date('AAPL', '2024-04-25', fetch_status='success')
        self.db.save_earnings_date('AAPL', '2024-05-02', fetch_status='success')

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM earnings_calendar WHERE ticker='AAPL'")
        count = cursor.fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

        result = self.db.get_earnings_date('AAPL')
        self.assertEqual(result['earnings_date'], '2024-05-02')

    def test_iv_history_days_filter(self):
        """Test that get_iv_history respects the days parameter"""
        old_conn = sqlite3.connect(self.db_path)
        old_cursor = old_conn.cursor()
        old_ts = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d %H:%M:%S')
        old_cursor.execute("INSERT INTO iv_history (ticker, timestamp, implied_volatility) VALUES (?, ?, ?)",
                           ('AAPL', old_ts, 0.25))
        old_conn.commit()
        old_conn.close()

        self.db.save_iv_data('AAPL', 0.35, stock_price=180.0)

        history_7d = self.db.get_iv_history('AAPL', days=7)
        self.assertEqual(len(history_7d), 1)
        self.assertEqual(history_7d[0]['implied_volatility'], 0.35)

        history_90d = self.db.get_iv_history('AAPL', days=90)
        self.assertEqual(len(history_90d), 2)

    def test_iv_data_none_fields(self):
        """Test saving IV data with optional fields as None"""
        result = self.db.save_iv_data('AAPL', 0.30)
        self.assertTrue(result)
        latest = self.db.get_latest_iv('AAPL')
        self.assertIsNotNone(latest)
        self.assertEqual(latest['implied_volatility'], 0.30)


# ═══════════════════════════════════════════════════════════════════
#  Earnings Repository Tests
# ═══════════════════════════════════════════════════════════════════

class TestEarningsRepository(unittest.TestCase):
    """Earnings calendar repository — save, error, and data preservation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_earnings.db')
        self.db = OptionsDatabase(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_save_and_get_richer_fields(self):
        self.db.save_earnings_date(
            'AAPL', '2026-05-15',
            time_of_day='post-market',
            fiscal_date_ending='2026-04-30',
            estimate=2.35,
            currency='USD',
            earnings_source='Alpha Vantage',
        )
        record = self.db.get_earnings_date('AAPL')
        self.assertEqual(record['earnings_date'], '2026-05-15')
        self.assertEqual(record['time_of_day'], 'post-market')
        self.assertEqual(record['fiscal_date_ending'], '2026-04-30')
        self.assertEqual(record['estimate'], 2.35)
        self.assertEqual(record['currency'], 'USD')
        self.assertEqual(record['earnings_source'], 'Alpha Vantage')

    def test_mark_earnings_error_preserves_prior_date(self):
        self.db.save_earnings_date('AAPL', '2026-05-15', fetch_status='success')
        self.db.mark_earnings_error('AAPL', 'Temporary network error')
        record = self.db.get_earnings_date('AAPL')
        self.assertEqual(record['earnings_date'], '2026-05-15')
        self.assertEqual(record['fetch_status'], 'error')
        self.assertEqual(record['error_message'], 'Temporary network error')

    def test_mark_earnings_error_preserves_richer_fields(self):
        self.db.save_earnings_date(
            'AAPL', '2026-05-15',
            time_of_day='post-market',
            estimate=2.35,
            currency='USD',
            earnings_source='Alpha Vantage',
        )
        self.db.mark_earnings_error('AAPL', 'Rate limited')
        record = self.db.get_earnings_date('AAPL')
        self.assertEqual(record['time_of_day'], 'post-market')
        self.assertEqual(record['estimate'], 2.35)
        self.assertEqual(record['currency'], 'USD')
        self.assertEqual(record['earnings_source'], 'Alpha Vantage')
        self.assertEqual(record['fetch_status'], 'error')

    def test_mark_earnings_error_on_nonexistent_ticker(self):
        result = self.db.mark_earnings_error('NONEXIST', 'No data')
        self.assertFalse(result)

    def test_get_pending_earnings_returns_richer_fields(self):
        self.db.save_earnings_date(
            'AAPL', '2026-05-15',
            time_of_day='post-market',
            fiscal_date_ending='2026-04-30',
            estimate=2.35,
            currency='USD',
            earnings_source='Alpha Vantage',
        )
        pending = self.db.get_pending_earnings(days_threshold=30)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['time_of_day'], 'post-market')
        self.assertEqual(pending[0]['estimate'], 2.35)
        self.assertEqual(pending[0]['currency'], 'USD')
        self.assertEqual(pending[0]['earnings_source'], 'Alpha Vantage')
        self.assertEqual(pending[0]['fiscal_date_ending'], '2026-04-30')


# ═══════════════════════════════════════════════════════════════════
#  Migration Tests
# ═══════════════════════════════════════════════════════════════════

class TestDatabaseMigrations(unittest.TestCase):
    """Test database migration logic — sequential, idempotent, rollover detection"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_migrations.db')

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def _column_names(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(orders)")
        cols = [row[1] for row in cursor.fetchall()]
        conn.close()
        return cols

    def _create_old_schema(self):
        """Create orders table with legacy ib_* columns and no modern columns."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DROP TABLE IF EXISTS orders")
        c.execute("""
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                option_type TEXT NOT NULL,
                action TEXT NOT NULL,
                strike REAL NOT NULL,
                expiration TEXT NOT NULL,
                premium REAL,
                quantity INTEGER DEFAULT 1,
                status TEXT DEFAULT 'pending',
                executed BOOLEAN DEFAULT 0,
                ib_order_id TEXT,
                ib_status TEXT
            )
        """)
        conn.commit()
        conn.close()

    def test_migration_full_sequence_from_old_schema(self):
        """All 3 migrations run in order when starting from legacy ib_* columns"""
        self._create_old_schema()
        self.assertIn('ib_order_id', self._column_names())
        self.assertIn('ib_status', self._column_names())
        self.assertNotIn('moomoo_order_id', self._column_names())
        self.assertNotIn('moomoo_status', self._column_names())
        self.assertNotIn('isRollover', self._column_names())

        migrate_database(self.db_path)

        cols = self._column_names()
        self.assertIn('moomoo_order_id', cols,
                      "ib_order_id should be renamed to moomoo_order_id")
        self.assertIn('moomoo_status', cols,
                      "ib_status should be renamed to moomoo_status")
        self.assertIn('isRollover', cols,
                      "isRollover column should be added")
        self.assertNotIn('ib_order_id', cols,
                         "ib_order_id should no longer exist")
        self.assertNotIn('ib_status', cols,
                         "ib_status should no longer exist")

    def test_migration_partial_ib_order_id_only(self):
        """Only 2 migrations run when starting from ib_order_id (no ib_status)"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DROP TABLE IF EXISTS orders")
        c.execute("""
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                option_type TEXT NOT NULL,
                action TEXT NOT NULL,
                strike REAL NOT NULL,
                expiration TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                executed BOOLEAN DEFAULT 0,
                ib_order_id TEXT
            )
        """)
        conn.commit()
        conn.close()

        migrate_database(self.db_path)

        cols = self._column_names()
        self.assertIn('moomoo_order_id', cols, "ib_order_id should be renamed")
        self.assertNotIn('ib_order_id', cols)
        self.assertIn('isRollover', cols, "isRollover should be added")
        # ib_status wasn't present so moomoo_status wasn't added by migration
        self.assertNotIn('moomoo_status', cols)

    def test_migration_idempotent_on_fresh_schema(self):
        """Running migration on a fresh modern DB is a no-op (no errors)"""
        create_tables_conn = sqlite3.connect(self.db_path)
        create_tables(create_tables_conn)
        create_tables_conn.close()

        cols_before = self._column_names()

        migrate_database(self.db_path)

        cols_after = self._column_names()
        self.assertEqual(cols_before, cols_after,
                         "Migration should not change an already-modern schema")

    def test_migration_idempotent_twice(self):
        """Running migrate_database twice on the same DB is safe"""
        self._create_old_schema()

        migrate_database(self.db_path)
        cols_after_first = set(self._column_names())

        migrate_database(self.db_path)
        cols_after_second = set(self._column_names())

        self.assertEqual(cols_after_first, cols_after_second)

    def test_migration_preserves_existing_data(self):
        """Data is preserved after migration from old schema"""
        self._create_old_schema()

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            INSERT INTO orders (timestamp, ticker, option_type, action, strike,
                                expiration, status, ib_order_id, ib_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ('2024-01-01 12:00:00', 'AAPL', 'PUT', 'SELL', 150.0,
              '20240419', 'pending', 'ib_123', 'Submitted'))
        conn.commit()
        conn.close()

        migrate_database(self.db_path)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM orders WHERE id = 1").fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row['ticker'], 'AAPL')
        self.assertEqual(row['strike'], 150.0)
        self.assertEqual(row['moomoo_order_id'], 'ib_123',
                         "ib_order_id value should be preserved as moomoo_order_id")
        self.assertEqual(row['moomoo_status'], 'Submitted',
                         "ib_status value should be preserved as moomoo_status")

    def test_migration_partial_ib_order_id_only_preserves_data(self):
        """Data preserved when only ib_order_id migration + isRollover runs"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DROP TABLE IF EXISTS orders")
        c.execute("""
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                option_type TEXT NOT NULL,
                action TEXT NOT NULL,
                strike REAL NOT NULL,
                expiration TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                executed BOOLEAN DEFAULT 0,
                ib_order_id TEXT
            )
        """)
        c.execute("""
            INSERT INTO orders (timestamp, ticker, option_type, action, strike,
                                expiration, status, ib_order_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ('2024-01-01 12:00:00', 'AAPL', 'PUT', 'SELL', 150.0,
              '20240419', 'pending', 'ib_999'))
        conn.commit()
        conn.close()

        migrate_database(self.db_path)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM orders WHERE id = 1").fetchone()
        conn.close()

        self.assertEqual(row['moomoo_order_id'], 'ib_999')

    def test_migration_idempotent_with_all_modern_columns(self):
        """Migrating a DB that already has moomoo_order_id, moomoo_status, isRollover is safe"""
        create_tables_conn = sqlite3.connect(self.db_path)
        create_tables(create_tables_conn)
        create_tables_conn.close()

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM pragma_table_info('orders') "
                  "WHERE name IN ('moomoo_order_id', 'moomoo_status', 'isRollover')")
        modern_cols = c.fetchone()[0]
        conn.close()
        self.assertEqual(modern_cols, 3, "Fresh schema should have all 3 modern columns")

        migrate_database(self.db_path)

        migrate_database(self.db_path)

    def test_migration_no_recommendations_table(self):
        """Migration is safe even when recommendations table doesn't exist"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DROP TABLE IF EXISTS orders")
        c.execute("""
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                option_type TEXT NOT NULL,
                action TEXT NOT NULL,
                strike REAL NOT NULL,
                expiration TEXT NOT NULL,
                ib_order_id TEXT
            )
        """)
        conn.commit()
        conn.close()

        migrate_database(self.db_path)

        cols = self._column_names()
        self.assertIn('moomoo_order_id', cols)
        self.assertIn('isRollover', cols)

    def test_init_triggers_migration(self):
        """OptionsDatabase.__init__ triggers migration automatically"""
        self._create_old_schema()

        db = OptionsDatabase(self.db_path)

        cols = self._column_names()
        self.assertIn('moomoo_order_id', cols)
        self.assertIn('moomoo_status', cols)
        self.assertIn('isRollover', cols)

    def test_default_db_path_is_resolved(self):
        """Test that OptionsDatabase resolves the default path correctly"""
        db = OptionsDatabase()
        self.assertIsNotNone(db)
        self.assertTrue(str(db.db_path).endswith('options.db'))


# ═══════════════════════════════════════════════════════════════════
#  Concurrent-Access Tests
# ═══════════════════════════════════════════════════════════════════

class TestDatabaseConcurrency(unittest.TestCase):
    """Test thread safety of database operations"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_concurrent.db')
        self.db = OptionsDatabase(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_concurrent_reads(self):
        """Multiple threads can read simultaneously"""
        # Seed some data
        for i in range(20):
            self.db.save_order({'ticker': f'TICK{i}', 'option_type': 'PUT',
                                 'action': 'SELL', 'strike': 100.0 + i,
                                 'expiration': '20240419'})

        results = []
        errors = []
        lock = threading.Lock()

        def reader(thread_id):
            try:
                local_db = OptionsDatabase(self.db_path)
                for _ in range(10):
                    orders = local_db.get_orders(limit=20)
                    with lock:
                        results.append(len(orders))
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Read errors: {errors}")
        self.assertEqual(len(results), 80)  # 8 threads × 10 reads
        self.assertTrue(all(r == 20 for r in results),
                        "All concurrent reads should return 20 orders")

    def test_concurrent_writes(self):
        """Multiple threads can write concurrently without data loss"""
        errors = []
        lock = threading.Lock()
        order_ids = []

        def writer(thread_id):
            try:
                local_db = OptionsDatabase(self.db_path)
                for i in range(10):
                    oid = local_db.save_order({
                        'ticker': f'WRITER{thread_id}',
                        'option_type': 'PUT',
                        'action': 'SELL',
                        'strike': 100.0 + i,
                        'expiration': '20240419',
                    })
                    with lock:
                        order_ids.append(oid)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Write errors: {errors}")
        self.assertEqual(len(order_ids), 40, "All 40 inserts should succeed")

        final_read = self.db.get_orders(limit=100)
        self.assertEqual(len(final_read), 40,
                         "All 40 orders should be retrievable")

    def test_concurrent_mixed_read_write(self):
        """Concurrent reads and writes don't interfere"""
        for i in range(10):
            self.db.save_order({'ticker': 'SEED', 'option_type': 'PUT',
                                 'action': 'SELL', 'strike': 100.0 + i,
                                 'expiration': '20240419'})

        errors = []
        lock = threading.Lock()
        reads_done = []
        write_ids = []

        def mixed_worker(thread_id):
            try:
                local_db = OptionsDatabase(self.db_path)
                for _ in range(5):
                    if thread_id % 2 == 0:
                        orders = local_db.get_orders(limit=100)
                        with lock:
                            reads_done.append(len(orders))
                    else:
                        oid = local_db.save_order({
                            'ticker': f'MIXED{thread_id}',
                            'option_type': 'PUT', 'action': 'SELL',
                            'strike': 100.0, 'expiration': '20240419',
                        })
                        with lock:
                            write_ids.append(oid)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=mixed_worker, args=(i,))
                   for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Mixed errors: {errors}")
        total = len(self.db.get_orders(limit=200))
        expected = 10 + len(write_ids)
        self.assertEqual(total, expected,
                         f"Expected {expected} total orders, got {total}")

    def test_concurrent_iv_writes(self):
        """Concurrent IV data writes don't corrupt the iv_history table"""
        errors = []
        lock = threading.Lock()

        def iv_writer(ticker):
            try:
                local_db = OptionsDatabase(self.db_path)
                for i in range(20):
                    local_db.save_iv_data(ticker, implied_volatility=0.20 + i * 0.01)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=iv_writer, args=(f'TICK{t}',))
                   for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"IV write errors: {errors}")

        total = 0
        for t in range(5):
            total += len(self.db.get_iv_history(f'TICK{t}', days=365))
        self.assertEqual(total, 100, "All 100 IV entries should be present")

    def test_concurrent_earnings_upsert(self):
        """Concurrent upserts on same earnings ticker (INSERT OR REPLACE)"""
        errors = []
        lock = threading.Lock()

        def earnings_writer(thread_id):
            try:
                local_db = OptionsDatabase(self.db_path)
                for _ in range(10):
                    local_db.save_earnings_date(
                        'AAPL',
                        f'2025-0{thread_id}-01',
                        fetch_status='success'
                    )
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=earnings_writer, args=(i,))
                   for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

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
            self.db.save_iv_data('AAPL', 0.30)

        errors = []
        lock = threading.Lock()

        def purger():
            try:
                local_db = OptionsDatabase(self.db_path)
                local_db.purge_old_iv_data(days=0)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        def writer():
            try:
                local_db = OptionsDatabase(self.db_path)
                for _ in range(20):
                    local_db.save_iv_data('AAPL', 0.35)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [
            threading.Thread(target=purger),
            threading.Thread(target=writer),
            threading.Thread(target=writer),
            threading.Thread(target=purger),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(errors), 0, f"Purge+write errors: {errors}")
        final_count = len(self.db.get_iv_history('AAPL', days=365))
        self.assertGreaterEqual(final_count, 0)


# ═══════════════════════════════════════════════════════════════════
#  Repository Isolation Tests
# ═══════════════════════════════════════════════════════════════════

class TestOrdersRepositoryDirect(unittest.TestCase):
    """Direct tests for OrdersRepository (bypass OptionsDatabase wrapper)"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_repo.db')
        conn = sqlite3.connect(self.db_path)
        create_tables(conn)
        conn.close()
        self.repo = OrdersRepository(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_get_pending_orders_empty(self):
        result = self.repo.get_pending_orders()
        self.assertEqual(len(result), 0)

    def test_get_pending_orders_with_data(self):
        self.repo.save_order({'ticker': 'AAPL', 'option_type': 'PUT',
                               'action': 'SELL', 'strike': 150.0,
                               'expiration': '20240419'})
        result = self.repo.get_pending_orders(limit=10)
        self.assertEqual(len(result), 1)

    def test_get_pending_orders_executed(self):
        oid = self.repo.save_order({'ticker': 'AAPL', 'option_type': 'PUT',
                                     'action': 'SELL', 'strike': 150.0,
                                     'expiration': '20240419'})
        self.repo.update_order_status(oid, 'executed', executed=True)
        pending = self.repo.get_pending_orders(limit=10)
        self.assertEqual(len(pending), 0)
        executed = self.repo.get_pending_orders(executed=True, limit=10)
        self.assertEqual(len(executed), 1)

    def test_get_pending_orders_with_is_rollover(self):
        oid1 = self.repo.save_order({'ticker': 'AAPL', 'option_type': 'PUT',
                                      'action': 'SELL', 'strike': 150.0,
                                      'expiration': '20240419', 'isRollover': False})
        oid2 = self.repo.save_order({'ticker': 'AAPL', 'option_type': 'PUT',
                                      'action': 'SELL', 'strike': 155.0,
                                      'expiration': '20240419', 'isRollover': True})
        no_roll = self.repo.get_pending_orders(isRollover=False)
        self.assertEqual(len(no_roll), 1)
        roll = self.repo.get_pending_orders(isRollover=True)
        self.assertEqual(len(roll), 1)
        all_pending = self.repo.get_pending_orders(limit=10)
        self.assertEqual(len(all_pending), 2)

    def test_update_order_status_with_partial_details(self):
        oid = self.repo.save_order({'ticker': 'AAPL', 'option_type': 'PUT',
                                     'action': 'SELL', 'strike': 150.0,
                                     'expiration': '20240419'})
        result = self.repo.update_order_status(
            oid, 'filled', executed=True,
            execution_details={'moomoo_order_id': 'moo_42'}
        )
        self.assertTrue(result)
        order = self.repo.get_order(oid)
        self.assertEqual(order['moomoo_order_id'], 'moo_42')
        self.assertIsNone(order['moomoo_status'])

    def test_update_order_status_with_empty_details(self):
        oid = self.repo.save_order({'ticker': 'AAPL', 'option_type': 'PUT',
                                     'action': 'SELL', 'strike': 150.0,
                                     'expiration': '20240419'})
        result = self.repo.update_order_status(oid, 'cancelled')
        self.assertTrue(result)
        order = self.repo.get_order(oid)
        self.assertEqual(order['status'], 'cancelled')


class TestTradeEventsRepositoryDirect(unittest.TestCase):
    """Direct tests for TradeEventsRepository"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_te.db')
        conn = sqlite3.connect(self.db_path)
        create_tables(conn)
        conn.close()
        self.repo = TradeEventsRepository(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_get_trade_analytics_empty(self):
        analytics = self.repo.get_trade_analytics()
        self.assertEqual(analytics['total_exits'], 0)
        self.assertEqual(analytics['wins'], 0)
        self.assertEqual(analytics['win_rate'], 0)
        self.assertEqual(analytics['avg_leakage'], 0)
        self.assertEqual(analytics['roll_count'], 0)
        self.assertEqual(analytics['per_symbol'], [])
        self.assertEqual(analytics['exit_events'], [])

    def test_get_trade_analytics_with_exits(self):
        self.repo.save_trade_event({
            'event_type': 'exit', 'ticker': 'AAPL', 'option_type': 'PUT',
            'strike': 150.0, 'expiration': '20240419', 'pnl': 50.0, 'leakage': 5.0,
        })
        self.repo.save_trade_event({
            'event_type': 'exit', 'ticker': 'AAPL', 'option_type': 'PUT',
            'strike': 150.0, 'expiration': '20240419', 'pnl': -20.0, 'leakage': 3.0,
        })
        self.repo.save_trade_event({
            'event_type': 'roll', 'ticker': 'AAPL', 'option_type': 'PUT',
            'strike': 150.0, 'expiration': '20240419',
            'from_strike': 145.0, 'to_strike': 150.0,
            'premium_in': 200.0, 'premium_out': 150.0,
        })

        analytics = self.repo.get_trade_analytics()
        self.assertEqual(analytics['total_exits'], 2)
        self.assertEqual(analytics['wins'], 1)
        self.assertEqual(analytics['win_rate'], 50.0)
        self.assertEqual(analytics['roll_count'], 1)
        self.assertEqual(analytics['avg_leakage'], 4.0)
        self.assertEqual(len(analytics['per_symbol']), 1)

    def test_get_trade_analytics_with_zero_leakage_exits(self):
        self.repo.save_trade_event({
            'event_type': 'exit', 'ticker': 'AAPL', 'option_type': 'PUT',
            'strike': 150.0, 'expiration': '20240419', 'pnl': 10.0, 'leakage': 0.0,
        })
        analytics = self.repo.get_trade_analytics()
        self.assertEqual(analytics['total_exits'], 1)
        self.assertEqual(analytics['avg_leakage'], 0.0)

    def test_get_trade_analytics_target_hit_and_stopped(self):
        self.repo.save_trade_event({
            'event_type': 'target_hit', 'ticker': 'AAPL', 'option_type': 'PUT',
            'strike': 150.0, 'expiration': '20240419', 'pnl': 100.0,
        })
        self.repo.save_trade_event({
            'event_type': 'stopped', 'ticker': 'TSLA', 'option_type': 'PUT',
            'strike': 200.0, 'expiration': '20240419', 'pnl': -150.0,
        })
        analytics = self.repo.get_trade_analytics()
        self.assertEqual(analytics['total_exits'], 2)
        self.assertEqual(analytics['wins'], 1)
        self.assertEqual(analytics['win_rate'], 50.0)


if __name__ == '__main__':
    unittest.main()
