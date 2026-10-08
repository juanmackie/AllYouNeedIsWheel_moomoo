"""Durable, account-scoped storage for folded wheel cycles."""

from __future__ import annotations

import json
import logging
import sqlite3

from core.utils import market_now
from db.sqlite_pool import pooled_connection
from db.trade_events_repository import _identity_where

logger = logging.getLogger("db.wheel_cycles")

_CYCLE_COLUMNS = (
    "cycle_id",
    "env",
    "account_id",
    "ticker",
    "state",
    "origin",
    "opened_at",
    "closed_at",
    "shares",
    "gross_basis_per_share",
    "credits_net",
    "open_credit",
    "fees_known",
    "completeness",
    "reasons_json",
    "adjusted_basis_per_share",
    "events_json",
    "updated_at",
)


class WheelCyclesRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def save_cycles(self, cycles) -> int:
        rows = [cycle for cycle in (cycles or []) if isinstance(cycle, dict) and cycle.get("cycle_id")]
        if not rows:
            return 0
        placeholders = ", ".join("?" for _ in _CYCLE_COLUMNS)
        updates = ", ".join(f"{column}=excluded.{column}" for column in _CYCLE_COLUMNS if column != "cycle_id")
        query = f"""
            INSERT INTO wheel_cycles ({", ".join(_CYCLE_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT(cycle_id) DO UPDATE SET {updates}
        """
        values = []
        for cycle in rows:
            reasons = cycle.get("reasons", cycle.get("reasons_json", []))
            events = cycle.get("events", cycle.get("events_json", []))
            values.append(
                (
                    str(cycle["cycle_id"]),
                    str(cycle.get("env", "") or ""),
                    str(cycle.get("account_id", "") or ""),
                    str(cycle.get("ticker", "") or "").upper(),
                    str(cycle.get("state", "needs_review") or "needs_review"),
                    str(cycle.get("origin", "") or ""),
                    str(cycle.get("opened_at", "") or ""),
                    cycle.get("closed_at"),
                    float(cycle.get("shares", 0) or 0),
                    cycle.get("gross_basis_per_share"),
                    float(cycle.get("credits_net", 0) or 0),
                    cycle.get("open_credit"),
                    int(bool(cycle.get("fees_known"))),
                    str(cycle.get("completeness", "partial") or "partial"),
                    reasons if isinstance(reasons, str) else json.dumps(reasons, sort_keys=True),
                    cycle.get("adjusted_basis_per_share"),
                    events if isinstance(events, str) else json.dumps(events, sort_keys=True),
                    str(cycle.get("updated_at", "") or market_now().isoformat()),
                )
            )
        try:
            with pooled_connection(self.db_path) as conn:
                before = conn.total_changes
                conn.executemany(query, values)
                conn.commit()
                return conn.total_changes - before
        except Exception:
            logger.error("Error saving wheel cycles", exc_info=True)
            raise

    def get_cycles(self, env=None, account_id=None, ticker=None, states=None, limit=500):
        try:
            with pooled_connection(self.db_path, row_factory=sqlite3.Row) as conn:
                query = "SELECT * FROM wheel_cycles WHERE 1=1"
                params = []
                where, identity_params = _identity_where(env, account_id)
                query += where
                params.extend(identity_params)
                if ticker:
                    query += " AND ticker = ?"
                    params.append(str(ticker).upper())
                if states:
                    state_values = [str(state) for state in states if state]
                    if state_values:
                        query += f" AND state IN ({', '.join('?' for _ in state_values)})"
                        params.extend(state_values)
                query += " ORDER BY updated_at DESC, opened_at DESC LIMIT ?"
                params.append(max(1, int(limit)))
                rows = [dict(row) for row in conn.execute(query, params).fetchall()]
            for row in rows:
                for name, target in (("reasons_json", "reasons"), ("events_json", "events")):
                    try:
                        row[target] = json.loads(row.get(name) or "[]")
                    except (TypeError, json.JSONDecodeError):
                        row[target] = []
            return rows
        except Exception:
            logger.error("Error getting wheel cycles", exc_info=True)
            return []
