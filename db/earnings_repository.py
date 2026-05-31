import sqlite3
from datetime import datetime, timedelta
import logging

from .sqlite_pool import pooled_connection

logger = logging.getLogger('db.earnings')


class EarningsRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def save_earnings_date(self, ticker, earnings_date, fetch_status='success', error_message=None,
                           time_of_day=None, fiscal_date_ending=None, estimate=None,
                           currency=None, earnings_source=None):
        try:
            with pooled_connection(self.db_path) as conn:
                cursor = conn.cursor()

                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                cursor.execute('''
                    INSERT OR REPLACE INTO earnings_calendar
                    (ticker, earnings_date, last_updated, fetch_status, error_message,
                     time_of_day, fiscal_date_ending, estimate, currency, earnings_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (ticker, earnings_date, timestamp, fetch_status, error_message,
                      time_of_day, fiscal_date_ending, estimate, currency, earnings_source))

                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error saving earnings date for {ticker}: {str(e)}")
            return False

    def mark_earnings_error(self, ticker, error_message, earnings_source=None):
        try:
            with pooled_connection(self.db_path) as conn:
                cursor = conn.cursor()
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                cursor.execute('''
                    UPDATE earnings_calendar
                    SET last_updated = ?, fetch_status = 'error', error_message = ?,
                        earnings_source = COALESCE(?, earnings_source)
                    WHERE ticker = ?
                ''', (timestamp, error_message, earnings_source, ticker))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error marking earnings error for {ticker}: {str(e)}")
            return False

    def get_earnings_date(self, ticker):
        try:
            with pooled_connection(self.db_path, row_factory=sqlite3.Row) as conn:
                cursor = conn.cursor()

                cursor.execute('SELECT * FROM earnings_calendar WHERE ticker = ?', (ticker,))
                row = cursor.fetchone()

                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting earnings date for {ticker}: {str(e)}")
            return None

    def get_pending_earnings(self, days_threshold=7):
        try:
            with pooled_connection(self.db_path) as conn:
                cursor = conn.cursor()

                today = datetime.now().date()
                future_date = (today + timedelta(days=days_threshold)).strftime('%Y-%m-%d')
                today_str = today.strftime('%Y-%m-%d')

                cursor.execute('''
                    SELECT ticker, earnings_date, time_of_day, fiscal_date_ending,
                           estimate, currency, earnings_source
                    FROM earnings_calendar
                    WHERE earnings_date >= ? AND earnings_date <= ?
                    ORDER BY earnings_date
                ''', (today_str, future_date))

                rows = cursor.fetchall()

                return [
                    {
                        'ticker': row[0],
                        'earnings_date': row[1],
                        'time_of_day': row[2],
                        'fiscal_date_ending': row[3],
                        'estimate': row[4],
                        'currency': row[5],
                        'earnings_source': row[6],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.error(f"Error getting pending earnings: {str(e)}")
            return []

    def get_tickers_needing_earnings_update(self, hours_threshold=24):
        try:
            with pooled_connection(self.db_path) as conn:
                cursor = conn.cursor()

                cutoff_time = (datetime.now() - timedelta(hours=hours_threshold)).strftime('%Y-%m-%d %H:%M:%S')

                cursor.execute('''
                    SELECT ticker FROM earnings_calendar
                    WHERE last_updated < ? OR fetch_status = 'pending' OR fetch_status = 'error'
                ''', (cutoff_time,))

                rows = cursor.fetchall()

                return [row[0] for row in rows]
        except Exception as e:
            logger.error(f"Error getting tickers needing earnings update: {str(e)}")
            return []
