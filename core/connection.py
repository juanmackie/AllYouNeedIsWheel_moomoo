"""
Stock and Options Trading Connection Module for moomoo
"""

import logging
import time
import os
import traceback
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import pytz
from moomoo import *

# Import our logging configuration
from core.logging_config import get_logger

# Configure logging
logger = get_logger('autotrader.connection', 'moomoo')

class MoomooConnection:
    """
    Class for managing connection to moomoo OpenD
    """
    def __init__(self, host='127.0.0.1', port=11111, readonly=True):
        """
        Initialize the moomoo connection
        
        Args:
            host (str): OpenD host (default: 127.0.0.1)
            port (int): OpenD port (default: 11111)
            readonly (bool): Whether to operate in readonly mode (simulation)
        """
        self.host = host
        self.port = port
        self.readonly = readonly
        self.quote_ctx = None
        self.trd_ctx = None
        self._connected = False
        self.trading_password = os.environ.get('MOOMOO_TRADING_PASSWORD', '')
        
    def connect(self):
        """
        Connect to moomoo OpenD
        """
        try:
            if self._connected:
                return True
            
            # Close existing contexts if they exist to avoid leaks
            self.disconnect()
            
            # Initialize Quote Context
            self.quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
            
            # Initialize Trade Context
            self.trd_ctx = OpenSecTradeContext(host=self.host, port=self.port)

            self._connected = True
            
            # If not readonly (live trading), unlock the trade context
            if not self.readonly and self.trading_password:
                ret, data = self.trd_ctx.unlock_trade(self.trading_password)
                if ret != RET_OK:
                    logger.warning(f"Failed to unlock trade: {data}")

            logger.info(f"Successfully connected to moomoo OpenD at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Error connecting to moomoo: {str(e)}")
            logger.debug(traceback.format_exc())
            self._connected = False
            return False
    
    def disconnect(self):
        """
        Disconnect from moomoo OpenD
        """
        if self.quote_ctx:
            try:
                self.quote_ctx.close()
            except:
                pass
            self.quote_ctx = None
        if self.trd_ctx:
            try:
                self.trd_ctx.close()
            except:
                pass
            self.trd_ctx = None
        self._connected = False
        logger.info("Disconnected from moomoo")
    
    def is_connected(self):
        """
        Check if connected to moomoo
        """
        if not self._connected or self.quote_ctx is None:
            return False

        try:
            ret, data = self.quote_ctx.get_global_state()
            return ret == RET_OK
        except:
            return False

    def _format_symbol(self, symbol):
        """Format symbol to moomoo format (e.g., US.AAPL)"""
        if '.' not in symbol:
            return f"US.{symbol}"
        return symbol

    def get_stock_price(self, symbol):
        """
        Get the current price of a stock
        """
        if not self.is_connected():
            if not self.connect():
                return None
        
        symbol = self._format_symbol(symbol)
        try:
            ret, data = self.quote_ctx.get_market_snapshot([symbol])
            if ret == RET_OK and not data.empty:
                price = data.iloc[0].get('last_price')
                if price is None or price == 0:
                    price = data.iloc[0].get('prev_close_price')
                return float(price)
            else:
                logger.error(f"Failed to get price for {symbol}: {data}")
                return None
        except Exception as e:
            logger.error(f"Error getting {symbol} price: {str(e)}")
            return None

    def get_option_chain(self, symbol, expiration=None, right='C', target_strike=None):
        """
        Get option chain for a given symbol
        """
        if not self.is_connected():
            if not self.connect():
                return None
        
        symbol = self._format_symbol(symbol)
        try:
            opt_type = OptionType.CALL if right == 'C' else OptionType.PUT
            
            # Format expiration for moomoo (YYYY-MM-DD)
            start_date = None
            end_date = None
            if expiration:
                if len(expiration) == 8:
                    start_date = f"{expiration[0:4]}-{expiration[4:6]}-{expiration[6:8]}"
                    end_date = start_date
            
            # Correct parameters for get_option_chain: start, end, option_type
            ret, data = self.quote_ctx.get_option_chain(
                code=symbol,
                start=start_date,
                end=end_date,
                option_type=opt_type
            )
            
            if ret != RET_OK:
                logger.error(f"Failed to get option chain for {symbol}: {data}")
                return None
            
            stock_price = self.get_stock_price(symbol)
            
            result = {
                'symbol': symbol.split('.')[-1],
                'expiration': expiration.replace('-', '') if expiration else '',
                'stock_price': stock_price,
                'right': right,
                'options': []
            }
            
            if data.empty:
                return result

            # Filtering and getting snapshots
            if target_strike:
                data['strike_diff'] = (data['strike_price'] - float(target_strike)).abs()
                data = data.sort_values('strike_diff').head(20)

            option_codes = data['code'].tolist()
            if not option_codes:
                return result

            ret, snap_data = self.quote_ctx.get_market_snapshot(option_codes)
            if ret == RET_OK:
                for _, row in snap_data.iterrows():
                    opt_expiry = row.get('option_expiry_date', '')
                    if opt_expiry:
                        opt_expiry = opt_expiry.replace('-', '')
                        
                    option_data = {
                        'strike': float(row.get('option_strike_price', 0)),
                        'expiration': opt_expiry,
                        'option_type': 'CALL' if row.get('option_type') == 'CALL' else 'PUT',
                        'bid': float(row.get('bid_price', 0)),
                        'ask': float(row.get('ask_price', 0)),
                        'last': float(row.get('last_price', 0)),
                        'volume': int(row.get('volume', 0)),
                        'open_interest': int(row.get('open_interest', 0)),
                        'implied_volatility': float(row.get('option_implied_volatility', 0)),
                        'delta': float(row.get('option_delta', 0)),
                        'gamma': float(row.get('option_gamma', 0)),
                        'theta': float(row.get('option_theta', 0)),
                        'vega': float(row.get('option_vega', 0))
                    }
                    result['options'].append(option_data)
            
            if not result['expiration'] and result['options']:
                result['expiration'] = result['options'][0]['expiration']

            return result
        except Exception as e:
            logger.error(f"Error retrieving option chain for {symbol}: {str(e)}")
            logger.debug(traceback.format_exc())
            return None

    def get_portfolio(self):
        """
        Get current portfolio positions and account information
        """
        if not self.is_connected():
            if not self.connect():
                return None
        
        try:
            trd_env = TrdEnv.SIMULATE if self.readonly else TrdEnv.REAL
            
            ret, acc_data = self.trd_ctx.accinfo_query(trd_env=trd_env)
            if ret != RET_OK:
                logger.error(f"Failed to get account info: {acc_data}")
                return None
            
            acc = acc_data.iloc[0]
            account_info = {
                'account_id': str(acc.get('acc_id', '')),
                'available_cash': float(acc.get('cash', 0)),
                'account_value': float(acc.get('total_assets', 0)),
                'excess_liquidity': float(acc.get('net_assets', 0)),
                'initial_margin': float(acc.get('frozen_cash', 0)),
                'leverage_percentage': 0,
                'positions': {},
                'is_frozen': False
            }
            
            ret, pos_data = self.trd_ctx.position_list_query(trd_env=trd_env)
            if ret == RET_OK and not pos_data.empty:
                # To get full details (like expiration for options), we may need snapshots
                option_positions = pos_data[pos_data['sec_type'] == 'OPT']
                if not option_positions.empty:
                    opt_ret, opt_snaps = self.quote_ctx.get_market_snapshot(option_positions['code'].tolist())
                    if opt_ret == RET_OK:
                        # Merge snapshot info back or use it to build details
                        opt_snaps_dict = opt_snaps.set_index('code').to_dict('index')
                    else:
                        opt_snaps_dict = {}
                else:
                    opt_snaps_dict = {}

                for _, pos in pos_data.iterrows():
                    symbol = pos.get('code', '')
                    sec_type = pos.get('sec_type', '')
                    
                    pos_key = symbol
                    pos_details = {
                        'shares': float(pos.get('qty', 0)),
                        'avg_cost': float(pos.get('cost_price', 0)),
                        'market_price': float(pos.get('last_price', 0)),
                        'market_value': float(pos.get('market_val', 0)),
                        'unrealized_pnl': float(pos.get('pl_val', 0)),
                        'security_type': 'STK' if sec_type == 'STOCK' else ('OPT' if sec_type == 'OPT' else sec_type)
                    }
                    
                    if sec_type == 'OPT' and symbol in opt_snaps_dict:
                        snap = opt_snaps_dict[symbol]
                        pos_details.update({
                            'expiration': snap.get('option_expiry_date', '').replace('-', ''),
                            'strike': float(snap.get('option_strike_price', 0)),
                            'option_type': 'CALL' if snap.get('option_type') == 'CALL' else 'PUT'
                        })

                    account_info['positions'][pos_key] = pos_details
            
            return account_info
        except Exception as e:
            logger.error(f"Error getting portfolio: {str(e)}")
            logger.debug(traceback.format_exc())
            return None

    def create_option_contract(self, symbol, expiry, strike, option_type):
        """
        Find the option code for the given parameters.
        """
        symbol = self._format_symbol(symbol)
        opt_type = OptionType.CALL if option_type.upper() in ['C', 'CALL'] else OptionType.PUT
        
        moomoo_expiry = f"{expiry[0:4]}-{expiry[4:6]}-{expiry[6:8]}" if len(expiry) == 8 else expiry
            
        ret, data = self.quote_ctx.get_option_chain(code=symbol, start=moomoo_expiry, end=moomoo_expiry, option_type=opt_type)
        if ret == RET_OK:
            match = data[data['strike_price'] == float(strike)]
            if not match.empty:
                return match.iloc[0]['code']
        
        return None

    def place_order(self, option_code, quantity, action, limit_price):
        """
        Place an order in moomoo
        """
        if not self.is_connected():
            if not self.connect():
                return None

        try:
            trd_env = TrdEnv.SIMULATE if self.readonly else TrdEnv.REAL
            trd_side = TrdSide.BUY if action.upper() == 'BUY' else TrdSide.SELL
            
            ret, data = self.trd_ctx.place_order(
                price=float(limit_price),
                qty=float(quantity),
                code=option_code,
                trd_side=trd_side,
                order_type=OrderType.NORMAL,
                trd_env=trd_env,
                trd_market=TrdMarket.US
            )
            
            if ret == RET_OK:
                order_id = data.iloc[0]['order_id']
                return {
                    'order_id': order_id,
                    'status': 'Submitted',
                    'filled': 0,
                    'remaining': quantity,
                    'avg_fill_price': 0
                }
            else:
                logger.error(f"Failed to place order: {data}")
                return None
        except Exception as e:
            logger.error(f"Error placing order: {str(e)}")
            return None

    def check_order_status(self, order_id):
        """
        Check order status
        """
        if not self.is_connected():
            if not self.connect():
                return None

        try:
            trd_env = TrdEnv.SIMULATE if self.readonly else TrdEnv.REAL
            ret, data = self.trd_ctx.order_list_query(order_id=str(order_id), trd_env=trd_env)
            
            if ret == RET_OK and not data.empty:
                order = data.iloc[0]
                status_str = str(order.get('order_status', ''))
                return {
                    'status': status_str.capitalize(),
                    'filled': float(order.get('dealt_qty', 0)),
                    'remaining': float(order.get('qty', 0)) - float(order.get('dealt_qty', 0)),
                    'avg_fill_price': float(order.get('dealt_avg_price', 0)),
                    'last_fill_price': float(order.get('dealt_avg_price', 0)),
                    'commission': 0,
                    'why_held': ''
                }
            return None
        except Exception as e:
            logger.error(f"Error checking order status: {str(e)}")
            return None

    def cancel_order(self, order_id):
        """
        Cancel an order
        """
        if not self.is_connected():
            if not self.connect():
                return None

        try:
            trd_env = TrdEnv.SIMULATE if self.readonly else TrdEnv.REAL
            ret, data = self.trd_ctx.modify_order(
                modify_op=ModifyOrderOp.CANCEL,
                order_id=str(order_id),
                qty=0,
                price=0,
                trd_env=trd_env
            )
            
            if ret == RET_OK:
                return {'success': True, 'message': f"Cancellation request sent for order {order_id}"}
            else:
                return {'success': False, 'error': str(data)}
        except Exception as e:
            logger.error(f"Error cancelling order: {str(e)}")
            return {'success': False, 'error': str(e)}
