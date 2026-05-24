import json
import sqlite3
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger('db.evaluator')


class EvaluatorRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ── Signals ──────────────────────────────────────────────────────────

    def record_surfaced_signals(self, signals: list[dict], source: str = 'recommendations') -> None:
        """Record finalized signals that were surfaced to the user.

        Maps from the formatted signal dict (as returned by
        _format_recommendation) into the evaluator_signals table.
        """
        for s in signals:
            wd = s.get('wheel_decision', {})
            try:
                self.record_signal({
                    'ticker': s.get('ticker'),
                    'option_type': s.get('option_type'),
                    'strike': s.get('strike'),
                    'expiration': s.get('expiration'),
                    'dte': s.get('dte'),
                    'signal_type': s.get('signal_type', 'csp'),
                    'strategy': s.get('strategy', 'wheel'),
                    'source': s.get('data_source', source),
                    'rank': s.get('rank', 0),
                    'score': s.get('score', 0),
                    'confidence': s.get('confidence', 100),
                    'annualized_return': s.get('annualized_return'),
                    'premium_per_contract': s.get('premium_per_contract'),
                    'delta': s.get('delta'),
                    'iv': s.get('implied_volatility'),
                    'cash_required': s.get('capital_required') or s.get('cash_required'),
                    'capital_at_risk': wd.get('capital_at_risk') or s.get('strike', 0) * 100,
                    'broker_buying_power': None,
                    'portfolio_hash': None,
                    'score_details': s.get('score_details', {}),
                    'full_payload': s,
                })
            except Exception as e:
                logger.warning(f"Failed to record surfaced signal for {s.get('ticker')}: {e}")

    def record_signal(self, signal: dict) -> str:
        recommendation_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO evaluator_signals
                    (recommendation_id, ticker, option_type, strike, expiration, dte,
                     signal_type, strategy, source, rank, score, confidence,
                     annualized_return, premium_per_contract, delta, iv,
                     cash_required, capital_at_risk, broker_buying_power,
                     portfolio_hash, score_details_json, full_payload_json,
                     status, shown_to_user)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, 'surfaced', 1)
            """, (
                recommendation_id,
                signal.get('ticker'),
                signal.get('option_type'),
                signal.get('strike'),
                signal.get('expiration'),
                signal.get('dte'),
                signal.get('signal_type'),
                signal.get('strategy'),
                signal.get('source', 'moomoo'),
                signal.get('rank'),
                signal.get('score'),
                signal.get('confidence'),
                signal.get('annualized_return'),
                signal.get('premium_per_contract'),
                signal.get('delta'),
                signal.get('iv'),
                signal.get('cash_required'),
                signal.get('capital_at_risk'),
                signal.get('broker_buying_power'),
                signal.get('portfolio_hash'),
                json.dumps(signal.get('score_details', {})),
                json.dumps(signal.get('full_payload', {})),
            ))
            conn.commit()
        return recommendation_id

    def get_signal_by_id(self, recommendation_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM evaluator_signals WHERE recommendation_id=?",
                (recommendation_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_pending_resolution_signals(self, limit: int = 100) -> list[dict]:
        today = datetime.now().strftime('%Y%m%d')
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM evaluator_signals
                WHERE status IN ('surfaced', 'observed_open')
                  AND expiration < ?
                ORDER BY expiration ASC
                LIMIT ?
            """, (today, limit)).fetchall()
            return [dict(r) for r in rows]

    def get_observable_open_signals(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM evaluator_signals
                WHERE status = 'surfaced'
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_valid_training_outcomes(self, limit: int = 200) -> list[dict]:
        valid_outcomes = ('expired_worthless', 'assigned', 'called_away',
                          'closed_profit', 'closed_loss', 'rolled_profit', 'rolled_loss')
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT s.* FROM evaluator_signals s
                WHERE s.resolved_outcome IN ({})
                  AND s.actual_return IS NOT NULL
                ORDER BY s.resolved_at DESC
                LIMIT ?
            """.format(','.join('?' * len(valid_outcomes))),
                tuple(valid_outcomes) + (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def update_signal_status(self, recommendation_id: str, status: str,
                             resolved_outcome: str = None,
                             actual_return: float = None,
                             linked_position_key: str = None,
                             resolved_at: str = None) -> bool:
        fields = ['status=?']
        params = [status]
        if resolved_outcome is not None:
            fields.append('resolved_outcome=?')
            params.append(resolved_outcome)
        if actual_return is not None:
            fields.append('actual_return=?')
            params.append(actual_return)
        if linked_position_key is not None:
            fields.append('linked_position_key=?')
            params.append(linked_position_key)
        if resolved_at is not None:
            fields.append('resolved_at=?')
            params.append(resolved_at)
        params.append(recommendation_id)
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE evaluator_signals SET {} WHERE recommendation_id=?".format(','.join(fields)),
                params
            )
            conn.commit()
            return cur.rowcount > 0

    # ── Feedback ─────────────────────────────────────────────────────────

    def get_feedback_biases(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evaluator_feedback_bias ORDER BY factor"
            ).fetchall()
            return [dict(r) for r in rows]

    def save_feedback_event(self, recommendation_id: str, ticker: str,
                            factor: str, predicted_contrib: float,
                            actual_return: float, error: float,
                            outcome_type: str) -> None:
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO evaluator_feedback_events
                    (recommendation_id, ticker, factor,
                     predicted_contrib, actual_return, error, outcome_type)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (recommendation_id, ticker, factor, predicted_contrib,
                  actual_return, error, outcome_type))
            existing = conn.execute(
                "SELECT * FROM evaluator_feedback_bias WHERE factor=?",
                (factor,)
            ).fetchone()
            now = datetime.now().isoformat()
            if existing:
                old_count = existing['sample_count']
                old_mean = existing['mean_error']
                new_count = old_count + 1
                new_mean = old_mean + (error - old_mean) / new_count
                alpha = 0.1
                old_mult = existing['bias_multiplier']
                correction = 1.0 - (new_mean * 2.0)
                new_mult = old_mult * (1 - alpha) + correction * alpha
                new_mult = max(0.5, min(2.0, new_mult))
                conn.execute("""
                    UPDATE evaluator_feedback_bias SET
                        mean_error=?, sample_count=?, bias_multiplier=?,
                        last_updated=?
                    WHERE factor=?
                """, (new_mean, new_count, new_mult, now, factor))
            else:
                conn.execute("""
                    INSERT INTO evaluator_feedback_bias
                        (factor, mean_error, sample_count, bias_multiplier, last_updated)
                    VALUES (?, ?, 1, ?, ?)
                """, (factor, error, 1.0 - error * 2.0, now))
            conn.commit()

    def get_feedback_summary(self) -> dict:
        biases = self.get_feedback_biases()
        with self._connect() as conn:
            total_events = conn.execute(
                "SELECT COUNT(*) as c FROM evaluator_feedback_events"
            ).fetchone()['c']
        over = [b for b in biases if b['bias_multiplier'] < 0.95]
        under = [b for b in biases if b['bias_multiplier'] > 1.05]
        return {
            'total_factors': len(biases),
            'total_events': total_events,
            'over_predicting': [{'factor': b['factor'], 'mult': round(b['bias_multiplier'], 3)} for b in over],
            'under_predicting': [{'factor': b['factor'], 'mult': round(b['bias_multiplier'], 3)} for b in under],
            'sample_count': sum(b['sample_count'] for b in biases),
        }

    def get_adjusted_weights(self, base_weights: dict) -> dict:
        biases = {r['factor']: r['bias_multiplier'] for r in self.get_feedback_biases()}
        adjusted = {}
        for factor, base_weight in base_weights.items():
            mult = biases.get(factor, 1.0)
            adjusted[factor] = round(base_weight * mult, 4)
        return adjusted

    def get_valid_sample_count(self) -> int:
        valid_outcomes = ('expired_worthless', 'assigned', 'called_away',
                          'closed_profit', 'closed_loss', 'rolled_profit', 'rolled_loss')
        with self._connect() as conn:
            row = conn.execute("""
                SELECT COUNT(*) as c FROM evaluator_signals
                WHERE resolved_outcome IN ({})
            """.format(','.join('?' * len(valid_outcomes))),
                valid_outcomes
            ).fetchone()
            return row['c'] if row else 0

    # ── Calibrations ────────────────────────────────────────────────────

    def save_calibration(self, cycle: int, samples: int, loss: float,
                         weights: dict, shadow_loss: float = None,
                         accepted: bool = False) -> None:
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO evaluator_calibrations
                    (cycle, samples, loss, shadow_loss, weights_json, accepted)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (cycle, samples, loss, shadow_loss,
                  json.dumps(weights), 1 if accepted else 0))
            conn.commit()

    def get_latest_calibration(self) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM evaluator_calibrations ORDER BY cycle DESC LIMIT 1"
            ).fetchone()
            result = dict(row) if row else None
            if result and result['weights_json']:
                try:
                    result['weights'] = json.loads(result['weights_json'])
                except (json.JSONDecodeError, TypeError):
                    result['weights'] = {}
            return result

    def get_calibration_history(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evaluator_calibrations ORDER BY cycle DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_next_calibration_cycle(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(cycle) as m FROM evaluator_calibrations"
            ).fetchone()
            return (row['m'] or 0) + 1

    # ── Scheduler state ──────────────────────────────────────────────────

    def get_scheduler_state(self, name: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM evaluator_scheduler_state WHERE name=?",
                (name,)
            ).fetchone()
            return dict(row) if row else None

    def set_scheduler_state(self, name: str, status: str, message: str = '') -> None:
        with self._connect() as conn:
            now = datetime.now().isoformat()
            conn.execute("""
                INSERT OR REPLACE INTO evaluator_scheduler_state
                    (name, last_run, last_status, last_message)
                VALUES (?, ?, ?, ?)
            """, (name, now, status, message))
            conn.commit()

    def get_all_scheduler_states(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evaluator_scheduler_state"
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Stats ────────────────────────────────────────────────────────────

    def get_summary_stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as c FROM evaluator_signals"
            ).fetchone()['c']
            resolved = conn.execute(
                "SELECT COUNT(*) as c FROM evaluator_signals WHERE resolved_outcome IS NOT NULL"
            ).fetchone()['c']
            expired = conn.execute(
                "SELECT COUNT(*) as c FROM evaluator_signals WHERE resolved_outcome='expired_worthless'"
            ).fetchone()['c']
            assigned = conn.execute(
                "SELECT COUNT(*) as c FROM evaluator_signals WHERE resolved_outcome='assigned'"
            ).fetchone()['c']
            unknown = conn.execute(
                "SELECT COUNT(*) as c FROM evaluator_signals WHERE resolved_outcome='unknown'"
            ).fetchone()['c']
            ignored = conn.execute(
                "SELECT COUNT(*) as c FROM evaluator_signals WHERE status='ignored'"
            ).fetchone()['c']
            return {
                'total_recommendations': total,
                'resolved': resolved,
                'expired_worthless': expired,
                'assigned': assigned,
                'unknown_resolutions': unknown,
                'ignored': ignored,
                'assignment_rate': round(assigned / max(resolved, 1) * 100, 1),
                'expiry_rate': round(expired / max(resolved, 1) * 100, 1),
            }

    def get_recent_signals(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evaluator_signals ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
