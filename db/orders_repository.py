import sqlite3
from datetime import datetime
import traceback
import logging

logger = logging.getLogger('db.orders')


class OrdersRepository:
    def __init__(self, db_path):
        self.db_path = db_path

    def save_order(self, order_data):
        try:
            conn = sqlite3.connect(self.db_path); conn.row_factory = None
            cursor = conn.cursor()
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ticker = order_data.get('ticker', '')
            option_type = order_data.get('option_type', '')
            action = order_data.get('action', 'SELL')
            strike = order_data.get('strike', 0)
            expiration = order_data.get('expiration', '')
            premium = order_data.get('premium', 0)
            quantity = order_data.get('quantity', 1)

            bid = order_data.get('bid', 0)
            ask = order_data.get('ask', 0)
            last = order_data.get('last', 0)

            delta = order_data.get('delta', 0)
            gamma = order_data.get('gamma', 0)
            theta = order_data.get('theta', 0)
            vega = order_data.get('vega', 0)
            implied_volatility = order_data.get('implied_volatility', 0)

            open_interest = order_data.get('open_interest', 0)
            volume = order_data.get('volume', 0)
            is_mock = order_data.get('is_mock', False)

            earnings_max_contracts = order_data.get('earnings_max_contracts', 0)
            earnings_premium_per_contract = order_data.get('earnings_premium_per_contract', 0)
            earnings_total_premium = order_data.get('earnings_total_premium', 0)
            earnings_return_on_cash = order_data.get('earnings_return_on_cash', 0)
            earnings_return_on_capital = order_data.get('earnings_return_on_capital', 0)

            is_rollover = order_data.get('isRollover', False)

            cursor.execute('''
                INSERT INTO orders
                (timestamp, ticker, option_type, action, strike, expiration, premium, quantity,
                 bid, ask, last, delta, gamma, theta, vega, implied_volatility,
                 open_interest, volume, is_mock,
                 earnings_max_contracts, earnings_premium_per_contract,
                 earnings_total_premium, earnings_return_on_cash,
                 earnings_return_on_capital, status, executed, isRollover)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                timestamp, ticker, option_type, action, strike, expiration, premium, quantity,
                bid, ask, last, delta, gamma, theta, vega, implied_volatility,
                open_interest, volume, is_mock,
                earnings_max_contracts, earnings_premium_per_contract,
                earnings_total_premium, earnings_return_on_cash,
                earnings_return_on_capital, 'pending', False, is_rollover
            ))

            record_id = cursor.lastrowid
            conn.commit()
            return record_id
        except Exception as e:
            logger.error(f"Error saving order: {str(e)}")
            return None
        finally:
            try: conn.close()
            except Exception: pass

    def get_pending_orders(self, executed=False, limit=50, isRollover=None):
        if executed:
            return self.get_orders(executed=executed, limit=limit, isRollover=isRollover)
        else:
            return self.get_orders(status_filter=['pending', 'processing'], limit=limit, isRollover=isRollover)

    def update_order_status(self, order_id, status, executed=False, execution_details=None):
        try:
            conn = sqlite3.connect(self.db_path); conn.row_factory = None
            cursor = conn.cursor()

            set_parts = ['status = ?', 'executed = ?']
            params = [status, executed]

            if execution_details and isinstance(execution_details, dict):
                field_mappings = {
                    'moomoo_order_id': 'moomoo_order_id',
                    'moomoo_status': 'moomoo_status',
                    'filled': 'filled',
                    'remaining': 'remaining',
                    'avg_fill_price': 'avg_fill_price',
                    'is_mock': 'is_mock'
                }

                for api_field, db_field in field_mappings.items():
                    if api_field in execution_details:
                        set_parts.append(f"{db_field} = ?")
                        params.append(execution_details[api_field])

            params.append(order_id)

            update_query = f"""
                UPDATE orders
                SET {', '.join(set_parts)}
                WHERE id = ?
            """

            cursor.execute(update_query, params)
            affected_rows = cursor.rowcount
            conn.commit()

            return affected_rows > 0
        except Exception as e:
            logger.error(f"Error updating order status: {str(e)}")
            logger.error(traceback.format_exc())
            return False
        finally:
            try: conn.close()
            except Exception: pass

    def delete_order(self, order_id):
        try:
            conn = sqlite3.connect(self.db_path); conn.row_factory = None
            cursor = conn.cursor()

            cursor.execute('DELETE FROM orders WHERE id = ?', (order_id,))
            affected_rows = cursor.rowcount
            conn.commit()
            return affected_rows > 0
        except Exception as e:
            logger.error(f"Error deleting order: {str(e)}")
            return False
        finally:
            try: conn.close()
            except Exception: pass

    def update_order_quantity(self, order_id, quantity):
        try:
            conn = sqlite3.connect(self.db_path); conn.row_factory = None
            cursor = conn.cursor()

            cursor.execute('SELECT status FROM orders WHERE id = ?', (order_id,))
            order = cursor.fetchone()
            if not order:
                logger.error(f"No order found with ID {order_id}")
                return False

            if order[0] != 'pending':
                logger.error(f"Cannot update quantity for order with status '{order[0]}'")
                return False

            cursor.execute('''
                UPDATE orders
                SET quantity = ?, timestamp = ?
                WHERE id = ?
            ''', (quantity, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), order_id))

            affected_rows = cursor.rowcount
            conn.commit()

            if affected_rows > 0:
                logger.info(f"Successfully updated quantity to {quantity} for order {order_id}")
                return True
            else:
                logger.warning(f"No changes made to order {order_id}")
                return False

        except Exception as e:
            logger.error(f"Error updating order quantity: {str(e)}")
            logger.error(traceback.format_exc())
            return False
        finally:
            try: conn.close()
            except Exception: pass

    def get_order(self, order_id):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
            row = cursor.fetchone()

            if not row:
                return None
            return dict(row)

        except Exception as e:
            logger.error(f"Error getting order: {str(e)}")
            return None
        finally:
            try: conn.close()
            except Exception: pass

    def get_orders(self, status=None, executed=None, ticker=None, limit=50, status_filter=None, isRollover=None):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT * FROM orders WHERE 1=1"
            params = []

            if status_filter is not None and isinstance(status_filter, list) and status_filter:
                placeholders = ', '.join(['?' for _ in status_filter])
                query += f" AND status IN ({placeholders})"
                params.extend(status_filter)
            elif status is not None:
                query += " AND status = ?"
                params.append(status)

            if executed is not None:
                query += " AND executed = ?"
                params.append(executed)

            if ticker is not None:
                query += " AND ticker = ?"
                params.append(ticker)

            if isRollover is not None:
                query += " AND isRollover = ?"
                params.append(isRollover)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting orders: {str(e)}")
            return []
        finally:
            try: conn.close()
            except Exception: pass
