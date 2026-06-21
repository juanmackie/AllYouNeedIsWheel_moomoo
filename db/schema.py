import traceback
import logging

from .sqlite_pool import pooled_connection

logger = logging.getLogger('db.schema')

SCHEMA_VERSION = 6


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
        CREATE INDEX IF NOT EXISTS idx_recommendations_ticker_timestamp
        ON recommendations(ticker, timestamp)
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
            UNIQUE(ticker)
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_earnings_calendar_ticker_date
        ON earnings_calendar(ticker, earnings_date)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_earnings_calendar_last_updated
        ON earnings_calendar(last_updated)
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

    # ── Wheel Scan Ledger (borrowed from Vibe-Trading run cards) ────────────
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scan_ledger (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_type           TEXT NOT NULL,
            timestamp           TEXT NOT NULL,
            config_hash         TEXT NOT NULL,
            portfolio_hash      TEXT NOT NULL,
            scoring_version     TEXT NOT NULL DEFAULT '1.0',
            data_sources_json   TEXT NOT NULL DEFAULT '[]',
            warnings_json       TEXT NOT NULL DEFAULT '[]',
            top_signals_json    TEXT NOT NULL DEFAULT '[]',
            blocked_candidates_json TEXT NOT NULL DEFAULT '[]',
            total_candidates    INTEGER DEFAULT 0,
            passed_count        INTEGER DEFAULT 0,
            blocked_count       INTEGER DEFAULT 0,
            elapsed_seconds     REAL DEFAULT 0.0,
            error_message       TEXT,
            created_at          TEXT DEFAULT (datetime('now'))
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_scan_ledger_timestamp
        ON scan_ledger(timestamp)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_scan_ledger_type_ts
        ON scan_ledger(scan_type, timestamp)
    ''')

    # ── Wheel Playbook Hypotheses (borrowed from Vibe-Trading hypothesis registry) ──
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS playbook_hypotheses (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            hypothesis_id   TEXT NOT NULL UNIQUE,
            title           TEXT NOT NULL,
            description     TEXT NOT NULL,
            category        TEXT NOT NULL DEFAULT 'general',
            status          TEXT NOT NULL DEFAULT 'exploring',
            tags_json       TEXT NOT NULL DEFAULT '[]',
            notes           TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_playbook_hypotheses_status
        ON playbook_hypotheses(status)
    ''')

    # ── Option Chain Snapshots (persistent cache for after-hours / broker-unavailable) ──
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS option_chain_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT NOT NULL,
            expiration  TEXT NOT NULL,
            right       TEXT NOT NULL,
            stock_price REAL,
            chain_json  TEXT NOT NULL,
            source      TEXT NOT NULL DEFAULT 'broker',
            as_of       TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now')),
            UNIQUE(ticker, expiration, right)
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_option_chain_snapshots_ticker
        ON option_chain_snapshots(ticker)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_option_chain_snapshots_as_of
        ON option_chain_snapshots(as_of)
    ''')


def migrate_database(db_path):
    try:
        with pooled_connection(db_path) as conn:
            cursor = conn.cursor()

            current_version = cursor.execute('PRAGMA user_version').fetchone()[0]
            if current_version >= SCHEMA_VERSION:
                logger.info("Database schema already at version %s", current_version)
                return

            if current_version < 1:
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

                cursor.execute('PRAGMA user_version = 1')
                conn.commit()

            if current_version < 2:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS scan_ledger (
                        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                        scan_type           TEXT NOT NULL,
                        timestamp           TEXT NOT NULL,
                        config_hash         TEXT NOT NULL,
                        portfolio_hash      TEXT NOT NULL,
                        scoring_version     TEXT NOT NULL DEFAULT '1.0',
                        data_sources_json   TEXT NOT NULL DEFAULT '[]',
                        warnings_json       TEXT NOT NULL DEFAULT '[]',
                        top_signals_json    TEXT NOT NULL DEFAULT '[]',
                        blocked_candidates_json TEXT NOT NULL DEFAULT '[]',
                        total_candidates    INTEGER DEFAULT 0,
                        passed_count        INTEGER DEFAULT 0,
                        blocked_count       INTEGER DEFAULT 0,
                        elapsed_seconds     REAL DEFAULT 0.0,
                        error_message       TEXT,
                        created_at          TEXT DEFAULT (datetime('now'))
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_scan_ledger_timestamp ON scan_ledger(timestamp)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_scan_ledger_type_ts ON scan_ledger(scan_type, timestamp)")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS playbook_hypotheses (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        hypothesis_id   TEXT NOT NULL UNIQUE,
                        title           TEXT NOT NULL,
                        description     TEXT NOT NULL,
                        category        TEXT NOT NULL DEFAULT 'general',
                        status          TEXT NOT NULL DEFAULT 'exploring',
                        tags_json       TEXT NOT NULL DEFAULT '[]',
                        notes           TEXT DEFAULT '',
                        created_at      TEXT DEFAULT (datetime('now')),
                        updated_at      TEXT DEFAULT (datetime('now'))
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_playbook_hypotheses_status ON playbook_hypotheses(status)")
                cursor.execute('PRAGMA user_version = 2')
                conn.commit()

            if current_version < 3:
                # Drop evaluator/calibrator tables — feature fully retired.
                # Historical outcomes are no longer used for training.
                for table in (
                    'evaluator_signals',
                    'evaluator_feedback_bias',
                    'evaluator_feedback_events',
                    'evaluator_calibrations',
                    'evaluator_scheduler_state',
                ):
                    cursor.execute(f"DROP TABLE IF EXISTS {table}")
                    logger.info("Migration: Dropped %s table (evaluator/calibrator retired)", table)
                cursor.execute('PRAGMA user_version = 3')
                conn.commit()

            if current_version < 4:
                # Add missing indexes for recommendations and earnings_calendar
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_recommendations_ticker_timestamp ON recommendations(ticker, timestamp)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_earnings_calendar_ticker_date ON earnings_calendar(ticker, earnings_date)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_earnings_calendar_last_updated ON earnings_calendar(last_updated)")
                logger.info("Migration: Added missing indexes for recommendations and earnings_calendar")
                cursor.execute('PRAGMA user_version = 4')
                conn.commit()

            if current_version < 5:
                # Enforce one earnings row per ticker and keep the freshest update.
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS earnings_calendar_new (
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
                        UNIQUE(ticker)
                    )
                """)
                cursor.execute("""
                    INSERT OR IGNORE INTO earnings_calendar_new (
                        id, ticker, earnings_date, last_updated, fetch_status,
                        error_message, time_of_day, fiscal_date_ending, estimate,
                        currency, earnings_source
                    )
                    SELECT
                        e.id, e.ticker, e.earnings_date, e.last_updated, e.fetch_status,
                        e.error_message, e.time_of_day, e.fiscal_date_ending, e.estimate,
                        e.currency, e.earnings_source
                    FROM earnings_calendar e
                    WHERE e.id = (
                        SELECT e2.id
                        FROM earnings_calendar e2
                        WHERE e2.ticker = e.ticker
                        ORDER BY datetime(e2.last_updated) DESC, e2.id DESC
                        LIMIT 1
                    )
                """)
                cursor.execute("DROP TABLE earnings_calendar")
                cursor.execute("ALTER TABLE earnings_calendar_new RENAME TO earnings_calendar")
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_earnings_calendar_ticker ON earnings_calendar(ticker)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_earnings_calendar_ticker_date ON earnings_calendar(ticker, earnings_date)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_earnings_calendar_last_updated ON earnings_calendar(last_updated)")
                logger.info("Migration: Deduplicated earnings_calendar by ticker")
                cursor.execute('PRAGMA user_version = 5')
                conn.commit()

            if current_version < 6:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS option_chain_snapshots (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        ticker      TEXT NOT NULL,
                        expiration  TEXT NOT NULL,
                        right       TEXT NOT NULL,
                        stock_price REAL,
                        chain_json  TEXT NOT NULL,
                        source      TEXT NOT NULL DEFAULT 'broker',
                        as_of       TEXT NOT NULL,
                        created_at  TEXT DEFAULT (datetime('now')),
                        UNIQUE(ticker, expiration, right)
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_option_chain_snapshots_ticker ON option_chain_snapshots(ticker)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_option_chain_snapshots_as_of ON option_chain_snapshots(as_of)")
                logger.info("Migration: Created option_chain_snapshots table for persistent chain cache")
                cursor.execute('PRAGMA user_version = 6')
                conn.commit()

            logger.info("Database migration completed successfully (schema version %s)", SCHEMA_VERSION)
    except Exception as e:
        logger.error(f"Error during database migration: {str(e)}")
        logger.error(traceback.format_exc())
