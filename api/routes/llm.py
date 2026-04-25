"""
LLM Advisor API routes

Single endpoint that gathers all trading data and returns AI-generated
trade suggestions (opens, closes, rolls).
"""

from flask import Blueprint, jsonify, request
import logging
import traceback

logger = logging.getLogger('api.routes.llm')

bp = Blueprint('llm', __name__, url_prefix='/api/llm')


@bp.route('/suggestions', methods=['POST'])
def get_suggestions():
    """
    Generate AI-powered trade suggestions based on all available data.

    Gathers portfolio summary, stock/option positions (scored for roll pressure),
    top recommendations, VIX regime, and macro context, then sends it to the
    configured LLM for analysis.

    No request body required — all data is gathered server-side.

    Returns:
        200: { success: true, text: "...", provider: "openai", model: "gpt-4o" }
        503: { success: false, error: "LLM not configured ..." }
        500: { success: false, error: "..." }
    """
    logger.info("POST /suggestions — generating trade suggestions")

    try:
        from api.services.llm_service import get_suggestions as _get_suggestions

        result = _get_suggestions()

        if result['success']:
            return jsonify(result), 200
        else:
            # 503 = service unavailable (LLM not configured or disabled)
            return jsonify(result), 503

    except Exception as exc:
        logger.error(f"Error in /api/llm/suggestions: {exc}")
        logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': str(exc),
        }), 500
