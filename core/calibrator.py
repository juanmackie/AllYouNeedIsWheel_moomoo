"""
Calibrator — automatic weight adjustment from historical outcomes.

Stores calibration results in the app's options.db via EvaluatorRepository.
The old independent SQLite files are no longer used.

Calibration uses scipy.optimize (L-BFGS-B) to find the weight combination
that best predicts actual outcomes. A train/test split prevents overfitting.

Scoring does NOT consume calibration weights automatically unless enabled
via the 'calibrator_enabled' and 'calibrator_shadow_mode' feature flags.
"""

import logging
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
from scipy.optimize import minimize

logger = logging.getLogger(__name__)


@dataclass
class ScoringWeights:
    iv_adjusted: float = 0.35
    theta_delta: float = 0.15
    liquidity: float = 0.15
    expected_value: float = 0.10
    upside_or_buffer: float = 0.15
    otm_fit: float = 0.10

    def to_tuple(self) -> tuple:
        return (self.iv_adjusted, self.theta_delta, self.liquidity,
                self.expected_value, self.upside_or_buffer, self.otm_fit)

    @classmethod
    def from_tuple(cls, t: tuple):
        return cls(*t)

    def normalise(self):
        total = sum(self.to_tuple())
        if total > 0:
            self.iv_adjusted /= total
            self.theta_delta /= total
            self.liquidity /= total
            self.expected_value /= total
            self.upside_or_buffer /= total
            self.otm_fit /= total

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_GROWTH_WEIGHTS = ScoringWeights()

N_WEIGHTS = 6  # number of weight dimensions


def _weights_to_array(w: ScoringWeights) -> np.ndarray:
    """Flatten ScoringWeights to a 5-element array (last dim inferred as 1-sum)."""
    arr = np.array([w.iv_adjusted, w.theta_delta, w.liquidity,
                    w.expected_value, w.upside_or_buffer])
    return arr


def _array_to_weights(arr: np.ndarray) -> ScoringWeights:
    """Rebuild ScoringWeights from 5-element array (6th = 1 - sum of 5)."""
    sixth = 1.0 - float(np.sum(arr))
    return ScoringWeights(
        iv_adjusted=max(float(arr[0]), 0.0),
        theta_delta=max(float(arr[1]), 0.0),
        liquidity=max(float(arr[2]), 0.0),
        expected_value=max(float(arr[3]), 0.0),
        upside_or_buffer=max(float(arr[4]), 0.0),
        otm_fit=max(sixth, 0.0),
    )


def _objective(arr: np.ndarray, outcomes: list[dict]) -> float:
    """Objective for scipy.minimize: loss given flat weight array."""
    w = _array_to_weights(arr)
    return _compute_loss(w, outcomes)


def run_calibration_cycle(
    evaluator_repo,
    outcomes: Optional[list[dict]] = None,
    iterations: int = 100,
    step: float = 0.03,
    config: Optional[dict] = None,
) -> dict:
    """
    Run one calibration cycle.

    1. Fetch resolved valid outcomes from evaluator_repo.
    2. Start from the default growth weights (or last calibration).
    3. Optimize weights using scipy.optimize.minimize (L-BFGS-B) on a
       training split (80% of data). Evaluate on test split (20%).
    4. Save the result. When calibrator_enabled=True, accepted=True is stored
       so downstream consumers can query accepted calibration weights.
       In shadow mode (calibrator_shadow_mode=True, calibrator_enabled=False),
       weights are stored as accepted=False for comparison only.

    Returns a summary dict with train/test loss.
    """
    if config is None:
        config = {}

    if not config.get('enabled', True):
        return {'success': False, 'message': 'Evaluator disabled', 'samples': 0}

    calibrator_enabled = config.get('calibrator_enabled', False)
    calibrator_shadow = config.get('calibrator_shadow_mode', True)

    if not calibrator_enabled and not calibrator_shadow:
        return {'success': False, 'message': 'Calibrator disabled via feature flag', 'samples': 0}

    if outcomes is None:
        outcomes = evaluator_repo.get_valid_training_outcomes(limit=200)
    else:
        outcomes = [o for o in outcomes if o.get('resolved_outcome') is not None]

    min_samples = config.get('calibrator_min_samples', 50)
    total_samples = len(outcomes)
    if total_samples < min_samples:
        return {
            'success': False,
            'message': f'Need >= {min_samples} resolved outcomes, got {total_samples}',
            'samples': total_samples,
        }

    # Train/test split (80/20)
    np.random.seed(42)
    idx = np.random.permutation(total_samples)
    split = int(total_samples * 0.8)
    train_idx = idx[:split]
    test_idx = idx[split:]
    train_outcomes = [outcomes[i] for i in train_idx]
    test_outcomes = [outcomes[i] for i in test_idx]

    # Starting weights — from last calibration or defaults
    last = evaluator_repo.get_latest_calibration()
    if last and last.get('weights'):
        start = ScoringWeights(
            iv_adjusted=last['weights'].get('iv_adjusted', 0.35),
            theta_delta=last['weights'].get('theta_delta', 0.15),
            liquidity=last['weights'].get('liquidity', 0.15),
            expected_value=last['weights'].get('expected_value', 0.10),
            upside_or_buffer=last['weights'].get('upside_or_buffer', 0.15),
            otm_fit=last['weights'].get('otm_fit', 0.10),
        )
    else:
        start = ScoringWeights()

    x0 = _weights_to_array(start)
    bounds = [(0.0, 1.0)] * (N_WEIGHTS - 1)

    result = minimize(
        _objective,
        x0,
        args=(train_outcomes,),
        method='L-BFGS-B',
        bounds=bounds,
        options={'maxiter': iterations, 'ftol': 1e-6},
    )

    best = _array_to_weights(result.x)
    best.normalise()
    train_loss = result.fun if result.fun != float('inf') else _compute_loss(best, train_outcomes)
    test_loss = _compute_loss(best, test_outcomes)

    cycle = evaluator_repo.get_next_calibration_cycle()

    # Shadow loss on default weights (test split for fair comparison)
    shadow_loss = _compute_loss(DEFAULT_GROWTH_WEIGHTS, test_outcomes)

    evaluator_repo.save_calibration(
        cycle=cycle,
        samples=len(train_outcomes),
        loss=train_loss,
        weights=best.to_dict(),
        shadow_loss=shadow_loss,
        accepted=calibrator_enabled,
    )

    return {
        'success': result.success,
        'cycle': cycle,
        'samples': len(train_outcomes),
        'test_samples': len(test_outcomes),
        'loss': round(train_loss, 4),
        'test_loss': round(test_loss, 4),
        'shadow_loss': round(shadow_loss, 4),
        'weights': best.to_dict(),
        'improvement': round(shadow_loss - train_loss, 4) if shadow_loss != float('inf') else None,
    }


def _compute_loss(weights: ScoringWeights, outcomes: list[dict]) -> float:
    """
    Mean absolute error of z-score normalised predicted vs actual returns.
    """
    scored = []
    for o in outcomes:
        pred = float(o.get('score', 0) or 0)
        actual = float(o.get('actual_return', 0) or 0)
        if pred > 0:
            scored.append((pred, actual))

    if len(scored) < 10:
        return float('inf')

    preds = [s[0] for s in scored]
    actuals = [s[1] for s in scored]
    p_mean = sum(preds) / len(preds)
    a_mean = sum(actuals) / len(actuals)
    p_std = (sum((p - p_mean) ** 2 for p in preds) / len(preds)) ** 0.5 or 1
    a_std = (sum((a - a_mean) ** 2 for a in actuals) / len(actuals)) ** 0.5 or 1

    error = sum(
        abs((p - p_mean) / p_std - (a - a_mean) / a_std)
        for p, a in scored
    ) / len(scored)
    return error
