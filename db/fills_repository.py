"""Account-scoped broker fill and cash-flow persistence (schema v10).

Verified broker evidence for outcome measurement:

- ``option_fills`` — one row per broker deal (options and underlying stock),
  keyed by the broker deal id (``fill_id``) so re-ingestion is idempotent.
  ``fees`` stays NULL until the order-level fee query resolves it: unknown
  fees are distinct from zero fees.
- ``account_cash_flows`` — deposits/withdrawals and other cash movements,
  persisted separately so trading-profit attribution can exclude capital
  movements by construction.

Rows are scoped by (env, account_id) using the same opaque account identity
as every other repository (C04), mirroring
``db.trade_events_repository.TradeEventsRepository``.
"""

import json
import logging
import sqlite3
from datetime import datetime

from .sqlite_pool import pooled_connection
from .trade_events_repository import _identity_where

logger = logging.getLogger("db.fills")

_FILL_COLUMNS = (
    "fill_id",
    "order_id",
    "run_id",
    "captured_at",
    "ingested_at",
    "env",
    "account_id",
    "ticker",
    "security_type",
    "contract_key",
    "option_type",
    "strike",
    "expiration",
    "side",
    "qty",
    "price",
    "fees",
    "raw_json",
)


class FillsRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def save_fills(self, fill_rows):
        """Insert broker fills idempotently; return the count of new rows.

        Rows without a ``fill_id`` are rejected (the broker deal id is the
        only safe idempotency key). Re-ingesting an existing fill is a no-op.
        """
        if not fill_rows:
            return 0
        inserted = 0
        try:
            with pooled_connection(self.db_path) as conn:
                cursor = conn.cursor()
                for row in fill_rows:
                    if not isinstance(row, dict):
                        continue
                    fill_id = str(row.get("fill_id", "") or "").strip()
                    if not fill_id:
                        logger.warning("Skipping fill without fill_id: ticker=%s", row.get("ticker", ""))
                        continue
                    raw = row.get("raw_json", {})
                    if isinstance(raw, dict):
                        raw = json.dumps(raw)
                    fees = row.get("fees")
                    fees = float(fees) if fees is not None else None
                    strike = row.get("strike")
                    strike = float(strike) if strike is not None else None
                    cursor.execute(
                        f"""
                        INSERT OR IGNORE INTO option_fills ({", ".join(_FILL_COLUMNS)})
                        VALUES ({", ".join("?" for _ in _FILL_COLUMNS)})
                        """,
                        (
                            fill_id,
                            str(row.get("order_id", "") or ""),
                            str(row.get("run_id", "") or ""),
                            str(row.get("captured_at", "") or ""),
                            str(row.get("ingested_at", "") or datetime.now().isoformat()),
                            str(row.get("env", "") or ""),
                            str(row.get("account_id", "") or ""),
                            str(row.get("ticker", "") or ""),
                            str(row.get("security_type", "") or ""),
                            str(row.get("contract_key", "") or ""),
                            str(row.get("option_type", "") or ""),
                            strike,
                            str(row.get("expiration", "") or ""),
                            str(row.get("side", "") or "").upper(),
                            float(row.get("qty", 0) or 0),
                            float(row.get("price", 0) or 0),
                            fees,
                            raw,
                        ),
                    )
                    inserted += cursor.rowcount
                conn.commit()
        except Exception:
            logger.error("Error saving fills", exc_info=True)
            raise
        if inserted:
            logger.info("Ingested %d new fill(s)", inserted)
        return inserted

    def get_fills(
        self,
        env=None,
        account_id=None,
        ticker=None,
        contract_key=None,
        security_type=None,
        order_id=None,
        start=None,
        end=None,
        limit=5000,
    ):
        """Return account-scoped fills, newest first, as dicts."""
        try:
            with pooled_connection(self.db_path, row_factory=sqlite3.Row) as conn:
                query = "SELECT * FROM option_fills WHERE 1=1"
                params = []
                if ticker:
                    query += " AND ticker = ?"
                    params.append(ticker)
                if contract_key:
                    query += " AND contract_key = ?"
                    params.append(contract_key)
                if security_type:
                    query += " AND security_type = ?"
                    params.append(security_type)
                if order_id:
                    query += " AND order_id = ?"
                    params.append(order_id)
                if start:
                    query += " AND captured_at >= ?"
                    params.append(start)
                if end:
                    query += " AND captured_at <= ?"
                    params.append(end)
                where, identity_params = _identity_where(env, account_id)
                query += where
                params.extend(identity_params)
                query += " ORDER BY captured_at DESC, id DESC LIMIT ?"
                params.append(limit)
                rows = [dict(row) for row in conn.execute(query, params).fetchall()]
            for row in rows:
                raw = row.get("raw_json")
                if isinstance(raw, str) and raw:
                    try:
                        row["raw_json"] = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        row["raw_json"] = {}
            return rows
        except Exception:
            logger.error("Error getting fills", exc_info=True)
            return []

    def get_latest_fill_captured_at(self, env=None, account_id=None):
        """Return the newest captured_at for this account, or None."""
        try:
            with pooled_connection(self.db_path) as conn:
                query = "SELECT captured_at FROM option_fills WHERE 1=1"
                params = []
                where, identity_params = _identity_where(env, account_id)
                query += where
                params.extend(identity_params)
                query += " ORDER BY captured_at DESC LIMIT 1"
                row = conn.execute(query, params).fetchone()
            return row[0] if row else None
        except Exception:
            logger.error("Error getting latest fill timestamp", exc_info=True)
            return None

    def update_fill_fees(self, fee_by_fill_id):
        """Set resolved order-level fees on fill rows; return updated count."""
        if not fee_by_fill_id:
            return 0
        updated = 0
        try:
            with pooled_connection(self.db_path) as conn:
                cursor = conn.cursor()
                for fill_id, fee in fee_by_fill_id.items():
                    cursor.execute(
                        "UPDATE option_fills SET fees = ? WHERE fill_id = ? AND fees IS NULL",
                        (float(fee), str(fill_id)),
                    )
                    updated += cursor.rowcount
                conn.commit()
        except Exception:
            logger.error("Error updating fill fees", exc_info=True)
            raise
        return updated

    def get_order_ids_missing_fees(self, env=None, account_id=None):
        """Return distinct order ids whose fee allocation is still unknown."""
        try:
            with pooled_connection(self.db_path) as conn:
                query = "SELECT DISTINCT order_id FROM option_fills WHERE order_id != '' AND fees IS NULL"
                params = []
                where, identity_params = _identity_where(env, account_id)
                query += where
                params.extend(identity_params)
                rows = conn.execute(query, params).fetchall()
            return [row[0] for row in rows]
        except Exception:
            logger.error("Error getting order ids missing fees", exc_info=True)
            return []


class CashFlowRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def save_cash_flows(self, flow_rows):
        """Insert cash-flow rows idempotently (keyed by broker cashflow_id)."""
        if not flow_rows:
            return 0
        inserted = 0
        try:
            with pooled_connection(self.db_path) as conn:
                cursor = conn.cursor()
                for row in flow_rows:
                    if not isinstance(row, dict):
                        continue
                    cashflow_id = str(row.get("cashflow_id", "") or "").strip()
                    if not cashflow_id:
                        logger.warning("Skipping cash flow without cashflow_id")
                        continue
                    raw = row.get("raw_json", {})
                    if isinstance(raw, dict):
                        raw = json.dumps(raw)
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO account_cash_flows (
                            cashflow_id, clearing_date, settlement_date, currency,
                            direction, flow_type, amount, remark, env, account_id, raw_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cashflow_id,
                            str(row.get("clearing_date", "") or ""),
                            str(row.get("settlement_date", "") or ""),
                            str(row.get("currency", "") or ""),
                            str(row.get("direction", "") or "").upper(),
                            str(row.get("flow_type", "") or ""),
                            float(row.get("amount", 0) or 0),
                            str(row.get("remark", "") or ""),
                            str(row.get("env", "") or ""),
                            str(row.get("account_id", "") or ""),
                            raw,
                        ),
                    )
                    inserted += cursor.rowcount
                conn.commit()
        except Exception:
            logger.error("Error saving cash flows", exc_info=True)
            raise
        return inserted

    def get_cash_flows(self, env=None, account_id=None, start=None, end=None, limit=5000):
        """Return account-scoped cash flows, newest first, as dicts."""
        try:
            with pooled_connection(self.db_path, row_factory=sqlite3.Row) as conn:
                query = "SELECT * FROM account_cash_flows WHERE 1=1"
                params = []
                if start:
                    query += " AND clearing_date >= ?"
                    params.append(start)
                if end:
                    query += " AND clearing_date <= ?"
                    params.append(end)
                where, identity_params = _identity_where(env, account_id)
                query += where
                params.extend(identity_params)
                query += " ORDER BY clearing_date DESC, id DESC LIMIT ?"
                params.append(limit)
                rows = [dict(row) for row in conn.execute(query, params).fetchall()]
            for row in rows:
                raw = row.get("raw_json")
                if isinstance(raw, str) and raw:
                    try:
                        row["raw_json"] = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        row["raw_json"] = {}
            return rows
        except Exception:
            logger.error("Error getting cash flows", exc_info=True)
            return []
