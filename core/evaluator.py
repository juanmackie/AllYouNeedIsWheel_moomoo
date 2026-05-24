"""
Evaluator — tracks surfaced signal outcomes and feeds feedback loop.

Uses the app's OptionsDatabase (options.db) instead of independent SQLite files.
Does NOT record candidates during scoring — only final surfaced signals.

Signal state machine:
    surfaced -> observed_open -> resolved_valid / resolved_unknown / ignored

Outcome types that are VALID for training:
    expired_worthless, assigned, called_away, closed_profit, closed_loss,
    rolled_profit, rolled_loss

Outcome types that are NOT valid for training:
    unknown, ignored, still_open, manual_unlinked
"""

import logging
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────────

_VALID_TRAINING_OUTCOMES = {
    'expired_worthless', 'assigned', 'called_away',
    'closed_profit', 'closed_loss', 'rolled_profit', 'rolled_loss',
}


def is_valid_training_outcome(outcome: str) -> bool:
    return outcome in _VALID_TRAINING_OUTCOMES


_OPTION_SYMBOL_RE = re.compile(r'^([A-Z]+?)\d{6,8}[CP]\d+$')


def _extract_underlying(symbol) -> str:
    """Extract the underlying ticker from a broker symbol.

    Handles:
        AAPL                        → AAPL
        US.AAPL                     → AAPL
        US.AAPL20250516P00150000    → AAPL
        AAPL250516C00150000         → AAPL
    """
    if not symbol:
        return ''
    s = str(symbol).upper().replace('US.', '')
    m = _OPTION_SYMBOL_RE.match(s)
    if m:
        return m.group(1)
    return s


def _normalize_exp(exp) -> str:
    """Normalize any expiration format to YYYYMMDD."""
    if exp is None:
        return ''
    if isinstance(exp, (int, float)):
        exp = str(int(exp))
    elif not isinstance(exp, str):
        try:
            if isinstance(exp, datetime):
                return exp.strftime('%Y%m%d')
            exp = str(exp)
        except Exception:
            return str(exp)
    cleaned = exp.strip().replace('-', '').replace('/', '')
    if cleaned.isdigit() and len(cleaned) == 8:
        return cleaned
    return exp


# ── Public API ──────────────────────────────────────────────────────────

def record_surfaced_signals(
    evaluator_repo,
    signals: list[dict],
    portfolio_context: dict,
) -> list[str]:
    """
    Record final surfaced signals. Returns list of recommendation_ids.

    Only the top-ranked signals that are actually shown to the user
    get recorded. This is called after signal selection, NOT during
    candidate scoring.
    """
    portfolio_hash = _compute_portfolio_hash_for_record(portfolio_context)
    ids = []
    for signal in signals:
        signal['portfolio_hash'] = portfolio_hash
        signal['full_payload'] = signal.get('full_payload', {})
        try:
            rid = evaluator_repo.record_signal(signal)
            if rid:
                ids.append(rid)
        except Exception as e:
            logger.warning("Failed to record signal %s: %s", signal.get('ticker'), e)
    return ids


def _compute_portfolio_hash_for_record(portfolio_context: dict) -> str:
    """Lightweight portfolio hash for tracking."""
    try:
        positions = portfolio_context.get('positions', {})
        cash = portfolio_context.get('cash_balance', 0)
        parts = []
        for symbol, pos in sorted(positions.items()):
            qty = float(pos.get('position', 0) or 0)
            parts.append(f"{symbol}:{qty}")
        return str(hash("|".join(parts) + f"|CASH:{cash}"))
    except Exception:
        return "unknown"


def run_evaluation_cycle(
    evaluator_repo,
    portfolio_service=None,
    config: Optional[dict] = None,
) -> dict:
    """
    Run one evaluation cycle:
    1. Find signals past expiration that need resolution.
    2. If portfolio_service is available, check actual broker state.
    3. Otherwise mark as unknown (not training data).
    4. Update scheduler state.

    Returns summary dict.
    """
    if config is None:
        config = {}

    if not config.get('enabled', True):
        return {'checked': 0, 'resolved': 0, 'skipped': 0, 'errors': 0,
                'message': 'Evaluator disabled'}

    auto_resolve = config.get('auto_resolve', True)
    if not auto_resolve:
        return {'checked': 0, 'resolved': 0, 'skipped': 0, 'errors': 0,
                'message': 'Auto-resolve disabled'}

    pending = evaluator_repo.get_pending_resolution_signals(limit=50)
    if not pending:
        return {'checked': 0, 'resolved': 0, 'skipped': 0, 'errors': 0,
                'message': 'No pending signals'}

    resolved_count = 0
    skipped_count = 0
    error_count = 0

    for signal in pending:
        try:
            outcome, actual_return = _resolve_signal_outcome(
                signal, portfolio_service
            )
            if outcome == 'unknown' and not _can_determine_outcome(signal, portfolio_service):
                evaluator_repo.update_signal_status(
                    signal['recommendation_id'],
                    status='ignored',
                    resolved_outcome='unknown',
                    resolved_at=datetime.now().isoformat(),
                )
                skipped_count += 1
                continue

            if is_valid_training_outcome(outcome):
                evaluator_repo.update_signal_status(
                    signal['recommendation_id'],
                    status='resolved_valid',
                    resolved_outcome=outcome,
                    actual_return=actual_return,
                    resolved_at=datetime.now().isoformat(),
                )
                resolved_count += 1

                # Feed into feedback loop
                try:
                    _feed_feedback(evaluator_repo, signal, outcome, actual_return, config)
                except Exception as e:
                    logger.warning("Feedback update failed for %s: %s",
                                   signal['recommendation_id'], e)
            else:
                evaluator_repo.update_signal_status(
                    signal['recommendation_id'],
                    status='resolved_unknown',
                    resolved_outcome=outcome or 'unknown',
                    resolved_at=datetime.now().isoformat(),
                )
                skipped_count += 1
        except Exception as e:
            logger.error("Error resolving signal %s: %s",
                         signal.get('recommendation_id', '?'), e)
            error_count += 1

    return {
        'checked': len(pending),
        'resolved': resolved_count,
        'skipped': skipped_count,
        'errors': error_count,
    }


def _resolve_signal_outcome(signal: dict, portfolio_service) -> tuple:
    """
    Determine outcome for an expired signal.

    If portfolio_service is available, check actual broker state.
    Otherwise return ('unknown', 0.0) so it does NOT train the model.
    Uses _extract_underlying() to handle both bare ticker (AAPL) and
    full broker option symbol (US.AAPL20250516P00150000) matching.
    """
    if not portfolio_service:
        return ('unknown', 0.0)

    try:
        ticker = signal['ticker'].upper()
        option_type = signal['option_type']
        strike = signal['strike']
        expiration = _normalize_exp(signal.get('expiration', ''))

        # Get current short option positions
        positions = portfolio_service.get_positions('OPT') or []
        matching = [
            p for p in positions
            if _extract_underlying(p.get('symbol', '')) == ticker
            and p.get('option_type', '').upper() == option_type.upper()
            and abs(float(p.get('strike', 0) or 0) - strike) < 0.01
            and _normalize_exp(p.get('expiration', '')) == expiration
        ]

        if not matching:
            # Option no longer exists in broker — likely expired or closed
            stock_positions = portfolio_service.get_positions('STK') or []
            stock_qty = 0
            stock_found = False
            for sp in stock_positions:
                if _extract_underlying(sp.get('symbol', '')) == ticker:
                    stock_found = True
                    stock_qty = abs(float(sp.get('position', 0) or 0))
                    break

            if option_type == 'PUT':
                if stock_found and stock_qty >= 100:
                    return ('assigned', _estimate_assignment_return(signal))
                return ('expired_worthless', signal.get('premium_per_contract', 0) or 0)
            elif option_type == 'CALL':
                if not stock_found or stock_qty == 0:
                    return ('called_away', _estimate_called_away_return(signal))
                if stock_qty < 100:
                    return ('called_away', _estimate_called_away_return(signal))
                return ('expired_worthless', signal.get('premium_per_contract', 0) or 0)
            else:
                return ('closed_profit', signal.get('premium_per_contract', 0) or 0)

        # Position still exists
        pos = matching[0]
        pos_qty = abs(float(pos.get('position', 0) or 0))

        if pos_qty <= 0:
            return ('closed_profit', signal.get('premium_per_contract', 0) or 0)

        return ('still_open', None)

    except Exception as e:
        logger.warning("Error resolving outcome for %s: %s",
                       signal.get('ticker', '?'), e)
        return ('unknown', 0.0)


def _can_determine_outcome(signal: dict, portfolio_service) -> bool:
    """Return True if the resolver can reasonably determine the outcome."""
    return portfolio_service is not None


def _estimate_assignment_return(signal: dict) -> float:
    premium = float(signal.get('premium_per_contract', 0) or 0)
    return premium  # Premium kept is the realized gain


def _estimate_called_away_return(signal: dict) -> float:
    premium = float(signal.get('premium_per_contract', 0) or 0)
    return premium


def _feed_feedback(evaluator_repo, signal: dict, outcome: str,
                   actual_return: float, config: dict) -> None:
    """Feed a resolved outcome into the feedback loop."""
    if not is_valid_training_outcome(outcome):
        return

    feedback_enabled = config.get('feedback_enabled', False)
    min_samples = config.get('feedback_min_valid_samples', 30)

    if not feedback_enabled:
        return

    valid_count = evaluator_repo.get_valid_sample_count()
    if valid_count < min_samples:
        logger.info(
            "Feedback inactive: %d valid samples < %d minimum",
            valid_count, min_samples,
        )
        return

    score_details = _parse_score_details(signal.get('score_details_json'))
    if not score_details:
        return

    total_score = float(signal.get('score', 0) or 0)
    if total_score <= 0:
        return

    for factor, val in score_details.items():
        if not isinstance(val, (int, float)) or val <= 0:
            continue
        predicted_contrib = val / total_score if total_score > 0 else 0
        if predicted_contrib <= 0.01:
            continue

        error = predicted_contrib - (actual_return / 100.0) if actual_return > 0 else predicted_contrib

        evaluator_repo.save_feedback_event(
            recommendation_id=signal.get('recommendation_id', ''),
            ticker=signal.get('ticker', ''),
            factor=factor,
            predicted_contrib=round(predicted_contrib, 4),
            actual_return=round(actual_return, 2),
            error=round(error, 4),
            outcome_type=outcome,
        )


def _parse_score_details(score_details_json) -> dict:
    import json
    if not score_details_json:
        return {}
    try:
        return json.loads(score_details_json) if isinstance(score_details_json, str) else score_details_json
    except (json.JSONDecodeError, TypeError):
        return {}
