"""
Database module for SQLite logging of trades
"""

import json
import logging
from contextlib import contextmanager
from pathlib import Path

from .earnings_repository import EarningsRepository
from .iv_repository import IVRepository
from .option_chain_repository import OptionChainRepository
from .schema import create_tables, migrate_database
from .sqlite_pool import (
    get_connection_pool_stats,
    pooled_connection,
    register_pool_handle,
    release_pool_handle,
)
from .trade_events_repository import TradeEventsRepository

logger = logging.getLogger("db.database")


class OptionsDatabase:
    def __init__(self, db_name=None):
        self._closed = False
        if db_name is None:
            self.db_path = Path(__file__).parent.parent / "options.db"
        else:
            self.db_path = Path(db_name).resolve()

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        register_pool_handle(self.db_path)

        with pooled_connection(self.db_path) as conn:
            create_tables(conn)
            conn.commit()
        migrate_database(self.db_path)

        self._iv = IVRepository(self.db_path)
        self._earnings = EarningsRepository(self.db_path)
        self._trade_events = TradeEventsRepository(self.db_path)
        self._option_chains = OptionChainRepository(self.db_path)

        logger.info("OptionsDatabase initialized at %s", self.db_path)
        logger.debug(
            "Repositories: IV=%s Earnings=%s TradeEvents=%s OptionChains=%s",
            type(self._iv).__name__,
            type(self._earnings).__name__,
            type(self._trade_events).__name__,
            type(self._option_chains).__name__,
        )

    @contextmanager
    def transaction(self):
        """Context manager for atomic multi-step database operations.

        Usage:
            with db.transaction():
                db.save_iv_data(...)
                db.save_earnings_date(...)
        """
        with pooled_connection(self.db_path) as conn:
            try:
                yield conn
                conn.commit()
                logger.debug("DB transaction committed")
            except Exception:
                conn.rollback()
                logger.warning("DB transaction rolled back", exc_info=True)
                raise

    # --- Settings (key/value) ---

    def get_setting(self, key: str, default=None):
        """Return a persisted setting value, or default when absent."""
        with pooled_connection(self.db_path) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str):
        """Persist a setting value (upsert)."""
        with pooled_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')
                """,
                (key, value),
            )
            conn.commit()

    # --- App-managed watchlist symbols (SQLite) ---

    def get_watchlist_symbols(self):
        """Return app-managed watchlist symbols: [{symbol, origin, created_at}]."""
        with pooled_connection(self.db_path) as conn:
            rows = conn.execute("SELECT symbol, origin, created_at FROM watchlist ORDER BY symbol").fetchall()
        return [{"symbol": row[0], "origin": row[1], "created_at": row[2]} for row in rows]

    def upsert_watchlist_symbol(self, symbol: str, origin: str = "app"):
        """Add/replace an app-managed watchlist symbol (canonicalized by caller)."""
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return False
        with pooled_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO watchlist (symbol, origin, created_at) VALUES (?, ?, datetime('now'))
                ON CONFLICT(symbol) DO UPDATE SET origin = excluded.origin, created_at = datetime('now')
                """,
                (symbol, origin),
            )
            conn.commit()
        return True

    def remove_watchlist_symbol(self, symbol: str):
        """Remove an app-managed watchlist symbol."""
        symbol = str(symbol or "").strip().upper()
        with pooled_connection(self.db_path) as conn:
            cur = conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))
            conn.commit()
        return cur.rowcount > 0

    # --- Completed wheel run snapshots + refresh attempts ---

    def save_run_snapshot(self, snapshot):
        """Persist a completed WheelRunSnapshot (one row, atomically)."""
        payload = snapshot.to_dict()
        run = snapshot.run
        with pooled_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO run_metadata (run_id, generated_at, published_at, env, account_id, status, snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    published_at = excluded.published_at,
                    status = excluded.status,
                    snapshot_json = excluded.snapshot_json
                """,
                (
                    run.run_id,
                    run.generated_at,
                    run.published_at,
                    run.env,
                    run.account_id,
                    run.status,
                    json.dumps(payload),
                ),
            )
            conn.commit()
        return run.run_id

    def get_latest_snapshot(self, env=None):
        """Return the most recently published snapshot dict for env (or any)."""
        import json as _json

        sql = "SELECT snapshot_json FROM run_metadata"
        params: list = []
        if env:
            sql += " WHERE env = ?"
            params.append(env)
        sql += " ORDER BY published_at DESC, generated_at DESC LIMIT 1"
        with pooled_connection(self.db_path) as conn:
            row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        try:
            return _json.loads(row[0])
        except (TypeError, ValueError):
            return None

    def save_refresh_attempt(self, attempt):
        """Persist a RefreshAttempt row."""
        with pooled_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO refresh_attempts (
                    attempt_id, run_id, state, stage, progress,
                    started_at, finished_at, latest_error, latest_failure_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(attempt_id) DO UPDATE SET
                    run_id = excluded.run_id,
                    state = excluded.state,
                    stage = excluded.stage,
                    progress = excluded.progress,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    latest_error = excluded.latest_error,
                    latest_failure_at = excluded.latest_failure_at
                """,
                (
                    attempt.attempt_id,
                    attempt.run_id,
                    attempt.state,
                    attempt.stage,
                    attempt.progress,
                    attempt.started_at,
                    attempt.finished_at,
                    attempt.latest_error,
                    attempt.latest_failure_at,
                ),
            )
            conn.commit()
        return attempt.attempt_id

    def get_latest_attempt(self):
        """Return the most recent RefreshAttempt row as a dict (or None)."""
        with pooled_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT attempt_id, run_id, state, stage, progress, started_at, finished_at, latest_error, latest_failure_at "
                "FROM refresh_attempts ORDER BY created_at DESC, attempt_id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "attempt_id": row[0],
            "run_id": row[1],
            "state": row[2],
            "stage": row[3],
            "progress": row[4],
            "started_at": row[5],
            "finished_at": row[6],
            "latest_error": row[7],
            "latest_failure_at": row[8],
        }

    # --- IV History ---

    def save_iv_data(self, ticker, implied_volatility, stock_price=None, option_type=None, expiration=None, dte=None):
        return self._iv.save_iv_data(
            ticker, implied_volatility, stock_price=stock_price, option_type=option_type, expiration=expiration, dte=dte
        )

    def get_iv_history(self, ticker, days=30):
        return self._iv.get_iv_history(ticker, days=days)

    def get_latest_iv(self, ticker):
        return self._iv.get_latest_iv(ticker)

    def purge_old_iv_data(self, days=45):
        return self._iv.purge_old_iv_data(days=days)

    # --- Earnings Calendar ---

    def save_earnings_date(
        self,
        ticker,
        earnings_date,
        fetch_status="success",
        error_message=None,
        time_of_day=None,
        fiscal_date_ending=None,
        estimate=None,
        currency=None,
        earnings_source=None,
    ):
        return self._earnings.save_earnings_date(
            ticker,
            earnings_date,
            fetch_status=fetch_status,
            error_message=error_message,
            time_of_day=time_of_day,
            fiscal_date_ending=fiscal_date_ending,
            estimate=estimate,
            currency=currency,
            earnings_source=earnings_source,
        )

    def get_earnings_date(self, ticker):
        return self._earnings.get_earnings_date(ticker)

    def get_pending_earnings(self, days_threshold=7):
        return self._earnings.get_pending_earnings(days_threshold=days_threshold)

    def mark_earnings_error(self, ticker, error_message, earnings_source=None):
        return self._earnings.mark_earnings_error(ticker, error_message=error_message, earnings_source=earnings_source)

    def get_tickers_needing_earnings_update(self, hours_threshold=24):
        return self._earnings.get_tickers_needing_earnings_update(hours_threshold=hours_threshold)

    # --- Trade Events ---

    def save_trade_event(self, event_data):
        self._trade_events.save_trade_event(event_data)

    def get_trade_events(self, ticker=None, event_type=None, limit=100):
        return self._trade_events.get_trade_events(ticker=ticker, event_type=event_type, limit=limit)

    def get_trade_analytics(self):
        return self._trade_events.get_trade_analytics()

    # --- Option Chain Snapshots ---

    def save_option_chain_snapshot(
        self, ticker, expiration, right, stock_price, chain_dict, source="broker", as_of=None
    ):
        return self._option_chains.save_snapshot(
            ticker, expiration, right, stock_price, chain_dict, source=source, as_of=as_of
        )

    def get_option_chain_snapshot(self, ticker, expiration, right):
        return self._option_chains.get_snapshot(ticker, expiration, right)

    def get_latest_option_chain(self, ticker, right, max_age_hours=168):
        return self._option_chains.get_latest_for_ticker(ticker, right, max_age_hours=max_age_hours)

    def get_all_latest_option_chains(self, tickers, right, max_age_hours=168):
        return self._option_chains.get_all_latest_for_tickers(tickers, right, max_age_hours=max_age_hours)

    def clear_old_option_chain_snapshots(self, days=14):
        return self._option_chains.clear_old_snapshots(days=days)

    def get_option_chain_snapshot_stats(self):
        return self._option_chains.get_stats()

    def close(self):
        if self._closed or not hasattr(self, "db_path"):
            return
        release_pool_handle(self.db_path)
        self._closed = True
        logger.info("OptionsDatabase closed: %s", self.db_path)

    def get_connection_pool_stats(self):
        return get_connection_pool_stats(self.db_path)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
