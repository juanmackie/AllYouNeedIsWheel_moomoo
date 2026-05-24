"""
Calibrator — automatic weight adjustment from historical outcomes.

Stores calibration results in the app's options.db via EvaluatorRepository.
The old independent SQLite files are no longer used.

Calibration is a simple gradient-free optimiser that tweaks weights in small
steps and keeps the combination that best predicts actual outcomes.

Scoring does NOT consume calibration weights automatically unless enabled
via the 'calibrator_enabled' and 'calibrator_shadow_mode' feature flags.
"""

import random
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

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
    3. Try random perturbations and keep the best.
    4. Save the result. When calibrator_enabled=True, accepted=True is stored
       so downstream consumers can query accepted calibration weights.
       In shadow mode (calibrator_shadow_mode=True, calibrator_enabled=False),
       weights are stored as accepted=False for comparison only.
    5. Calibration weights are NOT auto-applied to live scoring — that requires
       explicit integration in the scorer (future work).

    Returns a summary dict.
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
    if len(outcomes) < min_samples:
        return {
            'success': False,
            'message': f'Need >= {min_samples} resolved outcomes, got {len(outcomes)}',
            'samples': len(outcomes),
        }

    # Starting weights — from last calibration or defaults
    last = evaluator_repo.get_latest_calibration()
    if last and last.get('weights'):
        best = ScoringWeights(
            iv_adjusted=last['weights'].get('iv_adjusted', 0.35),
            theta_delta=last['weights'].get('theta_delta', 0.15),
            liquidity=last['weights'].get('liquidity', 0.15),
            expected_value=last['weights'].get('expected_value', 0.10),
            upside_or_buffer=last['weights'].get('upside_or_buffer', 0.15),
            otm_fit=last['weights'].get('otm_fit', 0.10),
        )
    else:
        best = ScoringWeights()

    best_loss = _compute_loss(best, outcomes)

    for i in range(iterations):
        candidate = _perturb(best, step)
        loss = _compute_loss(candidate, outcomes)
        if loss < best_loss:
            best = candidate
            best_loss = loss

    cycle = evaluator_repo.get_next_calibration_cycle()

    # Run shadow loss on current default weights for comparison
    shadow_loss = _compute_loss(DEFAULT_GROWTH_WEIGHTS, outcomes)

    evaluator_repo.save_calibration(
        cycle=cycle,
        samples=len(outcomes),
        loss=best_loss,
        weights=best.to_dict(),
        shadow_loss=shadow_loss,
        accepted=calibrator_enabled,
    )

    return {
        'success': True,
        'cycle': cycle,
        'samples': len(outcomes),
        'loss': round(best_loss, 4),
        'shadow_loss': round(shadow_loss, 4),
        'weights': best.to_dict(),
        'improvement': round(shadow_loss - best_loss, 4) if shadow_loss != float('inf') else None,
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


def _perturb(weights: ScoringWeights, step: float = 0.03) -> ScoringWeights:
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
