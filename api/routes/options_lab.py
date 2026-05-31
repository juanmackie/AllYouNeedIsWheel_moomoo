"""
Options Lab API route — payoff/scenario analysis for selected contracts.
"""

import logging
from flask import Blueprint, request, jsonify, current_app
from api.routes.utils import success_response, error_response
from api.services.options_lab import compute_options_lab, compute_roll_comparison

logger = logging.getLogger(__name__)

bp = Blueprint('options_lab', __name__, url_prefix='/api/options-lab')


@bp.route('/analyze', methods=['POST'])
def analyze_contract():
    """Compute Options Lab metrics for a single contract (pure calculator)."""
    try:
        data = request.get_json(silent=True) or {}
        contract = data.get('contract', {})
        if not contract.get('strike'):
            return error_response('Contract must include strike', status_code=400)
        option_type = str(contract.get('option_type', '')).upper()
        if option_type not in ('CALL', 'PUT'):
            return error_response("option_type must be 'CALL' or 'PUT'", status_code=400)
        result = compute_options_lab(contract, {})
        return success_response({'analysis': result})
    except Exception as e:
        logger.error(f"Error in options lab analysis: {e}")
        return error_response(str(e))


@bp.route('/roll-compare', methods=['POST'])
def compare_roll():
    """Compare current position with a roll target."""
    try:
        data = request.get_json(silent=True) or {}
        current = data.get('current_contract', {})
        roll_target = data.get('roll_contract', {})
        if not current.get('strike') or not roll_target.get('strike'):
            return error_response('Both current_contract and roll_contract required', status_code=400)
        result = compute_roll_comparison(current, roll_target)
        return success_response({'comparison': result})
    except Exception as e:
        logger.error(f"Error in roll comparison: {e}")
        return error_response(str(e))
