import json
import logging
import sqlite3
from datetime import datetime

from .sqlite_pool import pooled_connection

logger = logging.getLogger("db.trade_events")


def _identity_where(env, account_id):
    """Build a WHERE fragment and params that scope by environment/account.

    C04: when an account identity is supplied, only that account's inferred
    journal qualifies; events with an empty account_id are legacy/unidentifiable
    and are quarantined from active-account reads.
    """
    clauses = []
    params = []
    if env:
        clauses.append("env = ?")
        params.append(env)
    if account_id:
        clauses.append("account_id = ?")
        params.append(account_id)
    if clauses:
        return " AND " + " AND ".join(clauses), params
    return "", params


class TradeEventsRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def save_trade_event(self, event_data):
        try:
            with pooled_connection(self.db_path) as conn:
                cursor = conn.cursor()

                timestamp = event_data.get("timestamp", datetime.now().isoformat())
                details_json = event_data.get("details", {})
                if isinstance(details_json, dict):
                    details_json = json.dumps(details_json)

                pnl = event_data.get("pnl")
                if pnl is not None:
                    pnl = float(pnl)

                cursor.execute(
                    """
                    INSERT INTO trade_events (
                        timestamp, event_type, ticker, option_type, strike, expiration,
                        from_strike, from_expiration, to_strike, to_expiration,
                        premium_in, premium_out, pnl, leakage, reason,
                        env, account_id, provenance, details
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        timestamp,
                        event_data.get("event_type", ""),
                        event_data.get("ticker", ""),
                        event_data.get("option_type", ""),
                        float(event_data.get("strike", 0) or 0),
                        event_data.get("expiration", ""),
                        float(event_data.get("from_strike", 0) or 0),
                        event_data.get("from_expiration", ""),
                        float(event_data.get("to_strike", 0) or 0),
                        event_data.get("to_expiration", ""),
                        float(event_data.get("premium_in", 0) or 0),
                        float(event_data.get("premium_out", 0) or 0),
                        pnl,
                        float(event_data.get("leakage", 0) or 0),
                        event_data.get("reason", ""),
                        event_data.get("env", "") or "",
                        event_data.get("account_id", "") or "",
                        event_data.get("provenance", "verified") or "verified",
                        details_json,
                    ),
                )
                conn.commit()
                logger.info(
                    "Trade event saved: type=%s ticker=%s opt=%s strike=%.2f exp=%s env=%s account=%s pnl=%s",
                    event_data.get("event_type"),
                    event_data.get("ticker"),
                    event_data.get("option_type"),
                    float(event_data.get("strike", 0) or 0),
                    event_data.get("expiration"),
                    event_data.get("env", "") or "",
                    event_data.get("account_id", "") or "",
                    pnl,
                )
        except Exception:
            logger.error(
                "Error saving trade event: type=%s ticker=%s",
                event_data.get("event_type"),
                event_data.get("ticker"),
                exc_info=True,
            )
            raise

    def get_trade_events(self, ticker=None, event_type=None, limit=100, env=None, account_id=None):
        try:
            with pooled_connection(self.db_path, row_factory=sqlite3.Row) as conn:
                cursor = conn.cursor()

                query = "SELECT * FROM trade_events WHERE 1=1"
                params = []

                if ticker:
                    query += " AND ticker = ?"
                    params.append(ticker)
                if event_type:
                    query += " AND event_type = ?"
                    params.append(event_type)

                where, identity_params = _identity_where(env, account_id)
                query += where
                params.extend(identity_params)

                query += " ORDER BY timestamp DESC LIMIT ?"
                params.append(limit)

                cursor.execute(query, params)
                rows = cursor.fetchall()

                results = []
                for row in rows:
                    event = dict(row)
                    if event.get("details") and isinstance(event["details"], str):
                        try:
                            event["details"] = json.loads(event["details"])
                        except (json.JSONDecodeError, TypeError):
                            event["details"] = {}
                    results.append(event)

                return results
        except Exception as e:
            logger.error(f"Error getting trade events: {str(e)}")
            return []

    def get_trade_analytics(self, env=None, account_id=None):
        try:
            with pooled_connection(self.db_path, row_factory=sqlite3.Row) as conn:
                cursor = conn.cursor()

                identity_sql, identity_params = _identity_where(env, account_id)
                identity_join = ""
                if identity_sql:
                    identity_join = identity_sql

                cursor.execute(
                    f"""
                    SELECT ticker, event_type, pnl, leakage, premium_in, premium_out,
                           strike, expiration, reason, provenance
                    FROM trade_events
                    WHERE event_type IN ('exit', 'target_hit', 'stopped')
                    {identity_join}
                    ORDER BY timestamp
                    """,
                    identity_params,
                )
                exit_events = [dict(row) for row in cursor.fetchall()]

                total_exits = len(exit_events)
                # C05: unknown outcomes (pnl NULL from inference) never enter the
                # win-rate denominator; they are reported separately.
                measured = [e for e in exit_events if e.get("pnl") is not None]
                wins = sum(1 for e in measured if e["pnl"] > 0)
                measured_exits = len(measured)
                unknown_exits = total_exits - measured_exits
                win_rate = (wins / measured_exits * 100) if measured_exits > 0 else 0

                events_with_leakage = [e for e in exit_events if (e.get("leakage") or 0) > 0]
                avg_leakage = (
                    sum(e.get("leakage", 0) for e in events_with_leakage) / len(events_with_leakage)
                    if events_with_leakage
                    else 0
                )

                cursor.execute(
                    f"""
                    SELECT ticker, from_strike, from_expiration, to_strike, to_expiration,
                           premium_in, premium_out
                    FROM trade_events
                    WHERE event_type = 'roll'
                    {identity_join}
                    ORDER BY timestamp
                    """,
                    identity_params,
                )
                roll_events = [dict(row) for row in cursor.fetchall()]

                cursor.execute(
                    f"""
                    SELECT ticker,
                           COUNT(*) as total_events,
                           SUM(CASE WHEN event_type = 'entry' THEN 1 ELSE 0 END) as entries,
                           SUM(CASE WHEN event_type IN ('exit', 'target_hit', 'stopped') THEN 1 ELSE 0 END) as exits,
                           SUM(CASE WHEN event_type = 'roll' THEN 1 ELSE 0 END) as rolls,
                           AVG(CASE WHEN event_type IN ('exit', 'target_hit', 'stopped') THEN pnl ELSE NULL END) as avg_pnl
                    FROM trade_events
                    WHERE 1=1
                    {identity_join}
                    GROUP BY ticker
                    ORDER BY total_events DESC
                    """,
                    identity_params,
                )
                per_symbol = [dict(row) for row in cursor.fetchall()]

                return {
                    "total_exits": total_exits,
                    "measured_exits": measured_exits,
                    "unknown_exits": unknown_exits,
                    "wins": wins,
                    "win_rate": round(win_rate, 1),
                    "avg_leakage": round(avg_leakage, 2),
                    "roll_count": len(roll_events),
                    "per_symbol": per_symbol,
                    "exit_events": exit_events,
                }
        except Exception as e:
            logger.error(f"Error getting trade analytics: {str(e)}")
            return {
                "total_exits": 0,
                "measured_exits": 0,
                "unknown_exits": 0,
                "wins": 0,
                "win_rate": 0,
                "avg_leakage": 0,
                "roll_count": 0,
                "per_symbol": [],
                "exit_events": [],
            }
