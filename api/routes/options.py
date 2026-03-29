"""
Options API routes
"""

from flask import Blueprint, request, jsonify, current_app
from api.services.options_service import OptionsService
from core.connection import probe_opend_status
from core.cache_manager import recommendation_cache, RecommendationCache
import traceback
import logging
import time
import json
import datetime
import threading

# Set up logger
logger = logging.getLogger('api.routes.options')

bp = Blueprint('options', __name__, url_prefix='/api/options')
options_service = OptionsService()


def _trigger_background_refresh(cache_key, limit, portfolio_hash):
    """
    Trigger a background refresh of recommendations after returning stale cache.
    """
    def refresh_task():
        try:
            logger.info(f"Background refresh started for {cache_key}")
            # Get fresh data
            result = options_service.get_top_recommendations(limit=limit)
            
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
    return jsonify({
        'error': status.get('message', 'OpenD is unavailable.'),
        'error_code': error_code,
        'opend_status': status
    }), 503


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
        if options_service.connection:
            conn_info = options_service.connection.get_connection_info()
        
        return jsonify({
            'success': True,
            'connection_pool': pool_stats,
            'service_connection': conn_info,
            'service_initialized': options_service.connection is not None
        })
    except Exception as e:
        logger.error(f"Error getting connection status: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


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
        return jsonify({"error": f"Invalid option_type: {option_type}. Must be 'CALL' or 'PUT'"}), 400
    
    # Use the existing module-level instance instead of creating a new one
    # Call the service with appropriate parameters including the new option_type and expiration
    result = options_service.get_otm_options(
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
        return jsonify({"error": "No tickers provided"}), 400
    
    # Split tickers on commas if multiple are provided
    tickers = [t.strip() for t in tickers_param.split(',')]
    
    # Get stock prices for the tickers
    prices = {}
    try:
        for ticker in tickers:
            if ticker:
                # Use the options service to get the stock price without option data
                price = options_service.get_stock_price(ticker)
                prices[ticker] = price
        
        return jsonify({
            "status": "success",
            "data": prices
        })
    except Exception as e:
        logger.error(f"Error getting stock price for {tickers_param}: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e), "status": "error"}), 500

@bp.route('/order', methods=['POST'])
def save_order():
    """
    Save an option order to the database
    """
    try:
        # Get order data from request
        order_data = request.json
        if not order_data:
            return jsonify({"error": "No order data provided"}), 400
            
        # Validate required fields
        required_fields = ['ticker', 'option_type', 'strike', 'expiration']
        for field in required_fields:
            if field not in order_data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        # Save order to database
        order_id = options_service.db.save_order(order_data)
        
        if order_id:
            return jsonify({"success": True, "order_id": order_id}), 201
        else:
            return jsonify({"error": "Failed to save order"}), 500
    except Exception as e:
        logger.error(f"Error saving order: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@bp.route('/pending-orders', methods=['GET'])
def get_pending_orders():
    """
    Get pending option orders from the database
    
    Query parameters:
        executed (bool): Whether to fetch executed orders (default: false)
        isRollover (bool): Whether to fetch only rollover orders (default: None = all orders)
    """
    try:
        # Get executed parameter (optional)
        executed = request.args.get('executed', 'false').lower() == 'true'
        
        # Get isRollover parameter (optional)
        is_rollover_param = request.args.get('isRollover')
        is_rollover = None
        if is_rollover_param is not None:
            is_rollover = is_rollover_param.lower() == 'true'
        
        # Get pending orders from database
        orders = options_service.db.get_pending_orders(executed=executed, isRollover=is_rollover)
        
        return jsonify({"orders": orders})
    except Exception as e:
        logger.error(f"Error getting pending orders: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@bp.route('/order/<int:order_id>', methods=['DELETE'])
def delete_order(order_id):
    """
    Delete/cancel an order from the database.
    
    Args:
        order_id (int): ID of the order to delete
        
    Returns:
        JSON response with success status
    """
    logger.info(f"DELETE /order/{order_id} request received")
    
    try:
        # Get the database instance
        db = current_app.config.get('database')
        if not db:
            logger.error("Database not initialized")
            return jsonify({"error": "Database not initialized"}), 500
            
        # Try to get the order first to ensure it exists
        order = db.get_order(order_id)
        if not order:
            logger.error(f"Order with ID {order_id} not found")
            return jsonify({"error": f"Order with ID {order_id} not found"}), 404
            
        # Delete the order
        success = db.delete_order(order_id)
        
        if success:
            logger.info(f"Order with ID {order_id} successfully deleted")
            return jsonify({"success": True, "message": f"Order with ID {order_id} deleted"}), 200
        else:
            logger.error(f"Failed to delete order with ID {order_id}")
            return jsonify({"error": "Failed to delete order"}), 500
            
    except Exception as e:
        logger.error(f"Error deleting order: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@bp.route('/execute/<int:order_id>', methods=['POST'])
def execute_order(order_id):
    """
    Execute an order by sending it to moomoo OpenD.
    
    Args:
        order_id (int): ID of the order to execute
        
    Returns:
        JSON response with execution details
    """
    logger.info(f"POST /execute/{order_id} request received")
    
    try:
        # Get the database instance
        db = current_app.config.get('database')
        if not db:
            logger.error("Database not initialized")
            return jsonify({"error": "Database not initialized"}), 500
            
        # Use the options service to execute the order
        response, status_code = options_service.execute_order(order_id, db)
        
        # Return the response from the service
        return jsonify(response), status_code
            
    except Exception as e:
        logger.error(f"Error executing order: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@bp.route('/check-orders', methods=['POST'])
def check_orders():
    """
    Check status of pending/processing orders with moomoo API and update them in the database.
    
    Returns:
        JSON response with updated orders
    """
    logger.info("POST /check-orders request received")
    
    try:
        # Use the options service to check and update order statuses
        response = options_service.check_pending_orders()
        
        # Return the response from the service
        return jsonify(response), 200
            
    except Exception as e:
        logger.error(f"Error checking orders: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@bp.route('/rollover', methods=['POST'])
def rollover_option():
    """
    Create orders to roll over an option position.
    
    This creates two orders:
    1. Buy order to close the current option position
    2. Sell order to open a new option position
    
    Returns:
        JSON response with created orders
    """
    logger.info("POST /rollover request received")
    
    try:
        # Get order data from request
        rollover_data = request.json
        if not rollover_data:
            return jsonify({"error": "No rollover data provided"}), 400
            
        # Validate required fields for current option
        required_fields = ['ticker', 'current_option_type', 'current_strike', 'current_expiration', 
                           'new_strike', 'new_expiration', 'quantity']
        for field in required_fields:
            if field not in rollover_data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        # Create buy order to close current position
        buy_order = {
            'ticker': rollover_data['ticker'],
            'option_type': rollover_data['current_option_type'],
            'strike': rollover_data['current_strike'],
            'expiration': rollover_data['current_expiration'],
            'action': 'BUY',  # Buy to close
            'quantity': rollover_data['quantity'],
            'order_type': rollover_data.get('current_order_type', 'MARKET'),
            'limit_price': rollover_data.get('current_limit_price'),  # Already per-contract from frontend
            'bid': rollover_data.get('current_bid', 0),
            'ask': rollover_data.get('current_ask', 0),
            'isRollover': True
        }
        
        # Create sell order for new position
        sell_order = {
            'ticker': rollover_data['ticker'],
            'option_type': rollover_data['current_option_type'],  # Same option type
            'strike': rollover_data['new_strike'],
            'expiration': rollover_data['new_expiration'],
            'action': 'SELL',  # Sell to open
            'quantity': rollover_data['quantity'],
            'order_type': rollover_data.get('new_order_type', 'LIMIT'),
            'limit_price': rollover_data.get('new_limit_price', 0) * 100,  # Convert from per-share to per-contract
            'bid': rollover_data.get('new_bid', 0),
            'ask': rollover_data.get('new_ask', 0),
            'isRollover': True
        }
        
        # Save orders to database
        buy_order_id = options_service.db.save_order(buy_order)
        sell_order_id = options_service.db.save_order(sell_order)
        
        if buy_order_id and sell_order_id:
            return jsonify({
                "success": True, 
                "buy_order_id": buy_order_id,
                "sell_order_id": sell_order_id,
                "message": "Rollover orders created successfully"
            }), 201
        else:
            return jsonify({"error": "Failed to create one or more rollover orders"}), 500
            
    except Exception as e:
        logger.error(f"Error creating rollover orders: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@bp.route('/cancel/<int:order_id>', methods=['POST'])
def cancel_order(order_id):
    """
    Cancel an order, including those being processed by IBKR.
    
    Args:
        order_id (int): ID of the order to cancel
        
    Returns:
        JSON response with cancellation details
    """
    logger.info(f"POST /cancel/{order_id} request received")
    
    try:
        # Use the options service to cancel the order
        response, status_code = options_service.cancel_order(order_id)
        
        # Return the response from the service
        return jsonify(response), status_code
            
    except Exception as e:
        logger.error(f"Error canceling order: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@bp.route('/order/<int:order_id>/quantity', methods=['PUT'])
def update_order_quantity(order_id):
    """
    Update the quantity of a specific order
    
    Args:
        order_id (int): ID of the order to update
        
    Returns:
        JSON response with success status
    """
    logger.info(f"PUT /order/{order_id}/quantity request received")
    
    try:
        # Get request data
        request_data = request.json
        if not request_data or 'quantity' not in request_data:
            logger.error("Missing quantity in request")
            return jsonify({"error": "Missing quantity in request"}), 400
            
        quantity = int(request_data['quantity'])
        if quantity <= 0:
            logger.error(f"Invalid quantity: {quantity}")
            return jsonify({"error": "Quantity must be greater than 0"}), 400
            
        # Get the database instance
        db = current_app.config.get('database')
        if not db:
            logger.error("Database not initialized")
            return jsonify({"error": "Database not initialized"}), 500
            
        # Try to get the order first to ensure it exists
        order = db.get_order(order_id)
        if not order:
            logger.error(f"Order with ID {order_id} not found")
            return jsonify({"error": f"Order with ID {order_id} not found"}), 404
            
        # Check if order is in editable state
        if order['status'] != 'pending':
            logger.error(f"Cannot update quantity for order with status '{order['status']}'")
            return jsonify({"error": f"Cannot update quantity for non-pending orders"}), 400
            
        # Update the order quantity
        success = db.update_order_quantity(order_id, quantity)
        
        if success:
            logger.info(f"Order with ID {order_id} quantity updated to {quantity}")
            return jsonify({
                "success": True, 
                "message": f"Order quantity updated to {quantity}",
                "order_id": order_id,
                "quantity": quantity
            }), 200
        else:
            logger.error(f"Failed to update quantity for order with ID {order_id}")
            return jsonify({"error": "Failed to update order quantity"}), 500
            
    except ValueError as ve:
        logger.error(f"Invalid quantity value: {str(ve)}")
        return jsonify({"error": "Invalid quantity value"}), 400
    except Exception as e:
        logger.error(f"Error updating order quantity: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@bp.route('/expirations', methods=['GET'])
def get_option_expirations():
    """
    Get available expiration dates for options of a given ticker.
    
    Query parameters:
        ticker (str): The ticker symbol (e.g., 'NVDA')
        option_type (str, optional): 'CALL' or 'PUT' to filter by preferred DTE ranges
                                    CALL: 5-35 days, PUT: 7-45 days
                                    If not provided, returns all future expirations
        
    Returns:
        JSON response with a list of available expiration dates
    """
    logger.info("GET /expirations request received")
    
    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response

        # Get ticker from request
        ticker = request.args.get('ticker')
        if not ticker:
            return jsonify({"error": "No ticker provided"}), 400
        
        # Get optional option_type parameter
        option_type = request.args.get('option_type')
        if option_type:
            option_type = option_type.upper()
            if option_type not in ['CALL', 'PUT']:
                return jsonify({"error": "option_type must be 'CALL' or 'PUT'"}), 400
            
        # Call the service method to get option expirations
        result = options_service.get_option_expirations(ticker, option_type)
        
        # Check if there was an error
        if "error" in result:
            error_message = result["error"]
            logger.error(f"Error getting expirations for {ticker}: {error_message}")
            return jsonify({"error": error_message}), 404
            
        # Return successful response
        return jsonify(result)
            
    except Exception as e:
        logger.error(f"Error getting option expirations for {request.args.get('ticker', 'unknown')}: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

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
        
        # Get portfolio context for cache key and hash calculation
        try:
            portfolio_context = options_service._get_portfolio_context()
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
        result = options_service.get_top_recommendations(limit=limit)
        
        if "error" in result:
            error_message = result["error"]
            logger.error(f"Error getting top recommendations: {error_message}")
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
        return jsonify({"error": str(e)}), 500
       
