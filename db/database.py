"""
Database module for SQLite logging of trades
"""

from contextlib import contextmanager
from pathlib import Path
import logging

from .schema import create_tables, migrate_database
from .iv_repository import IVRepository
from .earnings_repository import EarningsRepository
from .trade_events_repository import TradeEventsRepository
from .evaluator_repository import EvaluatorRepository
from .sqlite_pool import (
    pooled_connection,
    get_connection_pool_stats,
    register_pool_handle,
    release_pool_handle,
)

logger = logging.getLogger('db.database')


class OptionsDatabase:
    def __init__(self, db_name=None):
        self._closed = False
        if db_name is None:
            self.db_path = Path(__file__).parent.parent / 'options.db'
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
        self._evaluator = EvaluatorRepository(self.db_path)

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
            except Exception:
                conn.rollback()
                raise

    # --- IV History ---

    def save_iv_data(self, ticker, implied_volatility, stock_price=None, option_type=None, expiration=None, dte=None):
        return self._iv.save_iv_data(ticker, implied_volatility, stock_price=stock_price, option_type=option_type, expiration=expiration, dte=dte)

    def get_iv_history(self, ticker, days=30):
        return self._iv.get_iv_history(ticker, days=days)

    def get_latest_iv(self, ticker):
        return self._iv.get_latest_iv(ticker)

    def purge_old_iv_data(self, days=45):
        return self._iv.purge_old_iv_data(days=days)

    # --- Earnings Calendar ---

    def save_earnings_date(self, ticker, earnings_date, fetch_status='success', error_message=None,
                           time_of_day=None, fiscal_date_ending=None, estimate=None,
                           currency=None, earnings_source=None):
        return self._earnings.save_earnings_date(
            ticker, earnings_date, fetch_status=fetch_status, error_message=error_message,
            time_of_day=time_of_day, fiscal_date_ending=fiscal_date_ending,
            estimate=estimate, currency=currency, earnings_source=earnings_source,
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

    # --- Evaluator (signals, feedback, calibration) ---

    @property
    def evaluator(self) -> EvaluatorRepository:
        return self._evaluator

    def record_evaluator_signal(self, signal: dict) -> str:
        return self._evaluator.record_signal(signal)

    def get_evaluator_summary_stats(self) -> dict:
        return self._evaluator.get_summary_stats()

    def get_evaluator_recent_signals(self, limit: int = 50) -> list[dict]:
        return self._evaluator.get_recent_signals(limit=limit)

    def get_evaluator_feedback_summary(self) -> dict:
        return self._evaluator.get_feedback_summary()

    def get_evaluator_calibration(self) -> dict:
        return self._evaluator.get_latest_calibration()

    def get_evaluator_calibration_history(self, limit: int = 20) -> list[dict]:
        return self._evaluator.get_calibration_history(limit=limit)

    def get_evaluator_scheduler_states(self) -> list[dict]:
        return self._evaluator.get_all_scheduler_states()

    def get_evaluator_valid_sample_count(self) -> int:
        return self._evaluator.get_valid_sample_count()

    def close(self):
        if self._closed or not hasattr(self, 'db_path'):
            return
        release_pool_handle(self.db_path)
        self._closed = True

    def get_connection_pool_stats(self):
        return get_connection_pool_stats(self.db_path)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
