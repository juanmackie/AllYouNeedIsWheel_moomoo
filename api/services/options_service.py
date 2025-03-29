"""
Options Service module
Handles options data retrieval and processing
"""

import logging
import math
import random
import time
from datetime import datetime, timedelta, time as datetime_time
import pandas as pd
from core.connection import IBConnection, Option, Stock, suppress_ib_logs
from core.utils import get_closest_friday, get_next_monthly_expiration, is_market_hours
from config import Config
from db.database import OptionsDatabase
import traceback
import concurrent.futures
from functools import partial
import json

logger = logging.getLogger('api.services.options')

class OptionsService:
    """
    Service for handling options data operations
    """
    def __init__(self):
        self.config = Config()
        logger.info(f"Options service using port: {self.config.get('port')}")
        self.connection = None
        db_path = self.config.get('db_path')
        self.db = OptionsDatabase(db_path)
        self.portfolio_service = None  # Will be initialized when needed
        
    def _ensure_connection(self):
        """
        Ensure that the IB connection exists and is connected.
        Reuses existing connection if already established.
        """
        try:
            if not is_market_hours():
                logger.info("Market is closed, skipping TWS connection")
                return None
            
            # If we already have a connected instance, just return it
            if self.connection is not None and self.connection.is_connected():
                logger.debug("Reusing existing TWS connection")
                return self.connection
            
            # If connection exists but is disconnected, try to reconnect with same client ID
            if self.connection is not None:
                logger.info("Existing connection found but disconnected, attempting to reconnect")
                if self.connection.connect():
                    logger.info("Successfully reconnected to TWS/IB Gateway with existing client ID")
                    return self.connection
                else:
                    logger.warning("Failed to reconnect with existing client ID, will create new connection")
        
            # No connection or reconnection failed, create a new one
            # Generate a unique client ID based on current timestamp and random number
            unique_client_id = int(time.time() % 10000) + random.randint(1000, 9999)
            logger.info(f"Creating new TWS connection with client ID: {unique_client_id}")
            
            port = self.config.get('port', 7497)
            logger.info(f"Connecting to TWS on port: {port}")
            
            self.connection = IBConnection(
                host=self.config.get('host', '127.0.0.1'),
                port=port,
                client_id=unique_client_id,  # Use the unique client ID instead of fixed ID 1
                timeout=self.config.get('timeout', 20),
                readonly=self.config.get('readonly', True)
            )
            
            # Try to connect with proper error handling
            if not self.connection.connect():
                logger.error("Failed to connect to TWS/IB Gateway")
                return None
            else:
                logger.info("Successfully connected to TWS/IB Gateway")
                return self.connection
        except Exception as e:
            logger.error(f"Error ensuring connection: {str(e)}")
            if "There is no current event loop" in str(e):
                logger.error("Asyncio event loop error - please check connection.py for proper handling")
            return None
        
    def _generate_mock_option_data(self, ticker, stock_price, otm_percentage, expiration, option_type=None):
        """
        Generate mock option data when real data is not available, using flattened data structure
        
        Args:
            ticker (str): Stock ticker symbol
            stock_price (float): Current stock price
            otm_percentage (float): Percentage out of the money
            expiration (str): Expiration date in YYYYMMDD format
            option_type (str, optional): Type of options to return ('CALL' or 'PUT'), if None returns both
            
        Returns:
            dict: Mock option data with flattened structure
        """
        logger.info(f"Generating mock option data for {ticker} at price {stock_price}, option_type={option_type}")
        
        # Get date information
        today = datetime.now()
        
        try:
            # Parse expiration date or use next monthly expiration if not provided
            if expiration:
                exp_date = datetime.strptime(expiration, '%Y%m%d')
            else:
                # Get next monthly expiration if none provided
                exp_date = get_next_monthly_expiration()
                expiration = exp_date.strftime('%Y%m%d')
            
            days_to_expiry = (exp_date - today).days
            if days_to_expiry < 0:
                days_to_expiry = 30  # Default to 30 days if expiration is invalid
                exp_date = today + timedelta(days=30)
                expiration = exp_date.strftime('%Y%m%d')
        except Exception as e:
            # Default to 30 days if expiration date is invalid
            logger.warning(f"Error parsing expiration date: {e}, using default")
            days_to_expiry = 30
            exp_date = today + timedelta(days=30)
            expiration = exp_date.strftime('%Y%m%d')
            
        # Calculate target strikes
        call_strike = round(stock_price * (1 + otm_percentage / 100), 2)
        put_strike = round(stock_price * (1 - otm_percentage / 100), 2)
        
        # Adjust to standard strike increments
        call_strike = self._adjust_to_standard_strike(call_strike)
        put_strike = self._adjust_to_standard_strike(put_strike)
        
        result = {
            'stock_price': stock_price,
            'expiration': expiration,
            'days_to_expiry': days_to_expiry,
            'otm_percentage': otm_percentage,
            'calls': [],
            'puts': []
        }
        
        # Calculate time value factor (more time = more extrinsic value)
        time_factor = min(1.0, days_to_expiry / 365)
        
        # Calculate implied volatility based on stock price and days to expiry
        # Higher stock prices and longer expirations typically have higher IV
        base_iv = 0.30  # 30% base IV
        
        # Generate call option data if requested
        if not option_type or option_type == 'CALL':
            # Calculate intrinsic value for call
            call_intrinsic = max(0, stock_price - call_strike)
            
            # Calculate IV with price factor for call
            call_price_factor = 1.0 + (abs(stock_price - call_strike) / stock_price) * 0.5
            call_iv = base_iv * call_price_factor * (1 + time_factor * 0.5)
            
            # Calculate extrinsic value based on IV, time, and distance from ATM
            call_atm_factor = 1.0 - min(1.0, abs(stock_price - call_strike) / stock_price)
            call_extrinsic = stock_price * call_iv * time_factor * call_atm_factor
            
            # Total option price
            call_price = call_intrinsic + call_extrinsic
            call_price = max(0.05, call_price)
            
            # Calculate delta for call
            call_delta = 0.5
            if stock_price > call_strike:
                call_delta = 0.6 + (0.4 * min(1.0, (stock_price - call_strike) / call_strike))
            else:
                call_delta = 0.4 * min(1.0, (stock_price / call_strike))
            
            # Generate bid/ask spread
            call_spread_factor = 0.05 + (0.15 * (1 - call_atm_factor))  # Wider spreads for further OTM options
            call_bid = round(call_price * (1 - call_spread_factor), 2)
            call_ask = round(call_price * (1 + call_spread_factor), 2)
            call_last = round((call_bid + call_ask) / 2, 2)
            
            # Calculate call option earnings data
            position_qty = 100  # Assume 100 shares per standard position
            max_contracts = int(position_qty / 100)  # Each contract represents 100 shares
            premium_per_contract = call_price * 100  # Premium per contract (100 shares)
            total_premium = premium_per_contract * max_contracts
            
            # Ensure we don't divide by zero or NaN
            if call_strike > 0 and max_contracts > 0:
                return_on_capital = (total_premium / (call_strike * 100 * max_contracts)) * 100
            else:
                return_on_capital = 0
            
            # Create call option data with flattened earnings data
            call_option = {
                'symbol': f"{ticker}{expiration}C{int(call_strike)}",
                'strike': call_strike,
                'expiration': expiration,
                'option_type': 'CALL',
                'bid': call_bid,
                'ask': call_ask,
                'last': call_last,
                'volume': int(random.uniform(100, 5000)),
                'open_interest': int(random.uniform(500, 20000)),
                'implied_volatility': round(call_iv * 100, 2) if call_iv is not None else 0,  # Convert to percentage
                'delta': round(call_delta, 5) if call_delta is not None else 0,
                'gamma': round(0.06 * call_atm_factor, 5) if call_atm_factor is not None else 0,
                'theta': round(-0.05 * call_price * time_factor, 5),
                'vega': round(0.1 * call_price * time_factor, 5),
                'is_mock': True,
                'earnings_max_contracts': max_contracts,
                'earnings_premium_per_contract': round(premium_per_contract, 2),
                'earnings_total_premium': round(total_premium, 2),
                'earnings_return_on_capital': round(return_on_capital, 2)
            }
            
            # Add to result
            result['calls'].append(call_option)
        
        # Generate put option data if requested
        if not option_type or option_type == 'PUT':
            # Calculate intrinsic value for put
            put_intrinsic = max(0, put_strike - stock_price)
            
            # Calculate IV with price factor for put
            put_price_factor = 1.0 + (abs(stock_price - put_strike) / stock_price) * 0.5
            put_iv = base_iv * put_price_factor * (1 + time_factor * 0.5)
            
            # Calculate extrinsic value based on IV, time, and distance from ATM
            put_atm_factor = 1.0 - min(1.0, abs(stock_price - put_strike) / stock_price)
            put_extrinsic = stock_price * put_iv * time_factor * put_atm_factor
            
            # Total option price
            put_price = put_intrinsic + put_extrinsic
            put_price = max(0.05, put_price)
            
            # Calculate delta for put
            put_delta = -0.5
            if stock_price < put_strike:
                put_delta = -0.6 - (0.4 * min(1.0, (put_strike - stock_price) / put_strike))
            else:
                put_delta = -0.4 * min(1.0, (put_strike / stock_price))
            
            # Generate bid/ask spread
            put_spread_factor = 0.05 + (0.15 * (1 - put_atm_factor))  # Wider spreads for further OTM options
            put_bid = round(put_price * (1 - put_spread_factor), 2)
            put_ask = round(put_price * (1 + put_spread_factor), 2)
            put_last = round((put_bid + put_ask) / 2, 2)
            
            # Calculate put option earnings data
            cash_required = put_strike * 100  # $100 per point of strike
            max_puts = int(1000 / cash_required) if cash_required > 0 else 1
            max_puts = max(1, max_puts)  # At least 1 contract
            premium_per_contract = put_price * 100  # Premium per contract (100 shares)
            total_premium = premium_per_contract * max_puts
            
            # Return on cash calculation
            if cash_required > 0:
                return_on_cash = (total_premium / (cash_required * max_puts)) * 100
            else:
                return_on_cash = 0
            
            # Create put option data with flattened earnings data
            put_option = {
                'symbol': f"{ticker}{expiration}P{int(put_strike)}",
                'strike': put_strike,
                'expiration': expiration,
                'option_type': 'PUT',
                'bid': put_bid,
                'ask': put_ask,
                'last': put_last,
                'volume': int(random.uniform(100, 5000)),
                'open_interest': int(random.uniform(500, 20000)),
                'implied_volatility': round(put_iv * 100, 2) if put_iv is not None else 0,  # Convert to percentage
                'delta': round(put_delta, 5) if put_delta is not None else 0,
                'gamma': round(0.06 * put_atm_factor, 5) if put_atm_factor is not None else 0,
                'theta': round(-0.05 * put_price * time_factor, 5),
                'vega': round(0.1 * put_price * time_factor, 5),
                'is_mock': True,
                'earnings_max_contracts': max_puts,
                'earnings_premium_per_contract': round(premium_per_contract, 2),
                'earnings_total_premium': round(total_premium, 2),
                'earnings_return_on_cash': round(return_on_cash, 2)
            }
            
            # Add to result
            result['puts'].append(put_option)
        
        return result
        
    def _adjust_to_standard_strike(self, price):
        """
        Adjust a price to a standard strike price
        
        Args:
            price (float): Price to adjust
            
        Returns:
            float: Adjusted standard strike price
        """
        return round(price)
      
    def execute_order(self, order_id, db):
        """
        Execute an order by sending it to TWS
        
        Args:
            order_id (int): The ID of the order to execute
            db: Database instance to retrieve and update order information
            
        Returns:
            dict: Execution result with status and details
        """
        logger.info(f"Executing order with ID {order_id}")
        
        try:
            # Try to get the order first to ensure it exists
            order = db.get_order(order_id)
            if not order:
                logger.error(f"Order with ID {order_id} not found")
                return {
                    "success": False,
                    "error": f"Order with ID {order_id} not found"
                }, 404
                
            # Check if order is in executable state
            if order['status'] != 'pending':
                logger.error(f"Cannot execute order with status '{order['status']}'")
                return {
                    "success": False,
                    "error": f"Cannot execute order with status '{order['status']}'. Only 'pending' orders can be executed."
                }, 400
                
            # Get connection to TWS
            suppress_ib_logs()
            
            # Use the existing connection method
            conn = self._ensure_connection()
            if not conn:
                logger.error("Failed to connect to TWS")
                return {
                    "success": False,
                    "error": "Failed to connect to TWS"
                }, 500
                
            # Get order details directly (no more nested JSON)
            ticker = order.get('ticker')
            if not ticker:
                conn.disconnect()
                return {
                    "success": False,
                    "error": "Missing ticker in order details"
                }, 400
                
            quantity = int(order.get('quantity', 0))
            if quantity <= 0:
                conn.disconnect()
                return {
                    "success": False,
                    "error": "Invalid quantity"
                }, 400
                
            order_type = 'LMT'
            action = order.get('action')
            
            # Extract option details
            expiry = order.get('expiration')
            strike = order.get('strike')
            option_type = order.get('option_type')
            
            if not all([expiry, strike, option_type]):
                conn.disconnect()
                return {
                    "success": False,
                    "error": "Missing option details (expiry, strike, or option_type)"
                }, 400
                
            # Get limit price - direct access from flattened structure
            try:
                # Get bid and ask directly from order fields
                bid = float(order.get('bid', 0))
                ask = float(order.get('ask', 0))
                
                # Calculate appropriate limit price
                if bid > 0 and ask > 0:
                    limit_price = (bid + ask) / 2
                elif bid > 0:
                    limit_price = bid
                elif ask > 0:
                    limit_price = ask
                else:
                    # Try premium as fallback
                    premium = float(order.get('premium', 0))
                    if premium > 0:
                        limit_price = premium
                    else:
                        limit_price = 0.05
                    
                # Ensure minimum price and round properly
                if limit_price < 0.01:
                    limit_price = 0.05
                limit_price = round(limit_price, 2)
                
            except (ValueError, TypeError) as e:
                logger.warning(f"Error calculating limit price: {e}. Using default.")
                limit_price = 0.05
            
            logger.debug(f"Final limit price: {limit_price}")
            
            # Create contract
            contract = conn.create_option_contract(
                symbol=ticker,
                expiry=expiry,
                strike=float(strike),
                option_type=option_type
            )
            
            if not contract:
                conn.disconnect()
                return {
                    "success": False,
                    "error": "Failed to create option contract"
                }, 500
                
            # Create order
            ib_order = conn.create_order(
                action=action,
                quantity=quantity,
                order_type=order_type,
                limit_price=limit_price
            )
            logger.debug(f"Created IB order: {ib_order}")
            if not ib_order:
                conn.disconnect()
                return {
                    "success": False,
                    "error": "Failed to create order"
                }, 500
                
            # Place order
            result = conn.place_order(contract, ib_order)
            conn.disconnect()
            
            if not result:
                return {
                    "success": False,
                    "error": "Failed to place order"
                }, 500
            logger.info(f"Order placed successfully: {result}")
            # Update order status in database
            execution_details = {
                "ib_order_id": result.get('order_id'),
                "ib_status": result.get('status'),
                "filled": result.get('filled'),
                "remaining": result.get('remaining'),
                "avg_fill_price": result.get('avg_fill_price'),
                "is_mock": result.get('is_mock', False)
            }
            
            # Update order status to 'processing'
            logger.info(f"Updating order {order_id} status to 'processing' with execution details: {execution_details}")
            update_result = db.update_order_status(
                order_id=order_id,
                status="processing",
                executed=True,  # Mark as executed since it's been sent to IBKR
                execution_details=execution_details
            )
            
            # Verify that the update was successful
            if update_result:
                logger.info(f"Order status update successful")
            else:
                logger.warning(f"Order status update may have failed. Checking current status...")
                current_order = db.get_order(order_id)
                if current_order:
                    logger.info(f"Current order status: {current_order.get('status')}, executed: {current_order.get('executed')}")
                else:
                    logger.error(f"Could not retrieve order {order_id} after update")
            
            logger.info(f"Order with ID {order_id} sent to TWS, IB order ID: {result.get('order_id')}")
            return {
                "success": True,
                "message": "Order sent to TWS",
                "order_id": order_id,
                "ib_order_id": result.get('order_id'),
                "status": "processing",
                "execution_details": execution_details
            }, 200
                
        except Exception as e:
            logger.error(f"Error executing order: {str(e)}")
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "error": str(e)
            }, 500
      
    def get_otm_options(self, ticker=None, otm_percentage=10, option_type=None):
        """
        Get option data based on OTM percentage from current price.
        
        Args:
            ticker (str, optional): Stock ticker symbol
            otm_percentage (int, optional): Percentage out of the money
            option_type (str, optional): Type of options to return ('CALL' or 'PUT'), if None returns both
            
        Returns:
            dict: Options data for the requested ticker
        """
        start_time = time.time()
        
        # Validate option_type if provided
        if option_type and option_type not in ['CALL', 'PUT']:
            logger.error(f"Invalid option_type: {option_type}. Must be 'CALL' or 'PUT'")
            return {'error': f"Invalid option_type: {option_type}. Must be 'CALL' or 'PUT'"}
            
        # Use _ensure_connection instead of creating a new connection each time
        conn = self._ensure_connection()
        if not conn:
            logger.error("Failed to establish connection to IB")
        
        is_market_open = is_market_hours()
        # If no tickers provided, get them from portfolio
        tickers = [ticker]
        if not tickers:
            logger.info("No tickers found, using default opportunity tickers for mock data")
            tickers = ['NVDA']
                
        expiration = get_closest_friday().strftime('%Y%m%d')
        # Process each ticker
        result = {}
        
        for ticker in tickers:
            try:
                ticker_data = self._process_ticker_for_otm(conn, ticker, otm_percentage, expiration, is_market_open, option_type)
                result[ticker] = ticker_data
            except Exception as e:
                logger.error(f"Error processing {ticker} for OTM options: {e}")
                logger.error(traceback.format_exc())
                result[ticker] = {"error": str(e)}
        
        elapsed = time.time() - start_time
        logger.info(f"Completed OTM-based options request in {elapsed:.2f}s, is_market_open={is_market_open}, option_type={option_type}")
        
        # Return the results
        return {'data': result}
        
    def _process_ticker_for_otm(self, conn, ticker, otm_percentage, expiration=None, is_market_open=None, option_type=None):
        """
        Process a single ticker for OTM options
        
        Args:
            conn: IB connection
            ticker (str): Stock ticker symbol
            otm_percentage (float): Percentage out of the money
            expiration (str, optional): Expiration date in YYYYMMDD format
            is_market_open (bool, optional): Whether the market is currently open
            option_type (str, optional): Type of options to return ('CALL' or 'PUT'), if None returns both
            
        Returns:
            dict: Processed options data for the ticker
        """
        logger.info(f"Processing {ticker} for {otm_percentage}% OTM options, option_type={option_type}")
        result = {}
        
        # Get stock price - either real or mock
        stock_price = None
        if conn and conn.is_connected() and is_market_open:
            try:
                logger.info(f"Attempting to get real-time stock price for {ticker}")
                stock_price = conn.get_stock_price(ticker)
                logger.info(f"Retrieved real-time stock price for {ticker}: ${stock_price}")
            except Exception as e:
                logger.error(f"Error getting real-time stock price for {ticker}: {e}")
                logger.error(traceback.format_exc())
        
        # If we don't have a stock price, use mock data
        if stock_price is None or not isinstance(stock_price, (int, float)) or stock_price <= 0:
            try:
                logger.info(f"Getting mock stock price for {ticker}")
                stock_data = self._get_mock_stock_data(ticker)
                stock_price = stock_data.get('last', 0)
                logger.info(f"Using mock stock price for {ticker}: ${stock_price}")
            except Exception as e:
                logger.error(f"Error getting mock stock price for {ticker}: {e}")
                logger.error(traceback.format_exc())
                stock_price = 100.0  # Default fallback price
        # Store stock price in result
        result['stock_price'] = stock_price
        
        # Get position information from portfolio
        position_size = 0
        try:
            # Import and use portfolio service to get position size if not already initialized
            if self.portfolio_service is None:
                from api.services.portfolio_service import PortfolioService
                self.portfolio_service = PortfolioService()
            
            # Get positions from portfolio service
            positions = self.portfolio_service.get_positions()
            
            # Find the matching ticker in positions
            for pos in positions:
                if pos.get('symbol') == ticker:
                    position_size = pos.get('position', 0)
                    logger.info(f"Found position for {ticker}: {position_size} shares")
                    break
            
            if position_size == 0:
                logger.info(f"No position found for {ticker}, using 0 shares")
        except Exception as e:
            logger.error(f"Error getting position for {ticker}: {e}")
            logger.error(traceback.format_exc())
        
        # Store position size in result
        result['position'] = position_size
        
        # Get options chain - either real or mock
        options_data = {}
        if conn and conn.is_connected() and is_market_open:
            try:
                logger.info(f"Attempting to get real-time options chain for {ticker}")
                # Calculate target strikes
                call_strike = round(stock_price * (1 + otm_percentage / 100), 2)
                put_strike = round(stock_price * (1 - otm_percentage / 100), 2)
                
                # Adjust to standard strike increments
                call_strike = self._adjust_to_standard_strike(call_strike)
                put_strike = self._adjust_to_standard_strike(put_strike)
                
                options = []
                
                # Get call options if requested
                if not option_type or option_type == 'CALL':
                    call_option = conn.get_option_chain(ticker, expiration, 'C', call_strike)
                    if call_option:
                        options.append(call_option)
                
                # Get put options if requested
                if not option_type or option_type == 'PUT':
                    put_option = conn.get_option_chain(ticker, expiration, 'P', put_strike)
                    if put_option:
                        options.append(put_option)
                
                if options:
                    logger.info(f"Successfully retrieved real-time options for {ticker}")
                    options_data = self._process_options_chain(options, ticker, stock_price, otm_percentage, option_type)
                    logger.info(f"Processed real-time options data for {ticker}")
                else:
                    logger.warning(f"Could not get real-time options chain for {ticker}")
            except Exception as e:
                logger.error(f"Error getting real-time options chain for {ticker}: {e}")
                logger.error(traceback.format_exc())
        
        # If we need to use mock data
        else:
            try:
                logger.info(f"Generating mock options data for {ticker} with {otm_percentage}% OTM")
                # Generate mock data with option type filtering
                options_data = self._generate_mock_option_data(ticker, stock_price, otm_percentage, expiration, option_type)
                logger.info(f"Successfully generated mock options data for {ticker}")
            except Exception as e:
                logger.error(f"Error generating mock options data for {ticker}: {e}")
                logger.error(traceback.format_exc())
                options_data = {'error': str(e)}
        
        # Add options data to result
        result.update(options_data)
        
        # Log summary of the results
        log_msg = f"Completed processing {ticker}"
        logger.info(log_msg)
        
        return result

    def _get_mock_stock_data(self, ticker):
        """Generate realistic mock stock data for a ticker"""
        # Use realistic default prices based on ticker
        default_prices = {
            'AAPL': 175.0,
            'MSFT': 410.0,
            'GOOGL': 150.0,
            'AMZN': 180.0,
            'META': 480.0,
            'TSLA': 175.0,
            'NVDA': 880.0,
            'AMD': 160.0,
            'INTC': 40.0,
            'SPY': 510.0,
            'QQQ': 430.0,
            'DIA': 380.0,
            'IWM': 210.0
        }
        
        # Get base price
        base_price = default_prices.get(ticker, 100.0)
        
        # Add small random variation (+/- 2%)
        variation = random.uniform(-0.02, 0.02)
        price = base_price * (1 + variation)
        
        # Round to 2 decimal places
        price = round(price, 2)
        
        # Create mock stock data structure
        return {
            'symbol': ticker,
            'last': price,
            'bid': round(price * 0.998, 2),  # 0.2% below last
            'ask': round(price * 1.002, 2),  # 0.2% above last
            'volume': random.randint(100000, 10000000),
            'open': round(price * (1 + random.uniform(-0.01, 0.01)), 2),
            'high': round(price * (1 + random.uniform(0, 0.015)), 2),
            'low': round(price * (1 - random.uniform(0, 0.015)), 2),
            'close': None,  # Not applicable for current day
            'is_mock': True
        } 

    def _process_options_chain(self, options_chains, ticker, stock_price, otm_percentage, option_type=None):
        """
        Process options chain data and format it with flattened structure
        
        Args:
            options_chains (list): List of option chain objects from IB
            ticker (str): Stock symbol
            stock_price (float): Current stock price
            otm_percentage (float): OTM percentage to filter strikes
            option_type (str): Type of options to return ('CALL' or 'PUT'), if None returns both
            
        Returns:
            dict: Formatted options data
        """
        try:
            if not options_chains:
                logger.error(f"No options data available for {ticker}")
                return {}
            
            result = {
                'symbol': ticker,
                'stock_price': stock_price,
                'otm_percentage': otm_percentage,
                'calls': [],
                'puts': []
            }
            
            # Process each option chain in the list
            for chain in options_chains:
                # Extract the list of options from the chain
                if not chain or 'options' not in chain:
                    logger.warning(f"Invalid option chain format for {ticker}: {chain}")
                    continue
                
                options_list = chain.get('options', [])
                
                # Process each option in the chain
                for option in options_list:
                    try:
                        # Skip if we're filtering by option type and this doesn't match
                        current_option_type = option.get('option_type')
                        if option_type and current_option_type:
                            if (option_type == 'CALL' and current_option_type != 'CALL') or \
                               (option_type == 'PUT' and current_option_type != 'PUT'):
                                continue
                        
                        # Calculate ATM factor for Greeks
                        strike = option.get('strike', 0)
                        # Handle NaN and missing values
                        bid = option.get('bid', 0)
                        ask = option.get('ask', 0)
                        last = option.get('last', 0)
                        
                        # If last is 0 or NaN, use mid price
                        if last == 0 or isinstance(last, float) and math.isnan(last):
                            last = (bid + ask) / 2 if bid > 0 or ask > 0 else 0.1
                        
                        # Handle NaN values for Greeks
                        iv = option.get('implied_volatility', 0)
                        if isinstance(iv, float) and math.isnan(iv):
                            iv = 0
                        
                        delta = option.get('delta', 0)
                        if isinstance(delta, float) and math.isnan(delta):
                            delta = 0
                        
                        gamma = option.get('gamma', 0)
                        if isinstance(gamma, float) and math.isnan(gamma):
                            gamma = 0
                        
                        theta = option.get('theta', 0)
                        if isinstance(theta, float) and math.isnan(theta):
                            theta = 0
                        
                        vega = option.get('vega', 0)
                        if isinstance(vega, float) and math.isnan(vega):
                            vega = 0
                        
                        open_interest = option.get('open_interest', 0)
                        if isinstance(open_interest, float) and math.isnan(open_interest):
                            open_interest = 0
                        
                        # Format option data with flattened structure
                        option_data = {
                            'symbol': f"{ticker}{option.get('expiration')}{'C' if option.get('option_type') == 'CALL' else 'P'}{int(strike)}",
                            'strike': strike,
                            'expiration': option.get('expiration'),
                            'option_type': option.get('option_type'),
                            'bid': bid,
                            'ask': ask,
                            'last': last,
                            'open_interest': int(open_interest),
                            'implied_volatility': round(iv * 100, 2) if iv is not None and iv < 1 and iv > 0 else (0 if iv is None else round(iv, 2)),  # Handle percentage vs decimal
                            'delta': round(delta, 5) if delta is not None else 0,
                            'gamma': round(gamma, 5) if gamma is not None else 0,
                            'theta': round(theta, 5) if theta is not None else 0,
                            'vega': round(vega, 5) if vega is not None else 0,
                            'is_mock': False
                        }
                        
                        # Calculate and add flattened earnings data based on option type 
                        if option.get('option_type') == 'CALL':
                            position_qty = 100  # Assume 100 shares per standard position
                            max_contracts = int(position_qty / 100)  # Each contract represents 100 shares
                            premium_per_contract = last * 100  # Premium per contract (100 shares)
                            total_premium = premium_per_contract * max_contracts
                            
                            # Ensure we don't divide by zero or NaN
                            if strike > 0 and max_contracts > 0:
                                return_on_capital = (total_premium / (strike * 100 * max_contracts)) * 100
                            else:
                                return_on_capital = 0
                            
                            # Add flattened earnings data
                            option_data['earnings_max_contracts'] = max_contracts
                            option_data['earnings_premium_per_contract'] = round(premium_per_contract, 2)
                            option_data['earnings_total_premium'] = round(total_premium, 2)
                            option_data['earnings_return_on_capital'] = round(return_on_capital, 2)
                            
                            # Add to calls list directly
                            result['calls'].append(option_data)
                            
                        elif option.get('option_type') == 'PUT':
                            position_value = strike * 100 * int(100 / 100)  # Cash needed to secure puts
                            max_contracts = 1 if strike <= 0 else int(position_value / (strike * 100))
                            premium_per_contract = last * 100  # Premium per contract
                            total_premium = premium_per_contract * max_contracts
                            
                            # Ensure we don't divide by zero or NaN
                            if position_value > 0:
                                return_on_cash = (total_premium / position_value) * 100
                            else:
                                return_on_cash = 0
                            
                            # Add flattened earnings data
                            option_data['earnings_max_contracts'] = max_contracts
                            option_data['earnings_premium_per_contract'] = round(premium_per_contract, 2)
                            option_data['earnings_total_premium'] = round(total_premium, 2)
                            option_data['earnings_return_on_cash'] = round(return_on_cash, 2)
                            
                            # Add to puts list directly
                            result['puts'].append(option_data)
                    
                    except Exception as e:
                        logger.error(f"Error processing individual option in chain for {ticker}: {str(e)}")
                        logger.error(traceback.format_exc())
            
            # Sort options by strike price
            result['calls'] = sorted(result['calls'], key=lambda x: x['strike'])
            result['puts'] = sorted(result['puts'], key=lambda x: x['strike'])
            
            # Final sanitization to ensure no NaN values exist in the result
            self._sanitize_result(result)
            
            return result
            
        except Exception as e:
            logger.error(f"Error processing options chain for {ticker}: {str(e)}")
            logger.error(traceback.format_exc())
            return {} 

    def _sanitize_result(self, result):
        """
        Sanitize the result dictionary by replacing any NaN values with 0
        
        Args:
            result (dict): The result dictionary to sanitize
        """
        if not result or not isinstance(result, dict):
            return
            
        # Helper function to recursively sanitize dictionaries
        def sanitize_dict(d):
            if not isinstance(d, dict):
                return
                
            for key, value in d.items():
                # Check if value is NaN
                if isinstance(value, float) and math.isnan(value):
                    d[key] = 0
                # Recursively sanitize nested dictionaries
                elif isinstance(value, dict):
                    sanitize_dict(value)
                # Sanitize items in lists
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            sanitize_dict(item)
        
        # Sanitize the entire result dictionary
        sanitize_dict(result)
        
    def check_pending_orders(self):
        """
        Check status of pending/processing orders and update them in the database
        by querying the TWS API for current status.
        
        Returns:
            dict: Result with updated orders
        """
        logger.info("Checking pending orders with TWS")
        
        try:
            # Get all pending and processing orders from database
            db = self.db
            try:
                orders = db.get_orders(
                    status_filter=['pending', 'processing'],
                    limit=50  # Limit to most recent orders
                )
                logger.info(f"Found {len(orders)} pending/processing orders to check")
                
                # Log details of each order at debug level
                for i, order in enumerate(orders):
                    logger.debug(f"Order {i+1}: ID={order.get('id')}, Status={order.get('status')}, " +
                                f"Executed={order.get('executed')}, IB ID={order.get('ib_order_id', 'None')}")
                    
            except Exception as db_error:
                logger.error(f"Error retrieving orders from database: {str(db_error)}")
                logger.error(traceback.format_exc())
                return {
                    "success": False,
                    "error": f"Database error: {str(db_error)}"
                }
            
            if not orders or len(orders) == 0:
                logger.info("No pending or processing orders found")
                return {
                    "success": True,
                    "message": "No pending or processing orders to check",
                    "updated_orders": []
                }
                
            # Connect to TWS
            conn = self._ensure_connection()
                
            updated_orders = []
            for order in orders:
                order_id = order.get('id')
                ib_order_id = order.get('ib_order_id')
                
                # Only check orders that have been submitted to IB
                if order.get('status') == 'processing' and ib_order_id:
                    try:
                        # Check status in TWS
                        ib_status = conn.check_order_status(ib_order_id)
                        
                        if ib_status:
                            # Determine new status based on IB status
                            new_status = "processing"  # Default if still being processed
                            executed = False  # Default not executed
                            
                            # Map IB status to our status
                            if ib_status.get('status') in ['Filled', 'ApiCancelled', 'Cancelled']:
                                if ib_status.get('status') == 'Filled':
                                    new_status = "executed"
                                    executed = True  # Mark as executed if filled
                                else:
                                    new_status = "canceled"
                                    executed = True  # Mark as executed if cancelled
                                    
                            # Update execution details
                            execution_details = {
                                "ib_order_id": ib_order_id,
                                "ib_status": ib_status.get('status'),
                                "filled": ib_status.get('filled', 0),
                                "remaining": ib_status.get('remaining', 0),
                                "avg_fill_price": ib_status.get('avg_fill_price', 0),
                                "commission": ib_status.get('commission', 0),
                                "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            }
                            
                            # Update database with new status
                            logger.info(f"Updating order {order_id} with new status: {new_status}, executed: {executed}")
                            update_result = db.update_order_status(
                                order_id=order_id,
                                status=new_status,
                                executed=executed,  # Set executed flag based on status
                                execution_details=execution_details
                            )
                            
                            if update_result:
                                logger.info(f"Successfully updated order {order_id} in database")
                                
                                # Add to list of updated orders
                                updated_order = order.copy()
                                updated_order['status'] = new_status
                                updated_order.update(execution_details)
                                updated_orders.append(updated_order)
                                
                                logger.info(f"Updated order {order_id} status to {new_status}, IB status: {ib_status.get('status')}")
                            else:
                                logger.error(f"Failed to update order {order_id} in database")
                                # Verify current order status
                                current_order = db.get_order(order_id)
                                if current_order:
                                    logger.info(f"Current order status: {current_order.get('status')}, " +
                                               f"executed: {current_order.get('executed')}")
                                else:
                                    logger.error(f"Could not find order {order_id} in database after update attempt")
                    except Exception as e:
                        logger.error(f"Error checking status for order {order_id}: {str(e)}")
                        logger.error(traceback.format_exc())
            
            # Disconnect from TWS
            if conn:
                conn.disconnect()
                
            return {
                "success": True,
                "message": f"Updated {len(updated_orders)} orders",
                "updated_orders": updated_orders
            }
                
        except Exception as e:
            logger.error(f"Error checking pending orders: {str(e)}")
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "error": str(e)
            }

    def cancel_order(self, order_id):
        """
        Cancel an order, supporting both pending and processing orders.
        If the order is processing on IBKR, it will attempt to cancel it via TWS API.
        Even if TWS cancellation fails, the order will still be marked as cancelled.
        
        Args:
            order_id (int): The ID of the order to cancel
            
        Returns:
            dict: Result with status and details
        """
        logger.info(f"Canceling order with ID {order_id}")
        
        try:
            # Get the order to check its current status
            db = self.db
            order = db.get_order(order_id)
            
            if not order:
                logger.error(f"Order with ID {order_id} not found")
                return {
                    "success": False,
                    "error": f"Order with ID {order_id} not found"
                }, 404
                
            # Check if order is in a cancelable state
            if order['status'] not in ['pending', 'processing']:
                logger.error(f"Cannot cancel order with status '{order['status']}'")
                return {
                    "success": False,
                    "error": f"Cannot cancel order with status '{order['status']}'. Only 'pending' or 'processing' orders can be canceled."
                }, 400
                
            # If the order is processing in IBKR, we need to cancel it there first
            if order['status'] == 'processing' and order.get('ib_order_id'):
                # Connect to TWS
                suppress_ib_logs()
                conn = None
                tws_cancel_success = False
                tws_error_message = None
                
                try:
                    conn = self._ensure_connection()
                    
                    if not conn:
                        logger.error("Failed to connect to TWS")
                        tws_error_message = "Failed to connect to TWS"
                    else:
                        # Call TWS API to cancel the order
                        ib_order_id = order.get('ib_order_id')
                        cancel_result = conn.cancel_order(ib_order_id)
                        
                        if not cancel_result.get('success', False):
                            logger.error(f"Failed to cancel order in TWS: {cancel_result.get('error')}")
                            tws_error_message = f"Failed to cancel order in TWS: {cancel_result.get('error')}"
                        else:
                            logger.info(f"Successfully requested cancellation in TWS for order ID {ib_order_id}")
                            
                            # Even if TWS accepts the cancellation request, the order might not be canceled immediately
                            # Check the actual status
                            ib_status = conn.check_order_status(ib_order_id)
                            
                            if ib_status.get('status') in ['PendingCancel', 'Cancelled', 'ApiCancelled']:
                                # Order is being canceled or already canceled in TWS
                                execution_details = {
                                    "ib_order_id": ib_order_id,
                                    "ib_status": ib_status.get('status'),
                                    "filled": ib_status.get('filled', 0),
                                    "remaining": ib_status.get('remaining', 0),
                                    "avg_fill_price": ib_status.get('avg_fill_price', 0),
                                    "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                }
                                
                                # Update order status in database
                                db.update_order_status(
                                    order_id=order_id,
                                    status="canceled",
                                    executed=True,  # Mark as executed since it's been fully processed
                                    execution_details=execution_details
                                )
                                
                                tws_cancel_success = True
                                
                            else:
                                # Order status doesn't indicate cancellation yet, but we requested it
                                execution_details = {
                                    "ib_order_id": ib_order_id,
                                    "ib_status": "PendingCancel",  # Force this status as we've requested cancellation
                                    "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                }
                                
                                # Update order status in database to indicate cancellation pending
                                db.update_order_status(
                                    order_id=order_id,
                                    status="canceled",  # Change from "canceling" to "canceled" to ensure it doesn't remain in progress
                                    executed=True,  # Mark as executed to remove from processing queue
                                    execution_details=execution_details
                                )
                                
                                tws_cancel_success = True
                
                except Exception as e:
                    logger.error(f"Error canceling order in TWS: {str(e)}")
                    logger.error(traceback.format_exc())
                    tws_error_message = f"Error canceling order in TWS: {str(e)}"
                
                finally:
                    # Clean up connection if it exists
                    if conn:
                        try:
                            conn.disconnect()
                        except:
                            pass
                    
                    # If TWS cancellation was successful, return the success response
                    if tws_cancel_success:
                        return {
                            "success": True,
                            "message": "Order canceled in TWS",
                            "order_id": order_id
                        }, 200
                    
                    # If we get here, TWS cancellation failed but we still want to mark the order as canceled
                    logger.warning(f"TWS cancellation failed, but marking order {order_id} as canceled in database")
                    
                    # Create execution details with the error
                    execution_details = {
                        "ib_order_id": order.get('ib_order_id'),
                        "ib_status": "ApiCancelled",  # Mark as API cancelled
                        "error": tws_error_message or "Unknown TWS error",
                        "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        "note": "Order marked as canceled in database despite TWS error"
                    }
                    
                    # Always update the order status in database regardless of TWS result
                    db.update_order_status(
                        order_id=order_id,
                        status="canceled",
                        executed=True,  # Mark as executed to remove from processing queue
                        execution_details=execution_details
                    )
                    
                    # Return partial success - we marked it as canceled in our system but TWS failed
                    return {
                        "success": True,
                        "message": "Order marked as canceled despite TWS error",
                        "order_id": order_id,
                        "tws_error": tws_error_message or "Unknown TWS error",
                        "warning": "Order may still be active in TWS"
                    }, 200
            
            # For pending orders, just update the database
            db.update_order_status(
                order_id=order_id,
                status="canceled",
                executed=True,  # Mark as executed since it's been fully processed
                execution_details={"last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            )
            
            logger.info(f"Order with ID {order_id} marked as canceled in database")
            return {
                "success": True,
                "message": "Order canceled",
                "order_id": order_id
            }, 200
                
        except Exception as e:
            logger.error(f"Error canceling order: {str(e)}")
            logger.error(traceback.format_exc())
            
            try:
                # Even in case of unexpected errors, try to mark the order as canceled
                execution_details = {
                    "error": str(e),
                    "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "note": "Order forcibly marked as canceled despite errors"
                }
                
                db.update_order_status(
                    order_id=order_id,
                    status="canceled",
                    executed=True,
                    execution_details=execution_details
                )
                
                logger.info(f"Order {order_id} forcibly marked as canceled despite errors")
                
                return {
                    "success": True,
                    "message": "Order forcibly marked as canceled despite errors",
                    "order_id": order_id,
                    "error": str(e)
                }, 200
                
            except Exception as inner_e:
                logger.error(f"Failed to forcibly cancel order: {str(inner_e)}")
                # If even this fails, return the original error
                return {
                    "success": False,
                    "error": str(e),
                    "secondary_error": str(inner_e)
                }, 500 