import sqlite3
from datetime import datetime, timedelta
import logging

logger = logging.getLogger('db.earnings')


class EarningsRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def save_earnings_date(self, ticker, earnings_date, fetch_status='success', error_message=None):
        try:
            conn = sqlite3.connect(self.db_path); conn.row_factory = None
            cursor = conn.cursor()

            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            cursor.execute('''
                INSERT OR REPLACE INTO earnings_calendar
                (ticker, earnings_date, last_updated, fetch_status, error_message)
                VALUES (?, ?, ?, ?, ?)
            ''', (ticker, earnings_date, timestamp, fetch_status, error_message))

            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error saving earnings date for {ticker}: {str(e)}")
            return False
        finally:
            try: conn.close()
            except Exception: pass

    def get_earnings_date(self, ticker):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute('SELECT * FROM earnings_calendar WHERE ticker = ?', (ticker,))
            row = cursor.fetchone()

            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting earnings date for {ticker}: {str(e)}")
            return None
        finally:
            try: conn.close()
            except Exception: pass

    def get_pending_earnings(self, days_threshold=7):
        try:
            conn = sqlite3.connect(self.db_path); conn.row_factory = None
            cursor = conn.cursor()

            today = datetime.now().date()
            future_date = (today + timedelta(days=days_threshold)).strftime('%Y-%m-%d')
            today_str = today.strftime('%Y-%m-%d')

            cursor.execute('''
                SELECT ticker, earnings_date FROM earnings_calendar
                WHERE earnings_date >= ? AND earnings_date <= ?
                ORDER BY earnings_date
            ''', (today_str, future_date))

            rows = cursor.fetchall()

            return [{'ticker': row[0], 'earnings_date': row[1]} for row in rows]
        except Exception as e:
            logger.error(f"Error getting pending earnings: {str(e)}")
            return []
        finally:
            try: conn.close()
            except Exception: pass

    def get_tickers_needing_earnings_update(self, hours_threshold=24):
        try:
            conn = sqlite3.connect(self.db_path); conn.row_factory = None
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
        finally:
            try: conn.close()
            except Exception: pass
