"""
Options API routes
"""

import logging
import traceback

from flask import Blueprint, current_app, jsonify, request

from api.routes.source_policy import (
    attach_source_policy,
    build_account_source_policy,
    build_research_source_policy,
)
from api.routes.utils import (
    enforce_route_rate_limit,
    error_response,
    normalize_ticker_list,
    success_response,
)
from api.routes.utils import (
    ensure_opend_available as _ensure_opend_available,
)
from core.presets import DEFAULT_PRESET_KEY, get_preset

# Set up logger
logger = logging.getLogger("api.routes.options")

bp = Blueprint("options", __name__, url_prefix="/api/options")


def get_options_service():
    """Get the registered options service singleton (lazy registry)."""
    import api

    return api.get_service("options")


@bp.route("/connection-status", methods=["GET"])
def connection_status():
    """
    Get detailed connection status for debugging connection cycling issues
    """
    try:
        from core.connection import MoomooConnection

        # Get pool stats
        pool_stats = MoomooConnection.get_connection_pool_stats()

        # Get connection info if available
        conn_info = None
        if get_options_service().connection:
            conn_info = get_options_service().connection.get_connection_info()

        return success_response(
            {
                "connection_pool": pool_stats,
                "service_connection": conn_info,
                "service_initialized": get_options_service().connection is not None,
            }
        )
    except Exception as e:
        logger.error(f"Error getting connection status: {e}")
        return error_response(str(e))


# Market status is now checked directly in the route functions


# Helper function to check market status with better error handling
@bp.route("/otm", methods=["GET"])
def otm_options():
    """
    Get option data based on OTM percentage from current price.
    """
    unavailable_response = _ensure_opend_available()
    if unavailable_response:
        return unavailable_response
    allowed, retry_after = enforce_route_rate_limit(
        "otm", request.remote_addr or "local", max_requests=60, window_seconds=60
    )
    if not allowed:
        return error_response("Rate limit exceeded", status_code=429, retry_after=retry_after)

    # Get parameters from request
    ticker = request.args.get("tickers")
    try:
        otm_percentage = float(request.args.get("otm", 10))
    except (TypeError, ValueError):
        return error_response("Invalid otm percentage", status_code=400)
    option_type = request.args.get("optionType")  # Parameter for filtering by option type
    if option_type:
        option_type = option_type.upper()
    expiration = request.args.get("expiration")  # New parameter for filtering by expiration date

    # Validate option_type if provided
    if option_type and option_type not in ["CALL", "PUT"]:
        return error_response(f"Invalid option_type: {option_type}. Must be 'CALL' or 'PUT'", status_code=400)

    valid_tickers, invalid_tickers = normalize_ticker_list(ticker or "")
    if invalid_tickers:
        return error_response(
            f"Invalid ticker(s): {', '.join(invalid_tickers)}",
            status_code=400,
        )
    ticker = valid_tickers[0] if valid_tickers else ""
    if not ticker:
        return error_response("No valid ticker provided", status_code=400)

    if option_type == "PUT":
        connection_config = current_app.config.get("connection_config", {})
        growth_mode = connection_config.get("growth_mode", {})
        screener_profile = growth_mode.get("screener_profile", {})
        min_otm_pct = float(screener_profile.get("csp_min_otm_pct", 5))
        max_otm_pct = float(screener_profile.get("csp_max_otm_pct", 15))
        if otm_percentage < min_otm_pct or otm_percentage > max_otm_pct:
            return error_response(
                f"PUT OTM must be between {min_otm_pct:.0f}% and {max_otm_pct:.0f}% for Growth Mode CSPs",
                status_code=400,
            )

    # Use the existing module-level instance instead of creating a new one
    # Call the service with appropriate parameters including the new option_type and expiration
    result = get_options_service().get_otm_options(
        ticker=ticker, otm_percentage=otm_percentage, option_type=option_type, expiration=expiration
    )

    return jsonify(
        attach_source_policy(
            result,
            build_research_source_policy(
                "otm_options",
                result,
                fallback_sources_allowed=[],
            ),
        )
    )


@bp.route("/stock-price", methods=["GET"])
def get_stock_price():
    """
    Get the current stock price for one or more tickers.
    This is a lightweight endpoint that only returns stock prices.
    """
    unavailable_response = _ensure_opend_available()
    if unavailable_response:
        return unavailable_response
    allowed, retry_after = enforce_route_rate_limit(
        "stock-price", request.remote_addr or "local", max_requests=60, window_seconds=60
    )
    if not allowed:
        return error_response("Rate limit exceeded", status_code=429, retry_after=retry_after)

    # Get ticker(s) from request
    tickers_param = request.args.get("tickers", "")
    if not tickers_param:
        return error_response("No tickers provided", status_code=400)

    # Split tickers on commas if multiple are provided
    tickers, invalid_tickers = normalize_ticker_list(tickers_param)
    if invalid_tickers:
        return error_response(
            f"Invalid ticker(s): {', '.join(invalid_tickers)}",
            status_code=400,
        )
    if not tickers:
        return error_response("No valid tickers provided", status_code=400)

    # Get stock prices for the tickers
    prices = {}
    try:
        for ticker in tickers:
            if ticker:
                # Use the options service to get the stock price without option data
                price = get_options_service().get_stock_price(ticker)
                prices[ticker] = price

        return jsonify(
            attach_source_policy(
                {"status": "success", "data": prices},
                build_research_source_policy(
                    "stock_prices",
                    prices,
                    fallback_sources_allowed=[],
                ),
            )
        )
    except Exception as e:
        logger.error(f"Error getting stock price for {tickers_param}: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))


@bp.route("/expirations", methods=["GET"])
def get_option_expirations():
    logger.info("GET /expirations request received")

    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response
        allowed, retry_after = enforce_route_rate_limit(
            "expirations", request.remote_addr or "local", max_requests=60, window_seconds=60
        )
        if not allowed:
            return error_response("Rate limit exceeded", status_code=429, retry_after=retry_after)

        ticker = request.args.get("ticker")
        valid_tickers, invalid_tickers = normalize_ticker_list(ticker or "")
        if invalid_tickers:
            return error_response(
                f"Invalid ticker(s): {', '.join(invalid_tickers)}",
                status_code=400,
            )
        if not valid_tickers:
            return error_response("No ticker provided", status_code=400)
        ticker = valid_tickers[0]

        option_type = request.args.get("option_type")
        if option_type:
            option_type = option_type.upper()
            if option_type not in ["CALL", "PUT"]:
                return error_response("option_type must be 'CALL' or 'PUT'", status_code=400)

        result = get_options_service().get_option_expirations(ticker, option_type)

        if "error" in result:
            logger.error(f"Error getting expirations for {ticker}: {result['error']}")
            return error_response(result["error"], status_code=404)

        return jsonify(
            attach_source_policy(
                result,
                build_research_source_policy(
                    "option_expirations",
                    result,
                    fallback_sources_allowed=[],
                ),
            )
        )

    except Exception as e:
        logger.error(f"Error getting option expirations for {request.args.get('ticker', 'unknown')}: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))


@bp.route("/screening-config", methods=["GET"])
def get_screening_config():
    """
    Get screening engine configuration: growth mode state, OTM defaults, CSP profile.
    Used by the frontend to set default tabs, OTM values, and help text.
    """
    try:
        from api.services.config import get_config

        config = get_config()
        growth_mode = config.get("growth_mode", {})
        screener_profile = growth_mode.get("screener_profile", {})
        min_dte = screener_profile.get("csp_min_dte", 30)
        max_dte = screener_profile.get("csp_max_dte", 45)
        preferred_dte = screener_profile.get("csp_preferred_dte", 37)
        min_otm_pct = screener_profile.get("csp_min_otm_pct", 5)
        max_otm_pct = screener_profile.get("csp_max_otm_pct", 15)
        return success_response(
            {
                "growth_mode_enabled": bool(growth_mode.get("enabled", True)),
                "csp_default_otm_pct": screener_profile.get("csp_default_otm_pct", 10),
                "call_default_otm_pct": screener_profile.get("call_default_otm_pct", 10),
                "default_tab": "PUT",
                "csp_min_dte": min_dte,
                "csp_max_dte": max_dte,
                "csp_preferred_dte": preferred_dte,
                "csp_min_otm_pct": min_otm_pct,
                "csp_max_otm_pct": max_otm_pct,
                "csp_profile_summary": {
                    "delta": f"{screener_profile.get('csp_target_delta', 0.30):.2f}",
                    "delta_tolerance": f"{screener_profile.get('csp_delta_tolerance', 0.12):.2f}",
                    "dte_range": f"{min_dte}-{max_dte}",
                    "preferred_dte": preferred_dte,
                    "otm_range": f"{min_otm_pct}-{max_otm_pct}",
                    "otm_pct": screener_profile.get("csp_default_otm_pct", 10),
                    "min_volatility_pct": screener_profile.get("min_volatility_pct", 4.5),
                },
            }
        )
    except Exception as e:
        # Degraded mode: derive fallbacks from the default preset (single
        # source of truth) and say so, instead of presenting hand-copied
        # literals as live config (F-O3).
        logger.error(f"Error getting screening config: {e}")
        sp = get_preset(DEFAULT_PRESET_KEY).to_screener_profile()
        min_dte = int(sp.get("csp_min_dte", 30))
        max_dte = int(sp.get("csp_max_dte", 45))
        preferred_dte = int(sp.get("csp_preferred_dte", 37))
        min_otm_pct = int(sp.get("csp_min_otm_pct", 5))
        max_otm_pct = int(sp.get("csp_max_otm_pct", 15))
        return success_response(
            {
                "growth_mode_enabled": True,
                "csp_default_otm_pct": int(sp.get("csp_default_otm_pct", 10)),
                "call_default_otm_pct": int(sp.get("call_default_otm_pct", 10)),
                "default_tab": "PUT",
                "csp_min_dte": min_dte,
                "csp_max_dte": max_dte,
                "csp_preferred_dte": preferred_dte,
                "csp_min_otm_pct": min_otm_pct,
                "csp_max_otm_pct": max_otm_pct,
                "csp_profile_summary": {
                    "delta": f"{float(sp.get('csp_target_delta', 0.30)):.2f}",
                    "delta_tolerance": f"{float(sp.get('csp_delta_tolerance', 0.12)):.2f}",
                    "dte_range": f"{min_dte}-{max_dte}",
                    "preferred_dte": preferred_dte,
                    "otm_range": f"{min_otm_pct}-{max_otm_pct}",
                    "otm_pct": int(sp.get("csp_default_otm_pct", 10)),
                    "min_volatility_pct": 4.5,
                },
                "degraded": True,
            }
        )


@bp.route("/cash-status", methods=["GET"])
def get_cash_status():
    try:
        opend_error = _ensure_opend_available()
        if opend_error:
            return opend_error

        # This route forces a broker round-trip (refresh=True), so it shares
        # the broker-backed rate-limit budget (F-O1).
        allowed, retry_after = enforce_route_rate_limit(
            "cash-status", request.remote_addr or "local", max_requests=30, window_seconds=60
        )
        if not allowed:
            return error_response("Rate limit exceeded", status_code=429, retry_after=retry_after)

        # Delegate CSP affordability to PortfolioContext, which computes true
        # available cash minus reserved short-put collateral (margin buying
        # power is display-only and is never used for CSP capacity).
        service = get_options_service()
        payload = service.portfolio_context_helper.get_cash_status(refresh=True)

        return success_response(
            attach_source_policy(
                payload,
                build_account_source_policy("cash_status"),
            )
        )

    except Exception as e:
        logger.error(f"Error getting cash status: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))


@bp.route("/watchlist-tickers", methods=["GET"])
def get_watchlist_tickers():
    """
    Get the canonical merged watchlist union used by CSP recommendations.
    """
    try:
        from api.services.config import get_config
        from api.services.watchlist_manager import WatchlistManager

        config = get_config()
        manager = WatchlistManager(config_provider=config)
        effective_tickers = manager.get_effective_watchlist()
        return success_response(
            {
                "tickers": effective_tickers,
                "count": len(effective_tickers),
                "mode": config.get("watchlist_mode", "static"),
                "growth_mode_enabled": True,
            }
        )
    except Exception as e:
        logger.warning(f"Failed to get effective watchlist, falling back to static: {e}")
        config = current_app.config.get("connection_config", {})
        tickers = config.get("watchlist", [])
        return success_response(
            {
                "tickers": tickers,
                "count": len(tickers),
                "mode": "static_fallback",
            }
        )


@bp.route("/analytics/lifecycle", methods=["GET"])
def get_trade_lifecycle():
    logger.info("GET /analytics/lifecycle request received")

    try:
        from api.services.config import get_config
        from db.database import OptionsDatabase

        db = OptionsDatabase(get_config().get("db_path"))

        ticker = request.args.get("ticker")
        event_type = request.args.get("event_type")
        try:
            limit = int(request.args.get("limit", 100))
        except (TypeError, ValueError):
            return error_response("limit must be an integer", status_code=400)
        limit = min(max(limit, 0), 1000)  # negative LIMIT means unlimited in SQLite

        events = db.get_trade_events(ticker=ticker, event_type=event_type, limit=limit)
        analytics = db.get_trade_analytics()

        return success_response(
            {
                "events": events,
                "analytics": analytics,
                "count": len(events),
            }
        )
    except Exception as e:
        logger.error(f"Error fetching trade lifecycle: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))


@bp.route("/analytics/leakage", methods=["GET"])
def get_leakage_analytics():
    logger.info("GET /analytics/leakage request received")

    try:
        from api.services.config import get_config
        from db.database import OptionsDatabase

        db = OptionsDatabase(get_config().get("db_path"))
        analytics = db.get_trade_analytics()

        return success_response(
            {
                "analytics": {
                    "win_rate": analytics.get("win_rate", 0),
                    "avg_leakage": analytics.get("avg_leakage", 0),
                    "total_exits": analytics.get("total_exits", 0),
                    "wins": analytics.get("wins", 0),
                    "roll_count": analytics.get("roll_count", 0),
                    "per_symbol": analytics.get("per_symbol", []),
                }
            }
        )
    except Exception as e:
        logger.error(f"Error fetching leakage analytics: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))
