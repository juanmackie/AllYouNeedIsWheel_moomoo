"""
Wheel Risk Panel API route — surface concentration, cash, earnings, macro risks.
"""

import logging
from flask import Blueprint, jsonify, current_app
from api.routes.utils import success_response, error_response
from api.services.wheel_risk_service import compute_wheel_risk

logger = logging.getLogger(__name__)

bp = Blueprint('wheel_risk', __name__, url_prefix='/api/wheel-risk')


def _build_earnings_exposure(portfolio_context: dict, db=None) -> dict:
    """Resolve earnings exposure for tickers with short options.

    Uses the provided database instance instead of opening a new one.
    Only includes tickers with earnings within the next 7 calendar days.
    """
    result = {"tickers_at_risk": [], "count": 0}
    try:
        short_calls = portfolio_context.get("short_calls", {})
        short_puts = portfolio_context.get("short_puts", {})
        tickers_in_options = set(short_calls.keys()) | set(short_puts.keys())
        if not tickers_in_options or db is None:
            return result
        from datetime import datetime, timedelta
        window_end = datetime.now().date() + timedelta(days=7)
        for ticker in tickers_in_options:
            info = db.get_earnings_date(ticker)
            if info and info.get("earnings_date"):
                earnings_date_str = info["earnings_date"]
                try:
                    earnings_date = datetime.strptime(str(earnings_date_str), "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    try:
                        earnings_date = datetime.strptime(str(earnings_date_str), "%Y%m%d").date()
                    except (ValueError, TypeError):
                        continue
                if earnings_date <= window_end:
                    result["tickers_at_risk"].append({
                        "ticker": ticker,
                        "earnings_date": earnings_date_str,
                    })
        result["count"] = len(result["tickers_at_risk"])
    except Exception as exc:
        logger.debug("Earnings exposure lookup failed: %s", exc)
    return result


@bp.route('/panel')
def get_risk_panel():
    """Get the wheel risk panel data for the current portfolio."""
    try:
        from api.services.portfolio_context import PortfolioContext
        svc = None
        try:
            import api
            portfolio_svc = api.get_service('portfolio')
            svc = PortfolioContext(portfolio_svc)
        except Exception:
            from api.services.portfolio_service import PortfolioService
            svc = PortfolioContext(PortfolioService())

        portfolio_context = svc.get_portfolio_context(refresh=True)

        # Populate earnings exposure from the app database (not a fresh OptionsDatabase)
        db = current_app.config.get('database')
        portfolio_context["earnings_exposure"] = _build_earnings_exposure(portfolio_context, db=db)

        config = {}
        try:
            config = current_app.config.get('connection_config', {})
        except Exception:
            pass

        risk_data = compute_wheel_risk(portfolio_context, config)
        return success_response({'risk_panel': risk_data})
    except Exception as e:
        logger.error(f"Error computing wheel risk panel: {e}")
        return error_response(str(e))
