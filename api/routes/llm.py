"""
LLM Advisor API routes

Single endpoint that gathers all trading data and returns AI-generated
trade suggestions (opens, closes, rolls).
"""

import logging
import traceback

from flask import Blueprint

from api.routes.utils import error_response, success_response

logger = logging.getLogger("api.routes.llm")

bp = Blueprint("llm", __name__, url_prefix="/api/llm")


@bp.route("/status", methods=["GET"])
def get_status():
    """Return LLM advisor availability for UI gating."""
    try:
        from api.services.llm_service import get_status as _get_status

        return success_response(_get_status())
    except Exception as exc:
        logger.error(f"Error in /api/llm/status: {exc}")
        logger.error(traceback.format_exc())
        return error_response(str(exc), status_code=500)


@bp.route("/suggestions", methods=["POST"])
def get_suggestions():
    logger.info("POST /suggestions — generating trade suggestions")

    try:
        from api.services.llm_service import get_suggestions as _get_suggestions

        result = _get_suggestions()

        if result["success"]:
            return success_response(result)
        return error_response(result.get("error", "LLM service unavailable"), status_code=503)

    except Exception as exc:
        logger.error(f"Error in /api/llm/suggestions: {exc}")
        logger.error(traceback.format_exc())
        return error_response(str(exc), status_code=500)
