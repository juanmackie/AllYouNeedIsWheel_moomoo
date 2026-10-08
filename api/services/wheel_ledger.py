"""Best-effort materialization of conservative, account-scoped wheel cycles."""

from __future__ import annotations

import logging
from collections import defaultdict

from core.wheel_cycle import classify_fills, fold_cycles, gate_basis

logger = logging.getLogger("api.services.wheel_ledger")


class WheelLedgerService:
    def __init__(self, database):
        self._db = database

    def recompute(self, env, account_id, positions=None, window_start=None):
        """Fold persisted fills and save cycles without making publish depend on it.

        ``window_start`` is the earliest date for which ingestion was requested,
        not the oldest row returned. Callers must not infer complete history
        from a sparse result.
        """
        if not isinstance(positions, dict) or not positions:
            return {"ok": True, "skipped": "broker_position_snapshot_unavailable", "cycles": 0}
        try:
            fills = self._db.get_fills(env=env, account_id=account_id, limit=10000)
            normalized = [{**row, "timestamp": row.get("captured_at")} for row in fills]
            events = classify_fills(normalized)
            by_ticker = defaultdict(list)
            for event in events:
                by_ticker[event.get("ticker", "")].append(event)

            stock_positions = positions if isinstance(positions, dict) else {}
            cycles = []
            for ticker, ticker_events in by_ticker.items():
                position = stock_positions.get(ticker, {}) or {}
                try:
                    broker_shares = float(position.get("position", position.get("shares", 0)) or 0)
                except (TypeError, ValueError):
                    broker_shares = None
                folded = fold_cycles(ticker_events)
                active = [cycle for cycle in folded if cycle.get("state") in {"assigned_uncovered", "cc_open"}]
                evidence = {
                    "window_start": window_start or self._history_window_start(env, account_id),
                    "ledger_shares": active[0].get("shares") if len(active) == 1 else None,
                    "broker_shares": broker_shares,
                    "eligible_cycle_ids": [active[0]["cycle_id"]] if len(active) == 1 else [],
                    "no_unclassified_zero_price_fills": not any(
                        event.get("event_type") == "unclassified_zero_price_option_fill" for event in ticker_events
                    ),
                }
                folded = fold_cycles(ticker_events, evidence)
                cycles.extend(folded)
            self._db.save_wheel_cycles(cycles)
            return {"ok": True, "cycles": len(cycles), "events": len(events)}
        except Exception as exc:  # Cycle refresh must never fail a scan/publish.
            logger.warning("Wheel-cycle recompute failed; keeping prior materialization: %s", exc)
            return {"ok": False, "error": str(exc), "cycles": 0}

    def _history_window_start(self, env, account_id):
        return self._db.get_setting(f"fill_history_window_start:{env}:{account_id}")

    def basis_for_position(self, ticker, position, env, account_id):
        """Return the effective basis and its provenance for a held ticker."""
        rows = self._db.get_wheel_cycles(env=env, account_id=account_id, ticker=ticker, limit=50)
        active = [row for row in rows if row.get("state") in {"assigned_uncovered", "cc_open"}]
        cycle = active[0] if len(active) == 1 else None
        basis, source = gate_basis(cycle, (position or {}).get("avg_cost"))
        return {
            "gate_basis": basis,
            "gate_basis_source": source,
            "cycle_completeness": cycle.get("completeness") if cycle else "partial",
            "cycle_state": cycle.get("state") if cycle else None,
        }
