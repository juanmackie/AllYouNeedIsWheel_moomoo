"""
Options API routes
"""

import datetime
import logging
import threading
import time
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
    opend_unavailable_response,
    success_response,
)
from core.cache_manager import RecommendationCache, recommendation_cache
from core.connection import probe_opend_status

# Set up logger
logger = logging.getLogger("api.routes.options")

bp = Blueprint("options", __name__, url_prefix="/api/options")

# Lazy service access - service created on first use
_options_service_instance = None
_generation_in_flight = {}  # cache_key -> started_at timestamp
_generation_in_flight_lock = threading.Lock()
GENERATION_IN_FLIGHT_TIMEOUT_SECONDS = 300


def get_options_service():
    """Get or create the options service instance."""
    global _options_service_instance
    if _options_service_instance is None:
        import api

        _options_service_instance = api.get_service("options")
    return _options_service_instance


def _generate_in_background(cache_key, limit, portfolio_hash):
    """
    Generate top recommendations in a background thread.
    Manages the in-flight registry so duplicate cold generations don't stack.
    Clears the in-flight flag on completion.
    """
    with _generation_in_flight_lock:
        if _generation_in_flight.get(cache_key):
            logger.info(f"Generation already in flight for {cache_key}, skipping")
            return False
        _generation_in_flight[cache_key] = time.time()

    def generation_task():
        try:
            logger.info(f"Background generation started for {cache_key}")
            result = get_options_service().get_top_recommendations(limit=limit)

            if "error" not in result:
                result = _normalize_top_recommendations_payload(result)
                recommendation_cache.set(cache_key, result, portfolio_hash)
                logger.info(f"Background generation completed for {cache_key}")
            else:
                recommendation_cache.mark_background_refresh_failed(cache_key)
                logger.error(f"Background generation failed for {cache_key}: {result['error']}")
        except Exception as e:
            recommendation_cache.mark_background_refresh_failed(cache_key)
            logger.error(f"Background generation exception for {cache_key}: {e}")
        finally:
            with _generation_in_flight_lock:
                _generation_in_flight.pop(cache_key, None)
            logger.info(f"In-flight flag cleared for {cache_key}")

    thread = threading.Thread(target=generation_task, daemon=True)
    thread.start()
    logger.info(f"Background generation thread started for {cache_key}")
    return True


def _get_generation_age(cache_key):
    with _generation_in_flight_lock:
        started_at = _generation_in_flight.get(cache_key)
    if not started_at:
        return None
    return time.time() - started_at


# Alias for backward compatibility
_trigger_background_refresh = _generate_in_background


def _normalize_top_recommendations_payload(payload):
    """Normalize legacy cached recommendation payloads to the signals contract."""
    if not isinstance(payload, dict):
        return payload

    normalized = dict(payload)
    if "signals" not in normalized:
        legacy_signals = list(normalized.get("recommendations") or normalized.get("best_plays") or [])
        if not legacy_signals and isinstance(normalized.get("lanes"), dict):
            lanes = normalized.get("lanes", {})
            for lane_key in ("covered_calls", "watchlist_csp", "long_calls", "long_puts"):
                lane = lanes.get(lane_key, {}) or {}
                legacy_signals.extend(lane.get("signals") or lane.get("recommendations") or [])
        if not legacy_signals:
            for lane_key in ("covered_calls", "watchlist_csps", "long_calls", "long_puts"):
                lane = normalized.get(lane_key, {}) or {}
                if isinstance(lane, dict):
                    legacy_signals.extend(lane.get("signals") or lane.get("recommendations") or [])
        normalized["signals"] = legacy_signals

    if "blocked_signals" not in normalized and normalized.get("blocked_candidates") is not None:
        normalized["blocked_signals"] = normalized.get("blocked_candidates", [])

    normalized.pop("recommendations", None)
    normalized.pop("best_plays", None)
    normalized.pop("lanes", None)
    normalized.pop("blocked_candidates", None)
    normalized["count"] = len(normalized.get("signals", []))
    return normalized


def _attach_top_recommendations_policy(payload):
    return attach_source_policy(
        payload,
        build_research_source_policy(
            "top_recommendations",
            payload,
            fallback_sources_allowed=[],
        ),
    )


def _build_top_recommendations_cache_response(payload, cache_metadata, cache_status):
    response_payload = _normalize_top_recommendations_payload(payload)
    response_payload["_cache"] = cache_metadata
    response_payload = _attach_top_recommendations_policy(response_payload)
    response = jsonify(response_payload)
    response.headers["X-Cache-Status"] = cache_status
    response.headers["X-Cache-Age"] = str(cache_metadata.get("cache_age_seconds", 0))
    return response, 200


def _build_top_recommendations_generating_response():
    return jsonify(
        _attach_top_recommendations_policy(
            {
                "generating": True,
                "count": 0,
                "signals": [],
                "message": "Signals are being computed. Check back shortly.",
            }
        )
    ), 202


def _build_top_recommendations_timeout_response():
    return jsonify(
        _attach_top_recommendations_policy(
            {
                "success": True,
                "generating": False,
                "generation_timed_out": True,
                "count": 0,
                "signals": [],
                "blocked_signals": [],
                "blocked_reason_counts": {},
                "generated_at": datetime.datetime.now().isoformat(),
                "message": "Signal generation is taking too long. Broker option-chain calls may be stalled.",
            }
        )
    ), 200


def _build_top_recommendations_stale_response(stale_result, stale_metadata, extra_cache_fields=None):
    stale_payload = _normalize_top_recommendations_payload(stale_result)
    cache_payload = {
        "cache_status": "STALE_FALLBACK",
        "cache_age_seconds": stale_metadata.get("cache_age_seconds", 0),
        "portfolio_changed": stale_metadata.get("portfolio_changed", False),
        "is_valid": True,
        "background_refresh_failed": False,
        "cached_at": stale_metadata.get("cached_at", ""),
    }
    if extra_cache_fields:
        cache_payload.update(extra_cache_fields)
    stale_payload["_cache"] = cache_payload
    stale_payload = _attach_top_recommendations_policy(stale_payload)
    response = jsonify(stale_payload)
    response.headers["X-Cache-Status"] = "STALE_FALLBACK"
    return response, 200


def _ensure_opend_available():
    connection_config = current_app.config.get("connection_config", {})
    status = probe_opend_status(
        host=connection_config.get("host", "127.0.0.1"), port=connection_config.get("port", 11111)
    )
    if status.get("status") == "connected":
        return None

    return opend_unavailable_response(status)


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


@bp.route("/top-recommendations", methods=["GET"])
def get_top_recommendations():
    """
    Get top N option signals across all portfolio positions.

    Returns the highest-scoring option opportunities (both calls and puts)
    filtered by capital availability and ranked by composite score.

    Query parameters:
        limit (int): Number of signals to return (default: 3, max: 10)

    Returns:
        JSON response with ranked signals
    """
    logger.info("GET /top-recommendations request received")

    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response
        allowed, retry_after = enforce_route_rate_limit(
            "top-recommendations", request.remote_addr or "local", max_requests=30, window_seconds=60
        )
        if not allowed:
            return error_response("Rate limit exceeded", status_code=429, retry_after=retry_after)

        # Get limit parameter (default 3)
        limit = request.args.get("limit", 3)
        try:
            limit = int(limit)
            if limit < 1:
                limit = 3
            elif limit > 10:
                limit = 10
        except (ValueError, TypeError):
            limit = 3

        # Use a cached portfolio snapshot for the cache key so we do not block
        # the request on a slow broker refresh.
        try:
            portfolio_context = get_options_service()._get_portfolio_context(refresh=False)
            current_portfolio_hash = RecommendationCache.calculate_portfolio_hash(portfolio_context)
        except Exception as e:
            logger.warning(f"Failed to get portfolio context for cache: {e}")
            portfolio_context = {}
            current_portfolio_hash = "no_portfolio"

        # Check for manual refresh parameter
        manual_refresh = request.args.get("refresh", "false").lower() == "true"

        # Cache key: limit + portfolio state (preset is part of config)
        cache_key = f"top_recommendations:limit={limit}:hash={current_portfolio_hash}"

        # Check cache unless manual refresh requested
        if not manual_refresh:
            cached_result, cache_metadata = recommendation_cache.get(cache_key, current_portfolio_hash)

            if cached_result is not None:
                logger.info(
                    f"Cache {cache_metadata['cache_status']} for top-recommendations "
                    f"(age={cache_metadata['cache_age_seconds']}s, "
                    f"portfolio_changed={cache_metadata['portfolio_changed']})"
                )

                if cache_metadata["cache_status"] == "STALE":
                    _trigger_background_refresh(cache_key, limit, current_portfolio_hash)

                return _build_top_recommendations_cache_response(
                    cached_result,
                    cache_metadata,
                    cache_metadata["cache_status"],
                )

        # Cache miss or manual refresh ? generate in background instead of blocking
        logger.info(f"Generating fresh top recommendations in background (manual_refresh={manual_refresh})")
        generation_started = _generate_in_background(cache_key, limit, current_portfolio_hash)
        generation_age = _get_generation_age(cache_key)
        # Try to serve stale cache while generation runs
        try:
            stale_result, stale_metadata = recommendation_cache.get(cache_key, current_portfolio_hash)
            if stale_result is not None and stale_metadata.get("cache_status") in ("STALE", "HIT"):
                logger.info("Returning stale cached signals while generating fresh data")
                return _build_top_recommendations_stale_response(stale_result, stale_metadata)
        except Exception:
            logger.warning("Stale cache fallback for top_recommendations failed", exc_info=True)
        # No stale data at all ? return generating signal immediately
        if not generation_started and generation_age and generation_age > GENERATION_IN_FLIGHT_TIMEOUT_SECONDS:
            logger.warning(
                "Top recommendations generation has been in flight for %.1fs; returning timeout diagnostic",
                generation_age,
            )
            return _build_top_recommendations_timeout_response()

        logger.info("No cache available ? returning generating signal to frontend")
        return _build_top_recommendations_generating_response()

    except Exception as e:
        logger.error(f"Error getting top recommendations: {str(e)}")
        logger.error(traceback.format_exc())
        # Last resort: try to return any stale cache
        try:
            stale_result, stale_metadata = recommendation_cache.get(cache_key, current_portfolio_hash)
            if stale_result is not None:
                logger.warning("Returning stale cached signals as last-resort fallback")
                response, status_code = _build_top_recommendations_stale_response(
                    stale_result,
                    stale_metadata,
                    {
                        "background_refresh_failed": True,
                        "error": str(e),
                    },
                )
                return response, status_code
        except Exception:
            logger.warning("Research stale cache fallback for top_recommendations failed", exc_info=True)
        return jsonify({"error": str(e)}), 500


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
        logger.error(f"Error getting screening config: {e}")
        return success_response(
            {
                "growth_mode_enabled": True,
                "csp_default_otm_pct": 10,
                "call_default_otm_pct": 10,
                "default_tab": "PUT",
                "csp_min_dte": 30,
                "csp_max_dte": 45,
                "csp_preferred_dte": 37,
                "csp_min_otm_pct": 5,
                "csp_max_otm_pct": 15,
                "csp_profile_summary": None,
            }
        )


@bp.route("/cash-status", methods=["GET"])
def get_cash_status():
    try:
        opend_error = _ensure_opend_available()
        if opend_error:
            return opend_error

        from api.services.portfolio_service import PortfolioService

        portfolio_service = PortfolioService()

        summary = portfolio_service.get_portfolio_summary() or {}
        option_positions = portfolio_service.get_positions("OPT") or []

        cash_balance = float(summary.get("cash_balance", summary.get("available_cash", 0)) or 0)
        true_cash = float(
            summary.get("available_cash", summary.get("cash_balance", summary.get("cash_available", 0))) or 0
        )
        if summary.get("buying_power") is not None:
            broker_buying_power_source = "buying_power"
        elif summary.get("excess_liquidity") is not None:
            broker_buying_power_source = "excess_liquidity"
        else:
            broker_buying_power_source = "available_cash"
        broker_buying_power = float(summary.get("buying_power", summary.get("excess_liquidity", true_cash)) or 0)
        excess_liquidity = float(summary.get("excess_liquidity", 0) or 0)
        available_cash = true_cash
        cash_reserved = 0.0
        open_puts = []

        for position in option_positions:
            pos_qty = int(position.get("position", 0) or 0)
            option_type = str(position.get("option_type", "") or "").upper()
            if pos_qty < 0 and option_type == "PUT":
                ticker = str(position.get("symbol", "") or "").replace("US.", "")
                strike = float(position.get("strike", 0) or 0)
                contracts = abs(pos_qty)
                expiration = position.get("expiration", "")
                cash_required = strike * 100 * contracts
                cash_reserved += cash_required
                open_puts.append(
                    {
                        "ticker": ticker,
                        "strike": strike,
                        "contracts": contracts,
                        "expiration": expiration,
                        "cash_required": round(cash_required, 2),
                    }
                )

        cash_available = max(0, available_cash - cash_reserved)
        cash_available_for_csp = max(0, broker_buying_power - cash_reserved)
        reserve_enabled = get_options_service().config.get("cash_reserve_enabled", True)

        return success_response(
            attach_source_policy(
                {
                    "cash_balance": round(cash_balance, 2),
                    "cash_reserved": round(cash_reserved, 2),
                    "cash_available": round(cash_available, 2),
                    "cash_available_for_csp": round(cash_available_for_csp, 2),
                    "cash_reserved_for_csp": round(cash_reserved, 2),
                    "broker_buying_power": round(broker_buying_power, 2),
                    "broker_buying_power_source": broker_buying_power_source,
                    "available_cash": round(available_cash, 2),
                    "excess_liquidity": round(excess_liquidity, 2),
                    "reserve_enabled": reserve_enabled,
                    "open_puts": open_puts,
                    "open_puts_count": len(open_puts),
                },
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
    Get the effective watchlist (includes dynamic/hybrid screening if configured).
    Returns the watchlist used for CSP recommendations and scanning.
    """
    try:
        from api.services.config import get_config
        from api.services.watchlist_manager import WatchlistManager

        config = get_config()
        manager = WatchlistManager(config_provider=config)
        growth_mode = config.get("growth_mode", {})
        effective_tickers = manager.get_effective_watchlist(growth_mode_config=growth_mode)
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
        limit = int(request.args.get("limit", 100))

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
