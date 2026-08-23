"""
Wheel Scan Ledger — permanent record of every scan the app has run.

Each scan produces one ledger entry (like a Vibe-Trading run card) recording:
  - scan_type (recommendations, roll, watchlist)
  - timestamp
  - config_hash (deterministic hash of active config)
  - portfolio_hash (snapshot of positions + cash)
  - data_sources used
  - warnings emitted
  - top signals and blocked candidates
  - counts and scoring version

This is read-only for users but inspectable via `/api/ledger`.
"""

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

SCORING_VERSION = "2.0-bid-tier"


@dataclass
class ScanLedgerEntry:
    scan_type: str = "recommendations"
    timestamp: str = ""
    config_hash: str = ""
    portfolio_hash: str = ""
    scoring_version: str = SCORING_VERSION
    data_sources: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    top_signals: list[dict] = field(default_factory=list)
    blocked_candidates: list[dict] = field(default_factory=list)
    total_candidates: int = 0
    passed_count: int = 0
    blocked_count: int = 0
    elapsed_seconds: float = 0.0
    error_message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def compute_config_hash(config: dict) -> str:
    stable = _stable_json(config)
    return hashlib.sha256(stable.encode()).hexdigest()[:16]


def compute_portfolio_hash(portfolio_context: dict) -> str:
    try:
        positions = portfolio_context.get("positions", {})
        cash = portfolio_context.get("cash_balance", 0)
        account = portfolio_context.get("account_value", 0)
        parts = []
        for symbol, pos in sorted(positions.items()):
            qty = float(pos.get("position", 0) or 0)
            parts.append(f"{symbol}:{qty}")
        raw = "|".join(parts) + f"|CASH:{cash}|ACCT:{account}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
    except Exception:
        return "unknown"


def extract_data_sources(portfolio_context: dict, decisions: list | None = None) -> list[dict]:
    sources: set[str] = set()
    if portfolio_context.get("vix_regime"):
        sources.add("vix_regime")
    if portfolio_context.get("broker_buying_power"):
        sources.add("moomoo_portfolio")
    if decisions:
        for d in decisions:
            wd = d if hasattr(d, "to_dict") else d.get("wheel_decision", d)
            if isinstance(wd, dict):
                for key in (
                    "price_source",
                    "chain_source",
                    "greeks_source",
                    "iv_source",
                    "earnings_source",
                ):
                    val = wd.get(key, "")
                    if val and val not in ("missing", ""):
                        sources.add(val)
            elif hasattr(wd, "price_source"):
                for attr in (
                    "price_source",
                    "chain_source",
                    "greeks_source",
                    "iv_source",
                    "earnings_source",
                ):
                    val = getattr(wd, attr, "")
                    if val and val not in ("missing", ""):
                        sources.add(val)
    return [{"name": s, "status": "used"} for s in sorted(sources)]


class ScanLedger:
    def __init__(self, db):
        self.db = db
        self._conn = None

    def _connect(self):
        if self._conn is None:
            from db.sqlite_pool import pooled_connection

            self._conn_ctx = pooled_connection(self.db.db_path)
            self._conn = self._conn_ctx.__enter__()
        return self._conn

    def close(self):
        if self._conn is not None:
            self._conn_ctx.__exit__(None, None, None)
            self._conn = None

    def record(self, entry: ScanLedgerEntry) -> int | None:
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


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str)
