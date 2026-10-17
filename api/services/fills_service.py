"""Broker-verified outcome ingestion service (read-only).

Fetches deal/fee/cash-flow evidence from OpenD through the query-only
connection surface and persists it account-scoped and idempotently:

- fills (options + underlying shares) keyed by the broker deal id;
- order-level fees allocated back onto fill rows;
- account cash movements (deposits/withdrawals) kept separate from trading
  profit — they never enter attribution arithmetic.

This service owns OpenD/error handling; the attribution math lives in pure
``core.outcome_attribution`` helpers and the persistence in
``db.fills_repository``. Query-only: no order/unlock/cancel/modify surface.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from core.connection_constants import _infer_security_type_from_code, _parse_option_code_metadata
from core.ticker_utils import parse_moomoo_symbol
from core.utils import parse_position_qty

logger = logging.getLogger("api.services.fills")

_DEAL_OK_STATUS = "OK"
_FEE_BACKFILL_BUDGET = 40  # order ids resolved per refresh call
_FEE_QUERY_BATCH_SIZE = 20  # order ids per broker fee query


def normalize_deal_row(row, env: str, opaque_account: str):
    """Normalize one OpenD deal row into an option_fills row (pure).

    Returns None for non-tradeable rows (unknown security type, cancelled
    deals, missing deal id). Option fills decode strike/expiration from the
    contract code and build the same contract_key format the snapshot book
    uses (``SYMBOL yyyymmdd C/P strike``) so fills join events/snapshots.
    """
    if not isinstance(row, dict):
        return None
    deal_id = str(row.get("deal_id", "") or "").strip()
    if not deal_id:
        return None
    status = str(row.get("status", "") or "").upper()
    if status and status != _DEAL_OK_STATUS:
        return None  # cancelled/rolled-back fills are not trading evidence

    code = str(row.get("code", "") or "").strip()
    security_type = _infer_security_type_from_code(code)
    if security_type not in ("OPT", "STK"):
        return None

    side_raw = str(row.get("trd_side", "") or "").upper()
    if "SELL" in side_raw:
        side = "SELL"
    elif "BUY" in side_raw:
        side = "BUY"
    else:
        side = side_raw

    ticker = ""
    contract_key = ""
    option_type = ""
    strike = None
    expiration = ""
    if security_type == "OPT":
        metadata = _parse_option_code_metadata(code)
        if not metadata:
            logger.warning("Skipping a deal with an undecodable option contract code")
            return None
        ticker = metadata["underlying"]
        option_type = metadata["option_type"]
        strike = float(metadata["strike"])
        expiration = metadata["expiration"]
        letter = option_type[:1] or "?"
        contract_key = f"{ticker} {expiration} {letter}{strike}"
    else:
        ticker = parse_moomoo_symbol(code)

    try:
        price = float(row.get("price", 0) or 0)
    except (TypeError, ValueError):
        price = 0.0

    return {
        "fill_id": deal_id,
        "order_id": str(row.get("order_id", "") or ""),
        "run_id": "",
        "captured_at": str(row.get("create_time", "") or ""),
        "ingested_at": datetime.now().isoformat(),
        "env": env or "",
        "account_id": opaque_account or "",
        "ticker": ticker,
        "security_type": security_type,
        "contract_key": contract_key,
        "option_type": option_type,
        "strike": strike,
        "expiration": expiration,
        "side": side,
        "qty": parse_position_qty(row.get("qty", 0)),
        "price": price,
        "fees": None,
        "raw_json": row,
    }


def refresh_fill_evidence(connection, database) -> dict:
    """Pull broker fills for one wheel refresh (query-only, idempotent).

    Owner decision (2026-10-17): fills are pulled on every refresh. Never
    raises; the result tells the caller whether this refresh's pull succeeded.
    """
    if connection is None or database is None:
        return {"ok": False, "reason": "broker_connection_unavailable"}
    try:
        result = FillsService(connection, database).ingest_history_fills(days=None)
    except Exception as exc:
        logger.warning("Fill pull failed during refresh (%s)", type(exc).__name__)
        return {"ok": False, "reason": "fill_pull_failed"}
    evidence = {"ok": bool(result.get("ok")), "reason": result.get("reason", "")}
    for key in ("ingested", "seen", "fees_complete"):
        if key in result:
            evidence[key] = result[key]
    return evidence


class FillsService:
    """Incremental, account-scoped ingestion of broker fills/fees/cash flows."""

    def __init__(self, connection, database):
        self._connection = connection
        self._db = database

    def _identity(self):
        return self._connection.resolve_portfolio_identity()

    def ingest_history_fills(self, days: int | None = None) -> dict:
        """Ingest recent fills incrementally; idempotent per broker deal id.

        The 90-day cap is the SDK's history window; repeated ingestion is
        cheap because existing fill_ids are ignored on insert.
        """
        now = datetime.now()
        try:
            env, opaque_account = self._identity()
        except Exception as exc:
            logger.warning("Fill ingestion skipped: account identity unavailable (%s)", type(exc).__name__)
            return {"ok": False, "reason": "account_identity_unavailable", "ingested": 0}

        coverage = self._db.get_history_fill_coverage(env, opaque_account)
        last_success = coverage.get("last_success_at")
        if days is None:
            try:
                days_since = max(0, (now.date() - date.fromisoformat(str(last_success)[:10])).days)
            except (TypeError, ValueError):
                days_since = 90
            query_days = min(90, max(1, days_since + 2))
        else:
            query_days = min(90, max(1, int(days)))
        start = (now - timedelta(days=query_days)).strftime("%Y-%m-%d")
        attempt_at = now.isoformat(timespec="seconds")
        self._db.set_history_fill_coverage(
            env,
            opaque_account,
            {
                "status": "in_progress",
                "last_attempt_at": attempt_at,
                "last_success_at": last_success,
                "first_covered_date": coverage.get("first_covered_date") or start,
                "reason": "",
            },
        )
        try:
            ret_rows = self._connection.get_history_deals(start=start, end="")
        except Exception as exc:
            self._record_failure(env, opaque_account, attempt_at, "history_query_failed", last_success, start)
            logger.error("Fill ingestion aborted: history query raised %s", type(exc).__name__)
            return {"ok": False, "reason": "history_query_failed", "ingested": 0}
        if ret_rows is None:
            self._record_failure(env, opaque_account, attempt_at, "history_query_failed", last_success, start)
            logger.error("Fill ingestion aborted: history query failed")
            return {"ok": False, "reason": "history_query_failed", "ingested": 0}

        rows = []
        for raw in ret_rows:
            normalized = normalize_deal_row(raw, env, opaque_account)
            if normalized is not None:
                rows.append(normalized)
        try:
            inserted = self._db.save_fills(rows)
            fees_updated, fees_ok = self._backfill_fees(env, opaque_account, rows)
        except Exception as exc:
            self._record_failure(env, opaque_account, attempt_at, "persistence_failed", last_success, start)
            logger.error("Fill ingestion failed while persisting data (%s)", type(exc).__name__)
            return {"ok": False, "reason": "persistence_failed", "ingested": 0}
        if not fees_ok:
            success_at = datetime.now().isoformat(timespec="seconds")
            self._db.set_history_fill_coverage(
                env,
                opaque_account,
                {
                    "status": "success",
                    "last_attempt_at": attempt_at,
                    "last_success_at": success_at,
                    "first_covered_date": coverage.get("first_covered_date") or start,
                    "reason": "fee_query_incomplete",
                },
            )
            return {
                "ok": True,
                "fees_complete": False,
                "reason": "fee_query_failed",
                "seen": len(ret_rows),
                "ingested": inserted,
                "fees_updated": fees_updated,
                "query_days": query_days,
                "covered_from": start,
            }
        success_at = datetime.now().isoformat(timespec="seconds")
        first_covered = coverage.get("first_covered_date") or start
        self._db.set_history_fill_coverage(
            env,
            opaque_account,
            {
                "status": "success",
                "last_attempt_at": attempt_at,
                "last_success_at": success_at,
                "first_covered_date": first_covered,
                "reason": "",
            },
        )
        logger.info(
            "Fill ingestion complete: %d deal row(s) seen, %d new",
            len(ret_rows),
            inserted,
        )
        return {
            "ok": True,
            "fees_complete": True,
            "seen": len(ret_rows),
            "ingested": inserted,
            "fees_updated": fees_updated,
            "query_days": query_days,
            "covered_from": start,
        }

    def _record_failure(self, env, account_id, attempt_at, reason, last_success, start):
        coverage = self._db.get_history_fill_coverage(env, account_id)
        self._db.set_history_fill_coverage(
            env,
            account_id,
            {
                "status": "failed",
                "last_attempt_at": attempt_at,
                "last_success_at": last_success,
                "first_covered_date": coverage.get("first_covered_date") or start,
                "reason": reason,
            },
        )

    def _backfill_fees(self, env: str, opaque_account: str, current_rows) -> tuple[int, bool]:
        """Resolve order-level fees and allocate them across each order's fills.

        Stored fills decide whether an order still lacks fees. Normalized broker
        rows always carry ``fees: None``, so they only restrict the candidates
        to orders in this pull; they never add an order whose stored fills
        already have fees.

        Allocation rule (deterministic): an order's fee is distributed across
        its fills proportionally to gross fill value; when all fills in the
        order have zero value, it is split equally by count.
        """
        stored_missing = set(self._db.get_order_ids_missing_fees(env=env, account_id=opaque_account))
        pulled = {str(row.get("order_id", "") or "") for row in current_rows or []}
        # Incremental pulls carry only recent deals, so fee-less orders from
        # earlier pulls are retried while their fills are inside the 90-day window.
        window_start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        recent = {
            str(fill.get("order_id", "") or "")
            for fill in self._db.get_fills(
                env=env,
                account_id=opaque_account,
                start=window_start,
                limit=5000,
            )
            if fill.get("fees") is None
        }
        missing = sorted((pulled | recent) & stored_missing)
        if not missing:
            return 0, True
        complete = True
        if len(missing) > _FEE_BACKFILL_BUDGET:
            logger.warning(
                "Fee backfill budget exceeded for this refresh; resolving the first %d orders", _FEE_BACKFILL_BUDGET
            )
            missing = missing[:_FEE_BACKFILL_BUDGET]
            complete = False
        updated = 0
        for offset in range(0, len(missing), _FEE_QUERY_BATCH_SIZE):
            order_ids = missing[offset : offset + _FEE_QUERY_BATCH_SIZE]
            fee_rows = self._connection.get_order_fees(order_ids)
            if fee_rows is None:
                logger.warning("Fee backfill stopped after a broker fee-query failure")
                return updated, False
            fee_by_order = {str(row.get("order_id", "")): float(row.get("fee_amount", 0) or 0) for row in fee_rows}
            for order_id in order_ids:
                if order_id not in fee_by_order:
                    logger.warning("Fee backfill incomplete: broker omitted one or more order fees")
                    complete = False
                    continue
                fills = self._db.get_fills(env=env, account_id=opaque_account, order_id=order_id)
                if fills:
                    allocation = self._allocate_order_fee(fee_by_order[order_id], fills)
                    updated += self._db.update_fill_fees(allocation)
        return updated, complete

    @staticmethod
    def _allocate_order_fee(fee_total: float, fills: list) -> dict:
        """Allocate one order's fee across its fills by gross value share.

        Proportional to |price × qty × 100| so premium-bearing legs carry the
        fee burden; when every fill is zero-valued the fee splits equally.
        """
        if not fills:
            return {}
        weights = []
        for fill in fills:
            weights.append(abs(float(fill.get("price", 0) or 0) * float(fill.get("qty", 0) or 0)))
        total_weight = sum(weights)
        if total_weight <= 0:
            share = float(fee_total) / len(fills)
            return {fill["fill_id"]: share for fill in fills}
        return {fill["fill_id"]: float(fee_total) * weight / total_weight for fill, weight in zip(fills, weights)}

    def ingest_cash_flows(self, clearing_dates) -> dict:
        """Ingest cash movements for explicit clearing dates (securities
        accounts are keyed by one clearing_date per query)."""
        try:
            env, opaque_account = self._identity()
        except Exception as exc:
            return {"ok": False, "error": f"Account resolution failed: {exc}", "ingested": 0}

        rows = []
        for clearing_date in clearing_dates or []:
            flow_rows = self._connection.get_cash_flow(clearing_date)
            if flow_rows is None:
                logger.warning(
                    "Cash-flow query failed for clearing date %s: %s",
                    clearing_date,
                    getattr(self._connection, "last_error", ""),
                )
                continue
            for row in flow_rows:
                if not isinstance(row, dict):
                    continue
                rows.append(
                    {
                        "cashflow_id": row.get("cashflow_id", ""),
                        "clearing_date": row.get("clearing_date", ""),
                        "settlement_date": row.get("settlement_date", ""),
                        "currency": row.get("currency", ""),
                        "direction": row.get("cashflow_direction", ""),
                        "flow_type": row.get("cashflow_type", ""),
                        "amount": row.get("cashflow_amount", 0),
                        "remark": row.get("cashflow_remark", ""),
                        "env": env,
                        "account_id": opaque_account,
                        "raw_json": row,
                    }
                )
        inserted = self._db.save_cash_flows(rows)
        return {"ok": True, "seen": len(rows), "ingested": inserted}
