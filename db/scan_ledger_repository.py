"""SQLite persistence for the wheel scan ledger.

``core.scan_ledger`` owns the pure ledger entry shape and hash helpers. This
repository owns the table reads/writes so the core decision layer stays free of
database dependencies.
"""

import json
import logging
import sqlite3
from datetime import datetime

from . import sqlite_pool

logger = logging.getLogger(__name__)


class ScanLedgerRepository:
    def __init__(self, db):
        self.db = db
        self._conn = None

    def _connect(self):
        if self._conn is None:
            self._conn_ctx = sqlite_pool.pooled_connection(self.db.db_path, row_factory=sqlite3.Row)
            self._conn = self._conn_ctx.__enter__()
        return self._conn

    def close(self):
        if self._conn is not None:
            self._conn_ctx.__exit__(None, None, None)
            self._conn = None

    def record(self, entry) -> int | None:
        try:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO scan_ledger
                    (scan_type, timestamp, config_hash, portfolio_hash, scoring_version,
                     data_sources_json, warnings_json, top_signals_json,
                     blocked_candidates_json, total_candidates, passed_count,
                     blocked_count, elapsed_seconds, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.scan_type,
                    entry.timestamp or datetime.now().isoformat(),
                    entry.config_hash,
                    entry.portfolio_hash,
                    entry.scoring_version,
                    json.dumps(entry.data_sources),
                    json.dumps(entry.warnings),
                    json.dumps(entry.top_signals),
                    json.dumps(entry.blocked_candidates),
                    entry.total_candidates,
                    entry.passed_count,
                    entry.blocked_count,
                    entry.elapsed_seconds,
                    entry.error_message,
                ),
            )
            conn.commit()
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        except Exception as exc:
            logger.error("Failed to record scan ledger entry: %s", exc)
            return None

    def get_recent(self, limit: int = 20, scan_type: str | None = None) -> list[dict]:
        try:
            conn = self._connect()
            if scan_type:
                rows = conn.execute(
                    "SELECT * FROM scan_ledger WHERE scan_type = ? ORDER BY id DESC LIMIT ?",
                    (scan_type, limit),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM scan_ledger ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [_row_to_dict(r) for r in rows]
        except Exception as exc:
            logger.error("Failed to query scan ledger: %s", exc)
            return []

    def get_by_id(self, entry_id: int) -> dict | None:
        try:
            conn = self._connect()
            row = conn.execute("SELECT * FROM scan_ledger WHERE id = ?", (entry_id,)).fetchone()
            return _row_to_dict(row) if row else None
        except Exception as exc:
            logger.error("Failed to get scan ledger entry %s: %s", entry_id, exc)
            return None

    def get_stats(self) -> dict:
        try:
            conn = self._connect()
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as total,
                    COUNT(CASE WHEN error_message IS NOT NULL AND error_message != '' THEN 1 END) as errors,
                    AVG(elapsed_seconds) as avg_elapsed,
                    AVG(total_candidates) as avg_candidates,
                    AVG(passed_count) as avg_passed,
                    AVG(blocked_count) as avg_blocked
                FROM scan_ledger
                """
            ).fetchone()
            return dict(row) if row else {}
        except Exception as exc:
            logger.error("Failed to get scan ledger stats: %s", exc)
            return {}


def _row_to_dict(row) -> dict:
    d = dict(row)
    for json_field in ("data_sources_json", "warnings_json", "top_signals_json", "blocked_candidates_json"):
        if json_field in d and isinstance(d[json_field], str):
            try:
                d[json_field] = json.loads(d[json_field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
