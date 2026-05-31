import traceback
import logging

from .sqlite_pool import pooled_connection

logger = logging.getLogger('db.schema')

SCHEMA_VERSION = 2


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

    # ── Evaluator tables (outcome tracking, feedback, calibration) ──────────

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS evaluator_signals (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id    TEXT NOT NULL UNIQUE,
            ticker               TEXT NOT NULL,
            option_type          TEXT NOT NULL,
            strike               REAL NOT NULL,
            expiration           TEXT NOT NULL,
            dte                  INTEGER,
            signal_type          TEXT,
            strategy             TEXT,
            source               TEXT,
            rank                 INTEGER,
            score                REAL,
            confidence           REAL,
            annualized_return    REAL,
            premium_per_contract REAL,
            delta                REAL,
            iv                   REAL,
            cash_required        REAL,
            capital_at_risk      REAL,
            broker_buying_power  REAL,
            portfolio_hash       TEXT,
            score_details_json   TEXT,
            full_payload_json    TEXT,
            status               TEXT NOT NULL DEFAULT 'surfaced',
            shown_to_user        INTEGER DEFAULT 1,
            user_action          TEXT,
            linked_position_key  TEXT,
            linked_trade_event_id INTEGER,
            resolved_at          TEXT,
            resolved_outcome     TEXT,
            actual_return        REAL,
            created_at           TEXT NOT NULL DEFAULT (datetime('now'))
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_evaluator_signals_status
        ON evaluator_signals(status)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_evaluator_signals_created_at
        ON evaluator_signals(created_at)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_evaluator_signals_pos_key
        ON evaluator_signals(ticker, option_type, strike, expiration)
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS evaluator_feedback_bias (
            factor          TEXT PRIMARY KEY,
            mean_error      REAL DEFAULT 0.0,
            sample_count    INTEGER DEFAULT 0,
            bias_multiplier REAL DEFAULT 1.0,
            last_updated    TEXT
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_evaluator_feedback_bias_factor
        ON evaluator_feedback_bias(factor)
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS evaluator_feedback_events (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id TEXT,
            ticker           TEXT,
            factor           TEXT,
            predicted_contrib REAL,
            actual_return    REAL,
            error            REAL,
            outcome_type     TEXT,
            created_at       TEXT DEFAULT (datetime('now'))
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS evaluator_calibrations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle       INTEGER NOT NULL,
            samples     INTEGER,
            loss        REAL,
            shadow_loss REAL,
            weights_json TEXT,
            accepted    INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS evaluator_scheduler_state (
            name         TEXT PRIMARY KEY,
            last_run     TEXT,
            last_status  TEXT,
            last_message TEXT
        )
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

            logger.info("Database migration completed successfully (schema version %s)", SCHEMA_VERSION)
    except Exception as e:
        logger.error(f"Error during database migration: {str(e)}")
        logger.error(traceback.format_exc())
