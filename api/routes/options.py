"""
Options API routes
"""

from flask import Blueprint, request, jsonify, current_app
from core.connection import probe_opend_status
from core.cache_manager import recommendation_cache, RecommendationCache
from api.routes.utils import error_response, success_response
import traceback
import logging
import time
import json
import datetime
import threading

# Set up logger
logger = logging.getLogger('api.routes.options')

bp = Blueprint('options', __name__, url_prefix='/api/options')

# Lazy service access - service created on first use
_options_service_instance = None


def get_options_service():
    """Get or create the options service instance."""
    global _options_service_instance
    if _options_service_instance is None:
        import api
        _options_service_instance = api.get_service('options')
    return _options_service_instance


def _trigger_background_refresh(cache_key, limit, portfolio_hash):
    """
    Trigger a background refresh of signals after returning stale cache.
    """
    def refresh_task():
        try:
            logger.info(f"Background refresh started for {cache_key}")
            # Get fresh data
            result = get_options_service().get_top_recommendations(limit=limit)
            
            if "error" not in result:
                # Cache the fresh data
                result = _normalize_top_recommendations_payload(result)
                recommendation_cache.set(cache_key, result, portfolio_hash)
                logger.info(f"Background refresh completed for {cache_key}")
            else:
                # Mark cache as invalid on failure
                recommendation_cache.mark_background_refresh_failed(cache_key)
                logger.error(f"Background refresh failed for {cache_key}: {result['error']}")
        except Exception as e:
            recommendation_cache.mark_background_refresh_failed(cache_key)
            logger.error(f"Background refresh exception for {cache_key}: {e}")
    
    # Start background thread
    thread = threading.Thread(target=refresh_task, daemon=True)
    thread.start()
    logger.info(f"Background refresh thread started for {cache_key}")


def _normalize_top_recommendations_payload(payload):
    """Normalize legacy cached recommendation payloads to the signals contract."""
    if not isinstance(payload, dict):
        return payload

    normalized = dict(payload)
    if 'signals' not in normalized:
        legacy_signals = list(normalized.get('recommendations') or normalized.get('best_plays') or [])
        if not legacy_signals and isinstance(normalized.get('lanes'), dict):
            lanes = normalized.get('lanes', {})
            for lane_key in ('covered_calls', 'watchlist_csp'):
                lane = lanes.get(lane_key, {}) or {}
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

def _ensure_opend_available():
    connection_config = current_app.config.get('connection_config', {})
    status = probe_opend_status(
        host=connection_config.get('host', '127.0.0.1'),
        port=connection_config.get('port', 11111)
    )
    if status.get('status') == 'connected':
        return None

    error_code = 'opend_login_required' if status.get('status') == 'login_required' else 'opend_unavailable'
    return error_response(status.get('message', 'OpenD is unavailable.'), status_code=503)


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
    
    return jsonify(result)

@bp.route('/stock-price', methods=['GET'])
def get_stock_price():
    """
    Get the current stock price for one or more tickers.
    This is a lightweight endpoint that only returns stock prices.
    """
    unavailable_response = _ensure_opend_available()
    if unavailable_response:
        return unavailable_response

    # Get ticker(s) from request
    tickers_param = request.args.get('tickers', '')
    if not tickers_param:
        return error_response("No tickers provided", status_code=400)
    
    # Split tickers on commas if multiple are provided
    tickers = [t.strip() for t in tickers_param.split(',')]
    
    # Get stock prices for the tickers
    prices = {}
    try:
        for ticker in tickers:
            if ticker:
                # Use the options service to get the stock price without option data
                price = get_options_service().get_stock_price(ticker)
                prices[ticker] = price
        
        return jsonify({
            "status": "success",
            "data": prices
        })
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

        ticker = request.args.get('ticker')
        if not ticker:
            return error_response("No ticker provided", status_code=400)
        
        option_type = request.args.get('option_type')
        if option_type:
            option_type = option_type.upper()
            if option_type not in ['CALL', 'PUT']:
                return error_response("option_type must be 'CALL' or 'PUT'", status_code=400)
            
        result = get_options_service().get_option_expirations(ticker, option_type)
        
        if "error" in result:
            logger.error(f"Error getting expirations for {ticker}: {result['error']}")
            return error_response(result["error"], status_code=404)
            
        return jsonify(result)
            
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
        
        # Get limit parameter (default 5)
        limit = request.args.get('limit', 5)
        try:
            limit = int(limit)
            if limit < 1:
                limit = 5
            elif limit > 10:
                limit = 10
        except (ValueError, TypeError):
            limit = 5
        
        # Get portfolio context for cache key and hash calculation
        try:
            portfolio_context = get_options_service()._get_portfolio_context()
            current_portfolio_hash = RecommendationCache.calculate_portfolio_hash(portfolio_context)
        except Exception as e:
            logger.warning(f"Failed to get portfolio context for cache: {e}")
            portfolio_context = {}
            current_portfolio_hash = "no_portfolio"
        
        # Check for manual refresh parameter
        manual_refresh = request.args.get('refresh', 'false').lower() == 'true'
        
        # Create cache key based on limit and portfolio state
        cache_key = f"top_recommendations:limit={limit}:hash={current_portfolio_hash}"
        
        # Check cache unless manual refresh requested
        if not manual_refresh:
            cached_result, cache_metadata = recommendation_cache.get(cache_key, current_portfolio_hash)
            
            if cached_result is not None:
                cached_result = _normalize_top_recommendations_payload(cached_result)
                # Cache hit - add metadata to response
                cached_result['_cache'] = cache_metadata
                
                response = jsonify(cached_result)
                response.headers['X-Cache-Status'] = cache_metadata['cache_status']
                response.headers['X-Cache-Age'] = str(cache_metadata['cache_age_seconds'])
                
                logger.info(f"Cache {cache_metadata['cache_status']} for top-recommendations "
                          f"(age={cache_metadata['cache_age_seconds']}s, "
                          f"portfolio_changed={cache_metadata['portfolio_changed']})")
                
                # If stale, trigger background refresh
                if cache_metadata['cache_status'] == 'STALE':
                    _trigger_background_refresh(cache_key, limit, current_portfolio_hash)
                
                return response, 200
        
        # Cache miss or manual refresh - get fresh data
        logger.info(f"Fetching fresh top recommendations (manual_refresh={manual_refresh})")
        try:
            result = get_options_service().get_top_recommendations(limit=limit)
        except Exception as e:
            logger.error(f"Error getting top recommendations (exception): {str(e)}")
            logger.error(traceback.format_exc())
            result = {"error": str(e)}

        if "error" in result:
            error_message = result["error"]
            logger.error(f"Error getting top recommendations: {error_message}")
            # Try to return stale cache as fallback before giving up
            try:
                stale_result, _ = recommendation_cache.get(cache_key, current_portfolio_hash)
                if stale_result is not None:
                    stale_result = _normalize_top_recommendations_payload(stale_result)
                    logger.warning("Returning stale cached signals as fallback")
                    stale_result['_cache'] = {
                        'cache_status': 'STALE_FALLBACK',
                        'cache_age_seconds': stale_result.get('_cache', {}).get('cache_age_seconds', 0),
                        'portfolio_changed': False,
                        'is_valid': True,
                        'background_refresh_failed': True,
                        'cached_at': stale_result.get('_cache', {}).get('cached_at', ''),
                        'error': error_message
                    }
                    response = jsonify(stale_result)
                    response.headers['X-Cache-Status'] = 'STALE_FALLBACK'
                    return response, 200
            except Exception:
                pass
            return jsonify({"error": error_message}), 500

        # Add cache metadata
        result = _normalize_top_recommendations_payload(result)
        result['_cache'] = {
            'cache_status': 'MISS',
            'cache_age_seconds': 0,
            'portfolio_changed': False,
            'is_valid': True,
            'background_refresh_failed': False,
            'cached_at': datetime.datetime.now().isoformat()
        }

        # Store in cache
        recommendation_cache.set(cache_key, result, current_portfolio_hash)
        logger.info(f"Cached fresh top recommendations for key={cache_key[:80]}...")

        # Return response with headers
        response = jsonify(result)
        response.headers['X-Cache-Status'] = 'MISS'
        response.headers['X-Cache-Age'] = '0'

        return response, 200

    except Exception as e:
        logger.error(f"Error getting top recommendations: {str(e)}")
        logger.error(traceback.format_exc())
        # Last resort: try to return any stale cache
        try:
            stale_result, _ = recommendation_cache.get(cache_key, current_portfolio_hash)
            if stale_result is not None:
                stale_result = _normalize_top_recommendations_payload(stale_result)
                logger.warning("Returning stale cached signals as last-resort fallback")
                stale_result['_cache'] = {
                    'cache_status': 'STALE_FALLBACK',
                    'cache_age_seconds': stale_result.get('_cache', {}).get('cache_age_seconds', 0),
                    'portfolio_changed': False,
                    'is_valid': True,
                    'background_refresh_failed': True,
                    'cached_at': stale_result.get('_cache', {}).get('cached_at', ''),
                    'error': str(e)
                }
                response = jsonify(stale_result)
                response.headers['X-Cache-Status'] = 'STALE_FALLBACK'
                return response, 200
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


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
                'min_iv_rank': screener_profile.get('min_iv_rank', 45),
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
        excess_liquidity = float(summary.get('excess_liquidity', 0) or 0)
        available_cash = max(cash_balance, excess_liquidity)
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
        
        cash_available = max(0, cash_balance - cash_reserved)
        cash_available_for_csp = max(0, available_cash)  # No subtraction — broker buying power is authoritative
        broker_buying_power = available_cash
        reserve_enabled = get_options_service().config.get('cash_reserve_enabled', True)
        
        return success_response({
            'cash_balance': round(cash_balance, 2),
            'cash_reserved': round(cash_reserved, 2),
            'cash_available': round(cash_available, 2),
            'cash_available_for_csp': round(cash_available_for_csp, 2),
            'cash_reserved_for_csp': round(cash_reserved, 2),
            'broker_buying_power': round(broker_buying_power, 2),
            'broker_buying_power_source': 'available_cash',
            'available_cash': round(available_cash, 2),
            'excess_liquidity': round(excess_liquidity, 2),
            'reserve_enabled': reserve_enabled,
            'open_puts': open_puts,
            'open_puts_count': len(open_puts)
        })
        
    except Exception as e:
        logger.error(f"Error getting cash status: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))

@bp.route('/vix-regime', methods=['GET'])
def get_vix_regime():
    logger.info("GET /vix-regime request received")
    
    try:
        regime = get_options_service()._get_vix_regime()
        
        return success_response({
            'vix_regime': regime
        })
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


@bp.route('/evaluator/stats', methods=['GET'])
def evaluator_stats():
    """Return evaluator summary statistics for the dashboard.

    Includes scheduler metadata, calibration summary, and feedback
    bias information so the dashboard can show one coherent status payload.
    """
    try:
        from core.evaluator import get_summary_stats, get_recent_outcomes
        from core.scheduler import get_scheduler_info
        from core.calibrator import get_latest_calibration
        from core.feedback_loop import get_feedback_summary

        stats = get_summary_stats()
        recent = get_recent_outcomes(days=30)
        scheduler_info = get_scheduler_info()
        latest_calibration = get_latest_calibration()
        feedback_summary = get_feedback_summary()

        calibration_data = None
        if latest_calibration:
            calibration_data = {
                'cycle': latest_calibration.get('cycle'),
                'loss': round(latest_calibration.get('loss', 0), 4) if latest_calibration.get('loss') else None,
                'samples': latest_calibration.get('samples'),
                'weights': {
                    'iv_adjusted': latest_calibration.get('w_iv_adjusted'),
                    'theta_delta': latest_calibration.get('w_theta_delta'),
                    'liquidity': latest_calibration.get('w_liquidity'),
                    'expected_value': latest_calibration.get('w_expected_value'),
                    'upside_or_buffer': latest_calibration.get('w_upside_buffer'),
                    'otm_fit': latest_calibration.get('w_otm_fit'),
                },
                'created_at': latest_calibration.get('created_at'),
            }

        return success_response({
            'stats': stats,
            'recent_outcomes': recent,
            'recent_count': len(recent),
            'scheduler': scheduler_info,
            'calibration': calibration_data,
            'feedback_summary': feedback_summary,
        })
    except Exception as e:
        logger.error(f"Error fetching evaluator stats: {e}")
        return success_response({
            'stats': {'total_recommendations': 0, 'resolved': 0},
            'recent_outcomes': [],
            'recent_count': 0,
            'scheduler': {'running': False, 'state': {}},
            'calibration': None,
            'feedback_summary': {},
        })


@bp.route('/evaluator/cron', methods=['POST'])
def evaluator_cron():
    """
    Trigger an evaluator cycle: check expired-but-unresolved recommendations
    and log outcomes.  Intended to be called by an external cron/scheduler.
    """
    try:
        from core.evaluator import run_evaluation_cycle
        result = run_evaluation_cycle()
        return success_response(result)
    except Exception as e:
        logger.error(f"Evaluator cron cycle failed: {e}")
        return error_response(str(e))


@bp.route('/feedback/biases', methods=['GET'])
def feedback_biases():
    """Return current factor bias multipliers from the feedback loop."""
    try:
        from core.feedback_loop import get_all_biases, get_feedback_summary
        biases = get_all_biases()
        summary = get_feedback_summary()
        return success_response({
            'biases': biases,
            'summary': summary,
        })
    except Exception as e:
        logger.error(f"Error fetching feedback biases: {e}")
        return success_response({'biases': [], 'summary': {}})


@bp.route('/feedback/events', methods=['GET'])
def feedback_events():
    """Return recent feedback events."""
    try:
        from core.feedback_loop import get_recent_events
        events = get_recent_events(limit=50)
        return success_response({'events': events})
    except Exception as e:
        logger.error(f"Error fetching feedback events: {e}")
        return success_response({'events': []})


@bp.route('/calibrator/run', methods=['POST'])
def calibrator_run():
    """Trigger a calibration cycle."""
    try:
        from core.calibrator import run_calibration_cycle
        result = run_calibration_cycle()
        return success_response(result)
    except Exception as e:
        logger.error(f"Calibration cycle failed: {e}")
        return error_response(str(e))


@bp.route('/calibrator/history', methods=['GET'])
def calibrator_history():
    """Return calibration history."""
    try:
        from core.calibrator import get_calibration_history
        history = get_calibration_history(limit=20)
        return success_response({'history': history})
    except Exception as e:
        logger.error(f"Error fetching calibration history: {e}")
        return success_response({'history': []})



