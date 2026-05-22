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
            error_message TEXT,
            time_of_day TEXT,
            fiscal_date_ending TEXT,
            estimate REAL,
            currency TEXT,
            earnings_source TEXT
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

        # Drop the orders table — execution subsystem has been removed
        cursor.execute("DROP TABLE IF EXISTS orders")
        logger.info("Migration: Dropped orders table (execution subsystem removed)")

        # --- Migration: Add richer earnings columns to earnings_calendar ---
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='earnings_calendar'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(earnings_calendar)")
            earnings_cols = {row[1] for row in cursor.fetchall()}

            for col, col_type in [
                ('time_of_day', 'TEXT'),
                ('fiscal_date_ending', 'TEXT'),
                ('estimate', 'REAL'),
                ('currency', 'TEXT'),
                ('earnings_source', 'TEXT'),
            ]:
                if col not in earnings_cols:
                    logger.info("Running migration: Adding %s to earnings_calendar", col)
                    cursor.execute("ALTER TABLE earnings_calendar ADD COLUMN %s %s" % (col, col_type))
                    logger.info("Migration completed: %s column added", col)

        conn.commit()
        conn.close()
        logger.info("Database migration completed successfully")
    except Exception as e:
        logger.error(f"Error during database migration: {str(e)}")
        logger.error(traceback.format_exc())
