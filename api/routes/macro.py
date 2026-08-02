"""
Macro Regime API Routes
Provides macro economic regime data for scoring context and dashboard display.
"""

from flask import Blueprint, jsonify

from api.routes.utils import error_response, success_response
from api.services.macro_regime_service import get_macro_service

bp = Blueprint("macro", __name__, url_prefix="/api/macro")


@bp.route("/regime", methods=["GET"])
def get_macro_regime():
    try:
        macro_service = get_macro_service()
        regime = macro_service.get_macro_regime()
        return jsonify(regime), 200
    except Exception as e:
        return jsonify(
            {
                "error": str(e),
                "enabled": False,
                "macro_multiplier": 1.0,
                "summary": "Error detecting macro regime",
                "advice": "Check server logs for details",
            }
        ), 500


@bp.route("/cache/status", methods=["GET"])
def get_cache_status():
    """
    Get macro cache status for monitoring.
    """
    try:
        macro_service = get_macro_service()
        status = macro_service.get_cache_status()
        return jsonify(status), 200
    except Exception as e:
        return error_response(str(e), status_code=500)


@bp.route("/cache/clear", methods=["POST"])
def clear_cache():
    """
    Clear macro cache (forces fresh FRED fetch on next request).

    Returns:
        dict: Success message
    """
    try:
        macro_service = get_macro_service()
        macro_service.clear_cache()
        return success_response({"message": "Macro cache cleared"})
    except Exception as e:
        return error_response(str(e))
