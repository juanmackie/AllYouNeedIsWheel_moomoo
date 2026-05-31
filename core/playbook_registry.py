"""
Wheel Playbook Registry — lightweight hypothesis storage for the wheel operator.

Each hypothesis is a testable statement about wheel strategy, e.g.:
  - "Avoid selling CSPs within 5 trading days of earnings"
  - "Prefer 20-35 DTE puts over weeklies"
  - "Roll at 21 DTE regardless of OTM distance"

Statuses: exploring → testing → validated / rejected
Also supports a 'monitoring' status for hypotheses being tracked.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

VALID_STATUSES = {"exploring", "testing", "validated", "rejected", "monitoring"}

DEFAULT_HYPOTHESES = [
    {
        "hypothesis_id": "earnings_avoid_5d",
        "title": "Avoid CSPs within 5 days of earnings",
        "description": "Do not open new CSP positions when the underlying reports earnings within 5 calendar days. Earnings events create asymmetric tail risk for put sellers.",
        "category": "earnings",
        "status": "exploring",
        "tags": ["csp", "earnings", "risk-management"],
    },
    {
        "hypothesis_id": "prefer_20_35_dte",
        "title": "Prefer 20-35 DTE puts over weeklies",
        "description": "Cash-secured puts in the 20-35 DTE range offer a better risk/reward balance than weeklies, with more theta decay time and less gamma risk.",
        "category": "dte",
        "status": "testing",
        "tags": ["csp", "dte", "theta"],
    },
    {
        "hypothesis_id": "roll_at_21_dte",
        "title": "Roll at 21 DTE regardless of OTM distance",
        "description": "Roll short options when they reach 21 DTE to capture the bulk of theta decay and avoid gamma acceleration.",
        "category": "roll",
        "status": "exploring",
        "tags": ["roll", "dte", "theta"],
    },
    {
        "hypothesis_id": "close_50pct_profit",
        "title": "Close positions at 50% of max profit",
        "description": "Close short options when 50% of the maximum premium has been captured. Reduces tail risk and frees up capital.",
        "category": "exit",
        "status": "testing",
        "tags": ["exit", "profit-target"],
    },
    {
        "hypothesis_id": "iv_rank_above_40",
        "title": "Only sell puts when IV rank > 40",
        "description": "Require IV rank above the 40th percentile to ensure adequate premium for the risk taken.",
        "category": "iv",
        "status": "exploring",
        "tags": ["csp", "iv", "premium"],
    },
]


@dataclass
class PlaybookHypothesis:
    hypothesis_id: str = ""
    title: str = ""
    description: str = ""
    category: str = "general"
    status: str = "exploring"
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class HypothesisRegistry:
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

    def _ensure_defaults(self):
        for h in DEFAULT_HYPOTHESES:
            try:
                conn = self._connect()
                existing = conn.execute(
                    "SELECT id FROM playbook_hypotheses WHERE hypothesis_id = ?",
                    (h["hypothesis_id"],),
                ).fetchone()
                if not existing:
                    conn.execute(
                        """
                        INSERT INTO playbook_hypotheses
                            (hypothesis_id, title, description, category, status, tags_json, notes, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, '', datetime('now'), datetime('now'))
                        """,
                        (h["hypothesis_id"], h["title"], h["description"],
                         h["category"], h["status"], json.dumps(h["tags"])),
                    )
                conn.commit()
            except Exception as exc:
                logger.warning("Failed to ensure default hypothesis %s: %s", h["hypothesis_id"], exc)

    def list_all(self, status: str | None = None) -> list[dict]:
        self._ensure_defaults()
        try:
            conn = self._connect()
            if status:
                rows = conn.execute(
                    "SELECT * FROM playbook_hypotheses WHERE status = ? ORDER BY updated_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM playbook_hypotheses ORDER BY updated_at DESC"
                ).fetchall()
            return [_row_to_hypothesis(r) for r in rows]
        except Exception as exc:
            logger.error("Failed to list hypotheses: %s", exc)
            return []

    def get(self, hypothesis_id: str) -> dict | None:
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM playbook_hypotheses WHERE hypothesis_id = ?",
                (hypothesis_id,),
            ).fetchone()
            return _row_to_hypothesis(row) if row else None
        except Exception as exc:
            logger.error("Failed to get hypothesis %s: %s", hypothesis_id, exc)
            return None

    def create(self, hypothesis: PlaybookHypothesis) -> bool:
        try:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO playbook_hypotheses
                    (hypothesis_id, title, description, category, status, tags_json, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    hypothesis.hypothesis_id,
                    hypothesis.title,
                    hypothesis.description,
                    hypothesis.category,
                    hypothesis.status if hypothesis.status in VALID_STATUSES else "exploring",
                    json.dumps(hypothesis.tags),
                    hypothesis.notes,
                ),
            )
            conn.commit()
            return True
        except Exception as exc:
            logger.error("Failed to create hypothesis %s: %s", hypothesis.hypothesis_id, exc)
            return False

    def update_status(self, hypothesis_id: str, status: str) -> bool:
        if status not in VALID_STATUSES:
            logger.warning("Invalid hypothesis status: %s", status)
            return False
        try:
            conn = self._connect()
            cursor = conn.execute(
                "UPDATE playbook_hypotheses SET status = ?, updated_at = datetime('now') WHERE hypothesis_id = ?",
                (status, hypothesis_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as exc:
            logger.error("Failed to update hypothesis %s: %s", hypothesis_id, exc)
            return False

    def update_notes(self, hypothesis_id: str, notes: str) -> bool:
        try:
            conn = self._connect()
            cursor = conn.execute(
                "UPDATE playbook_hypotheses SET notes = ?, updated_at = datetime('now') WHERE hypothesis_id = ?",
                (notes, hypothesis_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as exc:
            logger.error("Failed to update notes for %s: %s", hypothesis_id, exc)
            return False

    def delete(self, hypothesis_id: str) -> bool:
        try:
            conn = self._connect()
            cursor = conn.execute(
                "DELETE FROM playbook_hypotheses WHERE hypothesis_id = ?",
                (hypothesis_id,),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as exc:
            logger.error("Failed to delete hypothesis %s: %s", hypothesis_id, exc)
            return False


def _row_to_hypothesis(row) -> dict:
    d = dict(row)
    if "tags_json" in d and isinstance(d["tags_json"], str):
        try:
            d["tags"] = json.loads(d["tags_json"])
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
        del d["tags_json"]
    return d
