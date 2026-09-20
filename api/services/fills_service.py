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
from datetime import datetime, timedelta

from core.connection_constants import _infer_security_type_from_code, _parse_option_code_metadata
from core.ticker_utils import parse_moomoo_symbol
from core.utils import parse_position_qty

logger = logging.getLogger("api.services.fills")

_DEAL_OK_STATUS = "OK"


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
            logger.warning("Deal %s has undecodable option code %r; skipping", deal_id, code)
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


class FillsService:
    """Incremental, account-scoped ingestion of broker fills/fees/cash flows."""

    def __init__(self, connection, database):
        self._connection = connection
        self._db = database

    def _identity(self):
        return self._connection.resolve_portfolio_identity()

    def ingest_history_fills(self, days: int = 90) -> dict:
        """Ingest recent fills incrementally; idempotent per broker deal id.

        The 90-day cap is the SDK's history window; repeated ingestion is
        cheap because existing fill_ids are ignored on insert.
        """
        try:
            env, opaque_account = self._identity()
        except Exception as exc:
            return {"ok": False, "error": f"Account resolution failed: {exc}", "ingested": 0}

        start = (datetime.now() - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")
        ret_rows = self._connection.get_history_deals(start=start, end="")
        if ret_rows is None:
            error = getattr(self._connection, "last_error", "") or "history deal query failed"
            logger.error("Fill ingestion aborted: %s", error)
            return {"ok": False, "error": error, "ingested": 0}

        rows = []
        for raw in ret_rows:
            normalized = normalize_deal_row(raw, env, opaque_account)
            if normalized is not None:
                rows.append(normalized)
        inserted = self._db.save_fills(rows)
        fees_updated = self._backfill_fees(env, opaque_account)
        logger.info(
            "Fill ingestion complete: %d deal row(s) seen, %d new, env=%s account=%s",
            len(ret_rows),
            inserted,
            env,
            opaque_account,
        )
        return {"ok": True, "seen": len(ret_rows), "ingested": inserted, "fees_updated": fees_updated}

    def _backfill_fees(self, env: str, opaque_account: str) -> int:
        """Resolve order-level fees and allocate them across each order's fills.

        Allocation rule (deterministic): an order's fee is distributed across
        its fills proportionally to gross fill value; when all fills in the
        order have zero value, it is split equally by count.
        """
        missing = self._db.get_order_ids_missing_fees(env=env, account_id=opaque_account)
        if not missing:
            return 0
        fee_rows = self._connection.get_order_fees(missing)
        if fee_rows is None:
            logger.warning("Fee backfill skipped: %s", getattr(self._connection, "last_error", ""))
            return 0
        fee_by_order = {str(row.get("order_id", "")): float(row.get("fee_amount", 0) or 0) for row in fee_rows}
        if not fee_by_order:
            return 0

        updated = 0
        for order_id, fee_total in fee_by_order.items():
            fills = self._db.get_fills(env=env, account_id=opaque_account, order_id=order_id)
            if not fills:
                continue
            allocation = self._allocate_order_fee(fee_total, fills)
            updated += self._db.update_fill_fees(allocation)
        return updated

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
