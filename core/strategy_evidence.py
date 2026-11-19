"""Strategy evidence for the ranking in use (pure summary + local report loader).

The desk always shows what each pick optimises, and whether that rule has been
shown to make money. This module answers the second question from the offline
walk-forward replay (``tools/replay_signals.py``, summarised by
``tools/promotion_check.py``).

Truth rules:

* No report, an unreadable report, or an incomplete report yields ``status`` of
  ``no_evidence`` with named reasons. It never emits a track record.
* Figures are labelled simulated, after-cost, and dated. They are history, not a
  current edge, and this module NEVER changes a rank, a gate or a decision.
* Ranking stays the owner-approved ``capital_velocity_per_day``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

RANKING = "capital_velocity_per_day"
RANKING_OBJECTIVE = "executable return on deployed capital per day"
DEFAULT_REPORT_PATH = Path("_tmp") / "replay" / "report.json"


def _base_cell(report: dict) -> dict | None:
    method = report.get("method") or {}
    walk = report.get("walk_forward") or {}
    cells = walk.get("oos") or {}
    key = f"s{method.get('base_slippage')}_r{method.get('base_rate')}"
    cell = cells.get(key)
    return cell if isinstance(cell, dict) else None


def _age_days(generated_at, now: datetime) -> float | None:
    text = str(generated_at or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return round((now - stamp.astimezone(timezone.utc)).total_seconds() / 86_400.0, 1)


def summarize_evidence(report: dict | None, *, source: str | None = None, now: datetime | None = None) -> dict:
    """Summarise a replay report for display; never invent a result."""
    now = now or datetime.now(timezone.utc)
    base = {
        "ranking": RANKING,
        "objective": RANKING_OBJECTIVE,
        "status": "no_evidence",
        "simulated": True,
        "after_cost": True,
        "verdict": None,
        "reasons": [],
        "sample": None,
        "outcome": None,
        "limitations": [],
        "source": source,
        "generated_at": None,
        "age_days": None,
        "note": (
            "Simulated historical evidence from a replay of persisted signals. It is offline "
            "analysis only: it does not change the ranking, any gate, or a published decision."
        ),
    }
    if not isinstance(report, dict) or not report:
        base["reasons"] = ["no replay report has been generated"]
        return base

    method = report.get("method") or {}
    walk = report.get("walk_forward") or {}
    verdict = report.get("verdict") or {}
    cell = _base_cell(report)
    trade_counts = report.get("trade_counts") or {}
    unresolved = report.get("unresolved") or {}
    wf_debug = report.get("wf_policies_debug") or {}
    ticker_lists = [tickers for tickers in (wf_debug.get("tickers") or {}).values() if isinstance(tickers, list)]
    distinct_underlyings = len({str(ticker) for tickers in ticker_lists for ticker in tickers})
    base.update(
        status="available",
        verdict=verdict.get("verdict"),
        reasons=list(verdict.get("reasons") or []),
        generated_at=report.get("generated_at"),
        age_days=_age_days(report.get("generated_at"), now),
        limitations=list(report.get("limitations") or []),
        sample={
            "selected_contracts": sum(int(count or 0) for count in trade_counts.values()) if trade_counts else None,
            "unresolved_selections": (
                sum(int(count or 0) for reasons in unresolved.values() for count in (reasons or {}).values())
                if unresolved
                else None
            ),
            "distinct_underlyings": distinct_underlyings or None,
            "oos_cohorts": walk.get("oos_cohorts"),
            "min_oos_cohorts": method.get("min_oos_cohorts"),
            "base_slippage_per_share": method.get("base_slippage"),
            "base_cash_rate": method.get("base_rate"),
        },
    )
    if cell is None:
        base["reasons"].append("the report has no base-case out-of-sample cell")
        return base
    base["outcome"] = {
        # Excess is measured over the cash rate, on the same trades and dates as
        # the eligible pool, the whole pool, and SPY.
        "excess_per_capital_day": cell.get("pooled_excess_per_capday"),
        "ci_90": cell.get("ci_90"),
        "cohorts": cell.get("n_cohorts"),
        "trades": cell.get("n_trades"),
        "win_rate": cell.get("win_rate"),
        "assignment_rate": cell.get("assignment_rate"),
        "max_drawdown": cell.get("max_drawdown"),
        "worst_return_on_capital": cell.get("worst_return_on_capital"),
        "vs_pool": cell.get("diff_vs_pool"),
        "vs_pool_all": cell.get("diff_vs_pool_all"),
        "vs_spy": cell.get("diff_vs_spy"),
    }
    base["baseline_note"] = (
        "Excess per capital-day is measured over the base cash rate on the same trades and dates, "
        "with the eligible pool, the whole pool and SPY as baselines."
    )
    return base


def load_strategy_evidence(report_path: str | Path | None = None) -> dict:
    """Load the replay report from local disk and summarise it (read-only)."""
    path = Path(report_path) if report_path else DEFAULT_REPORT_PATH
    try:
        if not path.exists():
            return summarize_evidence(None, source=str(path))
        raw = path.read_text(encoding="utf-8")
        report = json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        summary = summarize_evidence(None, source=str(path))
        summary["reasons"] = [f"replay report could not be read: {type(exc).__name__}"]
        return summary
    return summarize_evidence(report, source=str(path))
