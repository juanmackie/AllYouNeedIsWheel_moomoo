"""
Feedback Loop — applies bias multipliers to scoring via EvaluatorRepository.

All state is stored in the app's options.db via EvaluatorRepository.
The old independent SQLite files (~/.wheel/evaluator/feedback.db) are no longer used.

Architecture:
    Evaluator records outcome -> calls _feed_feedback -> saves event + updates bias
    score_contract() calls evaluator_repo.get_adjusted_weights() for live multipliers
"""

import logging

logger = logging.getLogger(__name__)
