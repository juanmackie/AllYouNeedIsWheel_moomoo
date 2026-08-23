"""
Position alerts route.

Extracted from api/routes/portfolio.py (F008).
"""

import traceback

from flask import Blueprint

from api.routes.utils import ensure_opend_available as _ensure_opend_available
from api.routes.utils import error_response, success_response
from api.services.portfolio_scoring import build_portfolio_context, score_position
from core.logging_config import get_logger

logger = get_logger("api.routes.alerts", "api")

bp = Blueprint("alerts", __name__, url_prefix="/api/portfolio")


def _get_portfolio_service():
    import api

    return api.get_service("portfolio")


@bp.route("/alerts", methods=["GET"])
def get_position_alerts():
    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response

        positions = _get_portfolio_service().get_positions("OPT")
        if positions is None:
            return error_response("Failed to load positions", status_code=500)

        option_positions = positions if isinstance(positions, list) else positions.get("positions", [])
        if not option_positions:
            return success_response({"alerts": [], "count": 0})

        import api

        ps = _get_portfolio_service()
        portfolio_context, _, _ = build_portfolio_context(option_positions, ps)
        conn = ps._ensure_connection()
        iv_earnings_service = api.get_service("ivearnings")

        alerts = []
        for pos in option_positions:
            decision = score_position(pos, conn, portfolio_context, iv_earnings_service)
            if decision is None:
                continue

            if decision.roll_pressure >= 70:
                alerts.append(
                    {
                        "ticker": decision.ticker,
                        "option_type": decision.option_type,
                        "strike": decision.strike,
                        "expiration": decision.expiration,
                        "alert_type": "roll_pressure_urgent",
                        "severity": "urgent",
                        "message": f"High roll pressure ({decision.roll_pressure:.0f}%)",
                        "wheel_decision": decision.to_dict(),
                    }
                )
            elif decision.roll_pressure >= 40:
                alerts.append(
                    {
                        "ticker": decision.ticker,
                        "option_type": decision.option_type,
                        "strike": decision.strike,
                        "expiration": decision.expiration,
                        "alert_type": "roll_pressure_watch",
                        "severity": "warning",
                        "message": (f"Moderate roll pressure ({decision.roll_pressure:.0f}%)"),
                        "wheel_decision": decision.to_dict(),
                    }
                )

            if decision.profit_target_progress >= 50:
                alerts.append(
                    {
                        "ticker": decision.ticker,
                        "option_type": decision.option_type,
                        "strike": decision.strike,
                        "expiration": decision.expiration,
                        "alert_type": "profit_target_50",
                        "severity": "info",
                        "message": (f"50% profit target reached ({decision.profit_target_progress:.0f}%)"),
                        "wheel_decision": decision.to_dict(),
                    }
                )

            if decision.otm_pct < 0:
                alerts.append(
                    {
                        "ticker": decision.ticker,
                        "option_type": decision.option_type,
                        "strike": decision.strike,
                        "expiration": decision.expiration,
                        "alert_type": "strike_crossed",
                        "severity": "danger",
                        "message": f"Strike crossed ({abs(decision.otm_pct):.1f}% ITM)",
                        "wheel_decision": decision.to_dict(),
                    }
                )

            for blocker in decision.hard_blockers:
                alerts.append(
                    {
                        "ticker": decision.ticker,
                        "option_type": decision.option_type,
                        "strike": decision.strike,
                        "expiration": decision.expiration,
                        "alert_type": "hard_blocker",
                        "severity": "danger",
                        "message": f"Blocked: {blocker}",
                        "wheel_decision": decision.to_dict(),
                    }
                )

        severity_order = {"danger": 0, "urgent": 1, "warning": 2, "info": 3}
        alerts.sort(key=lambda x: severity_order.get(x["severity"], 4))

        return success_response({"alerts": alerts, "count": len(alerts)})

    except Exception as e:
        logger.error(f"Error getting position alerts: {e}")
        logger.error(traceback.format_exc())
        return error_response(str(e), status_code=500)
