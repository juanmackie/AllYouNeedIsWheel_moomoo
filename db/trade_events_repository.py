import sqlite3
import json
from datetime import datetime
import logging

from .sqlite_pool import pooled_connection

logger = logging.getLogger('db.trade_events')


class TradeEventsRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def save_trade_event(self, event_data):
        try:
            with pooled_connection(self.db_path) as conn:
                cursor = conn.cursor()

                timestamp = event_data.get('timestamp', datetime.now().isoformat())
                details_json = event_data.get('details', {})
                if isinstance(details_json, dict):
                    details_json = json.dumps(details_json)

                cursor.execute('''
                    INSERT INTO trade_events (
                        timestamp, event_type, ticker, option_type, strike, expiration,
                        from_strike, from_expiration, to_strike, to_expiration,
                        premium_in, premium_out, pnl, leakage, reason, details
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    timestamp,
                    event_data.get('event_type', ''),
                    event_data.get('ticker', ''),
                    event_data.get('option_type', ''),
                    float(event_data.get('strike', 0) or 0),
                    event_data.get('expiration', ''),
                    float(event_data.get('from_strike', 0) or 0),
                    event_data.get('from_expiration', ''),
                    float(event_data.get('to_strike', 0) or 0),
                    event_data.get('to_expiration', ''),
                    float(event_data.get('premium_in', 0) or 0),
                    float(event_data.get('premium_out', 0) or 0),
                    float(event_data.get('pnl', 0) or 0),
                    float(event_data.get('leakage', 0) or 0),
                    event_data.get('reason', ''),
                    details_json,
                ))

                conn.commit()
        except Exception:
            raise

    def get_trade_events(self, ticker=None, event_type=None, limit=100):
        try:
            with pooled_connection(self.db_path, row_factory=sqlite3.Row) as conn:
                cursor = conn.cursor()

                query = 'SELECT * FROM trade_events WHERE 1=1'
                params = []

                if ticker:
                    query += ' AND ticker = ?'
                    params.append(ticker)
                if event_type:
                    query += ' AND event_type = ?'
                    params.append(event_type)

                query += ' ORDER BY timestamp DESC LIMIT ?'
                params.append(limit)

                cursor.execute(query, params)
                rows = cursor.fetchall()

                results = []
                for row in rows:
                    event = dict(row)
                    if event.get('details') and isinstance(event['details'], str):
                        try:
                            event['details'] = json.loads(event['details'])
                        except (json.JSONDecodeError, TypeError):
                            event['details'] = {}
                    results.append(event)

                return results
        except Exception as e:
            logger.error(f"Error getting trade events: {str(e)}")
            return []

    def get_trade_analytics(self):
        try:
            with pooled_connection(self.db_path, row_factory=sqlite3.Row) as conn:
                cursor = conn.cursor()

                cursor.execute('''
                    SELECT ticker, event_type, pnl, leakage, premium_in, premium_out,
                           strike, expiration, reason
                    FROM trade_events
                    WHERE event_type IN ('exit', 'target_hit', 'stopped')
                    ORDER BY timestamp
                ''')
                exit_events = [dict(row) for row in cursor.fetchall()]

                total_exits = len(exit_events)
                wins = sum(1 for e in exit_events if (e.get('pnl') or 0) > 0)
                win_rate = (wins / total_exits * 100) if total_exits > 0 else 0

                events_with_leakage = [e for e in exit_events if (e.get('leakage') or 0) > 0]
                avg_leakage = (
                    sum(e.get('leakage', 0) for e in events_with_leakage) / len(events_with_leakage)
                    if events_with_leakage else 0
                )

                cursor.execute('''
                    SELECT ticker, from_strike, from_expiration, to_strike, to_expiration,
                           premium_in, premium_out
                    FROM trade_events
                    WHERE event_type = 'roll'
                    ORDER BY timestamp
                ''')
                roll_events = [dict(row) for row in cursor.fetchall()]

                cursor.execute('''
                    SELECT ticker,
                           COUNT(*) as total_events,
                           SUM(CASE WHEN event_type = 'entry' THEN 1 ELSE 0 END) as entries,
                           SUM(CASE WHEN event_type IN ('exit', 'target_hit', 'stopped') THEN 1 ELSE 0 END) as exits,
                           SUM(CASE WHEN event_type = 'roll' THEN 1 ELSE 0 END) as rolls,
                           AVG(CASE WHEN event_type IN ('exit', 'target_hit', 'stopped') THEN pnl ELSE NULL END) as avg_pnl
                    FROM trade_events
                    GROUP BY ticker
                    ORDER BY total_events DESC
                ''')
                per_symbol = [dict(row) for row in cursor.fetchall()]

                return {
                    'total_exits': total_exits,
                    'wins': wins,
                    'win_rate': round(win_rate, 1),
                    'avg_leakage': round(avg_leakage, 2),
                    'roll_count': len(roll_events),
                    'per_symbol': per_symbol,
                    'exit_events': exit_events,
                }
        except Exception as e:
            logger.error(f"Error getting trade analytics: {str(e)}")
            return {
                'total_exits': 0,
                'wins': 0,
                'win_rate': 0,
                'avg_leakage': 0,
                'roll_count': 0,
                'per_symbol': [],
                'exit_events': [],
            }
