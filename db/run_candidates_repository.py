"""Idempotent archive of hard-gate-passing candidates for later measurement."""

from __future__ import annotations

import hashlib
import json
import logging

from core.utils import market_now
from db.sqlite_pool import pooled_connection
from db.trade_events_repository import _identity_where

logger = logging.getLogger("db.run_candidates")


class RunCandidatesRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    @staticmethod
    def _candidate_id(run_id, candidate):
        identity = "\0".join(
            (
                str(run_id),
                str(candidate.get("ticker", "")).upper(),
                str(candidate.get("option_type", "")).upper(),
                str(candidate.get("expiration", "")),
                str(candidate.get("strike", "")),
            )
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def archive_in_transaction(self, conn, run, candidates, per_ticker_cap=10):
        seen: dict[str, int] = {}
        archived_at = market_now().isoformat()
        inserted = 0
        for rank, candidate in enumerate(candidates or [], 1):
            if not isinstance(candidate, dict):
                continue
            ticker = str(candidate.get("ticker") or "").strip().upper()
            option_type = str(candidate.get("option_type") or "").strip().upper()
            expiration = str(candidate.get("expiration") or "")
            try:
                strike = float(candidate.get("strike"))
            except (TypeError, ValueError):
                continue
            if not ticker or option_type not in {"PUT", "CALL"} or not expiration:
                continue
            count = seen.get(ticker, 0)
            if count >= max(1, int(per_ticker_cap)):
                continue
            seen[ticker] = count + 1
            payload = dict(candidate)
            cursor = conn.execute(
                """INSERT OR IGNORE INTO run_candidates (
                    candidate_id, run_id, env, account_id, ticker, option_type,
                    strike, expiration, dte, bid, stock_price, cash_required,
                    quote_fetched_at_utc, rank_position, candidate_json, archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self._candidate_id(run.run_id, candidate),
                    run.run_id,
                    run.env,
                    run.account_id,
                    ticker,
                    option_type,
                    strike,
                    expiration,
                    candidate.get("dte"),
                    candidate.get("bid"),
                    candidate.get("stock_price"),
                    candidate.get("cash_required"),
                    candidate.get("quote_fetched_at_utc"),
                    rank,
                    json.dumps(payload, sort_keys=True),
                    archived_at,
                ),
            )
            inserted += cursor.rowcount
        return inserted

    def get_candidates(self, env=None, account_id=None, expiration=None, limit=10000):
        try:
            with pooled_connection(self.db_path) as conn:
                where, params = _identity_where(env, account_id)
                query = "SELECT * FROM run_candidates WHERE 1=1" + where
                if expiration:
                    query += " AND expiration = ?"
                    params.append(str(expiration))
                query += " ORDER BY archived_at, rank_position LIMIT ?"
                params.append(max(1, int(limit)))
                rows = conn.execute(query, params).fetchall()
            output = []
            for row in rows:
                candidate = json.loads(row[14])
                output.append(
                    {
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
                    }
                )
            return output
        except Exception:
            logger.error("Error loading run candidates", exc_info=True)
            return []
