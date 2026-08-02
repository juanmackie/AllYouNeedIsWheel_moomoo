"""
Portfolio API routes
"""

from flask import Blueprint, current_app, jsonify, request

from api.routes.source_policy import attach_source_policy, build_account_source_policy
from api.routes.utils import error_response
from core.connection import probe_opend_status
from core.logging_config import get_logger

bp = Blueprint("portfolio", __name__, url_prefix="/api/portfolio")

# Lazy service access - service created on first use
_portfolio_service_instance = None


def get_portfolio_service():
    """Get or create the portfolio service instance."""
    global _portfolio_service_instance
    if _portfolio_service_instance is None:
        import api

        _portfolio_service_instance = api.get_service("portfolio")
    return _portfolio_service_instance


logger = get_logger("api.routes.portfolio", "api")


def _is_real_account_unavailable(message):
    if not message:
        return False

    return (
        "requested REAL account" in message
        or "No available real accounts" in message
        or "Nonexisting acc_id" in message
    )


def _service_unavailable_response(message, fallback_message):
    error_message = message or fallback_message
    extra = {}
    if _is_real_account_unavailable(error_message):
        extra["error_code"] = "real_account_unavailable"
        extra["opend_status"] = {"status": "real_account_unavailable", "message": error_message}
    return error_response(error_message, status_code=503, **extra)


def _ensure_opend_available():
    connection_config = current_app.config.get("connection_config", {})
    status = probe_opend_status(
        host=connection_config.get("host", "127.0.0.1"), port=connection_config.get("port", 11111)
    )
    if status.get("status") == "connected":
        return None

    error_code = "opend_login_required" if status.get("status") == "login_required" else "opend_unavailable"
    return error_response(
        status.get("message", "OpenD is unavailable."), status_code=503, error_code=error_code, opend_status=status
    )


@bp.route("/", methods=["GET"])
def get_portfolio():
    """Get the current portfolio information (broker truth only)."""
    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response

        results = get_portfolio_service().get_portfolio_summary()
        if results is None:
            return _service_unavailable_response(get_portfolio_service().last_error, "Failed to load portfolio summary")

        return jsonify(attach_source_policy(results, build_account_source_policy("portfolio")))
    except Exception as e:
        return error_response(str(e), status_code=500)


@bp.route("/positions", methods=["GET"])
def get_positions():
    """
    Get the current portfolio positions

    Query Parameters:
        type: Filter by position type (STK, OPT). If not provided, returns all positions.
    """
    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response

        # Get the position_type from query parameters
        position_type = request.args.get("type")
        # Validate position_type
        if position_type and position_type not in ["STK", "OPT"]:
            return error_response("Invalid position type. Supported types: STK, OPT", status_code=400)

        results = get_portfolio_service().get_positions(position_type)
        if results is None:
            return _service_unavailable_response(get_portfolio_service().last_error, "Failed to load positions")
        response = jsonify(results)
        response.headers["X-Source-Policy"] = "broker_only"
        response.headers["X-Source-Truth"] = "opend"
        return response
    except Exception as e:
        return error_response(str(e), status_code=500)


@bp.route("/weekly-income", methods=["GET"])
def get_weekly_income():
    """
    Get weekly option income from short options expiring this Friday.

    Returns:
        A JSON response containing weekly option income data:
        {
            "positions": [
                {
                    "symbol": "NVDA",
                    "option_type": "P",
                    "strike": 850.0,
                    "expiration": "20240510",
                    "position": 10,
                    "avg_cost": 15.5,
                    "current_price": 15.5,
                    "income": 155.0
                },
                ...
            ],
            "total_income": 155.0,
            "positions_count": 1,
            "this_friday": "20240510"
        }

        Error response:
        {
            "error": "Error message",
            "positions": [],
            "total_income": 0,
            "positions_count": 0
        }
    """
    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response

        results = get_portfolio_service().get_weekly_option_income()

        if "error" in results:
            payload = {"error": results["error"], "positions": [], "total_income": 0, "positions_count": 0}
            if _is_real_account_unavailable(results["error"]):
                payload["error_code"] = "real_account_unavailable"
                payload["opend_status"] = {"status": "real_account_unavailable", "message": results["error"]}
                return jsonify(payload), 503
            return jsonify(payload), 500

        return jsonify(attach_source_policy(results, build_account_source_policy("weekly_income"))), 200
    except Exception as e:
        return jsonify({"error": str(e), "positions": [], "total_income": 0, "positions_count": 0}), 500


# roll-pressure and alerts endpoints extracted to
# api/routes/roll_pressure.py and api/routes/alerts.py (F008)
