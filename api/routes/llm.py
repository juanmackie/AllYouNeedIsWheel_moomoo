"""
LLM Advisor API routes

Single endpoint that gathers all trading data and returns AI-generated
trade suggestions (opens, closes, rolls).
"""

from flask import Blueprint, jsonify
import logging
import traceback

logger = logging.getLogger('api.routes.llm')

bp = Blueprint('llm', __name__, url_prefix='/api/llm')


@bp.route('/suggestions', methods=['POST'])
def get_suggestions():
    logger.info("POST /suggestions — generating trade suggestions")

    try:
        from api.services.llm_service import get_suggestions as _get_suggestions

        result = _get_suggestions()

        if result['success']:
            return jsonify(result), 200
        return jsonify(result), 503

    except Exception as exc:
        logger.error(f"Error in /api/llm/suggestions: {exc}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(exc)}), 500
