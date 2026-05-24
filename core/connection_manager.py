"""
MoomooConnection - connection lifecycle, order management, and portfolio retrieval.
Extracted from core/connection.py for maintainability.
"""

import logging
import time
import os
import re
import traceback
import threading
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import pytz
try:
    from moomoo import (
        OpenQuoteContext,
        OpenSecTradeContext,
        RET_OK,
        RET_ERROR,
        SecurityFirm,
        TrdEnv,

        OptionType,
    )
except ImportError:
    # Allow graceful fallback during test collection / environments without full moomoo SDK
    OpenQuoteContext = None
    OpenSecTradeContext = None
    RET_OK = None
    RET_ERROR = None
    SecurityFirm = None
    TrdEnv = None

    OptionType = None

from core.logging_config import get_logger
from core.rate_limiter import RateLimiter
from core.connection_constants import (
    _safe_close_context,
    _clean_account_id,
    _env_name,
    _normalize_trd_env,
    _normalize_security_firm,
    _infer_security_type_from_code,
    _parse_option_code_metadata,
    _safe_float,
    _first_non_zero,
    _normalize_iv,
)
from core.context_factory import create_contexts
from core.ticker_utils import TickerCache, format_symbol

logger = get_logger('autotrader.connection', 'moomoo')


class MoomooConnection:
    """
    Class for managing connection to moomoo OpenD

    This class implements a singleton-like pattern per configuration to ensure
    connections are reused and properly managed across the application lifecycle.
    """

    # Class-level cache of connection instances to prevent multiple connections
    _instances = {}
    _instance_lock = threading.Lock()
    _option_chain_gate = threading.BoundedSemaphore(1)

    def __new__(cls, host='127.0.0.1', port=11111, readonly=True, account_id=None, portfolio_env=None, security_firm=None):
        cleaned_account_id = _clean_account_id(account_id)

        if TrdEnv is not None:
            default_portfolio_env = TrdEnv.SIMULATE if readonly else TrdEnv.REAL
            normalized_portfolio_env = _normalize_trd_env(portfolio_env, default_portfolio_env)
        else:
            normalized_portfolio_env = str(portfolio_env).strip().upper() if portfolio_env else ('SIMULATE' if readonly else 'REAL')

        if SecurityFirm is not None:
            normalized_security_firm = _normalize_security_firm(
                security_firm or os.environ.get('MOOMOO_SECURITY_FIRM'),
                SecurityFirm.FUTUSECURITIES,
            )
        else:
            normalized_security_firm = str(security_firm or os.environ.get('MOOMOO_SECURITY_FIRM') or '').strip().upper()

        normalized_portfolio_env_key = getattr(normalized_portfolio_env, 'name', str(normalized_portfolio_env).strip().upper())
        normalized_security_firm_key = getattr(normalized_security_firm, 'name', str(normalized_security_firm).strip().upper())
        key = f"{host}:{port}:{readonly}:{cleaned_account_id}:{normalized_portfolio_env_key}:{normalized_security_firm_key}"

        with cls._instance_lock:
            if key not in cls._instances:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instances[key] = instance
                logger.info(f"Created new MoomooConnection instance for {host}:{port}")
            else:
                logger.debug(f"Reusing existing MoomooConnection instance for {host}:{port}")

            return cls._instances[key]

    def __init__(self, host='127.0.0.1', port=11111, readonly=True, account_id=None, portfolio_env=None, security_firm=None):
        if self._initialized:
            return

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
        self._connection_lock = threading.Lock()
        self._last_activity = None
        self._initialized = True

        self._rate_limiter = RateLimiter(
            max_requests_per_window=30,
            rate_limit_window=60,
            burst_threshold=15,
            burst_window=10
        )

        self._ticker_cache = TickerCache(price_ttl=120, failed_ttl=300)

        self._option_chain_cache = {}
        self._cache_lock = threading.Lock()
        self._cache_ttl = 180

        # Expiration lists are much cheaper to reuse than to re-fetch.
        self._option_expiration_cache = {}
        self._expiration_cache_ttl = 300

        self._pending_requests = {}
        self._pending_requests_lock = threading.Lock()

        self._connection_created_at = datetime.now()

    def _get_cached_option_chain(self, symbol, expiration, right):
        cache_key = f"{symbol}_{expiration}_{right}"
        with self._cache_lock:
            if cache_key in self._option_chain_cache:
                cached_data, timestamp = self._option_chain_cache[cache_key]
                if time.time() - timestamp < self._cache_ttl:
                    logger.debug(f"Using cached option chain for {cache_key}")
                    return cached_data
                del self._option_chain_cache[cache_key]
        return None

    def _cache_option_chain(self, symbol, expiration, right, data):
        cache_key = f"{symbol}_{expiration}_{right}"
        with self._cache_lock:
            self._option_chain_cache[cache_key] = (data, time.time())
            logger.debug(f"Cached option chain for {cache_key}")

    def _get_cached_option_expirations(self, symbol):
        with self._cache_lock:
            if symbol in self._option_expiration_cache:
                cached_data, timestamp = self._option_expiration_cache[symbol]
                if time.time() - timestamp < self._expiration_cache_ttl:
                    logger.debug(f"Using cached option expirations for {symbol}")
                    return cached_data
                del self._option_expiration_cache[symbol]
        return None

    def _cache_option_expirations(self, symbol, data):
        with self._cache_lock:
            self._option_expiration_cache[symbol] = (data, time.time())
            logger.debug(f"Cached option expirations for {symbol}")

    @classmethod
    def get_connection_pool_stats(cls):
        with cls._instance_lock:
            return {
                'cached_instances': len(cls._instances),
                'instance_keys': list(cls._instances.keys())
            }

    def _account_id_arg(self, account_id):
        if not account_id:
            return 0
        try:
            return int(str(account_id))
        except (TypeError, ValueError):
            return account_id

    def _acquire_option_chain_gate(self, request_key):
        started_wait = time.time()
        MoomooConnection._option_chain_gate.acquire()
        wait_time = time.time() - started_wait
        if wait_time > 0.1:
            logger.info(
                "Queued option-chain work for %.2fs to reduce OpenD contention (%s)",
                wait_time,
                request_key,
            )
        return wait_time

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

    def _format_trade_error(self, action, data, trd_env, account_id=''):
        details = str(data)
        env_label = _env_name(trd_env)
        account_label = f" account {account_id}" if account_id else ' account'
        available_accounts = self._get_available_accounts()
        available_accounts_text = ', '.join(
            f"{account.get('acc_id')} ({_env_name(account.get('trd_env'))})"
            for account in available_accounts if account.get('acc_id')
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
        with self._connection_lock:
            if self._connected and self.is_connected():
                self._last_activity = datetime.now()
                return True
            try:
                logger.info(f"Connecting to moomoo OpenD at {self.host}:{self.port}")
                self._safe_disconnect()
                self.quote_ctx, self.trd_ctx = create_contexts(self.host, self.port, self.security_firm)

                self._connected = True
                self._account_cache = None
                self._last_activity = datetime.now()
                self.last_error = None

                logger.info(f"Successfully connected to moomoo OpenD at {self.host}:{self.port}")
                logger.info(f"Using security firm {self.security_firm} for filtered US trade context")
                return True
            except Exception as e:
                self.last_error = f"Error connecting to moomoo: {str(e)}"
                logger.error(f"Error connecting to moomoo: {str(e)}")
                logger.debug(traceback.format_exc())
                self._connected = False
                self._safe_disconnect()
                return False

    def _safe_disconnect(self):
        if self.quote_ctx:
            _safe_close_context(self.quote_ctx)
            self.quote_ctx = None
        if self.trd_ctx:
            _safe_close_context(self.trd_ctx)
            self.trd_ctx = None
        self._connected = False

    def disconnect(self):
        with self._connection_lock:
            self._safe_disconnect()
            self._account_cache = None
            logger.info("Disconnected from moomoo")

    def is_connected(self):
        if not self._connected or self.quote_ctx is None:
            return False
        try:
            ret, data = self.quote_ctx.get_global_state()
            if ret == RET_OK:
                self._last_activity = datetime.now()
                return True
            logger.debug(f"Connection check failed: {data}")
            return False
        except Exception as e:
            logger.debug(f"Connection health check failed: {e}")
            return False

    def get_connection_info(self):
        idle_time = None
        if self._last_activity:
            idle_time = (datetime.now() - self._last_activity).total_seconds()
        uptime_seconds = None
        if self._connection_created_at:
            uptime_seconds = (datetime.now() - self._connection_created_at).total_seconds()

        price_size, failed_size = self._ticker_cache.get_cache_stats()

        return {
            'connected': self._connected,
            'is_healthy': self.is_connected(),
            'host': self.host,
            'port': self.port,
            'last_activity': self._last_activity.isoformat() if self._last_activity else None,
            'idle_seconds': idle_time,
            'uptime_seconds': uptime_seconds,
            'has_quote_ctx': self.quote_ctx is not None,
            'has_trd_ctx': self.trd_ctx is not None,
            'readonly': self.readonly,
            'portfolio_env': _env_name(self.portfolio_env),
            'security_firm': str(self.security_firm),
            'account_id': self.account_id if self.account_id else 'auto',
            'stock_price_cache_size': price_size,
            'failed_tickers_count': failed_size,
            'rate_limit_stats': self._rate_limiter.get_stats(),
            'rate_limit_config': {
                'max_requests_per_window': self._rate_limiter.max_requests_per_window,
                'rate_limit_window': self._rate_limiter.rate_limit_window,
                'burst_threshold': self._rate_limiter.burst_threshold,
                'burst_window': self._rate_limiter.burst_window,
            }
        }

    def _format_symbol(self, symbol):
        return format_symbol(symbol)

    def _get_cached_stock_price(self, symbol):
        return self._ticker_cache.get_cached_price(symbol)

    def get_cached_stock_price(self, symbol):
        """Public accessor for cached stock price — does not touch the rate limiter."""
        return self._get_cached_stock_price(symbol)

    def _cache_stock_price(self, symbol, price):
        self._ticker_cache.cache_price(symbol, price)

    def _is_ticker_failed(self, symbol):
        return self._ticker_cache.is_ticker_failed(symbol)

    def _mark_ticker_failed(self, symbol):
        self._ticker_cache.mark_ticker_failed(symbol)

    def _get_or_create_pending_request(self, request_key):
        with self._pending_requests_lock:
            if request_key in self._pending_requests:
                return self._pending_requests[request_key], False
            event = threading.Event()
            self._pending_requests[request_key] = event
            return event, True

    def _complete_pending_request(self, request_key, result):
        with self._pending_requests_lock:
            if request_key in self._pending_requests:
                event = self._pending_requests.pop(request_key)
                self._pending_requests[f"{request_key}_result"] = result
                event.set()

    def _wait_for_pending_request(self, request_key, timeout=90):
        event, is_new = self._get_or_create_pending_request(request_key)
        if not is_new:
            logger.debug(f"Waiting for pending request: {request_key}")
            event.wait(timeout=timeout)
            with self._pending_requests_lock:
                result_key = f"{request_key}_result"
                if result_key in self._pending_requests:
                    return self._pending_requests.pop(result_key)
            logger.warning(f"Timeout waiting for pending request: {request_key}")
            return None
        return None

    def get_option_expiration_dates(self, symbol):
        symbol = self._format_symbol(symbol)
        request_key = f"option_expirations:{symbol}"

        cached_result = self._get_cached_option_expirations(symbol)
        if cached_result is not None:
            return cached_result

        pending_result = self._wait_for_pending_request(request_key)
        if pending_result is not None:
            cached_result = self._get_cached_option_expirations(symbol)
            return cached_result if cached_result is not None else pending_result

        try:
            self._rate_limiter.check_rate_limit()
            if not self.is_connected():
                if not self.connect():
                    result = (RET_ERROR, None)
                    self._complete_pending_request(request_key, result)
                    return result
            ret, data = self.quote_ctx.get_option_expiration_date(code=symbol)
            result = (ret, data)
            self._cache_option_expirations(symbol, result)
            self._complete_pending_request(request_key, result)
            return result
        except Exception as e:
            logger.error(f"Error getting option expirations for {symbol}: {str(e)}")
            result = (RET_ERROR, None)
            self._complete_pending_request(request_key, result)
            return result

    def get_stock_price(self, symbol):
        symbol = self._format_symbol(symbol)
        request_key = f"stock_price:{symbol}"

        if self._is_ticker_failed(symbol):
            logger.debug(f"Skipping API call for {symbol} - quote rights failure cached")
            return None

        cached_price = self._get_cached_stock_price(symbol)
        if cached_price is not None:
            return cached_price

        pending_result = self._wait_for_pending_request(request_key)
        if pending_result is not None:
            return pending_result

        try:
            self._rate_limiter.check_rate_limit()
            if not self.is_connected():
                if not self.connect():
                    self._complete_pending_request(request_key, None)
                    return None

            ret, data = self.quote_ctx.get_market_snapshot([symbol])
            if ret != RET_OK or data is None or data.empty:
                logger.error(f"Failed to get stock price for {symbol}: {data}")
                self._complete_pending_request(request_key, None)
                return None

            price = float(data.iloc[0].get('last_price', 0))
            self._cache_stock_price(symbol, price)
            self._complete_pending_request(request_key, price)
            return price
        except Exception as e:
            logger.error(f"Error getting stock price for {symbol}: {str(e)}")
            self._complete_pending_request(request_key, None)
            return None

    def get_option_chain(self, symbol, expiration=None, right='C', target_strike=None):
        symbol = self._format_symbol(symbol)
        cache_key = f"{symbol}_{expiration}_{right}"
        request_key = f"option_chain:{cache_key}"

        cached_result = self._get_cached_option_chain(symbol, expiration, right)
        if cached_result is not None:
            return cached_result

        event, is_new = self._get_or_create_pending_request(request_key)
        if not is_new:
            logger.debug(f"Waiting for pending request: {request_key}")
            event.wait(timeout=90)
            cached_result = self._get_cached_option_chain(symbol, expiration, right)
            if cached_result is not None:
                return cached_result
            logger.warning(f"Pending request completed but result not in cache: {request_key}")
            return None

        try:
            self._rate_limiter.check_rate_limit()
            if not self.is_connected():
                if not self.connect():
                    self._complete_pending_request(request_key, None)
                    return None

            opt_type = OptionType.CALL if right == 'C' else OptionType.PUT
            start_date = None
            end_date = None
            if expiration:
                if len(expiration) == 8:
                    start_date = f"{expiration[0:4]}-{expiration[4:6]}-{expiration[6:8]}"
                    end_date = start_date

            result = {
                'symbol': symbol.split('.')[-1],
                'expiration': expiration.replace('-', '') if expiration else '',
                'stock_price': None,
                'right': right,
                'options': []
            }

            self._acquire_option_chain_gate(request_key)
            try:
                ret, data = self.quote_ctx.get_option_chain(
                    code=symbol,
                    start=start_date,
                    end=end_date,
                    option_type=opt_type
                )

                if ret != RET_OK:
                    logger.error(f"Failed to get option chain for {symbol}: {data}")
                    self._complete_pending_request(request_key, None)
                    return None

                if data.empty:
                    self._cache_option_chain(symbol, expiration, right, result)
                    self._complete_pending_request(request_key, result)
                    return result

                if target_strike:
                    data['strike_diff'] = (data['strike_price'] - float(target_strike)).abs()
                    data = data.sort_values('strike_diff').head(20)

                option_codes = data['code'].tolist()
                if not option_codes:
                    self._cache_option_chain(symbol, expiration, right, result)
                    self._complete_pending_request(request_key, result)
                    return result

                ret, snap_data = self.quote_ctx.get_market_snapshot(option_codes)
                if ret == RET_OK:
                    for _, row in snap_data.iterrows():
                        opt_expiry = row.get('option_expiry_date', '') or row.get('strike_time', '')
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
                            'open_interest': int(row.get('option_open_interest', row.get('open_interest', 0)) or 0),
                            'implied_volatility': _normalize_iv(row.get('option_implied_volatility', 0)),
                            'delta': float(row.get('option_delta', 0)),
                            'gamma': float(row.get('option_gamma', 0)),
                            'theta': float(row.get('option_theta', 0)),
                            'vega': float(row.get('option_vega', 0))
                        }
                        result['options'].append(option_data)
            finally:
                MoomooConnection._option_chain_gate.release()

            if not result['expiration'] and result['options']:
                result['expiration'] = result['options'][0]['expiration']

            self._cache_option_chain(symbol, expiration, right, result)
            self._complete_pending_request(request_key, result)
            return result
        except Exception as e:
            logger.error(f"Error retrieving option chain for {symbol}: {str(e)}")
            logger.debug(traceback.format_exc())
            self._complete_pending_request(request_key, None)
            return None

    def get_portfolio(self):
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
                position_types = pos_data['code'].apply(_infer_security_type_from_code)
                option_positions = pos_data[position_types == 'OPT']
                if not option_positions.empty:
                    opt_ret, opt_snaps = self.quote_ctx.get_market_snapshot(option_positions['code'].tolist())
                    if opt_ret == RET_OK:
                        opt_snaps_dict = opt_snaps.set_index('code').to_dict('index')
                    else:
                        opt_snaps_dict = {}
                else:
                    opt_snaps_dict = {}

                for _, pos in pos_data.iterrows():
                    symbol = pos.get('code', '')
                    sec_type = _infer_security_type_from_code(symbol)
                    option_metadata = _parse_option_code_metadata(symbol) if sec_type == 'OPT' else None

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
                            'expiration': snap.get('option_expiry_date', '').replace('-', '') or (option_metadata or {}).get('expiration', ''),
                            'strike': _safe_float(snap.get('option_strike_price', (option_metadata or {}).get('strike', 0))),
                            'option_type': 'CALL' if snap.get('option_type') == 'CALL' else 'PUT'
                        })
                    elif sec_type == 'OPT' and option_metadata:
                        pos_details.update({
                            'expiration': option_metadata.get('expiration', ''),
                            'strike': _safe_float(option_metadata.get('strike', 0)),
                            'option_type': option_metadata.get('option_type', '')
                        })

                    account_info['positions'][pos_key] = pos_details

            return account_info
        except Exception as e:
            self.last_error = f"Error getting portfolio: {str(e)}"
            logger.error(f"Error getting portfolio: {str(e)}")
            logger.debug(traceback.format_exc())
            return None

    def create_option_contract(self, symbol, expiry, strike, option_type):
        symbol = self._format_symbol(symbol)
        opt_type = OptionType.CALL if option_type.upper() in ['C', 'CALL'] else OptionType.PUT

        moomoo_expiry = f"{expiry[0:4]}-{expiry[4:6]}-{expiry[6:8]}" if len(expiry) == 8 else expiry

        ret, data = self.quote_ctx.get_option_chain(code=symbol, start=moomoo_expiry, end=moomoo_expiry, option_type=opt_type)
        if ret == RET_OK:
            match = data[data['strike_price'] == float(strike)]
            if not match.empty:
                return match.iloc[0]['code']

        return None


