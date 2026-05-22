"""
Evaluator — logs predicted vs actual performance of wheel recommendations.

Can be triggered on a schedule (cron) to compare ex-ante scoring metrics
against ex-post outcomes:
  - Was the option assigned or did it expire worthless?
  - What was the actual annualized return?
  - Did the price shock exceed stress-loss estimates?
  - How accurate were the score, confidence, and risk-budget predictions?

Data is stored in a local SQLite table and can be queried by the
dashboard, fed into weight calibration (step 9), or used in the
feedback loop (step 10).
"""

import json
import sqlite3
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS recommendation_outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Identity
    ticker          TEXT NOT NULL,
    strike          REAL NOT NULL,
    expiration      TEXT NOT NULL,          -- YYYYMMDD
    option_type     TEXT NOT NULL,          -- PUT | CALL
    dte             INTEGER NOT NULL,

    -- Predicted (from WheelDecision at recommendation time)
    predicted_score         REAL,
    predicted_ann_return    REAL,
    predicted_delta         REAL,
    predicted_iv            REAL,
    predicted_premium       REAL,
    predicted_confidence    REAL,
    predicted_stress_loss   REAL,
    predicted_risk_budget   REAL,
    predicted_macro_mult    REAL,

    -- Score details for feedback loop (JSON)
    score_details           TEXT,            -- JSON dict of factor contributions

    -- Actual outcome (filled after expiration)
    actual_outcome          TEXT,            -- 'expired_worthless' | 'assigned' | 'rolled' | 'closed_early' | 'unknown'
    actual_premium_kept     REAL,            -- $ kept from the premium
    actual_days_held        INTEGER,
    actual_ann_return       REAL,
    actual_peak_adverse_move REAL,           -- max adverse move during hold
    actual_assignment_price  REAL,           -- if assigned, the effective price
    actual_notes            TEXT,

    -- Metadata
    source                  TEXT,            -- 'moomoo' | 'yfinance'
    strategy                TEXT,            -- 'csp' | 'covered_call'
    generated_at            TEXT,            -- ISO timestamp when recommended
    resolved_at             TEXT,            -- ISO timestamp when outcome recorded
    score_rationale         TEXT,

    UNIQUE(ticker, strike, expiration, option_type)
);
"""

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_db_lock = threading.Lock()
_db_path: Optional[Path] = None


def _get_db() -> sqlite3.Connection:
    global _db_path
    if _db_path is None:
        data_dir = Path.home() / '.wheel' / 'evaluator'
        data_dir.mkdir(parents=True, exist_ok=True)
        _db_path = data_dir / 'outcomes.db'
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    """Create the outcomes table if it does not exist."""
    with _db_lock:
        conn = _get_db()
        try:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
            logger.info("Evaluator DB initialised at %s", _db_path)
        finally:
            conn.close()


# Defer DB initialisation: do not create/open on import.
# Call init_db() explicitly before first use, or rely on lazy init
# in record_recommendation and other public functions.


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def record_recommendation(
    ticker: str,
    strike: float,
    expiration: str,
    option_type: str,
    dte: int,
    decision,
    source: str = 'moomoo',
    strategy: str = 'csp',
) -> bool:
    """
    Record a recommendation's predicted metrics for later outcome tracking.
    Stores score_details as JSON so the feedback loop can access factor
    contributions when the outcome is resolved.

    Returns True if inserted, False if already tracked (duplicate).
    """
    try:
        score_details_json = json.dumps(decision.score_details) if hasattr(decision, 'score_details') and decision.score_details else '{}'
        with _db_lock:
            conn = _get_db()
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO recommendation_outcomes
                        (ticker, strike, expiration, option_type, dte,
                         predicted_score, predicted_ann_return, predicted_delta,
                         predicted_iv, predicted_premium, predicted_confidence,
                         predicted_stress_loss, predicted_risk_budget,
                         predicted_macro_mult,
                         score_details,
                         source, strategy, generated_at, score_rationale)
                    VALUES (?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?,
                            ?, ?,
                            ?,
                            ?,
                            ?, ?, ?, ?)
                """, (
                    ticker, strike, expiration, option_type, dte,
                    decision.contract_score,
                    decision.annualized_return,
                    decision.delta,
                    decision.implied_volatility,
                    decision.premium_per_contract,
                    decision.confidence_score,
                    decision.stress_loss,
                    decision.risk_budget_used_pct,
                    decision.macro_multiplier,
                    score_details_json,
                    source, strategy,
                    datetime.now().isoformat(),
                    decision.score_rationale,
                ))
                conn.commit()
                return conn.total_changes > 0
            finally:
                conn.close()
    except Exception as e:
        logger.warning("Failed to record recommendation for %s: %s", ticker, e)
        return False


def resolve_outcome(
    ticker: str,
    strike: float,
    expiration: str,
    option_type: str,
    outcome: str,
    premium_kept: float = 0.0,
    days_held: Optional[int] = None,
    assignment_price: Optional[float] = None,
    notes: str = '',
) -> bool:
    """
    Record the actual outcome of a previously-recommended trade.

    Reads stored score_details and passes them to the feedback loop
    so factor contributions are updated with real outcome data.
    Called by the cron evaluator after expiration.
    """
    try:
        with _db_lock:
            conn = _get_db()
            try:
                actual_ann = 0.0
                row = conn.execute(
                    "SELECT predicted_premium, dte, predicted_ann_return, predicted_score, score_details FROM recommendation_outcomes "
                    "WHERE ticker=? AND strike=? AND expiration=? AND option_type=?",
                    (ticker, strike, expiration, option_type)
                ).fetchone()
                score_details = {}
                predicted_score = 0.0
                if row:
                    dte = row['dte'] or days_held or 0
                    if dte > 0 and row['predicted_premium'] and row['predicted_premium'] > 0:
                        actual_ann = (premium_kept / max(row['predicted_premium'], 0.01)) * (365 / dte) * 100
                    predicted_score = row['predicted_score'] or 0
                    sd_raw = row['score_details']
                    if sd_raw:
                        try:
                            score_details = json.loads(sd_raw)
                        except (json.JSONDecodeError, TypeError):
                            pass

                conn.execute("""
                    UPDATE recommendation_outcomes SET
                        actual_outcome=?,
                        actual_premium_kept=?,
                        actual_days_held=?,
                        actual_ann_return=?,
                        actual_assignment_price=?,
                        actual_notes=?,
                        resolved_at=?
                    WHERE ticker=? AND strike=? AND expiration=? AND option_type=?
                """, (
                    outcome, premium_kept, days_held, actual_ann,
                    assignment_price, notes, datetime.now().isoformat(),
                    ticker, strike, expiration, option_type,
                ))
                conn.commit()

                # Feed into feedback loop with stored score_details
                try:
                    from core.feedback_loop import record_outcome_feedback
                    record_outcome_feedback(
                        ticker=ticker,
                        score_details=score_details,
                        total_score=predicted_score,
                        actual_return=actual_ann,
                        option_type=option_type,
                    )
                except Exception:
                    pass

                return conn.total_changes > 0
            finally:
                conn.close()
    except Exception as e:
        logger.warning("Failed to resolve outcome for %s: %s", ticker, e)
        return False


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_pending_resolutions(limit: int = 50) -> list[dict]:
    """Return recommendations past expiration that have not been resolved."""
    today = datetime.now().strftime('%Y%m%d')
    with _db_lock:
        conn = _get_db()
        try:
            rows = conn.execute("""
                SELECT * FROM recommendation_outcomes
                WHERE expiration < ? AND actual_outcome IS NULL
                ORDER BY expiration ASC
                LIMIT ?
            """, (today, limit)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_recent_outcomes(days: int = 30) -> list[dict]:
    """Return recently resolved outcomes for dashboard / calibration."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with _db_lock:
        conn = _get_db()
        try:
            rows = conn.execute("""
                SELECT * FROM recommendation_outcomes
                WHERE resolved_at IS NOT NULL AND resolved_at > ?
                ORDER BY resolved_at DESC
                LIMIT 200
            """, (cutoff,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_summary_stats() -> dict:
    """Return aggregate calibration statistics."""
    with _db_lock:
        conn = _get_db()
        try:
            total = conn.execute("SELECT COUNT(*) as c FROM recommendation_outcomes").fetchone()['c']
            resolved = conn.execute(
                "SELECT COUNT(*) as c FROM recommendation_outcomes WHERE actual_outcome IS NOT NULL"
            ).fetchone()['c']
            expired = conn.execute(
                "SELECT COUNT(*) as c FROM recommendation_outcomes WHERE actual_outcome='expired_worthless'"
            ).fetchone()['c']
            assigned = conn.execute(
                "SELECT COUNT(*) as c FROM recommendation_outcomes WHERE actual_outcome='assigned'"
            ).fetchone()['c']
            return {
                'total_recommendations': total,
                'resolved': resolved,
                'expired_worthless': expired,
                'assigned': assigned,
                'assignment_rate': round(assigned / max(resolved, 1) * 100, 1),
                'expiry_rate': round(expired / max(resolved, 1) * 100, 1),
            }
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Cron integration stub
# ---------------------------------------------------------------------------

def run_evaluation_cycle() -> dict:
    """
    Called by cron / scheduler.  Finds expired-but-unresolved recommendations
    and checks their current status via Moomoo.

    Returns a summary dict with counts of resolved / skipped / errored items.
    """
    pending = get_pending_resolutions(limit=50)
    if not pending:
        return {'checked': 0, 'resolved': 0, 'skipped': 0, 'errors': 0}

    resolved_count = 0
    skipped_count = 0
    error_count = 0

    for rec in pending:
        # TODO: check actual position status via Moomoo SDK
        # For now, mark as resolved = 'unknown' with a note
        ok = resolve_outcome(
            ticker=rec['ticker'],
            strike=rec['strike'],
            expiration=rec['expiration'],
            option_type=rec['option_type'],
            outcome='unknown',
            notes='Auto-resolved by evaluator cycle (no Moomoo check implemented)',
        )
        if ok:
            resolved_count += 1
        else:
            error_count += 1

    return {
        'checked': len(pending),
        'resolved': resolved_count,
        'skipped': skipped_count,
        'errors': error_count,
    }


# Deferred initialisation — do NOT call at import time so that importing
# core.evaluator (or modules that import it, like recommendations.py) does
# not create ~/.wheel/evaluator/outcomes.db until the first actual use.
# Callers should use _ensure_init() or rely on lazy initialisation within
# each public function.

_init_done = False


def _ensure_init() -> None:
    """Lazy init: create the DB and schema on first actual use."""
    global _init_done
    if not _init_done:
        init_db()
        _init_done = True
