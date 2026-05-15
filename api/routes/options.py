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
    Trigger a background refresh of recommendations after returning stale cache.
    """
    def refresh_task():
        try:
            logger.info(f"Background refresh started for {cache_key}")
            # Get fresh data
            result = get_options_service().get_top_recommendations(limit=limit)
            
            if "error" not in result:
                # Cache the fresh data
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
    otm_percentage = float(request.args.get('otm', 10))
    option_type = request.args.get('optionType')  # Parameter for filtering by option type
    expiration = request.args.get('expiration')   # New parameter for filtering by expiration date
    
    # Validate option_type if provided
    if option_type and option_type not in ['CALL', 'PUT']:
        return error_response(f"Invalid option_type: {option_type}. Must be 'CALL' or 'PUT'", status_code=400)
    
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

@bp.route('/order', methods=['POST'])
def save_order():
    try:
        order_data = request.json
        if not order_data:
            return error_response("No order data provided", status_code=400)
        required_fields = ['ticker', 'option_type', 'strike', 'expiration']
        for field in required_fields:
            if field not in order_data:
                return error_response(f"Missing required field: {field}", status_code=400)
        order_id = get_options_service().db.save_order(order_data)
        if order_id:
            return success_response({"order_id": order_id}, status_code=201)
        return error_response("Failed to save order")
    except Exception as e:
        logger.error(f"Error saving order: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))

@bp.route('/pending-orders', methods=['GET'])
def get_pending_orders():
    """
    Get pending option orders from the database
    
    Query parameters:
        executed (bool): Whether to fetch executed orders (default: false)
        isRollover (bool): Whether to fetch only rollover orders (default: None = all orders)
    """
    try:
        executed = request.args.get('executed', 'false').lower() == 'true'
        is_rollover_param = request.args.get('isRollover')
        is_rollover = None
        if is_rollover_param is not None:
            is_rollover = is_rollover_param.lower() == 'true'
        orders = get_options_service().db.get_pending_orders(executed=executed, isRollover=is_rollover)
        return jsonify({"orders": orders})
    except Exception as e:
        logger.error(f"Error getting pending orders: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))

@bp.route('/order/<int:order_id>', methods=['DELETE'])
def delete_order(order_id):
    logger.info(f"DELETE /order/{order_id} request received")
    
    try:
        db = current_app.config.get('database')
        if not db:
            return error_response("Database not initialized")
        order = db.get_order(order_id)
        if not order:
            return error_response(f"Order with ID {order_id} not found", status_code=404)
        success = db.delete_order(order_id)
        if success:
            return success_response({"message": f"Order with ID {order_id} deleted"})
        return error_response("Failed to delete order")
    except Exception as e:
        logger.error(f"Error deleting order: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))

@bp.route('/execute/<int:order_id>', methods=['POST'])
def execute_order(order_id):
    logger.info(f"POST /execute/{order_id} request received")
    
    try:
        db = current_app.config.get('database')
        if not db:
            return error_response("Database not initialized")
        response, status_code = get_options_service().execute_order(order_id, db)
        return jsonify(response), status_code
    except Exception as e:
        logger.error(f"Error executing order: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))

@bp.route('/check-orders', methods=['POST'])
def check_orders():
    logger.info("POST /check-orders request received")
    
    try:
        response = get_options_service().check_pending_orders()
        return jsonify(response), 200
    except Exception as e:
        logger.error(f"Error checking orders: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))

@bp.route('/rollover', methods=['POST'])
def rollover_option():
    logger.info("POST /rollover request received")
    
    try:
        rollover_data = request.json
        if not rollover_data:
            return error_response("No rollover data provided", status_code=400)
            
        required_fields = ['ticker', 'current_option_type', 'current_strike', 'current_expiration', 
                           'new_strike', 'new_expiration', 'quantity']
        for field in required_fields:
            if field not in rollover_data:
                return error_response(f"Missing required field: {field}", status_code=400)
        
        buy_order = {
            'ticker': rollover_data['ticker'],
            'option_type': rollover_data['current_option_type'],
            'strike': rollover_data['current_strike'],
            'expiration': rollover_data['current_expiration'],
            'action': 'BUY',
            'quantity': rollover_data['quantity'],
            'order_type': rollover_data.get('current_order_type', 'MARKET'),
            'limit_price': rollover_data.get('current_limit_price'),
            'bid': rollover_data.get('current_bid', 0),
            'ask': rollover_data.get('current_ask', 0),
            'isRollover': True
        }
        
        sell_order = {
            'ticker': rollover_data['ticker'],
            'option_type': rollover_data['current_option_type'],
            'strike': rollover_data['new_strike'],
            'expiration': rollover_data['new_expiration'],
            'action': 'SELL',
            'quantity': rollover_data['quantity'],
            'order_type': rollover_data.get('new_order_type', 'LIMIT'),
            'limit_price': rollover_data.get('new_limit_price', 0) * 100,
            'bid': rollover_data.get('new_bid', 0),
            'ask': rollover_data.get('new_ask', 0),
            'isRollover': True
        }
        
        buy_order_id = get_options_service().db.save_order(buy_order)
        sell_order_id = get_options_service().db.save_order(sell_order)
        
        if buy_order_id and sell_order_id:
            return success_response({
                "buy_order_id": buy_order_id,
                "sell_order_id": sell_order_id,
                "message": "Rollover orders created successfully"
            }, status_code=201)
        return error_response("Failed to create one or more rollover orders")
            
    except Exception as e:
        logger.error(f"Error creating rollover orders: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))

@bp.route('/cancel/<int:order_id>', methods=['POST'])
def cancel_order(order_id):
    logger.info(f"POST /cancel/{order_id} request received")
    
    try:
        response, status_code = get_options_service().cancel_order(order_id)
        return jsonify(response), status_code
    except Exception as e:
        logger.error(f"Error canceling order: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))

@bp.route('/order/<int:order_id>/quantity', methods=['PUT'])
def update_order_quantity(order_id):
    logger.info(f"PUT /order/{order_id}/quantity request received")
    
    try:
        request_data = request.json
        if not request_data or 'quantity' not in request_data:
            return error_response("Missing quantity in request", status_code=400)
            
        quantity = int(request_data['quantity'])
        if quantity <= 0:
            return error_response("Quantity must be greater than 0", status_code=400)
            
        db = current_app.config.get('database')
        if not db:
            return error_response("Database not initialized")
        order = db.get_order(order_id)
        if not order:
            return error_response(f"Order with ID {order_id} not found", status_code=404)
        if order['status'] != 'pending':
            return error_response("Cannot update quantity for non-pending orders", status_code=400)
            
        success = db.update_order_quantity(order_id, quantity)
        if success:
            return success_response({
                "message": f"Order quantity updated to {quantity}",
                "order_id": order_id,
                "quantity": quantity
            })
        return error_response("Failed to update order quantity")
            
    except ValueError:
        return error_response("Invalid quantity value", status_code=400)
    except Exception as e:
        logger.error(f"Error updating order quantity: {str(e)}")
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
    Get top N option recommendations across all portfolio positions.
    
    Returns the highest-scoring option opportunities (both calls and puts)
    filtered by capital availability and ranked by composite score.
    
    Query parameters:
        limit (int): Number of recommendations to return (default: 3, max: 10)
        
    Returns:
        JSON response with ranked recommendations
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
                    logger.warning("Returning stale cached recommendations as fallback")
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
                logger.warning("Returning stale cached recommendations as last-resort fallback")
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
        cash_available_for_csp = max(0, available_cash - cash_reserved)
        reserve_enabled = get_options_service().config.get('cash_reserve_enabled', True)
        
        return success_response({
            'cash_balance': round(cash_balance, 2),
            'cash_reserved': round(cash_reserved, 2),
            'cash_available': round(cash_available, 2),
            'cash_available_for_csp': round(cash_available_for_csp, 2),
            'cash_reserved_for_csp': round(cash_reserved, 2),
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
        effective_tickers = manager.get_effective_watchlist()
        return success_response({
            'tickers': effective_tickers,
            'count': len(effective_tickers),
            'mode': config.get('watchlist_mode', 'static'),
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


@bp.route('/prefilled-close', methods=['POST'])
def create_prefilled_close_order():
    logger.info("POST /prefilled-close request received")

    try:
        data = request.get_json()
        if not data:
            return error_response('No data provided', status_code=400)

        required = ['ticker', 'option_type', 'strike', 'expiration', 'quantity']
        missing = [f for f in required if not data.get(f)]
        if missing:
            return error_response(f'Missing fields: {", ".join(missing)}', status_code=400)

        ticker = data['ticker']
        option_type = data['option_type'].upper()
        strike = float(data['strike'])
        expiration = str(data['expiration'])
        quantity = int(data['quantity'])

        conn = get_options_service()._ensure_connection()
        if not conn:
            return error_response('Failed to connect to moomoo', status_code=503)

        try:
            chain = conn.get_option_chain(
                ticker, expiration,
                'P' if option_type == 'PUT' else 'C',
                target_strike=strike
            )
            if chain and chain.get('options'):
                matching = [opt for opt in chain['options'] if float(opt.get('strike', 0)) == strike]
                if matching:
                    contract = matching[0]
                    bid = float(contract.get('bid', 0) or 0)
                    ask = float(contract.get('ask', 0) or 0)
                    mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
                else:
                    bid = ask = mid_price = 0
            else:
                bid = ask = mid_price = 0
        except Exception as chain_err:
            logger.warning(f"Could not fetch option chain: {chain_err}")
            bid = ask = mid_price = 0

        limit_price = float(data.get('limit_price', 0) or 0)
        if limit_price <= 0 and mid_price > 0:
            limit_price = round(mid_price, 2)
        elif limit_price <= 0:
            limit_price = 0.05

        order = {
            'ticker': ticker, 'option_type': option_type, 'strike': strike,
            'expiration': expiration, 'action': 'BUY', 'quantity': quantity,
            'order_type': 'LIMIT', 'limit_price': limit_price,
            'bid': bid, 'ask': ask, 'mid_price': round(mid_price, 4),
        }

        return success_response({'quote': order})

    except Exception as e:
        logger.error(f"Error creating prefilled close order: {str(e)}")
        logger.error(traceback.format_exc())
        return error_response(str(e))
        
