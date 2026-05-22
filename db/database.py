"""
Database module for SQLite logging of trades
"""

import sqlite3
from pathlib import Path
import logging

from .schema import create_tables, migrate_database
from .iv_repository import IVRepository
from .earnings_repository import EarningsRepository
from .trade_events_repository import TradeEventsRepository

logger = logging.getLogger('db.database')


class OptionsDatabase:
    def __init__(self, db_name=None):
        if db_name is None:
            self.db_path = Path(__file__).parent.parent / 'options.db'
        else:
            self.db_path = Path(db_name).resolve()

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(self.db_path))
        create_tables(conn)
        conn.close()
        migrate_database(self.db_path)

        self._iv = IVRepository(self.db_path)
        self._earnings = EarningsRepository(self.db_path)
        self._trade_events = TradeEventsRepository(self.db_path)

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
