import sqlite3
from datetime import datetime, timedelta
import logging

from .sqlite_pool import pooled_connection

logger = logging.getLogger('db.iv_history')


class IVRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def save_iv_data(self, ticker, implied_volatility, stock_price=None, option_type=None, expiration=None, dte=None):
        try:
            with pooled_connection(self.db_path) as conn:
                cursor = conn.cursor()

                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                cursor.execute('''
                    INSERT INTO iv_history
                    (ticker, timestamp, implied_volatility, stock_price, option_type, expiration, dte)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (ticker, timestamp, implied_volatility, stock_price, option_type, expiration, dte))

                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error saving IV data for {ticker}: {str(e)}")
            return False

    def get_iv_history(self, ticker, days=30):
        try:
            with pooled_connection(self.db_path, row_factory=sqlite3.Row) as conn:
                cursor = conn.cursor()

                cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

                cursor.execute('''
                    SELECT * FROM iv_history
                    WHERE ticker = ? AND timestamp > ?
                    ORDER BY timestamp DESC
                ''', (ticker, cutoff_date))

                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting IV history for {ticker}: {str(e)}")
            return []

    def get_latest_iv(self, ticker):
        try:
            with pooled_connection(self.db_path, row_factory=sqlite3.Row) as conn:
                cursor = conn.cursor()

                cursor.execute('''
                    SELECT * FROM iv_history
                    WHERE ticker = ?
                    ORDER BY timestamp DESC
                    LIMIT 1
                ''', (ticker,))

                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting latest IV for {ticker}: {str(e)}")
            return None

    def purge_old_iv_data(self, days=45):
        try:
            with pooled_connection(self.db_path) as conn:
                cursor = conn.cursor()

                cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

                cursor.execute('DELETE FROM iv_history WHERE timestamp < ?', (cutoff_date,))
                deleted = cursor.rowcount
                conn.commit()

                logger.info(f"Purged {deleted} old IV history records")
                return deleted
        except Exception as e:
            logger.error(f"Error purging old IV data: {str(e)}")
            return 0
