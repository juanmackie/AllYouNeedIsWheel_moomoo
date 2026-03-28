"""
Stock and Options Trading Connection Module for moomoo
"""

import logging
import time
import os
import socket
import re
import traceback
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import pytz
from moomoo import *

# Import our logging configuration
from core.logging_config import get_logger

# Configure logging
logger = get_logger('autotrader.connection', 'moomoo')


def _safe_close_context(context):
    if context is None:
        return
    try:
        context.close()
    except Exception:
        pass


def _is_truthy_flag(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'y', 'ok', 'connected', 'ready'}
    return False


def _clean_account_id(value):
    if value is None:
        return ''

    account_id = str(value).strip()
    if not account_id or account_id.upper() == 'YOUR_MOOMOO_ACCOUNT_ID':
        return ''

    return account_id


def _env_name(trd_env):
    return 'SIMULATE' if trd_env == TrdEnv.SIMULATE else 'REAL'


def _normalize_trd_env(value, default_env):
    if value is None:
        return default_env

    if value in (TrdEnv.SIMULATE, TrdEnv.REAL):
        return value

    text = str(value).strip().upper()
    if text in {'SIM', 'SIMULATE', 'PAPER'}:
        return TrdEnv.SIMULATE
    if text in {'REAL', 'LIVE'}:
        return TrdEnv.REAL

    return default_env


def _normalize_security_firm(value, default_firm=SecurityFirm.FUTUSECURITIES):
    if value is None:
        return default_firm

    if value in {
        SecurityFirm.FUTUSECURITIES,
        SecurityFirm.FUTUINC,
        SecurityFirm.FUTUSG,
        SecurityFirm.FUTUAU,
        SecurityFirm.FUTUCA,
        SecurityFirm.FUTUJP,
        SecurityFirm.FUTUMY,
    }:
        return value

    attr_name = str(value).strip().upper()
    if hasattr(SecurityFirm, attr_name):
        return getattr(SecurityFirm, attr_name)

    logger.warning(f"Unknown moomoo security firm '{value}', falling back to {default_firm}")
    return default_firm


def _infer_security_type_from_code(code):
    if not code:
        return ''

    normalized = str(code).strip()
    option_pattern = r'^[A-Z]{2}\.[A-Z]+\d{6}[CP]\d+$'
    if re.match(option_pattern, normalized):
        return 'OPT'

    stock_pattern = r'^[A-Z]{2}\.[A-Z]+$'
    if re.match(stock_pattern, normalized):
        return 'STK'

    return ''


def _safe_float(value, default=0.0):
    if value in (None, '', 'N/A', 'nan', 'NaN'):
        return default

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _first_non_zero(*values):
    for value in values:
        numeric_value = _safe_float(value, None)
        if numeric_value is not None and numeric_value != 0:
            return numeric_value

    for value in values:
        numeric_value = _safe_float(value, None)
        if numeric_value is not None:
            return numeric_value

    return 0.0


def probe_opend_status(host='127.0.0.1', port=11111):
    """
    Probe the local OpenD endpoint and return a UI-friendly status payload.
    """
    status = {
        'status': 'unknown',
        'connected': False,
        'reachable': False,
        'host': host,
        'port': port,
        'message': 'Checking OpenD status...',
        'details': {}
    }

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.5)
    try:
        status['reachable'] = sock.connect_ex((host, int(port))) == 0
    except Exception as exc:
        status['status'] = 'error'
        status['message'] = f'Could not probe OpenD port: {exc}'
        return status
    finally:
        sock.close()

    if not status['reachable']:
        status['status'] = 'unavailable'
        status['message'] = 'OpenD is not running on the configured host and port.'
        return status

    quote_ctx = None
    try:
        quote_ctx = OpenQuoteContext(host=host, port=port)
        ret, data = quote_ctx.get_global_state()

        details = {}
        if hasattr(data, 'empty') and not data.empty and hasattr(data, 'to_dict'):
            try:
                records = data.to_dict('records')
                if records:
                    details = records[0]
            except Exception:
                details = {}

        status['details'] = details

        if ret == RET_OK:
            login_flags = [
                details[key] for key in details
                if 'login' in key.lower() or 'logined' in key.lower() or 'logged' in key.lower()
            ]
            if login_flags and not all(_is_truthy_flag(flag) for flag in login_flags):
                status['status'] = 'login_required'
                status['message'] = 'OpenD is running, but you still need to log in or complete verification.'
            else:
                status['status'] = 'connected'
                status['connected'] = True
                status['message'] = 'OpenD is running and ready.'
        else:
            error_text = str(data)
            if 'login' in error_text.lower() or 'verify' in error_text.lower():
                status['status'] = 'login_required'
                status['message'] = 'OpenD is running, but a manual login or verification step is still required.'
            else:
                status['status'] = 'available'
                status['message'] = 'OpenD is reachable but not fully ready yet.'
            status['details'] = {'error': error_text}
    except Exception as exc:
        status['status'] = 'available'
        status['message'] = f'OpenD is reachable but not ready yet: {exc}'
        status['details'] = {'error': str(exc)}
    finally:
        _safe_close_context(quote_ctx)

    return status

class MoomooConnection:
    """
    Class for managing connection to moomoo OpenD
    """
    def __init__(self, host='127.0.0.1', port=11111, readonly=True, account_id=None, portfolio_env=None, security_firm=None):
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
        self.account_id = _clean_account_id(account_id)
        self.portfolio_env = _normalize_trd_env(
            portfolio_env,
            TrdEnv.SIMULATE if readonly else TrdEnv.REAL
        )
        self.security_firm = _normalize_security_firm(
            security_firm or os.environ.get('MOOMOO_SECURITY_FIRM'),
            SecurityFirm.FUTUSECURITIES
        )
        self.quote_ctx = None
        self.trd_ctx = None
        self._connected = False
        self._account_cache = None
        self.last_error = None
        self.trading_password = os.environ.get('MOOMOO_TRADING_PASSWORD', '')

    def _account_id_arg(self, account_id):
        if not account_id:
            return 0

        try:
            return int(str(account_id))
        except (TypeError, ValueError):
            return account_id

    def _get_available_accounts(self, refresh=False):
        if self.trd_ctx is None:
            return []

        if self._account_cache is not None and not refresh:
            return self._account_cache

        try:
            ret, data = self.trd_ctx.get_acc_list()
            if ret != RET_OK or data is None or getattr(data, 'empty', True):
                self._account_cache = []
                return self._account_cache

            accounts = []
            for record in data.to_dict('records'):
                accounts.append({
                    'acc_id': str(record.get('acc_id', '')).strip(),
                    'trd_env': record.get('trd_env', TrdEnv.SIMULATE),
                    'security_firm': record.get('security_firm', '')
                })

            self._account_cache = accounts
            return accounts
        except Exception as exc:
            logger.warning(f"Could not load available accounts from OpenD: {exc}")
            self._account_cache = []
            return self._account_cache

    def _find_account_by_id(self, account_id):
        if not account_id:
            return None

        for account in self._get_available_accounts():
            if account.get('acc_id') == str(account_id):
                return account

        return None

    def _find_account_by_env(self, trd_env):
        for account in self._get_available_accounts():
            if account.get('trd_env') == trd_env:
                return account

        return None

    def _resolve_portfolio_account(self):
        desired_env = self.portfolio_env

        if self.account_id:
            matched_account = self._find_account_by_id(self.account_id)
            if matched_account:
                return matched_account.get('trd_env', TrdEnv.REAL), matched_account.get('acc_id')

            return desired_env, self.account_id

        fallback_account = self._find_account_by_env(desired_env)
        return desired_env, fallback_account.get('acc_id') if fallback_account else ''

    def _resolve_order_account(self):
        default_env = TrdEnv.SIMULATE if self.readonly else TrdEnv.REAL
        if self.account_id:
            matched_account = self._find_account_by_id(self.account_id)
            if matched_account and matched_account.get('trd_env') == default_env:
                return default_env, matched_account.get('acc_id')

        fallback_account = self._find_account_by_env(default_env)
        return default_env, fallback_account.get('acc_id') if fallback_account else ''

    def _format_trade_error(self, action, data, trd_env, account_id=''):
        details = str(data)
        env_label = _env_name(trd_env)
        account_label = f" account {account_id}" if account_id else ' account'
        available_accounts = self._get_available_accounts()
        available_accounts_text = ', '.join(
            f"{account.get('acc_id')} ({_env_name(account.get('trd_env'))})"
            for account in available_accounts
            if account.get('acc_id')
        )

        if 'No available real accounts' in details or 'Nonexisting acc_id' in details:
            suffix = ''
            if available_accounts_text:
                suffix = f" Available accounts exposed by OpenD right now: {available_accounts_text}."

            return (
                f"OpenD is connected, but the requested {env_label}{account_label} is not available to the API yet. "
                "The value here must be a trading account ID (`acc_id`), not your moomoo login/user ID. "
                f"The app is currently using security firm {self.security_firm}. "
                "Complete the moomoo API questionnaire/agreement, confirm API permissions, and unlock real trading if required, then try again. "
                f"{suffix} "
                f"OpenD said: {details}"
            )

        return f"Failed to {action} for {env_label}{account_label}: {details}"
        
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
            
            # REAL AU accounts exposing US authority work through the generic
            # securities context with a US market filter, not OpenUSTradeContext.
            self.trd_ctx = OpenSecTradeContext(
                host=self.host,
                port=self.port,
                filter_trdmarket=TrdMarket.US,
                security_firm=self.security_firm
            )

            self._connected = True
            self._account_cache = None
            self.last_error = None
            
            # If not readonly (live trading), unlock the trade context
            if not self.readonly and self.trading_password:
                ret, data = self.trd_ctx.unlock_trade(self.trading_password)
                if ret != RET_OK:
                    logger.warning(f"Failed to unlock trade: {data}")

            logger.info(f"Successfully connected to moomoo OpenD at {self.host}:{self.port}")
            logger.info(f"Using security firm {self.security_firm} for filtered US trade context")
            return True
        except Exception as e:
            self.last_error = f"Error connecting to moomoo: {str(e)}"
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
        self._account_cache = None
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
            trd_env, account_id = self._resolve_portfolio_account()
            ret, acc_data = self.trd_ctx.accinfo_query(
                trd_env=trd_env,
                acc_id=self._account_id_arg(account_id)
            )
            if ret != RET_OK:
                self.last_error = self._format_trade_error('get account info', acc_data, trd_env, account_id)
                logger.error(self.last_error)
                return None
            
            acc = acc_data.iloc[0]
            # OpenD returns portfolio-level totals in the account's base currency
            # (HKD for this AU account), but this app operates on US stocks/options
            # and the UI formats everything as USD. Prefer the USD-specific fields so
            # the dashboard matches the actual US position values.
            available_cash = _first_non_zero(
                acc.get('us_avl_withdrawal_cash'),
                acc.get('us_cash'),
                acc.get('usd_net_cash_power'),
                acc.get('cash', 0)
            )
            account_value = _first_non_zero(
                acc.get('usd_assets'),
                acc.get('us_cash'),
                acc.get('total_assets', 0)
            )
            excess_liquidity = _first_non_zero(
                acc.get('usd_net_cash_power'),
                acc.get('us_avl_withdrawal_cash'),
                acc.get('available_funds'),
                acc.get('avl_withdrawal_cash', 0)
            )
            initial_margin = _first_non_zero(
                acc.get('initial_margin'),
                acc.get('margin_call_margin'),
                acc.get('maintenance_margin'),
                acc.get('frozen_cash', 0)
            )

            account_info = {
                'account_id': str(acc.get('acc_id', account_id or '')),
                'trading_env': _env_name(trd_env),
                'available_cash': available_cash,
                'account_value': account_value,
                'excess_liquidity': excess_liquidity,
                'initial_margin': initial_margin,
                'currency': 'USD',
                'leverage_percentage': 0,
                'positions': {},
                'is_frozen': False
            }
            
            ret, pos_data = self.trd_ctx.position_list_query(
                trd_env=trd_env,
                acc_id=self._account_id_arg(account_id)
            )
            if ret == RET_OK and not pos_data.empty:
                # To get full details (like expiration for options), we may need snapshots
                position_types = pos_data['code'].apply(_infer_security_type_from_code)
                option_positions = pos_data[position_types == 'OPT']
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
                    sec_type = _infer_security_type_from_code(symbol)
                    
                    pos_key = symbol
                    pos_details = {
                        'shares': _safe_float(pos.get('qty', 0)),
                        'avg_cost': _safe_float(pos.get('average_cost', pos.get('cost_price', 0))),
                        'market_price': _safe_float(pos.get('nominal_price', pos.get('last_price', 0))),
                        'market_value': _safe_float(pos.get('market_val', 0)),
                        'unrealized_pnl': _safe_float(pos.get('unrealized_pl', pos.get('pl_val', 0))),
                        'security_type': sec_type
                    }
                    
                    if sec_type == 'OPT' and symbol in opt_snaps_dict:
                        snap = opt_snaps_dict[symbol]
                        pos_details.update({
                            'expiration': snap.get('option_expiry_date', '').replace('-', ''),
                            'strike': _safe_float(snap.get('option_strike_price', 0)),
                            'option_type': 'CALL' if snap.get('option_type') == 'CALL' else 'PUT'
                        })

                    account_info['positions'][pos_key] = pos_details

            return account_info
        except Exception as e:
            self.last_error = f"Error getting portfolio: {str(e)}"
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
            trd_env, account_id = self._resolve_order_account()
            trd_side = TrdSide.BUY if action.upper() == 'BUY' else TrdSide.SELL
            
            ret, data = self.trd_ctx.place_order(
                price=float(limit_price),
                qty=float(quantity),
                code=option_code,
                trd_side=trd_side,
                order_type=OrderType.NORMAL,
                trd_env=trd_env,
                acc_id=self._account_id_arg(account_id),
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
            trd_env, account_id = self._resolve_order_account()
            ret, data = self.trd_ctx.order_list_query(
                order_id=str(order_id),
                trd_env=trd_env,
                acc_id=self._account_id_arg(account_id)
            )
            
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
            trd_env, account_id = self._resolve_order_account()
            ret, data = self.trd_ctx.modify_order(
                modify_op=ModifyOrderOp.CANCEL,
                order_id=str(order_id),
                qty=0,
                price=0,
                trd_env=trd_env,
                acc_id=self._account_id_arg(account_id)
            )
            
            if ret == RET_OK:
                return {'success': True, 'message': f"Cancellation request sent for order {order_id}"}
            else:
                return {'success': False, 'error': str(data)}
        except Exception as e:
            logger.error(f"Error cancelling order: {str(e)}")
            return {'success': False, 'error': str(e)}
