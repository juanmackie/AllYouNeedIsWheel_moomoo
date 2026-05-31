"""
Wheel Playbook Routes — hypothesis registry for wheel strategy testing.
"""

import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app
from api.routes.utils import success_response, error_response
from core.playbook_registry import HypothesisRegistry, PlaybookHypothesis, VALID_STATUSES

logger = logging.getLogger(__name__)

bp = Blueprint('playbook', __name__, url_prefix='/api/playbook')


def _get_registry():
    db = current_app.config.get('database')
    if not db:
        return None
    return HypothesisRegistry(db)


@bp.route('/hypotheses')
def list_hypotheses():
    """List all playbook hypotheses, optionally filtered by status."""
    reg = _get_registry()
    if not reg:
        return error_response('Database not available', status_code=503)
    try:
        status = request.args.get('status')
        if status and status not in VALID_STATUSES:
            return error_response(f'Invalid status. Must be one of: {", ".join(sorted(VALID_STATUSES))}', status_code=400)
        hypotheses = reg.list_all(status=status)
        return success_response({
            'hypotheses': hypotheses,
            'count': len(hypotheses),
        })
    except Exception as e:
        logger.error(f"Error listing hypotheses: {e}")
        return error_response(str(e))
    finally:
        reg.close()


@bp.route('/hypotheses/<hypothesis_id>')
def get_hypothesis(hypothesis_id: str):
    """Get a single hypothesis by ID."""
    reg = _get_registry()
    if not reg:
        return error_response('Database not available', status_code=503)
    try:
        hyp = reg.get(hypothesis_id)
        if not hyp:
            return error_response('Hypothesis not found', status_code=404)
        return success_response({'hypothesis': hyp})
    except Exception as e:
        logger.error(f"Error fetching hypothesis {hypothesis_id}: {e}")
        return error_response(str(e))
    finally:
        reg.close()


@bp.route('/hypotheses', methods=['POST'])
def create_hypothesis():
    """Create a new playbook hypothesis."""
    reg = _get_registry()
    if not reg:
        return error_response('Database not available', status_code=503)
    try:
        data = request.get_json(silent=True) or {}
        required = ('hypothesis_id', 'title', 'description')
        missing = [k for k in required if not data.get(k)]
        if missing:
            return error_response(f'Missing required fields: {", ".join(missing)}', status_code=400)
        hyp = PlaybookHypothesis(
            hypothesis_id=str(data['hypothesis_id']).strip(),
            title=str(data['title']).strip(),
            description=str(data['description']).strip(),
            category=str(data.get('category', 'general')).strip(),
            status=str(data.get('status', 'exploring')).strip(),
            tags=data.get('tags', []),
            notes=str(data.get('notes', '')).strip(),
        )
        if hyp.status not in VALID_STATUSES:
            hyp.status = 'exploring'
        if not reg.create(hyp):
            return error_response('Hypothesis ID already exists or creation failed', status_code=409)
        return success_response({'hypothesis': hyp.to_dict()}, status_code=201)
    except Exception as e:
        logger.error(f"Error creating hypothesis: {e}")
        return error_response(str(e))
    finally:
        reg.close()


@bp.route('/hypotheses/<hypothesis_id>/status', methods=['PUT'])
def update_hypothesis_status(hypothesis_id: str):
    """Update the status of a hypothesis."""
    reg = _get_registry()
    if not reg:
        return error_response('Database not available', status_code=503)
    try:
        data = request.get_json(silent=True) or {}
        status = str(data.get('status', '')).strip()
        if not status or status not in VALID_STATUSES:
            return error_response(f'Invalid status. Must be one of: {", ".join(sorted(VALID_STATUSES))}', status_code=400)
        if not reg.update_status(hypothesis_id, status):
            return error_response('Hypothesis not found or update failed', status_code=404)
        hyp = reg.get(hypothesis_id)
        return success_response({'hypothesis': hyp})
    except Exception as e:
        logger.error(f"Error updating hypothesis status: {e}")
        return error_response(str(e))
    finally:
        reg.close()


@bp.route('/hypotheses/<hypothesis_id>/notes', methods=['PUT'])
def update_hypothesis_notes(hypothesis_id: str):
    """Update the notes of a hypothesis."""
    reg = _get_registry()
    if not reg:
        return error_response('Database not available', status_code=503)
    try:
        data = request.get_json(silent=True) or {}
        notes = str(data.get('notes', '')).strip()
        if not reg.update_notes(hypothesis_id, notes):
            return error_response('Hypothesis not found or update failed', status_code=404)
        hyp = reg.get(hypothesis_id)
        return success_response({'hypothesis': hyp})
    except Exception as e:
        logger.error(f"Error updating hypothesis notes: {e}")
        return error_response(str(e))
    finally:
        reg.close()


@bp.route('/hypotheses/<hypothesis_id>', methods=['DELETE'])
def delete_hypothesis(hypothesis_id: str):
    """Delete a hypothesis."""
    reg = _get_registry()
    if not reg:
        return error_response('Database not available', status_code=503)
    try:
        if not reg.delete(hypothesis_id):
            return error_response('Hypothesis not found', status_code=404)
        return success_response({'deleted': True})
    except Exception as e:
        logger.error(f"Error deleting hypothesis {hypothesis_id}: {e}")
        return error_response(str(e))
    finally:
        reg.close()
