"""
Order Executor module - handles order execution and management
Extracted from the monolithic options_service.py for maintainability.
"""

import logging
import traceback
from datetime import datetime
from db.database import OptionsDatabase

logger = logging.getLogger('api.services.order_executor')


class OrderExecutor:
    """
    Handles order execution, checking, and cancellation.
    """
    
    def __init__(self, connection_provider, db, portfolio_service_provider=None):
        self._connection_provider = connection_provider
        self.db = db
        self._portfolio_service_provider = portfolio_service_provider
        
    def _get_connection(self):
        return self._connection_provider._ensure_connection()
    
    def _get_portfolio_service(self):
        if self._portfolio_service_provider:
            return self._portfolio_service_provider.portfolio_service
        return None
    
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
            conn = self._get_connection()
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
                "moomoo_order_id": result.get('order_id'),
                "moomoo_status": result.get('status'),
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

            # Auto-capture lifecycle event
            try:
                is_rollover = bool(order.get('isRollover', False))
                event_type = 'roll' if is_rollover else 'entry'

                event_data = {
                    'event_type': event_type,
                    'ticker': ticker,
                    'option_type': option_type,
                    'strike': float(strike),
                    'expiration': str(expiry),
                    'premium_in': round(limit_price * 100, 2) if action == 'BUY' else round(limit_price * 100, 2),
                    'premium_out': 0,
                    'pnl': 0,
                    'leakage': 0,
                    'reason': 'rollover' if is_rollover else 'new_entry',
                    'details': {
                        'order_id': order_id,
                        'moomoo_order_id': result.get('order_id'),
                        'action': action,
                        'quantity': quantity,
                        'limit_price': limit_price,
                    }
                }

                # For rollovers, capture from/to transition
                if is_rollover:
                    event_data['from_strike'] = float(order.get('from_strike', 0) or 0)
                    event_data['from_expiration'] = str(order.get('from_expiration', '') or '')
                    event_data['to_strike'] = float(order.get('to_strike', 0) or strike)
                    event_data['to_expiration'] = str(order.get('to_expiration', '') or expiry)

                db.save_trade_event(event_data)
            except Exception as event_err:
                logger.warning(f"Failed to save trade event: {event_err}")

            # Invalidate portfolio cache after trade
            portfolio_service = self._get_portfolio_service()
            if portfolio_service:
                portfolio_service.invalidate_cache()

            return {
                "success": True,
                "message": "Order sent to moomoo",
                "order_id": order_id,
                "moomoo_order_id": result.get('order_id'),
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
      
    def check_pending_orders(self):
        """
        Check status of pending/processing orders in moomoo
        """
        try:
            orders = self.db.get_orders(status_filter=['pending', 'processing'], limit=50)
            if not orders:
                return {"success": True, "message": "No pending orders", "updated_orders": []}
                
            conn = self._get_connection()
            updated_orders = []
            for order in orders:
                moomoo_order_id = order.get('moomoo_order_id')
                if order.get('status') == 'processing' and moomoo_order_id:
                    status_info = conn.check_order_status(moomoo_order_id)
                    if status_info:
                        new_status = "processing"
                        executed = False
                        if status_info.get('status') in ['Filled', 'Cancelled', 'Dealt']:
                            new_status = "executed" if status_info.get('status') in ['Filled', 'Dealt'] else "canceled"
                            executed = True
                            
                        execution_details = {
                            "moomoo_order_id": moomoo_order_id,
                            "moomoo_status": status_info.get('status'),
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
            
            if order['status'] == 'processing' and order.get('moomoo_order_id'):
                conn = self._get_connection()
                res = conn.cancel_order(order.get('moomoo_order_id'))
                if res.get('success'):
                    self.db.update_order_status(order_id=order_id, status="canceled", executed=True)
                    return {"success": True, "message": "Order canceled"}, 200
            
            self.db.update_order_status(order_id=order_id, status="canceled", executed=True)
            return {"success": True, "message": "Order canceled"}, 200
        except Exception as e:
            return {"success": False, "error": str(e)}, 500
