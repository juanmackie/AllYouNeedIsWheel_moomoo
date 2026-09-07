"""Portfolio snapshot repository — one row per completed wheel run.

Feeds equity history (NAV/cash over time) and position-diff trade-event
inference. Rows are keyed by run_id so history can be joined to
``run_metadata`` snapshots.
"""

import json
import logging

from .sqlite_pool import pooled_connection

logger = logging.getLogger("db.portfolio_snapshots")


def _identity_where(env, account_id):
    """Scope snapshot reads by environment/account (C04). Empty account_id rows
    are legacy/unidentifiable and are quarantined from active-account history.
    Returns (where_sql, params); callers prepend 'WHERE 1=1'."""
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


class PortfolioSnapshotsRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def save_portfolio_snapshot(self, snapshot: dict) -> bool:
        """Insert one snapshot. Idempotent per run_id; returns True on write."""
        try:
            positions = snapshot.get("positions", [])
            if isinstance(positions, list):
                positions_json = json.dumps(positions)
            else:
                positions_json = "[]"

            with pooled_connection(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO portfolio_snapshots (
                        run_id, captured_at, env, account_id,
                        net_liquidation, cash_available, cash_reserved_for_csp,
                        cash_available_for_csp, broker_buying_power, positions_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(snapshot.get("run_id", "") or ""),
                        str(snapshot.get("captured_at", "") or ""),
                        str(snapshot.get("env", "") or ""),
                        str(snapshot.get("account_id", "") or ""),
                        float(snapshot.get("net_liquidation", 0) or 0),
                        float(snapshot.get("cash_available", 0) or 0),
                        float(snapshot.get("cash_reserved_for_csp", 0) or 0),
                        float(snapshot.get("cash_available_for_csp", 0) or 0),
                        float(snapshot.get("broker_buying_power", 0) or 0),
                        positions_json,
                    ),
                )
                conn.commit()
                return True
        except Exception as exc:
            logger.error("Error saving portfolio snapshot: %s", exc)
            return False

    def get_latest_portfolio_snapshot(self, env=None, account_id=None) -> dict | None:
        """Most recent snapshot for the given (env, account), or any when omitted."""
        try:
            where, params = _identity_where(env, account_id)
            with pooled_connection(self.db_path, row_factory=None) as conn:
                row = conn.execute(
                    """
                    SELECT run_id, captured_at, env, account_id,
                           net_liquidation, cash_available, cash_reserved_for_csp,
                           cash_available_for_csp, broker_buying_power, positions_json
                    FROM portfolio_snapshots
                    WHERE 1=1
                    """
                    + where
                    + " ORDER BY datetime(captured_at) DESC, id DESC LIMIT 1",
                    params,
                ).fetchone()
            return self._row_to_dict(row)
        except Exception as exc:
            logger.error("Error loading latest portfolio snapshot: %s", exc)
            return None

    def get_portfolio_history(self, limit: int = 180, env=None, account_id=None, unbounded: bool = False) -> list[dict]:
        """Snapshot series oldest-first (chart-friendly), newest last, scoped to
        an (env, account) identity (C04); any identity when omitted.

        ``unbounded=True`` ignores ``limit`` and returns the complete account
        history. Growth-pace callers use it so the durable baseline (the true
        first snapshot) never shifts with the chart's ``limit`` parameter (C07).
        """
        try:
            if not unbounded:
                limit = min(max(int(limit if limit is not None else 180), 0), 1000)
                if limit == 0:
                    return []
            where, params = _identity_where(env, account_id)
            tail = "" if unbounded else " LIMIT ?"
            query_params = params if unbounded else params + [limit]
            with pooled_connection(self.db_path, row_factory=None) as conn:
                rows = conn.execute(
                    """
                    SELECT run_id, captured_at, env, account_id,
                           net_liquidation, cash_available, cash_reserved_for_csp,
                           cash_available_for_csp, broker_buying_power, positions_json
                    FROM portfolio_snapshots
                    WHERE 1=1
                    """
                    + where
                    + " ORDER BY datetime(captured_at) DESC, id DESC"
                    + tail,
                    query_params,
                ).fetchall()
            snapshots = [self._row_to_dict(row) for row in rows]
            snapshots.reverse()
            return snapshots
        except Exception as exc:
            logger.error("Error loading portfolio history: %s", exc)
            return []

    @staticmethod
    def _row_to_dict(row) -> dict | None:
        if row is None:
            return None
        try:
            positions = json.loads(row[9]) if row[9] else []
        except (TypeError, ValueError):
            positions = []
        return {
            "run_id": row[0],
            "captured_at": row[1],
            "env": row[2],
            "account_id": row[3],
            "net_liquidation": row[4],
            "cash_available": row[5],
            "cash_reserved_for_csp": row[6],
            "cash_available_for_csp": row[7],
            "broker_buying_power": row[8],
            "positions": positions,
        }
