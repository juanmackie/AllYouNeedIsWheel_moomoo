"""Broker-verified outcome ingestion service (read-only).

Fetches deal/fee/cash-flow evidence from OpenD through the query-only
connection surface and persists it account-scoped and idempotently:

- fills (options + underlying shares) keyed by the broker deal id;
- order-level fees allocated back onto fill rows;
- account cash movements (deposits/withdrawals) kept separate from trading
  profit — they never enter attribution arithmetic. Cash movements are queried
  one clearing date at a time, in a budgeted slice on each wheel refresh.

This service owns OpenD/error handling; the attribution math lives in pure
``core.outcome_attribution`` helpers and the persistence in
``db.fills_repository``. Query-only: no order/unlock/cancel/modify surface.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from core.connection_constants import infer_security_type_from_code, parse_option_code_metadata
from core.ticker_utils import parse_moomoo_symbol
from core.utils import parse_position_qty

logger = logging.getLogger("api.services.fills")

_DEAL_OK_STATUS = "OK"
DEFAULT_HISTORY_START = "2025-01-01"
_FEE_BACKFILL_BUDGET = 40  # order ids resolved per refresh call
_FEE_QUERY_BATCH_SIZE = 20  # order ids per broker fee query
_HISTORY_WINDOW_DAYS = 89  # one broker deal query covers at most 90 days inclusive
_CASH_FLOW_DAYS_PER_REFRESH = 18  # stays inside the broker's 20-per-30-seconds quota


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
    security_type = infer_security_type_from_code(code)
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
        metadata = parse_option_code_metadata(code)
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


def history_windows(start: date, end: date) -> list[tuple[str, str]]:
    """Split ``[start, end]`` into overlapping inclusive windows (pure).

    Each window spans at most 90 days inclusive, which is the broker's per-query
    limit. The next window starts on the previous window's end date, so a
    date-only end can never drop that day's deals. Repeated deals are free:
    ``fill_id`` dedupes them. The loop ends when a window reaches ``end``.
    Returns ``[]`` when ``start`` is after ``end``.
    """
    windows = []
    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=_HISTORY_WINDOW_DAYS), end)
        windows.append((cursor.isoformat(), window_end.isoformat()))
        if window_end >= end:
            break
        cursor = window_end
    return windows


def _parse_date(value) -> date | None:
    """Return the ``YYYY-MM-DD`` prefix of a stored value as a date, or None."""
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _parse_start(value) -> date:
    """Return the configured history start, falling back to the built-in default."""
    return _parse_date(value) or date.fromisoformat(DEFAULT_HISTORY_START)


def _weekdays_between(first: date, last: date, *, descending: bool) -> list[date]:
    """Return Monday-to-Friday dates in ``[first, last]`` (pure)."""
    days = []
    cursor = first
    while cursor <= last:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days[::-1] if descending else days


def _first_weekday_on_or_after(value: date) -> date:
    """Return ``value`` moved forward to the next Monday-to-Friday date (pure)."""
    while value.weekday() >= 5:
        value += timedelta(days=1)
    return value


def _cash_flow_row(row: dict, env: str, opaque_account: str) -> dict:
    """Normalize one OpenD cash-flow row into an account_cash_flows row (pure)."""
    return {
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


def refresh_fill_evidence(connection, database, history_start=None) -> dict:
    """Pull broker fills and cash flows for one wheel refresh (query-only, idempotent).

    Owner decision (2026-10-17): fills are pulled on every refresh. Cash flows
    are refreshed in a budgeted slice after the fill pull, whether or not the
    fill pull succeeded. Never raises; the top-level ``ok`` and ``reason``
    describe the fill pull only, and ``cash_flows`` reports the cash-flow slice.
    """
    if connection is None or database is None:
        return {"ok": False, "reason": "broker_connection_unavailable"}
    service = FillsService(connection, database)
    try:
        result = service.ingest_history_fills(days=None, history_start=history_start)
        evidence = {"ok": bool(result.get("ok")), "reason": result.get("reason", "")}
        for key in ("ingested", "seen", "fees_complete"):
            if key in result:
                evidence[key] = result[key]
    except Exception as exc:
        logger.warning("Fill pull failed during refresh (%s)", type(exc).__name__)
        evidence = {"ok": False, "reason": "fill_pull_failed"}
    try:
        cash_result = service.refresh_cash_flows(history_start=history_start)
        evidence["cash_flows"] = {"ok": bool(cash_result.get("ok")), "complete": bool(cash_result.get("complete"))}
    except Exception as exc:
        logger.warning("Cash-flow refresh failed during refresh (%s)", type(exc).__name__)
        evidence["cash_flows"] = {"ok": False, "reason": "cash_flow_refresh_failed"}
    return evidence


class FillsService:
    """Incremental, account-scoped ingestion of broker fills/fees/cash flows."""

    def __init__(self, connection, database):
        self._connection = connection
        self._db = database

    def _identity(self):
        return self._connection.resolve_portfolio_identity()

    def ingest_history_fills(self, days: int | None = None, history_start: str | None = None) -> dict:
        """Ingest broker fills incrementally; idempotent per broker deal id.

        OpenD answers at most 90 days per deal query, so the pull is split into
        overlapping windows (see ``history_windows``). The first pull (or a pull whose covered start is
        later than ``history_start``) backfills from ``history_start``. Later
        pulls start two days before the last success. An explicit ``days`` sets
        the lookback directly. A failed window saves nothing and keeps the
        covered start, so the next pull retries the whole backfill.
        """
        today = date.today()
        try:
            env, opaque_account = self._identity()
        except Exception as exc:
            logger.warning("Fill ingestion skipped: account identity unavailable (%s)", type(exc).__name__)
            return {"ok": False, "reason": "account_identity_unavailable", "ingested": 0}

        floor = _parse_start(history_start)
        coverage = self._db.get_history_fill_coverage(env, opaque_account)
        last_success = _parse_date(coverage.get("last_success_at"))
        covered_from = _parse_date(coverage.get("first_covered_date"))
        if days is not None:
            from_date = today - timedelta(days=max(1, int(days)))
        elif covered_from is None or covered_from > floor or last_success is None:
            from_date = floor
        else:
            from_date = max(floor, last_success - timedelta(days=2))
        from_date = min(from_date, today)
        attempt_at = datetime.now().isoformat(timespec="seconds")
        self._db.set_history_fill_coverage(
            env,
            opaque_account,
            {
                "status": "in_progress",
                "last_attempt_at": attempt_at,
                "last_success_at": coverage.get("last_success_at"),
                "first_covered_date": coverage.get("first_covered_date"),
                "reason": "",
            },
        )

        windows = history_windows(from_date, today)
        ret_rows = []
        for index, (window_start, window_end) in enumerate(windows):
            # The last window is open-ended, so OpenD includes today's deals.
            query_end = "" if index == len(windows) - 1 else window_end
            try:
                window_rows = self._connection.get_history_deals(start=window_start, end=query_end)
            except Exception as exc:
                self._record_failure(env, opaque_account, attempt_at, "history_query_failed", coverage)
                logger.error("Fill ingestion aborted: history query raised %s", type(exc).__name__)
                return {"ok": False, "reason": "history_query_failed", "ingested": 0}
            if window_rows is None:
                self._record_failure(env, opaque_account, attempt_at, "history_query_failed", coverage)
                logger.error("Fill ingestion aborted: history query failed")
                return {"ok": False, "reason": "history_query_failed", "ingested": 0}
            ret_rows.extend(window_rows)

        # Overlapping windows return the boundary day twice; count and save each deal once.
        unique_rows = {}
        for raw in ret_rows:
            deal_id = str(raw.get("deal_id", "") or "").strip() if isinstance(raw, dict) else ""
            unique_rows[deal_id or id(raw)] = raw
        ret_rows = list(unique_rows.values())

        rows = []
        for raw in ret_rows:
            normalized = normalize_deal_row(raw, env, opaque_account)
            if normalized is not None:
                rows.append(normalized)
        try:
            inserted = self._db.save_fills(rows)
            fees_updated, fees_ok = self._backfill_fees(env, opaque_account, rows)
        except Exception as exc:
            self._record_failure(env, opaque_account, attempt_at, "persistence_failed", coverage)
            logger.error("Fill ingestion failed while persisting data (%s)", type(exc).__name__)
            return {"ok": False, "reason": "persistence_failed", "ingested": 0}

        success_at = datetime.now().isoformat(timespec="seconds")
        first_covered = from_date if covered_from is None else min(covered_from, from_date)
        self._db.set_history_fill_coverage(
            env,
            opaque_account,
            {
                "status": "success",
                "last_attempt_at": attempt_at,
                "last_success_at": success_at,
                "first_covered_date": first_covered.isoformat(),
                "reason": "" if fees_ok else "fee_query_incomplete",
            },
        )
        result = {
            "ok": True,
            "fees_complete": fees_ok,
            "seen": len(ret_rows),
            "ingested": inserted,
            "fees_updated": fees_updated,
            "query_days": (today - from_date).days,
            "covered_from": from_date.isoformat(),
            "windows": len(windows),
        }
        if not fees_ok:
            result["reason"] = "fee_query_failed"
            return result
        logger.info(
            "Fill ingestion complete: %d deal row(s) seen, %d new",
            len(ret_rows),
            inserted,
        )
        return result

    def _record_failure(self, env, account_id, attempt_at, reason, coverage):
        """Record a failed pull. The covered start is kept, never lowered."""
        self._db.set_history_fill_coverage(
            env,
            account_id,
            {
                "status": "failed",
                "last_attempt_at": attempt_at,
                "last_success_at": coverage.get("last_success_at"),
                "first_covered_date": coverage.get("first_covered_date"),
                "reason": reason,
            },
        )

    def _backfill_fees(self, env: str, opaque_account: str, current_rows) -> tuple[int, bool]:
        """Resolve order-level fees and allocate them across each order's fills.

        Stored fills decide whether an order still lacks fees. Normalized broker
        rows always carry ``fees: None``, so they only restrict the candidates
        to orders in this pull; they never add an order whose stored fills
        already have fees.

        Candidates are every stored order that still lacks fees, whatever its
        age. Orders from this pull come first, so the per-refresh budget favors
        fresh fills. Allocation rule (deterministic): an order's fee is
        distributed across its fills proportionally to gross fill value; when all
        fills in the order have zero value, it is split equally by count.
        """
        stored_missing = set(self._db.get_order_ids_missing_fees(env=env, account_id=opaque_account))
        pulled = {str(row.get("order_id", "") or "") for row in current_rows or []}
        missing = sorted(pulled & stored_missing) + sorted(stored_missing - pulled)
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
        failed = 0
        for clearing_date in clearing_dates or []:
            flow_rows = self._connection.get_cash_flow(clearing_date)
            if flow_rows is None:
                failed += 1
                logger.warning(
                    "Cash-flow query failed for clearing date %s: %s",
                    clearing_date,
                    getattr(self._connection, "last_error", ""),
                )
                continue
            for row in flow_rows:
                if isinstance(row, dict):
                    rows.append(_cash_flow_row(row, env, opaque_account))
        inserted = self._db.save_cash_flows(rows)
        return {"ok": failed == 0, "seen": len(rows), "ingested": inserted, "failed_dates": failed}

    def refresh_cash_flows(
        self,
        history_start: str | None = None,
        max_days: int = _CASH_FLOW_DAYS_PER_REFRESH,
        today: date | None = None,
    ) -> dict:
        """Pull a budgeted slice of cash movements; never raises.

        Each refresh queries at most ``max_days`` weekdays, one clearing date per
        broker call. The forward pass re-pulls from two days before ``latest``
        through today. The backward pass continues from ``earliest - 1`` down to
        ``history_start``. Forward dates use the budget first. A failed forward
        pass skips the backward pass in that refresh, because the same broker
        error would repeat. Coverage records only the contiguous run of
        successful dates.
        """
        today = today or date.today()
        try:
            env, opaque_account = self._identity()
        except Exception as exc:
            logger.warning("Cash-flow refresh skipped: account identity unavailable (%s)", type(exc).__name__)
            return {"ok": False, "reason": "account_identity_unavailable", "queried": 0, "ingested": 0}

        floor = _parse_start(history_start)
        coverage = self._db.get_cash_flow_coverage(env, opaque_account)
        earliest = _parse_date(coverage.get("earliest"))
        latest = _parse_date(coverage.get("latest"))
        attempt_at = datetime.now().isoformat(timespec="seconds")

        budget = max(0, int(max_days))
        rows = []
        queried = 0
        forward_ok = []
        forward_failed = False
        if latest is not None:
            forward_dates = _weekdays_between(latest - timedelta(days=2), today, descending=False)[:budget]
            forward_ok, forward_failed, used = self._query_cash_flow_dates(forward_dates, env, opaque_account, rows)
            queried += used
        backward_ok = []
        backward_failed = False
        if not forward_failed:
            backward_start = today if earliest is None else earliest - timedelta(days=1)
            backward_dates = _weekdays_between(floor, backward_start, descending=True)[: budget - queried]
            backward_ok, backward_failed, used = self._query_cash_flow_dates(backward_dates, env, opaque_account, rows)
            queried += used

        new_latest = forward_ok[-1] if forward_ok else latest
        new_earliest = backward_ok[-1] if backward_ok else earliest
        if earliest is None and backward_ok:
            new_latest = new_latest or backward_ok[0]
        failed = forward_failed or backward_failed
        try:
            inserted = self._db.save_cash_flows(rows)
        except Exception as exc:
            logger.error("Cash-flow refresh failed while persisting data (%s)", type(exc).__name__)
            return {"ok": False, "reason": "persistence_failed", "queried": queried, "ingested": 0}
        self._db.set_cash_flow_coverage(
            env,
            opaque_account,
            {
                "status": "partial" if failed else "success",
                "last_attempt_at": attempt_at,
                "earliest": new_earliest.isoformat() if new_earliest else None,
                "latest": new_latest.isoformat() if new_latest else None,
                "reason": "cash_flow_query_failed" if failed else "",
            },
        )
        complete = new_earliest is not None and new_earliest <= _first_weekday_on_or_after(floor)
        return {
            "ok": not failed,
            "queried": queried,
            "ingested": inserted,
            "earliest": new_earliest.isoformat() if new_earliest else None,
            "latest": new_latest.isoformat() if new_latest else None,
            "complete": complete,
        }

    def _query_cash_flow_dates(self, dates, env, opaque_account, rows):
        """Query clearing dates in order and stop at the first failure.

        Appends normalized rows to ``rows``. Returns the successful dates (a
        contiguous run from the first date), whether a query failed, and the
        number of broker queries made.
        """
        ok_dates = []
        for day in dates:
            try:
                flow_rows = self._connection.get_cash_flow(day.isoformat())
            except Exception as exc:
                logger.warning("Cash-flow query raised for one clearing date (%s)", type(exc).__name__)
                flow_rows = None
            if flow_rows is None:
                return ok_dates, True, len(ok_dates) + 1
            rows.extend(_cash_flow_row(row, env, opaque_account) for row in flow_rows if isinstance(row, dict))
            ok_dates.append(day)
        return ok_dates, False, len(ok_dates)
