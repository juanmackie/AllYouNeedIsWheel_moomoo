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
from core.connection import MoomooConnection
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
        self.connection = None
        db_path = self.config.get('db_path')
        self.db = OptionsDatabase(db_path)
        self.portfolio_service = None  # Will be initialized when needed
        
    def _ensure_connection(self):
        """
        Ensure that the moomoo connection exists and is connected.
        Reuses existing connection if already established.
        """
        try:
            # If we already have a connected instance, just return it
            if self.connection is not None and self.connection.is_connected():
                logger.debug("Reusing existing moomoo connection")
                return self.connection
            
            # If connection exists but is disconnected, try to reconnect
            if self.connection is not None:
                logger.info("Existing connection found but disconnected, attempting to reconnect")
                if self.connection.connect():
                    logger.info("Successfully reconnected to moomoo OpenD")
                    return self.connection
                else:
                    logger.warning("Failed to reconnect, will create new connection")
        
            # No connection or reconnection failed, create a new one
            logger.info("Creating new moomoo connection")
            
            self.connection = MoomooConnection(
                host=self.config.get('host', '127.0.0.1'),
                port=self.config.get('port', 11111),
                readonly=self.config.get('readonly', True)
            )
            
            # Try to connect with proper error handling
            if not self.connection.connect():
                logger.error("Failed to connect to moomoo OpenD")
                return None
            else:
                logger.info("Successfully connected to moomoo OpenD")
                return self.connection
        except Exception as e:
            logger.error(f"Error ensuring connection: {str(e)}")
            return None
        
    def _adjust_to_standard_strike(self, price):
        """
        Adjust a price to a standard strike price
        """
        return round(price)
      
    def execute_order(self, order_id, db):
        """
        Execute an order by sending it to moomoo
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
                
            # Get connection to moomoo
            conn = self._ensure_connection()
            if not conn:
                logger.error("Failed to connect to moomoo")
                return {
                    "success": False,
                    "error": "Failed to connect to moomoo"
                }, 500
                
            ticker = order.get('ticker')
            quantity = int(order.get('quantity', 0))
            action = order.get('action')
            
            # Extract option details
            expiry = order.get('expiration')
            strike = order.get('strike')
            option_type = order.get('option_type')
            
            if not all([expiry, strike, option_type]):
                return {
                    "success": False,
                    "error": "Missing option details (expiry, strike, or option_type)"
                }, 400
            
            # Find the moomoo option code
            option_code = conn.create_option_contract(ticker, expiry, strike, option_type)
            if not option_code:
                return {
                    "success": False,
                    "error": f"Failed to find moomoo option code for {ticker} {expiry} {strike} {option_type}"
                }, 400

            # Calculate limit price (similar logic as before but adapted)
            bid = float(order.get('bid', 0) or 0)
            ask = float(order.get('ask', 0) or 0)
            last = float(order.get('last', 0) or 0)

            if bid > 0 and ask > 0:
                limit_price = (bid + ask) / 2
            elif bid > 0:
                limit_price = bid
            elif last > 0:
                limit_price = last
            else:
                limit_price = 0.05

            limit_price = round(limit_price, 2)

            # Place order
            result = conn.place_order(option_code, quantity, action, limit_price)
            
            if not result:
                return {
                    "success": False,
                    "error": "Failed to place order in moomoo"
                }, 500

            logger.info(f"Order placed successfully in moomoo: {result}")

            # Update order status in database
            execution_details = {
                "ib_order_id": result.get('order_id'), # Keeping key name for compatibility
                "ib_status": result.get('status'),
                "filled": result.get('filled'),
                "remaining": result.get('remaining'),
                "avg_fill_price": result.get('avg_fill_price'),
                "limit_price": limit_price,
            }
            
            db.update_order_status(
                order_id=order_id,
                status="processing",
                executed=True,
                execution_details=execution_details
            )
            
            return {
                "success": True,
                "message": "Order sent to moomoo",
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
      
    def get_otm_options(self, ticker, otm_percentage=10, option_type=None, expiration=None):
        """
        Get option contracts that are OTM by the specified percentage
        """
        start_time = time.time()
        
        if option_type and option_type not in ['CALL', 'PUT']:
            return {'error': f"Invalid option_type: {option_type}. Must be 'CALL' or 'PUT'"}
            
        conn = self._ensure_connection()
        if not conn:
            return {'error': 'Failed to establish connection to moomoo'}
        
        tickers = [ticker]
        result = {}
        
        for ticker in tickers:
            try:
                ticker_data = self._process_ticker_for_otm(conn, ticker, otm_percentage, expiration, option_type)
                result[ticker] = ticker_data
            except Exception as e:
                logger.error(f"Error processing {ticker} for OTM options: {e}")
                result[ticker] = {"error": str(e)}
        
        elapsed = time.time() - start_time
        return {'data': result}
        
    def _process_ticker_for_otm(self, conn, ticker, otm_percentage, expiration=None, option_type=None):
        """
        Process a single ticker for OTM options
        """
        result = {}
        stock_price = conn.get_stock_price(ticker)
        
        if stock_price is None or stock_price <= 0:
            return {'error': 'Unable to obtain valid stock price from moomoo'}
                
        result['stock_price'] = stock_price
        
        # Get position information
        position_size = 0
        try:
            if self.portfolio_service is None:
                from api.services.portfolio_service import PortfolioService
                self.portfolio_service = PortfolioService()
            
            positions = self.portfolio_service.get_positions()
            for pos in positions:
                if pos.get('symbol') == ticker or pos.get('symbol') == f"US.{ticker}":
                    position_size = pos.get('position', 0)
                    break
        except Exception as e:
            logger.error(f"Error getting position for {ticker}: {e}")
        
        result['position'] = position_size
        
        # Calculate target strikes
        call_strike = self._adjust_to_standard_strike(stock_price * (1 + otm_percentage / 100))
        put_strike = self._adjust_to_standard_strike(stock_price * (1 - otm_percentage / 100))
        
        # Use provided expiration if available, otherwise get default
        target_expiration = expiration
        if not target_expiration:
            target_expiration = get_closest_friday().strftime('%Y%m%d')
        
        options_chains = []
        if not option_type or option_type == 'CALL':
            call_chain = conn.get_option_chain(ticker, target_expiration, 'C', call_strike)
            if call_chain:
                options_chains.append(call_chain)

        if not option_type or option_type == 'PUT':
            put_chain = conn.get_option_chain(ticker, target_expiration, 'P', put_strike)
            if put_chain:
                options_chains.append(put_chain)

        if not options_chains:
            return {'error': 'No options data available from moomoo'}

        formatted_data = self._process_options_chain(options_chains, ticker, stock_price, otm_percentage, option_type)
        result.update(formatted_data)
        
        return result

    def _process_options_chain(self, options_chains, ticker, stock_price, otm_percentage, option_type=None):
        """
        Process options chain data and format it
        """
        try:
            result = {
                'symbol': ticker,
                'stock_price': stock_price,
                'otm_percentage': otm_percentage,
                'calls': [],
                'puts': []
            }
            
            for chain in options_chains:
                options_list = chain.get('options', [])
                for option in options_list:
                    strike = option.get('strike', 0)
                    bid = option.get('bid', 0)
                    ask = option.get('ask', 0)
                    last = option.get('last', 0)

                    if last == 0:
                        last = (bid + ask) / 2 if bid > 0 or ask > 0 else 0.01

                    option_data = {
                        'symbol': f"{ticker}{option.get('expiration')}{'C' if option.get('option_type') == 'CALL' else 'P'}{int(strike)}",
                        'strike': strike,
                        'expiration': option.get('expiration'),
                        'option_type': option.get('option_type'),
                        'bid': bid,
                        'ask': ask,
                        'last': last,
                        'open_interest': int(option.get('open_interest', 0)),
                        'implied_volatility': round(option.get('implied_volatility', 0), 2),
                        'delta': round(option.get('delta', 0), 5),
                        'gamma': round(option.get('gamma', 0), 5),
                        'theta': round(option.get('theta', 0), 5),
                        'vega': round(option.get('vega', 0), 5)
                    }

                    # Add earnings data
                    if option.get('option_type') == 'CALL':
                        max_contracts = 1
                        premium_per_contract = last * 100
                        total_premium = premium_per_contract * max_contracts
                        return_on_capital = (total_premium / (strike * 100)) * 100 if strike > 0 else 0
                        
                        option_data.update({
                            'earnings_max_contracts': max_contracts,
                            'earnings_premium_per_contract': round(premium_per_contract, 2),
                            'earnings_total_premium': round(total_premium, 2),
                            'earnings_return_on_capital': round(return_on_capital, 2)
                        })
                        result['calls'].append(option_data)
                    else:
                        max_contracts = 1
                        premium_per_contract = last * 100
                        total_premium = premium_per_contract * max_contracts
                        return_on_cash = (total_premium / (strike * 100)) * 100 if strike > 0 else 0
                        
                        option_data.update({
                            'earnings_max_contracts': max_contracts,
                            'earnings_premium_per_contract': round(premium_per_contract, 2),
                            'earnings_total_premium': round(total_premium, 2),
                            'earnings_return_on_cash': round(return_on_cash, 2)
                        })
                        result['puts'].append(option_data)
            
            result['calls'] = sorted(result['calls'], key=lambda x: x['strike'])
            result['puts'] = sorted(result['puts'], key=lambda x: x['strike'])
            return result
        except Exception as e:
            logger.error(f"Error processing options chain: {e}")
            return {}

    def _sanitize_result(self, result):
        if not result or not isinstance(result, dict):
            return
        def sanitize_dict(d):
            if not isinstance(d, dict): return
            for key, value in d.items():
                if isinstance(value, float) and math.isnan(value): d[key] = 0
                elif isinstance(value, dict): sanitize_dict(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict): sanitize_dict(item)
        sanitize_dict(result)
        
    def check_pending_orders(self):
        """
        Check status of pending/processing orders in moomoo
        """
        try:
            orders = self.db.get_orders(status_filter=['pending', 'processing'], limit=50)
            if not orders:
                return {"success": True, "message": "No pending orders", "updated_orders": []}
                
            conn = self._ensure_connection()
            updated_orders = []
            for order in orders:
                ib_order_id = order.get('ib_order_id')
                if order.get('status') == 'processing' and ib_order_id:
                    status_info = conn.check_order_status(ib_order_id)
                    if status_info:
                        new_status = "processing"
                        executed = False
                        if status_info.get('status') in ['Filled', 'Cancelled', 'Dealt']:
                            new_status = "executed" if status_info.get('status') in ['Filled', 'Dealt'] else "canceled"
                            executed = True
                            
                        execution_details = {
                            "ib_order_id": ib_order_id,
                            "ib_status": status_info.get('status'),
                            "filled": status_info.get('filled', 0),
                            "remaining": status_info.get('remaining', 0),
                            "avg_fill_price": status_info.get('avg_fill_price', 0),
                            "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        }

                        self.db.update_order_status(order_id=order.get('id'), status=new_status, executed=executed, execution_details=execution_details)
                        updated_orders.append({**order, 'status': new_status, **execution_details})
            
            return {"success": True, "updated_orders": updated_orders}
        except Exception as e:
            logger.error(f"Error checking pending orders: {e}")
            return {"success": False, "error": str(e)}

    def cancel_order(self, order_id):
        """
        Cancel an order in moomoo
        """
        try:
            order = self.db.get_order(order_id)
            if not order: return {"success": False, "error": "Order not found"}, 404
            
            if order['status'] == 'processing' and order.get('ib_order_id'):
                conn = self._ensure_connection()
                res = conn.cancel_order(order.get('ib_order_id'))
                if res.get('success'):
                    self.db.update_order_status(order_id=order_id, status="canceled", executed=True)
                    return {"success": True, "message": "Order canceled"}, 200
            
            self.db.update_order_status(order_id=order_id, status="canceled", executed=True)
            return {"success": True, "message": "Order canceled"}, 200
        except Exception as e:
            return {"success": False, "error": str(e)}, 500

    def get_stock_price(self, ticker):
        conn = self._ensure_connection()
        return conn.get_stock_price(ticker) if conn else 0

    def get_option_expirations(self, ticker):
        """
        Get available expiration dates for options from moomoo
        """
        try:
            conn = self._ensure_connection()
            if not conn: return {"error": "No connection"}
            
            ticker = conn._format_symbol(ticker)
            ret, data = conn.quote_ctx.get_option_expiration_date(code=ticker)
            if ret != 0: return {"error": f"Failed to get expirations: {data}"}
            
            expirations = []
            for date in data['expiration_date'].tolist():
                expirations.append({
                    "value": date.replace('-', ''),
                    "label": date
                })
            return {"ticker": ticker, "expirations": expirations}
        except Exception as e:
            return {"error": str(e)}
