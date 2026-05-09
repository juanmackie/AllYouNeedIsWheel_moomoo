import sqlite3
import traceback
import logging

logger = logging.getLogger('db.schema')


def create_tables(conn):
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            option_type TEXT NOT NULL,
            action TEXT NOT NULL,
            strike REAL NOT NULL,
            expiration TEXT NOT NULL,
            premium REAL,
            details TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
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
            bid REAL DEFAULT 0,
            ask REAL DEFAULT 0,
            last REAL DEFAULT 0,
            delta REAL DEFAULT 0,
            gamma REAL DEFAULT 0,
            theta REAL DEFAULT 0,
            vega REAL DEFAULT 0,
            implied_volatility REAL DEFAULT 0,
            open_interest INTEGER DEFAULT 0,
            volume INTEGER DEFAULT 0,
            is_mock BOOLEAN DEFAULT 0,
            earnings_max_contracts INTEGER DEFAULT 0,
            earnings_premium_per_contract REAL DEFAULT 0,
            earnings_total_premium REAL DEFAULT 0,
            earnings_return_on_cash REAL DEFAULT 0,
            earnings_return_on_capital REAL DEFAULT 0,
            moomoo_order_id TEXT,
            moomoo_status TEXT,
            filled INTEGER DEFAULT 0,
            remaining INTEGER DEFAULT 0,
            avg_fill_price REAL DEFAULT 0,
            isRollover BOOLEAN DEFAULT 0
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS iv_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            implied_volatility REAL NOT NULL,
            stock_price REAL,
            option_type TEXT,
            expiration TEXT,
            dte INTEGER
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_iv_history_ticker_timestamp
        ON iv_history(ticker, timestamp)
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS earnings_calendar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL UNIQUE,
            earnings_date TEXT,
            last_updated TEXT NOT NULL,
            fetch_status TEXT DEFAULT 'pending',
            error_message TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            ticker TEXT NOT NULL,
            option_type TEXT NOT NULL,
            strike REAL NOT NULL,
            expiration TEXT NOT NULL,
            from_strike REAL,
            from_expiration TEXT,
            to_strike REAL,
            to_expiration TEXT,
            premium_in REAL,
            premium_out REAL,
            pnl REAL,
            leakage REAL,
            reason TEXT,
            details TEXT
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_trade_events_ticker_timestamp
        ON trade_events(ticker, timestamp)
    ''')


def migrate_database(db_path):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("PRAGMA table_info(orders)")
        columns = cursor.fetchall()
        column_names = [column[1] for column in columns]

        if 'ib_order_id' in column_names and 'moomoo_order_id' not in column_names:
            logger.info("Running migration: Renaming ib_order_id to moomoo_order_id")
            cursor.execute("ALTER TABLE orders RENAME COLUMN ib_order_id TO moomoo_order_id")
            logger.info("Migration completed: ib_order_id renamed to moomoo_order_id")
            cursor.execute("PRAGMA table_info(orders)")
            columns = cursor.fetchall()
            column_names = [column[1] for column in columns]

        if 'ib_status' in column_names and 'moomoo_status' not in column_names:
            logger.info("Running migration: Renaming ib_status to moomoo_status")
            cursor.execute("ALTER TABLE orders RENAME COLUMN ib_status TO moomoo_status")
            logger.info("Migration completed: ib_status renamed to moomoo_status")
            cursor.execute("PRAGMA table_info(orders)")
            columns = cursor.fetchall()
            column_names = [column[1] for column in columns]

        if 'isRollover' not in column_names:
            logger.info("Running migration: Adding isRollover column to orders table")
            cursor.execute("ALTER TABLE orders ADD COLUMN isRollover BOOLEAN DEFAULT 0")
            logger.info("Migration completed: isRollover column added")

            cursor.execute("""
                WITH order_pairs AS (
                    SELECT o1.id as buy_id, o2.id as sell_id
                    FROM orders o1
                    JOIN orders o2 ON o1.ticker = o2.ticker
                                  AND o1.option_type = o2.option_type
                                  AND datetime(o1.timestamp) BETWEEN datetime(o2.timestamp, '-2 minutes') AND datetime(o2.timestamp, '+2 minutes')
                                  AND o1.action = 'BUY' AND o2.action = 'SELL'
                                  AND o1.isRollover = 0 AND o2.isRollover = 0
                )
                SELECT buy_id, sell_id FROM order_pairs
            """)

            potential_rollover_pairs = cursor.fetchall()

            if potential_rollover_pairs:
                logger.info(f"Found {len(potential_rollover_pairs)} potential rollover order pairs")

                for buy_id, sell_id in potential_rollover_pairs:
                    cursor.execute("UPDATE orders SET isRollover = 1 WHERE id = ?", (buy_id,))
                    cursor.execute("UPDATE orders SET isRollover = 1 WHERE id = ?", (sell_id,))

                logger.info(f"Migration: Marked {len(potential_rollover_pairs) * 2} orders as potential rollovers")

        conn.commit()
        conn.close()
        logger.info("Database migration completed successfully")
    except Exception as e:
        logger.error(f"Error during database migration: {str(e)}")
        logger.error(traceback.format_exc())
