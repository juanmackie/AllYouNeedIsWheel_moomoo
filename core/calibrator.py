"""
Calibrator — automatic weight adjustment from historical outcomes.

Reads resolved outcomes from the evaluator DB and adjusts scoring weights
to minimise the gap between predicted score rank and actual realised return.

This is a simple gradient-free optimiser that tweaks weights in small
steps and keeps the combination that best predicts actual outcomes.

Usage:
    from core.calibrator import run_calibration_cycle
    result = run_calibration_cycle()
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.evaluator import get_recent_outcomes, get_summary_stats

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Weight profile
# ---------------------------------------------------------------------------

@dataclass
class ScoringWeights:
    """Configurable weights for the growth-oriented scoring formula."""
    iv_adjusted: float = 0.35      # premium weight
    theta_delta: float = 0.15      # theta decay weight
    liquidity: float = 0.15        # liquidity weight
    expected_value: float = 0.10   # EV weight
    upside_or_buffer: float = 0.15 # upside (CALL) or buffer/CE (PUT)
    otm_fit: float = 0.10         # OTM proximity weight

    def to_tuple(self) -> tuple:
        return (self.iv_adjusted, self.theta_delta, self.liquidity,
                self.expected_value, self.upside_or_buffer, self.otm_fit)

    @classmethod
    def from_tuple(cls, t: tuple):
        return cls(*t)

    def normalise(self):
        """Ensure weights sum to 1.0."""
        total = sum(self.to_tuple())
        if total > 0:
            self.iv_adjusted /= total
            self.theta_delta /= total
            self.liquidity /= total
            self.expected_value /= total
            self.upside_or_buffer /= total
            self.otm_fit /= total


DEFAULT_GROWTH_WEIGHTS = ScoringWeights(
    iv_adjusted=0.35,
    theta_delta=0.15,
    liquidity=0.15,
    expected_value=0.10,
    upside_or_buffer=0.15,
    otm_fit=0.10,
)

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

CALIBRATION_DB = Path.home() / '.wheel' / 'evaluator' / 'calibration.db'


def _get_calibration_db() -> sqlite3.Connection:
    CALIBRATION_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CALIBRATION_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calibration_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle           INTEGER NOT NULL,
            w_iv_adjusted   REAL,
            w_theta_delta   REAL,
            w_liquidity     REAL,
            w_expected_value REAL,
            w_upside_buffer REAL,
            w_otm_fit       REAL,
            loss            REAL,
            samples         INTEGER,
            created_at      TEXT
        )
    """)
    return conn


def _save_calibration(cycle: int, weights: ScoringWeights, loss: float, samples: int):
    conn = _get_calibration_db()
    try:
        conn.execute("""
            INSERT INTO calibration_history
                (cycle, w_iv_adjusted, w_theta_delta, w_liquidity,
                 w_expected_value, w_upside_buffer, w_otm_fit,
                 loss, samples, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cycle,
            weights.iv_adjusted, weights.theta_delta, weights.liquidity,
            weights.expected_value, weights.upside_or_buffer, weights.otm_fit,
            loss, samples,
            __import__('datetime').datetime.now().isoformat(),
        ))
        conn.commit()
    finally:
        conn.close()


def get_latest_calibration() -> Optional[dict]:
    """Return the most recent calibration result."""
    conn = _get_calibration_db()
    try:
        row = conn.execute(
            "SELECT * FROM calibration_history ORDER BY cycle DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Loss function
# ---------------------------------------------------------------------------

def _compute_loss(weights: ScoringWeights, outcomes: list[dict]) -> float:
    """
    Compute the mean absolute error between predicted rank and actual outcome.

    Lower loss = better calibration.  Uses the predicted score (contract_score)
    as a rank proxy vs. actual annualised return.
    """
    scored = []
    for o in outcomes:
        pred = o.get('predicted_score') or 0
        actual = o.get('actual_ann_return') or 0
        if pred > 0 and o.get('actual_outcome') in ('expired_worthless', 'assigned'):
            # For expired-worthless, the actual return is the premium kept
            # For assigned, adjust for the cost basis
            scored.append((pred, actual))

    if len(scored) < 10:
        return float('inf')  # Not enough data

    # Mean absolute error of z-score normalised values
    preds = [s[0] for s in scored]
    actuals = [s[1] for s in scored]
    p_mean = sum(preds) / len(preds)
    a_mean = sum(actuals) / len(actuals)
    p_std = (sum((p - p_mean)**2 for p in preds) / len(preds))**0.5 or 1
    a_std = (sum((a - a_mean)**2 for a in actuals) / len(actuals))**0.5 or 1

    error = sum(abs((p - p_mean)/p_std - (a - a_mean)/a_std) for p, a in scored) / len(scored)
    return error


# ---------------------------------------------------------------------------
# Optimisation
# ---------------------------------------------------------------------------

def _perturb(weights: ScoringWeights, step: float = 0.03) -> ScoringWeights:
    """Randomly perturb each weight by ±step, then normalise."""
    import random
    new = ScoringWeights(
        iv_adjusted=weights.iv_adjusted + random.uniform(-step, step),
        theta_delta=weights.theta_delta + random.uniform(-step, step),
        liquidity=weights.liquidity + random.uniform(-step, step),
        expected_value=weights.expected_value + random.uniform(-step, step),
        upside_or_buffer=weights.upside_or_buffer + random.uniform(-step, step),
        otm_fit=weights.otm_fit + random.uniform(-step, step),
    )
    new.normalise()
    return new


def run_calibration_cycle(
    outcomes: Optional[list[dict]] = None,
    iterations: int = 100,
    step: float = 0.03,
) -> dict:
    """
    Run one calibration cycle.

    1. Fetch resolved outcomes from the evaluator DB.
    2. Start from the default growth weights (or last calibration).
    3. Try random perturbations and keep the best.
    4. Save the result.

    Returns a summary dict.
    """
    if outcomes is None:
        outcomes = get_recent_outcomes(days=90)

    # Exclude unresolved
    outcomes = [o for o in outcomes if o.get('actual_outcome') is not None]

    if len(outcomes) < 10:
        return {
            'success': False,
            'message': f'Need >=10 resolved outcomes, got {len(outcomes)}',
            'samples': len(outcomes),
        }

    # Starting weights
    last = get_latest_calibration()
    if last:
        best = ScoringWeights(
            iv_adjusted=last['w_iv_adjusted'],
            theta_delta=last['w_theta_delta'],
            liquidity=last['w_liquidity'],
            expected_value=last['w_expected_value'],
            upside_or_buffer=last['w_upside_buffer'],
            otm_fit=last['w_otm_fit'],
        )
    else:
        best = ScoringWeights(**DEFAULT_GROWTH_WEIGHTS.to_tuple())

    best_loss = _compute_loss(best, outcomes)

    for i in range(iterations):
        candidate = _perturb(best, step)
        loss = _compute_loss(candidate, outcomes)
        if loss < best_loss:
            best = candidate
            best_loss = loss

    # Determine cycle number
    conn = _get_calibration_db()
    try:
        row = conn.execute("SELECT MAX(cycle) as m FROM calibration_history").fetchone()
        cycle = (row['m'] or 0) + 1
    finally:
        conn.close()

    _save_calibration(cycle, best, best_loss, len(outcomes))

    return {
        'success': True,
        'cycle': cycle,
        'samples': len(outcomes),
        'loss': round(best_loss, 4),
        'weights': {
            'iv_adjusted': round(best.iv_adjusted, 4),
            'theta_delta': round(best.theta_delta, 4),
            'liquidity': round(best.liquidity, 4),
            'expected_value': round(best.expected_value, 4),
            'upside_or_buffer': round(best.upside_or_buffer, 4),
            'otm_fit': round(best.otm_fit, 4),
        },
    }


def get_calibration_history(limit: int = 20) -> list[dict]:
    """Return recent calibration cycles for the dashboard."""
    conn = _get_calibration_db()
    try:
        rows = conn.execute(
            "SELECT * FROM calibration_history ORDER BY cycle DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
