import json
import logging
import sqlite3
from datetime import datetime, timezone

from .sqlite_pool import pooled_connection

logger = logging.getLogger("db.option_chain")


class OptionChainRepository:
    """Persistent option chain snapshots for after-hours / broker-unavailable fallback."""

    def __init__(self, db_path):
        self.db_path = db_path

    def save_snapshot(self, ticker, expiration, right, stock_price, chain_dict, source="broker", as_of=None):
        """Persist or update an option chain snapshot, keyed by (ticker, expiration, right)."""
        if as_of is None:
            as_of = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        with pooled_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO option_chain_snapshots
                    (ticker, expiration, right, stock_price, chain_json, source, as_of)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    expiration,
                    right,
                    float(stock_price) if stock_price else None,
                    json.dumps(chain_dict, default=str),
                    source,
                    as_of,
                ),
            )
            conn.commit()
            logger.debug("Saved option chain snapshot for %s %s %s (source=%s)", ticker, expiration, right, source)

    def get_snapshot(self, ticker, expiration, right):
        """Get a specific snapshot by (ticker, expiration, right)."""
        with pooled_connection(self.db_path, row_factory=sqlite3.Row) as conn:
            row = conn.execute(
                "SELECT * FROM option_chain_snapshots WHERE ticker = ? AND expiration = ? AND right = ?",
                (ticker, expiration, right),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def get_latest_for_ticker(self, ticker, right, max_age_hours=168):
        """Get the latest snapshot for a ticker and option type.

        Args:
            ticker: Underlying symbol
            right: 'C' or 'P'
            max_age_hours: Maximum age in hours (default 168 = 7 days)
        """
        with pooled_connection(self.db_path, row_factory=sqlite3.Row) as conn:
            row = conn.execute(
                """
                SELECT * FROM option_chain_snapshots
                WHERE ticker = ? AND right = ?
                  AND datetime(as_of) >= datetime('now', ?)
                ORDER BY as_of DESC
                LIMIT 1
                """,
                (ticker, right, f"-{max_age_hours} hours"),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def get_all_latest_for_tickers(self, tickers, right, max_age_hours=168):
        """Get the latest snapshot for each ticker in the list.

        Args:
            tickers: List of underlying symbols
            right: 'C' or 'P'
            max_age_hours: Maximum age in hours (default 168 = 7 days)
        """
        if not tickers:
            return {}
        placeholders = ",".join("?" for _ in tickers)
        with pooled_connection(self.db_path, row_factory=sqlite3.Row) as conn:
            rows = conn.execute(
                f"""
                SELECT ticker, expiration, right, stock_price, chain_json, source, as_of, MAX(as_of) as max_as_of
                FROM option_chain_snapshots
                WHERE ticker IN ({placeholders}) AND right = ?
                  AND datetime(as_of) >= datetime('now', ?)
                GROUP BY ticker
                """,
                (*tickers, right, f"-{max_age_hours} hours"),
            ).fetchall()
            return {row["ticker"]: self._row_to_dict(row) for row in rows}

    def clear_old_snapshots(self, days=14):
        """Remove snapshots older than the specified days."""
        with pooled_connection(self.db_path) as conn:
            conn.execute(
                "DELETE FROM option_chain_snapshots WHERE datetime(as_of) < datetime('now', ?)", (f"-{days} days",)
            )
            conn.commit()
            logger.info("Cleared option chain snapshots older than %d days", days)

    def get_stats(self):
        """Get count and age statistics for the snapshots."""
        with pooled_connection(self.db_path, row_factory=sqlite3.Row) as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as total,
                    COUNT(DISTINCT ticker) as distinct_tickers,
                    COALESCE(CAST(ROUND(AVG(
                        (julianday('now') - julianday(as_of)) * 24
                    )) AS INTEGER), 0) as avg_age_hours,
                    MAX(as_of) as newest,
                    MIN(as_of) as oldest
                FROM option_chain_snapshots
                """
            ).fetchone()
            return dict(row) if row else {}

    @staticmethod
    def _row_to_dict(row):
        if row is None:
            return None
        d = dict(row)
        if "chain_json" in d and isinstance(d["chain_json"], str):
            try:
                d["chain_data"] = json.loads(d["chain_json"])
            except (json.JSONDecodeError, TypeError):
                d["chain_data"] = None
        return d
