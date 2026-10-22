"""Account P&L service: local SQLite reads for the broker-truth P&L model.

Reads persisted fills, cash flows, NAV history, and the latest portfolio
snapshot for one (env, account) identity, then delegates the math to the pure
``core.account_pnl.build_account_pnl``. It never queries OpenD. Optional
readers that a database object does not provide are treated as empty.
"""

from datetime import datetime

from core.account_pnl import build_account_pnl

_ROW_LIMIT = 100000


def _read(database, reader_name: str, default, **kwargs):
    """Call an optional repository reader; return ``default`` when absent or None."""
    reader = getattr(database, reader_name, None)
    if reader is None:
        return default
    result = reader(**kwargs)
    return default if result is None else result


def build_account_pnl_payload(database, env, account_id) -> dict:
    """Return the account P&L read model plus coverage and generation time."""
    fills = _read(database, "get_fills", [], env=env, account_id=account_id, limit=_ROW_LIMIT)
    snapshot = _read(database, "get_latest_portfolio_snapshot", None, env=env, account_id=account_id)
    cash_flows = _read(database, "get_cash_flows", [], env=env, account_id=account_id, limit=_ROW_LIMIT)
    nav_history = _read(database, "get_portfolio_history", [], env=env, account_id=account_id, unbounded=True)
    cash_flow_coverage = _read(database, "get_cash_flow_coverage", {}, env=env, account_id=account_id)
    fill_coverage = _read(database, "get_history_fill_coverage", {}, env=env, account_id=account_id)

    snapshot = snapshot or {}
    payload = build_account_pnl(
        fills,
        snapshot.get("positions") or [],
        cash_flows=cash_flows,
        nav_history=nav_history,
        positions_as_of=snapshot.get("captured_at"),
        cash_flow_coverage=cash_flow_coverage,
    )
    payload["fill_coverage"] = fill_coverage
    payload["cash_flow_coverage"] = cash_flow_coverage
    payload["generated_at"] = datetime.now().isoformat()
    return payload
