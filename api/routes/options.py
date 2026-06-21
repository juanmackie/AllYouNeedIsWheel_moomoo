"""
Options API routes
"""

from flask import Blueprint, request, jsonify, current_app
from core.connection import probe_opend_status
from core.cache_manager import recommendation_cache, RecommendationCache
from api.routes.source_policy import (
    attach_source_policy,
    build_account_source_policy,
    build_research_source_policy,
)
from api.routes.utils import (
    error_response,
    success_response,
    normalize_ticker_list,
    enforce_route_rate_limit,
    opend_unavailable_response,
)
import traceback
import logging
import time
import datetime
import threading

# Set up logger
logger = logging.getLogger('api.routes.options')

bp = Blueprint('options', __name__, url_prefix='/api/options')

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
        _options_service_instance = api.get_service('options')
    return _options_service_instance


def _generate_in_background(cache_key, limit, portfolio_hash, include_long_options=False, ignore_cash_limits=False, screener_overrides=None):
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
            result = get_options_service().get_top_recommendations(
                limit=limit,
                include_long_options=include_long_options,
                ignore_cash_limits=ignore_cash_limits,
                screener_overrides=screener_overrides or {},
            )

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
    if 'signals' not in normalized:
        legacy_signals = list(normalized.get('recommendations') or normalized.get('best_plays') or [])
        if not legacy_signals and isinstance(normalized.get('lanes'), dict):
            lanes = normalized.get('lanes', {})
            for lane_key in ('covered_calls', 'watchlist_csp', 'long_calls', 'long_puts'):
                lane = lanes.get(lane_key, {}) or {}
                legacy_signals.extend(lane.get('signals') or lane.get('recommendations') or [])
        if not legacy_signals:
            for lane_key in ('covered_calls', 'watchlist_csps', 'long_calls', 'long_puts'):
                lane = normalized.get(lane_key, {}) or {}
                if isinstance(lane, dict):
                    legacy_signals.extend(lane.get('signals') or lane.get('recommendations') or [])
        normalized['signals'] = legacy_signals

    if 'blocked_signals' not in normalized and normalized.get('blocked_candidates') is not None:
        normalized['blocked_signals'] = normalized.get('blocked_candidates', [])

    normalized.pop('recommendations', None)
    normalized.pop('best_plays', None)
    normalized.pop('lanes', None)
    normalized.pop('blocked_candidates', None)
    normalized['count'] = len(normalized.get('signals', []))
    return normalized


def _attach_top_recommendations_policy(payload):
    return attach_source_policy(
        payload,
        build_research_source_policy(
            'top_recommendations',
            payload,
            fallback_sources_allowed=['yfinance', 'openbb', 'alpha_vantage'],
        ),
    )


def _build_top_recommendations_cache_response(payload, cache_metadata, cache_status):
    response_payload = _normalize_top_recommendations_payload(payload)
    response_payload['_cache'] = cache_metadata
    response_payload = _attach_top_recommendations_policy(response_payload)
    response = jsonify(response_payload)
    response.headers['X-Cache-Status'] = cache_status
    response.headers['X-Cache-Age'] = str(cache_metadata.get('cache_age_seconds', 0))
    return response, 200


def _build_top_recommendations_generating_response():
    return jsonify(_attach_top_recommendations_policy({
        'generating': True,
        'count': 0,
        'signals': [],
        'message': 'Signals are being computed. Check back shortly.',
    })), 202


def _build_top_recommendations_timeout_response():
    return jsonify(_attach_top_recommendations_policy({
        'success': True,
        'generating': False,
        'generation_timed_out': True,
        'count': 0,
        'signals': [],
        'blocked_signals': [],
        'blocked_reason_counts': {},
        'generated_at': datetime.datetime.now().isoformat(),
        'message': 'Signal generation is taking too long. Broker option-chain calls may be stalled.',
    })), 200


def _build_top_recommendations_stale_response(stale_result, stale_metadata, extra_cache_fields=None):
    stale_payload = _normalize_top_recommendations_payload(stale_result)
    cache_payload = {
        'cache_status': 'STALE_FALLBACK',
        'cache_age_seconds': stale_metadata.get('cache_age_seconds', 0),
        'portfolio_changed': stale_metadata.get('portfolio_changed', False),
        'is_valid': True,
        'background_refresh_failed': False,
        'cached_at': stale_metadata.get('cached_at', ''),
    }
    if extra_cache_fields:
        cache_payload.update(extra_cache_fields)
    stale_payload['_cache'] = cache_payload
    stale_payload = _attach_top_recommendations_policy(stale_payload)
    response = jsonify(stale_payload)
    response.headers['X-Cache-Status'] = 'STALE_FALLBACK'
    return response, 200

def _ensure_opend_available():
    connection_config = current_app.config.get('connection_config', {})
    status = probe_opend_status(
        host=connection_config.get('host', '127.0.0.1'),
        port=connection_config.get('port', 11111)
    )
    if status.get('status') == 'connected':
        return None

    return opend_unavailable_response(status)


@bp.route('/connection-status', methods=['GET'])
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
        
        return success_response({
            'connection_pool': pool_stats,
            'service_connection': conn_info,
            'service_initialized': get_options_service().connection is not None
        })
    except Exception as e:
        logger.error(f"Error getting connection status: {e}")
        return error_response(str(e))


# Market status is now checked directly in the route functions

# Helper function to check market status with better error handling
@bp.route('/otm', methods=['GET'])
def otm_options():
    """
    Get option data based on OTM percentage from current price.
    """
    unavailable_response = _ensure_opend_available()
    if unavailable_response:
        return unavailable_response
    allowed, retry_after = enforce_route_rate_limit('otm', request.remote_addr or 'local', max_requests=60, window_seconds=60)
    if not allowed:
        return error_response("Rate limit exceeded", status_code=429, retry_after=retry_after)

    # Get parameters from request
    ticker = request.args.get('tickers')
    try:
        otm_percentage = float(request.args.get('otm', 10))
    except (TypeError, ValueError):
        return error_response("Invalid otm percentage", status_code=400)
    option_type = request.args.get('optionType')  # Parameter for filtering by option type
    if option_type:
        option_type = option_type.upper()
    expiration = request.args.get('expiration')   # New parameter for filtering by expiration date
    
    # Validate option_type if provided
    if option_type and option_type not in ['CALL', 'PUT']:
        return error_response(f"Invalid option_type: {option_type}. Must be 'CALL' or 'PUT'", status_code=400)

    valid_tickers, invalid_tickers = normalize_ticker_list(ticker or '')
    if invalid_tickers:
        return error_response(
            f"Invalid ticker(s): {', '.join(invalid_tickers)}",
            status_code=400,
        )
    ticker = valid_tickers[0] if valid_tickers else ''
    if not ticker:
        return error_response("No valid ticker provided", status_code=400)

    if option_type == 'PUT':
        connection_config = current_app.config.get('connection_config', {})
        growth_mode = connection_config.get('growth_mode', {})
        screener_profile = growth_mode.get('screener_profile', {})
        min_otm_pct = float(screener_profile.get('csp_min_otm_pct', 5))
        max_otm_pct = float(screener_profile.get('csp_max_otm_pct', 15))
        if otm_percentage < min_otm_pct or otm_percentage > max_otm_pct:
            return error_response(
                f"PUT OTM must be between {min_otm_pct:.0f}% and {max_otm_pct:.0f}% for Growth Mode CSPs",
                status_code=400,
            )
    
    # Use the existing module-level instance instead of creating a new one
    # Call the service with appropriate parameters including the new option_type and expiration
    result = get_options_service().get_otm_options(
        ticker=ticker,
        otm_percentage=otm_percentage,
        option_type=option_type,
        expiration=expiration
    )
    
    return jsonify(
        attach_source_policy(
            result,
            build_research_source_policy(
                'otm_options',
                result,
                fallback_sources_allowed=['yfinance'],
            ),
        )
    )

@bp.route('/stock-price', methods=['GET'])
def get_stock_price():
    """
    Get the current stock price for one or more tickers.
    This is a lightweight endpoint that only returns stock prices.
    """
    unavailable_response = _ensure_opend_available()
    if unavailable_response:
        return unavailable_response
    allowed, retry_after = enforce_route_rate_limit('stock-price', request.remote_addr or 'local', max_requests=60, window_seconds=60)
    if not allowed:
        return error_response("Rate limit exceeded", status_code=429, retry_after=retry_after)

    # Get ticker(s) from request
    tickers_param = request.args.get('tickers', '')
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
        
        return jsonify(attach_source_policy({
            "status": "success",
            "data": prices
        }, build_research_source_policy(
            'stock_prices',
            prices,
            fallback_sources_allowed=[],
        )))
    except Exception as e:
        logger.error(f"Error getting stock price for {tickers_param}: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))

@bp.route('/expirations', methods=['GET'])
def get_option_expirations():
    logger.info("GET /expirations request received")
    
    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response
        allowed, retry_after = enforce_route_rate_limit('expirations', request.remote_addr or 'local', max_requests=60, window_seconds=60)
        if not allowed:
            return error_response("Rate limit exceeded", status_code=429, retry_after=retry_after)

        ticker = request.args.get('ticker')
        valid_tickers, invalid_tickers = normalize_ticker_list(ticker or '')
        if invalid_tickers:
            return error_response(
                f"Invalid ticker(s): {', '.join(invalid_tickers)}",
                status_code=400,
            )
        if not valid_tickers:
            return error_response("No ticker provided", status_code=400)
        ticker = valid_tickers[0]
        
        option_type = request.args.get('option_type')
        if option_type:
            option_type = option_type.upper()
            if option_type not in ['CALL', 'PUT']:
                return error_response("option_type must be 'CALL' or 'PUT'", status_code=400)
            
        result = get_options_service().get_option_expirations(ticker, option_type)
        
        if "error" in result:
            logger.error(f"Error getting expirations for {ticker}: {result['error']}")
            return error_response(result["error"], status_code=404)
            
        return jsonify(
            attach_source_policy(
                result,
                build_research_source_policy(
                    'option_expirations',
                    result,
                    fallback_sources_allowed=['yfinance'],
                ),
            )
        )
            
    except Exception as e:
        logger.error(f"Error getting option expirations for {request.args.get('ticker', 'unknown')}: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))

@bp.route('/top-recommendations', methods=['GET'])
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
        allowed, retry_after = enforce_route_rate_limit('top-recommendations', request.remote_addr or 'local', max_requests=30, window_seconds=60)
        if not allowed:
            return error_response("Rate limit exceeded", status_code=429, retry_after=retry_after)
        
        # Get limit parameter (default 3)
        limit = request.args.get('limit', 3)
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
        manual_refresh = request.args.get('refresh', 'false').lower() == 'true'
        
        # Create cache key based on limit and portfolio state
        include_long_options = request.args.get('include_long_options', 'true').lower() == 'true'
        ignore_cash_limits = request.args.get('ignore_cash_limits', 'false').lower() == 'true'

        # Screener profile overrides (user-tunable from UI)
        screener_overrides = {}
        for param, key in [('csp_min_otm_pct', 'csp_min_otm_pct'), ('csp_max_otm_pct', 'csp_max_otm_pct'),
                           ('csp_min_dte', 'csp_min_dte'), ('csp_max_dte', 'csp_max_dte'),
                           ('csp_target_delta', 'csp_target_delta'), ('min_csp_buying_power', 'min_csp_buying_power'),
                           ('min_volatility_pct', 'min_volatility_pct')]:
            raw = request.args.get(param)
            if raw is not None:
                try:
                    screener_overrides[key] = float(raw)
                except (ValueError, TypeError):
                    pass
        screener_suffix = ':'.join(f'{k}={v}' for k, v in sorted(screener_overrides.items())) if screener_overrides else 'none'

        cache_key = (
            f"top_recommendations:limit={limit}:include_long_options={include_long_options}:"
            f"ignore_cash_limits={ignore_cash_limits}:screener={screener_suffix}:hash={current_portfolio_hash}"
        )
        
        # Check cache unless manual refresh requested
        if not manual_refresh:
            cached_result, cache_metadata = recommendation_cache.get(cache_key, current_portfolio_hash)

            if cached_result is not None:
                logger.info(
                    f"Cache {cache_metadata['cache_status']} for top-recommendations "
                    f"(age={cache_metadata['cache_age_seconds']}s, "
                    f"portfolio_changed={cache_metadata['portfolio_changed']})"
                )

                if cache_metadata['cache_status'] == 'STALE':
                    _trigger_background_refresh(
                        cache_key,
                        limit,
                        current_portfolio_hash,
                        include_long_options=include_long_options,
                        ignore_cash_limits=ignore_cash_limits,
                        screener_overrides=screener_overrides,
                    )

                return _build_top_recommendations_cache_response(
                    cached_result,
                    cache_metadata,
                    cache_metadata['cache_status'],
                )
        
        # Cache miss or manual refresh ? generate in background instead of blocking
        logger.info(f"Generating fresh top recommendations in background (manual_refresh={manual_refresh})")
        generation_started = _generate_in_background(
            cache_key,
            limit,
            current_portfolio_hash,
            include_long_options=include_long_options,
            ignore_cash_limits=ignore_cash_limits,
            screener_overrides=screener_overrides,
        )
        generation_age = _get_generation_age(cache_key)
        # Try to serve stale cache while generation runs
        try:
            stale_result, stale_metadata = recommendation_cache.get(cache_key, current_portfolio_hash)
            if stale_result is not None and stale_metadata.get('cache_status') in ('STALE', 'HIT'):
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
                        'background_refresh_failed': True,
                        'error': str(e),
                    },
                )
                return response, status_code
        except Exception:
            logger.warning("Research stale cache fallback for top_recommendations failed", exc_info=True)
        return jsonify({"error": str(e)}), 500


_catalyst_watch_svc = None
_catalyst_scan_lock = threading.Lock()
_catalyst_cache: dict[str, tuple[dict, float]] = {}
CATALYST_SYNC_WAIT_SECONDS = 25

def _catalyst_cache_key(limit, min_pn, min_volume, min_fresh, max_scan_tickers, max_expirations):
    """Build deterministic cache key from query parameters."""
    return f"{limit}:{min_pn}:{min_volume}:{min_fresh}:{max_scan_tickers}:{max_expirations}"

def _catalyst_empty_response(generated_at=None, thresholds=None, message=None, cache_age_seconds=None, served_from_cache=False):
    """Full-shape response when catalyst scanning has not produced data yet."""
    ts = (generated_at or datetime.datetime.now()).isoformat()
    from api.services.config import get_config
    _cfg = get_config()
    _cat = _cfg.get("catalyst_flow", {})
    fallback_thresholds = {}
    for k in ("min_premium_notional", "min_fresh_volume_ratio", "min_volume",
              "max_expirations", "max_dte", "max_scan_tickers"):
        fallback_thresholds[k] = _cat.get(k)
    if thresholds:
        fallback_thresholds.update({k: v for k, v in thresholds.items() if v is not None})
    return {
        "success": True,
        "enabled": True,
        "signals": [],
        "count": 0,
        "generated_at": ts,
        "research_only": True,
        "scanned": 0,
        "cache_hits": 0,
        "errors": [],
        "tickers_scanned": [],
        "candidate_count": 0,
        "rejected_by_threshold_count": 0,
        "elapsed_seconds": 0,
        "scan_pending": True,
        "message": message or "Catalyst scan is still waiting for broker flow data.",
        "thresholds": fallback_thresholds,
        "served_from_cache": served_from_cache,
        "cache_age_seconds": cache_age_seconds,
        "fresh_attempted": True,
        "fresh_succeeded": False,
        "last_successful_generated_at": None,
    }


def _add_freshness_metadata(payload, served_from_cache, cache_age_seconds, fresh_attempted, fresh_succeeded, last_successful_generated_at):
    enriched = dict(payload)
    enriched["served_from_cache"] = served_from_cache
    enriched["cache_age_seconds"] = cache_age_seconds
    enriched["fresh_attempted"] = fresh_attempted
    enriched["fresh_succeeded"] = fresh_succeeded
    enriched["last_successful_generated_at"] = last_successful_generated_at
    return enriched

@bp.route('/catalyst-watch', methods=['GET'])
def catalyst_watch():
    """..."""
    global _catalyst_watch_svc, _catalyst_cache
    try:
        from api.services.catalyst_flow_service import CatalystFlowService
        from api.services.config import get_config

        cfg = get_config()
        catalyst_cfg = cfg.get("catalyst_flow", {})

        limit = request.args.get('limit', 6, type=int)
        limit = max(1, min(limit, 20))

        refresh = request.args.get('refresh', 'false').lower() == 'true'
        min_pn = request.args.get('min_premium_notional', type=int)
        min_volume = request.args.get('min_volume', type=int)
        min_fresh = request.args.get('min_fresh_volume_ratio', type=float)
        max_scan_tickers = request.args.get('max_scan_tickers', type=int)
        max_expirations = request.args.get('max_expirations', type=int)

        logger.debug(
            "GET /catalyst-watch params: limit=%s refresh=%s min_pn=%s min_vol=%s min_fresh=%s max_tickers=%s max_exp=%s",
            limit, refresh, min_pn, min_volume, min_fresh, max_scan_tickers, max_expirations,
        )

        if not catalyst_cfg.get("enabled", True):
            payload = _add_freshness_metadata({
                "success": True,
                "enabled": False,
                "signals": [],
                "count": 0,
                "generated_at": datetime.datetime.now().isoformat(),
                "research_only": True,
                "scanned": 0,
                "cache_hits": 0,
                "errors": [],
                "tickers_scanned": [],
                "candidate_count": 0,
                "rejected_by_threshold_count": 0,
                "elapsed_seconds": 0,
                "thresholds": {
                    "min_premium_notional": catalyst_cfg.get("min_premium_notional", 1_000_000),
                    "min_fresh_volume_ratio": catalyst_cfg.get("min_fresh_volume_ratio", 5),
                    "min_volume": catalyst_cfg.get("min_volume", 500),
                    "max_expirations": catalyst_cfg.get("max_expirations", 3),
                    "max_dte": catalyst_cfg.get("max_dte", 60),
                    "max_scan_tickers": catalyst_cfg.get("max_scan_tickers", 12),
                },
            }, served_from_cache=False, cache_age_seconds=None, fresh_attempted=False, fresh_succeeded=False, last_successful_generated_at=None)
            logger.debug("GET /catalyst-watch: disabled ? fresh_attempted=false served_from_cache=false")
            return jsonify(attach_source_policy(
                payload,
                build_research_source_policy('catalyst_watch', {}, fallback_sources_allowed=['yfinance']),
            ))

        if _catalyst_watch_svc is None:
            wl_manager = None
            try:
                from api.services.watchlist_manager import WatchlistManager
                wl_manager = WatchlistManager(config_provider=cfg)
            except Exception:
                logger.warning("WatchlistManager init failed for catalyst_watch", exc_info=True)
            _catalyst_watch_svc = CatalystFlowService(
                config_provider=cfg, watchlist_provider=wl_manager,
            )

        ck = _catalyst_cache_key(limit, min_pn, min_volume, min_fresh, max_scan_tickers, max_expirations)
        now = time.time()

        def _apply_source(result):
            return jsonify(attach_source_policy(
                result,
                build_research_source_policy('catalyst_watch', result, fallback_sources_allowed=['yfinance']),
            ))

        # Fast cache hit (5s TTL) for non-refresh, same-parameter requests
        if not refresh and ck in _catalyst_cache:
            cached_result, cached_ts = _catalyst_cache[ck]
            if now - cached_ts < 5:
                cached_age = round(now - cached_ts, 1)
                payload = _add_freshness_metadata(
                    cached_result,
                    served_from_cache=True,
                    cache_age_seconds=cached_age,
                    fresh_attempted=False,
                    fresh_succeeded=True,
                    last_successful_generated_at=cached_result.get("generated_at"),
                )
                logger.debug(
                    "GET /catalyst-watch: fast cache hit (age=%ss) ? served_from_cache=true fresh_attempted=false",
                    cached_age,
                )
                return _apply_source(payload)

        # Background scan that stores to parameter-aware cache
        def _bg_scan():
            if not _catalyst_scan_lock.acquire(blocking=False):
                return
            try:
                result = _catalyst_watch_svc.get_signals(
                    limit=limit,
                    refresh=refresh,
                    min_premium_notional=min_pn,
                    min_volume=min_volume,
                    min_fresh_volume_ratio=min_fresh,
                    max_scan_tickers=max_scan_tickers,
                    max_expirations=max_expirations,
                )
                _catalyst_cache[ck] = (result, time.time())
            except Exception:
                logger.exception("Background catalyst scan failed")
            finally:
                _catalyst_scan_lock.release()

        # If cache exists for these exact params, return it immediately and refresh in background
        if ck in _catalyst_cache:
            cached_result, cached_ts = _catalyst_cache[ck]
            bagr_age = round(now - cached_ts, 1)
            if _catalyst_scan_lock.acquire(blocking=False):
                _catalyst_scan_lock.release()
                t = threading.Thread(target=_bg_scan, daemon=True)
                t.start()
            payload = _add_freshness_metadata(
                cached_result,
                served_from_cache=True,
                cache_age_seconds=bagr_age,
                fresh_attempted=True,
                fresh_succeeded=False,
                last_successful_generated_at=cached_result.get("generated_at"),
            )
            logger.debug(
                "GET /catalyst-watch: stale cache (age=%ss) + bg refresh ? served_from_cache=true fresh_attempted=true",
                bagr_age,
            )
            return _apply_source(payload)

        # No cache for these params: run scan synchronously with generous timeout
        t = threading.Thread(target=_bg_scan, daemon=True)
        t.start()
        t.join(timeout=CATALYST_SYNC_WAIT_SECONDS)

        if ck in _catalyst_cache:
            cached_result, _ = _catalyst_cache[ck]
            payload = _add_freshness_metadata(
                cached_result,
                served_from_cache=False,
                cache_age_seconds=None,
                fresh_attempted=True,
                fresh_succeeded=True,
                last_successful_generated_at=cached_result.get("generated_at"),
            )
            logger.debug(
                "GET /catalyst-watch: fresh scan succeeded ? served_from_cache=false fresh_succeeded=true",
            )
            return _apply_source(payload)

        empty = _catalyst_empty_response(
            thresholds={
                "min_premium_notional": min_pn if min_pn is not None else catalyst_cfg.get("min_premium_notional"),
                "min_fresh_volume_ratio": min_fresh if min_fresh is not None else catalyst_cfg.get("min_fresh_volume_ratio"),
                "min_volume": min_volume if min_volume is not None else catalyst_cfg.get("min_volume"),
                "max_expirations": max_expirations if max_expirations is not None else catalyst_cfg.get("max_expirations"),
                "max_dte": catalyst_cfg.get("max_dte"),
                "max_scan_tickers": max_scan_tickers if max_scan_tickers is not None else catalyst_cfg.get("max_scan_tickers"),
            },
            served_from_cache=False,
            cache_age_seconds=None,
        )
        logger.debug(
            "GET /catalyst-watch: no cache, scan timed out ? served_from_cache=false fresh_attempted=true fresh_succeeded=false",
        )
        return _apply_source(empty)
    except Exception as e:
        logger.error(f"Error in catalyst watch: {e}")
        logger.error(traceback.format_exc())
        return error_response(str(e))


@bp.route('/screening-config', methods=['GET'])
def get_screening_config():
    """
    Get screening engine configuration: growth mode state, OTM defaults, CSP profile.
    Used by the frontend to set default tabs, OTM values, and help text.
    """
    try:
        from api.services.config import get_config
        config = get_config()
        growth_mode = config.get('growth_mode', {})
        screener_profile = growth_mode.get('screener_profile', {})
        min_dte = screener_profile.get('csp_min_dte', 30)
        max_dte = screener_profile.get('csp_max_dte', 45)
        preferred_dte = screener_profile.get('csp_preferred_dte', 37)
        min_otm_pct = screener_profile.get('csp_min_otm_pct', 5)
        max_otm_pct = screener_profile.get('csp_max_otm_pct', 15)
        return success_response({
            'growth_mode_enabled': bool(growth_mode.get('enabled', True)),
            'csp_default_otm_pct': screener_profile.get('csp_default_otm_pct', 10),
            'call_default_otm_pct': screener_profile.get('call_default_otm_pct', 10),
            'default_tab': 'PUT',
            'csp_min_dte': min_dte,
            'csp_max_dte': max_dte,
            'csp_preferred_dte': preferred_dte,
            'csp_min_otm_pct': min_otm_pct,
            'csp_max_otm_pct': max_otm_pct,
            'csp_profile_summary': {
                'delta': f"{screener_profile.get('csp_target_delta', 0.30):.2f}",
                'delta_tolerance': f"{screener_profile.get('csp_delta_tolerance', 0.12):.2f}",
                'dte_range': f"{min_dte}-{max_dte}",
                'preferred_dte': preferred_dte,
                'otm_range': f"{min_otm_pct}-{max_otm_pct}",
                'otm_pct': screener_profile.get('csp_default_otm_pct', 10),
                'min_volatility_pct': screener_profile.get('min_volatility_pct', 4.5),
            }
        })
    except Exception as e:
        logger.error(f"Error getting screening config: {e}")
        return success_response({
            'growth_mode_enabled': True,
            'csp_default_otm_pct': 10,
            'call_default_otm_pct': 10,
            'default_tab': 'PUT',
            'csp_min_dte': 30,
            'csp_max_dte': 45,
            'csp_preferred_dte': 37,
            'csp_min_otm_pct': 5,
            'csp_max_otm_pct': 15,
            'csp_profile_summary': None,
        })

@bp.route('/cash-status', methods=['GET'])
def get_cash_status():
    try:
        opend_error = _ensure_opend_available()
        if opend_error:
            return opend_error
        
        from api.services.portfolio_service import PortfolioService
        portfolio_service = PortfolioService()
        
        summary = portfolio_service.get_portfolio_summary() or {}
        option_positions = portfolio_service.get_positions('OPT') or []
        
        cash_balance = float(summary.get('cash_balance', summary.get('available_cash', 0)) or 0)
        true_cash = float(summary.get('available_cash', summary.get('cash_balance', summary.get('cash_available', 0))) or 0)
        if summary.get('buying_power') is not None:
            broker_buying_power_source = 'buying_power'
        elif summary.get('excess_liquidity') is not None:
            broker_buying_power_source = 'excess_liquidity'
        else:
            broker_buying_power_source = 'available_cash'
        broker_buying_power = float(summary.get('buying_power', summary.get('excess_liquidity', true_cash)) or 0)
        excess_liquidity = float(summary.get('excess_liquidity', 0) or 0)
        available_cash = true_cash
        cash_reserved = 0.0
        open_puts = []
        
        for position in option_positions:
            pos_qty = int(position.get('position', 0) or 0)
            option_type = str(position.get('option_type', '') or '').upper()
            if pos_qty < 0 and option_type == 'PUT':
                ticker = str(position.get('symbol', '') or '').replace('US.', '')
                strike = float(position.get('strike', 0) or 0)
                contracts = abs(pos_qty)
                expiration = position.get('expiration', '')
                cash_required = strike * 100 * contracts
                cash_reserved += cash_required
                open_puts.append({
                    'ticker': ticker, 'strike': strike, 'contracts': contracts,
                    'expiration': expiration, 'cash_required': round(cash_required, 2)
                })
        
        cash_available = max(0, available_cash - cash_reserved)
        cash_available_for_csp = max(0, broker_buying_power - cash_reserved)
        reserve_enabled = get_options_service().config.get('cash_reserve_enabled', True)
        
        return success_response(attach_source_policy({
            'cash_balance': round(cash_balance, 2),
            'cash_reserved': round(cash_reserved, 2),
            'cash_available': round(cash_available, 2),
            'cash_available_for_csp': round(cash_available_for_csp, 2),
            'cash_reserved_for_csp': round(cash_reserved, 2),
            'broker_buying_power': round(broker_buying_power, 2),
            'broker_buying_power_source': broker_buying_power_source,
            'available_cash': round(available_cash, 2),
            'excess_liquidity': round(excess_liquidity, 2),
            'reserve_enabled': reserve_enabled,
            'open_puts': open_puts,
            'open_puts_count': len(open_puts)
        }, build_account_source_policy('cash_status')))
        
    except Exception as e:
        logger.error(f"Error getting cash status: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))

@bp.route('/vix-regime', methods=['GET'])
def get_vix_regime():
    logger.info("GET /vix-regime request received")
    
    try:
        regime = get_options_service()._get_vix_regime()
        
        return success_response(attach_source_policy({
            'vix_regime': regime
        }, build_research_source_policy(
            'vix_regime',
            regime,
            fallback_sources_allowed=['openbb', 'yfinance'],
        )))
    except Exception as e:
        logger.error(f"Error fetching VIX regime: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))


@bp.route('/watchlist-tickers', methods=['GET'])
def get_watchlist_tickers():
    """
    Get the effective watchlist (includes dynamic/hybrid screening if configured).
    Returns the watchlist used for CSP recommendations and scanning.
    """
    try:
        from api.services.watchlist_manager import WatchlistManager
        from api.services.config import get_config
        config = get_config()
        manager = WatchlistManager(config_provider=config)
        growth_mode = config.get('growth_mode', {})
        effective_tickers = manager.get_effective_watchlist(
            growth_mode_config=growth_mode
        )
        return success_response({
            'tickers': effective_tickers,
            'count': len(effective_tickers),
            'mode': config.get('watchlist_mode', 'static'),
            'growth_mode_enabled': True,
        })
    except Exception as e:
        logger.warning(f"Failed to get effective watchlist, falling back to static: {e}")
        config = current_app.config.get('connection_config', {})
        tickers = config.get('watchlist', [])
        return success_response({
            'tickers': tickers,
            'count': len(tickers),
            'mode': 'static_fallback',
        })


@bp.route('/analytics/lifecycle', methods=['GET'])
def get_trade_lifecycle():
    logger.info("GET /analytics/lifecycle request received")

    try:
        from db.database import OptionsDatabase
        from api.services.config import get_config

        db = OptionsDatabase(get_config().get('db_path'))

        ticker = request.args.get('ticker')
        event_type = request.args.get('event_type')
        limit = int(request.args.get('limit', 100))

        events = db.get_trade_events(ticker=ticker, event_type=event_type, limit=limit)
        analytics = db.get_trade_analytics()

        return success_response({
            'events': events,
            'analytics': analytics,
            'count': len(events),
        })
    except Exception as e:
        logger.error(f"Error fetching trade lifecycle: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))


@bp.route('/analytics/leakage', methods=['GET'])
def get_leakage_analytics():
    logger.info("GET /analytics/leakage request received")

    try:
        from db.database import OptionsDatabase
        from api.services.config import get_config

        db = OptionsDatabase(get_config().get('db_path'))
        analytics = db.get_trade_analytics()

        return success_response({
            'analytics': {
                'win_rate': analytics.get('win_rate', 0),
                'avg_leakage': analytics.get('avg_leakage', 0),
                'total_exits': analytics.get('total_exits', 0),
                'wins': analytics.get('wins', 0),
                'roll_count': analytics.get('roll_count', 0),
                'per_symbol': analytics.get('per_symbol', []),
            }
        })
    except Exception as e:
        logger.error(f"Error fetching leakage analytics: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))

