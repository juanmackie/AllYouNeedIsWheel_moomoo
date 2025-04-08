"""
Options API routes
"""

from flask import Blueprint, request, jsonify, current_app
from api.services.options_service import OptionsService
import traceback
import logging
import time
import json
import datetime

# Set up logger
logger = logging.getLogger('api.routes.options')

bp = Blueprint('options', __name__, url_prefix='/api/options')
options_service = OptionsService()

# Market status is now checked directly in the route functions

# Helper function to check market status with better error handling
@bp.route('/otm', methods=['GET'])
def otm_options():
    """
    Get option data based on OTM percentage from current price.
    """
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
    Execute an order by sending it to TWS.
    
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
    Check status of pending/processing orders with TWS API and update them in the database.
    
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
        
    Returns:
        JSON response with a list of available expiration dates
    """
    logger.info("GET /expirations request received")
    
    try:
        # Get ticker from request
        ticker = request.args.get('ticker')
        if not ticker:
            return jsonify({"error": "No ticker provided"}), 400
            
        # Ensure connection to IB
        conn = options_service._ensure_connection()
        if not conn:
            return jsonify({"error": "Failed to establish connection to IB"}), 500
            
        # Import is_market_hours directly from core module
        from core.utils import is_market_hours
        
        # Get market status
        is_market_open = is_market_hours()
        logger.info(f"Market is {'open' if is_market_open else 'closed'}")
        
        # Set appropriate market data type based on market status
        if not is_market_open:
            conn.set_market_data_type(2)  # Frozen data when market is closed
        else:
            conn.set_market_data_type(1)  # Live data when market is open
            
        # Create a Stock object for the ticker
        from ib_async import Stock
        stock = Stock(ticker, 'SMART', 'USD')
        conn.ib.qualifyContracts(stock)
        
        # Get option chains to find available expirations
        chains = conn.ib.reqSecDefOptParams(stock.symbol, '', stock.secType, stock.conId)
        
        if not chains:
            logger.error(f"No option chains found for {ticker}")
            return jsonify({"error": f"No option chains found for {ticker}"}), 404
            
        # Get the first exchange's data (typically SMART)
        chain = next((c for c in chains if c.exchange == 'SMART' and c.expirations), chains[0])
        
        # Extract and filter valid expirations (only future dates)
        today = datetime.datetime.now().strftime('%Y%m%d')
        valid_expirations = []
        
        if chain and chain.expirations:
            # Sort expirations chronologically
            valid_expirations = sorted([exp for exp in chain.expirations if exp >= today])
            
            # Format the dates for better readability (YYYYMMDD -> YYYY-MM-DD)
            formatted_expirations = []
            for exp in valid_expirations:
                if len(exp) == 8:  # YYYYMMDD format
                    formatted_exp = f"{exp[0:4]}-{exp[4:6]}-{exp[6:8]}"
                    formatted_expirations.append({
                        "value": exp,  # Original format for API use
                        "label": formatted_exp  # Formatted for display
                    })
            
            return jsonify({
                "ticker": ticker,
                "expirations": formatted_expirations
            })
        else:
            logger.error(f"No valid expirations found for {ticker}")
            return jsonify({"error": f"No valid expirations found for {ticker}"}), 404
            
    except Exception as e:
        logger.error(f"Error getting option expirations for {request.args.get('ticker', 'unknown')}: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500
       