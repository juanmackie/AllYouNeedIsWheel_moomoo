"""Persistence for offline outcomes derived from archived run candidates."""

from __future__ import annotations

import json

from db.sqlite_pool import pooled_connection
from db.trade_events_repository import _identity_where


class ShadowOutcomesRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def get_candidates(self, env=None, account_id=None, limit=10000):
        where, params = _identity_where(env, account_id)
        query = (
            """
            SELECT c.candidate_id, c.run_id, c.env, c.account_id, c.ticker,
                   c.option_type, c.strike, c.expiration, c.dte, c.bid,
                   c.stock_price, c.cash_required, c.quote_fetched_at_utc,
                   c.rank_position, c.candidate_json, c.archived_at,
                   s.settled_at, s.expiry_close, s.settlement_source,
                   s.net_per_share, s.outcome_label, s.status, s.outcome_json
            FROM run_candidates c
            LEFT JOIN shadow_outcomes s ON s.candidate_id = c.candidate_id
            WHERE 1=1
        """
            + where
        )
        query += " ORDER BY c.expiration, c.ticker, c.rank_position LIMIT ?"
        params.append(max(1, int(limit)))
        with pooled_connection(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        result = []
        for row in rows:
            try:
                candidate = json.loads(row[14])
                outcome = json.loads(row[22] or "{}")
            except (TypeError, ValueError):
                candidate, outcome = {}, {}
            result.append(
                {
                    **candidate,
                    "candidate_id": row[0],
                    "run_id": row[1],
                    "env": row[2],
                    "account_id": row[3],
                    "ticker": row[4],
                    "option_type": row[5],
                    "strike": row[6],
                    "expiration": row[7],
                    "dte": row[8],
                    "bid": row[9],
                    "stock_price": row[10],
                    "cash_required": row[11],
                    "quote_fetched_at_utc": row[12],
                    "rank_position": row[13],
                    "candidate": candidate,
                    "archived_at": row[15],
                    "settled_at": row[16],
                    "expiry_close": row[17],
                    "settlement_source": row[18],
                    "net_per_share": row[19],
                    "outcome_label": row[20],
                    "status": row[21] or "pending",
                    **outcome,
                }
            )
        return result

    def settle(self, settlements):
        if not settlements:
            return 0
        with pooled_connection(self.db_path) as conn:
            updated = 0
            for item in settlements:
                cursor = conn.execute(
                    """INSERT INTO shadow_outcomes (
                        candidate_id, settled_at, expiry_close, settlement_source,
                        net_per_share, outcome_label, status, outcome_json
                    ) VALUES (?, ?, ?, ?, ?, ?, 'settled', ?)
                    ON CONFLICT(candidate_id) DO UPDATE SET
                        settled_at=excluded.settled_at,
                        expiry_close=excluded.expiry_close,
                        settlement_source=excluded.settlement_source,
                        net_per_share=excluded.net_per_share,
                        outcome_label=excluded.outcome_label,
                        status='settled',
                        outcome_json=excluded.outcome_json
                    WHERE shadow_outcomes.status != 'settled'""",
                    (
                        item["candidate_id"],
                        item["settled_at"],
                        item["expiry_close"],
                        item["settlement_source"],
                        item["net_per_share"],
                        item["outcome_label"],
                        json.dumps(item["outcome"], sort_keys=True),
                    ),
                )
                updated += cursor.rowcount
            conn.commit()
        return updated
