"""
Feedback Loop — continuously penalises factors that over- or under-predict.

When a recommendation resolves, the feedback loop compares each factor's
contribution to the predicted score against the actual outcome.  Factors
that consistently over-predict (score high, actual low) get their weight
reduced.  Factors that consistently under-predict get their weight increased.

This is a lightweight, online (single-pass) learner — no batch optimisation.

Architecture:
    outcome_resolved()  →  _update_factor_bias()  →  factor bias table
    get_adjusted_weights()  →  returns factor-level multipliers

The bias multipliers are applied as a post-hoc correction to the base scoring
weights in `wheel_decision.py`.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

FEEDBACK_DB = Path.home() / '.wheel' / 'evaluator' / 'feedback.db'

# Factor names that appear in score_details and can have bias.
FACTOR_KEYS = [
    'annualized',
    'liquidity',
    'delta_fit',
    'otm_fit',
    'iv_adjusted',
    'theta_delta',
    'expected_value',
    'iv_environment',
    # PUT-specific
    'buffer',
    'capital_fit',
    'capital_efficiency',
    # CALL-specific
    'upside',
    'cost_basis_fit',
]


@dataclass
class FactorBias:
    """Running bias estimate for a single scoring factor."""
    factor: str = ''
    mean_error: float = 0.0       # avg(predicted_contribution - actual_return)
    sample_count: int = 0
    bias_multiplier: float = 1.0  # <1.0 = over-predicts, >1.0 = under-predicts
    last_updated: str = ''


def _get_db() -> sqlite3.Connection:
    FEEDBACK_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(FEEDBACK_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS factor_bias (
            factor          TEXT PRIMARY KEY,
            mean_error      REAL DEFAULT 0.0,
            sample_count    INTEGER DEFAULT 0,
            bias_multiplier REAL DEFAULT 1.0,
            last_updated    TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT,
            factor          TEXT,
            predicted_contrib REAL,
            actual_return   REAL,
            error           REAL,
            created_at      TEXT
        )
    """)
    return conn


# ---------------------------------------------------------------------------
# Core update
# ---------------------------------------------------------------------------

def _compute_factor_contribution(
    factor: str,
    score_details: dict,
    total_score: float,
) -> float:
    """
    Estimate how much a single factor contributed to the total score.
    Returns a normalised contribution (0-1).
    """
    if not score_details or total_score <= 0:
        return 0.0
    val = float(score_details.get(factor, 0) or 0)
    return val / total_score if total_score > 0 else 0.0


def record_outcome_feedback(
    ticker: str,
    score_details: dict,
    total_score: float,
    actual_return: float,
    option_type: str = 'PUT',
) -> None:
    """
    Called after a recommendation resolves.
    Updates factor biases based on how each factor's contribution
    correlated with the actual return.
    """
    if not score_details or total_score <= 0:
        return

    conn = _get_db()
    now = datetime.now().isoformat()

    try:
        relevant_factors = FACTOR_KEYS[:]
        if option_type == 'CALL':
            # Exclude PUT-specific factors
            irrelevant = {'buffer', 'capital_fit', 'capital_efficiency'}
            relevant_factors = [f for f in relevant_factors if f not in irrelevant]
        elif option_type == 'PUT':
            irrelevant = {'upside', 'cost_basis_fit'}
            relevant_factors = [f for f in relevant_factors if f not in irrelevant]

        for factor in relevant_factors:
            contrib = _compute_factor_contribution(factor, score_details, total_score)
            if contrib <= 0.01:
                continue

            # Error: positive = factor over-predicted (score high, return low)
            # Negative = factor under-predicted (score low, return high)
            error = contrib - (actual_return / 100.0) if actual_return > 0 else contrib

            # Fetch current bias
            row = conn.execute(
                "SELECT * FROM factor_bias WHERE factor=?", (factor,)
            ).fetchone()

            if row:
                old_count = row['sample_count']
                old_mean = row['mean_error']
                new_count = old_count + 1
                # Online mean update
                new_mean = old_mean + (error - old_mean) / new_count
                # Exponential smoothing for bias multiplier
                alpha = 0.1  # sensitivity — higher = faster to react
                old_mult = row['bias_multiplier']
                # If mean_error > 0 (over-predicts), reduce multiplier
                # If mean_error < 0 (under-predicts), increase multiplier
                correction = 1.0 - (new_mean * 2.0)  # scale factor
                new_mult = old_mult * (1 - alpha) + correction * alpha
                new_mult = max(0.5, min(2.0, new_mult))  # clamp to [0.5, 2.0]

                conn.execute("""
                    UPDATE factor_bias SET
                        mean_error=?, sample_count=?, bias_multiplier=?,
                        last_updated=?
                    WHERE factor=?
                """, (new_mean, new_count, new_mult, now, factor))
            else:
                conn.execute("""
                    INSERT INTO factor_bias (factor, mean_error, sample_count,
                                             bias_multiplier, last_updated)
                    VALUES (?, ?, 1, ?, ?)
                """, (factor, error, 1.0 - error * 2.0, now))

            # Log event
            conn.execute("""
                INSERT INTO feedback_events
                    (ticker, factor, predicted_contrib, actual_return, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (ticker, factor, round(contrib, 4), round(actual_return, 2),
                  round(error, 4), now))

        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_all_biases() -> list[dict]:
    """Return current bias multipliers for all factors."""
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM factor_bias ORDER BY factor").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_adjusted_weights(base_weights: dict) -> dict:
    """
    Apply bias multipliers to base scoring weights.
    Returns a dict of {factor_name: adjusted_weight}.
    """
    biases = {r['factor']: r['bias_multiplier'] for r in get_all_biases()}
    adjusted = {}
    for factor, base_weight in base_weights.items():
        mult = biases.get(factor, 1.0)
        adjusted[factor] = round(base_weight * mult, 4)
    return adjusted


def get_recent_events(limit: int = 50) -> list[dict]:
    """Return recent feedback events for dashboard display."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM feedback_events ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def reset_feedback(factor: Optional[str] = None) -> None:
    """Reset bias for one factor (or all if None)."""
    conn = _get_db()
    try:
        if factor:
            conn.execute("DELETE FROM factor_bias WHERE factor=?", (factor,))
        else:
            conn.execute("DELETE FROM factor_bias")
        conn.commit()
    finally:
        conn.close()


def get_feedback_summary() -> dict:
    """Return a summary of current feedback state."""
    biases = get_all_biases()
    over = [b for b in biases if b['bias_multiplier'] < 0.95]
    under = [b for b in biases if b['bias_multiplier'] > 1.05]
    return {
        'total_factors': len(biases),
        'over_predicting': [{'factor': b['factor'], 'mult': round(b['bias_multiplier'], 3)} for b in over],
        'under_predicting': [{'factor': b['factor'], 'mult': round(b['bias_multiplier'], 3)} for b in under],
        'sample_count': sum(b['sample_count'] for b in biases),
    }
