"""
Earnings Routes
Earnings lock and locked tickers endpoints.
"""

import logging

from flask import Blueprint, jsonify, request

from api import get_service
from api.routes.utils import error_response, success_response
from core.ticker_utils import earnings_underlying_ticker

logger = logging.getLogger(__name__)

bp = Blueprint("earnings", __name__, url_prefix="/api/earnings")


def _get_earnings_service():
    return get_service("ivearnings")


@bp.route("/locked-tickers")
def get_locked_tickers():
    """
    Get tickers currently locked due to upcoming earnings (within lock_days).

    Query Parameters:
        lock_days: Number of days to consider (default: 5)

    Returns:
        JSON with locked tickers and their earnings dates.
    """
    lock_days = request.args.get("lock_days", 5, type=int)

    try:
        service = get_service("ivearnings")
        db = service.db

        # Get all tickers with earnings within lock_days
        locked = db.get_pending_earnings(days_threshold=lock_days)

        if not locked:
            return success_response(
                {
                    "locked": [],
                    "count": 0,
                    "lock_days": lock_days,
                }
            )

        from datetime import datetime

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        locked_list = []
        for item in locked:
            earnings_date = item.get("earnings_date")
            days_to = None
            if earnings_date:
                try:
                    ed = datetime.strptime(earnings_date[:10], "%Y-%m-%d")
                    days_to = (ed - today).days
                except Exception:
                    pass
            locked_list.append(
                {
                    "ticker": item.get("ticker"),
                    "earnings_date": earnings_date,
                    "days_to_earnings": days_to,
                    "time_of_day": item.get("time_of_day"),
                    "fiscal_date_ending": item.get("fiscal_date_ending"),
                    "estimate": item.get("estimate"),
                    "currency": item.get("currency"),
                    "earnings_source": item.get("earnings_source"),
                }
            )

        return success_response(
            {
                "locked": locked_list,
                "count": len(locked_list),
                "lock_days": lock_days,
            }
        )

    except Exception as e:
        logger.error(f"Error getting locked tickers: {e}")
        return error_response(str(e))


@bp.route("/lock-status")
def get_lock_status():
    """
    Get the current earnings lock status and configuration.

    Returns:
        JSON with lock configuration and status.
    """
    try:
        # Get lock_days from config (default 5)
        from config import DEFAULT_CONNECTION_CONFIG

        lock_days = DEFAULT_CONNECTION_CONFIG.get("earnings_lock_days", 5)

        return success_response(
            {
                "lock_days": lock_days,
                "enabled": True,
            }
        )

    except Exception as e:
        logger.error(f"Error getting lock status: {e}")
        return error_response(str(e))


@bp.route("/status")
def get_earnings_status():
    """Get earnings updater status and cache statistics."""
    service = _get_earnings_service()

    return jsonify(
        {
            "status": "manual",
            "scheduler": {"running": False, "note": "automatic scheduler removed"},
            "cache_stats": service.get_cache_stats(),
        }
    )


@bp.route("/update/<ticker>")
def update_single_earnings(ticker):
    """Manually update earnings for a single ticker."""
    service = _get_earnings_service()
    success = service.update_earnings_data(ticker)
    info = service.get_earnings_info(ticker)

    return jsonify(
        {
            "success": success,
            "ticker": ticker,
            "earnings_info": info,
        }
    )


@bp.route("/refresh", methods=["POST"])
def refresh_all_earnings():
    """Trigger a global update for all active symbols."""
    from api.routes.utils import enforce_route_rate_limit

    # External-provider batch call; rate-limit the trigger (F-O1).
    allowed, retry_after = enforce_route_rate_limit(
        "earnings-refresh", request.remote_addr or "local", max_requests=4, window_seconds=300
    )
    if not allowed:
        return error_response("Rate limit exceeded", status_code=429, retry_after=retry_after)

    service = _get_earnings_service()
    portfolio = get_service("portfolio")

    positions = portfolio.get_positions() or []

    all_tickers = set()
    for position in positions:
        normalized = earnings_underlying_ticker(str(position.get("symbol", "") or ""))
        if normalized:
            all_tickers.add(normalized)

    try:
        wm = get_service("watchlist")
        watchlist_tickers = [earnings_underlying_ticker(t.strip()) for t in wm.get_effective_watchlist() if t.strip()]
        all_tickers.update(t for t in watchlist_tickers if t)
    except Exception:
        logger.warning("Could not load watchlist tickers for earnings update", exc_info=True)

    if not all_tickers:
        return jsonify({"success": True, "updated": 0, "message": "No active symbols found"})

    result = service.batch_update_earnings(list(all_tickers))

    return jsonify(
        {
            "success": True,
            "updated_count": result["successful"],
            "failed_count": result["failed"],
            "total_attempted": len(all_tickers),
        }
    )


@bp.route("/pending")
def get_pending_earnings():
    """Get tickers with pending earnings in the next 7 days."""
    from flask import current_app

    from db.database import OptionsDatabase

    db = current_app.config.get("database") or OptionsDatabase()
    pending = db.get_pending_earnings(days_threshold=7)

    return jsonify(
        {
            "count": len(pending),
            "tickers": pending,
        }
    )
