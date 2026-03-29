"""
Stock and Options Trading Connection Module for moomoo
"""

import logging
import time
import os
import socket
import re
import traceback
import threading
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


def _parse_option_code_metadata(code):
    if not code:
        return None

    normalized = str(code).strip()
    suffix = normalized.split('.', 1)[1] if '.' in normalized else normalized
    match = re.match(r'^(?P<underlying>[A-Z]+)(?P<expiry>\d{6})(?P<right>[CP])(?P<strike>\d+)$', suffix)
    if not match:
        return None

    expiry = match.group('expiry')
    year = 2000 + int(expiry[0:2])
    month = expiry[2:4]
    day = expiry[4:6]

    return {
        'underlying': match.group('underlying'),
        'expiration': f"{year:04d}{month}{day}",
        'strike': int(match.group('strike')) / 1000,
        'option_type': 'CALL' if match.group('right') == 'C' else 'PUT'
    }


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
    
    This class implements a singleton-like pattern per configuration to ensure
    connections are reused and properly managed across the application lifecycle.
    """
    
    # Class-level cache of connection instances to prevent multiple connections
    _instances = {}
    _instance_lock = threading.Lock()
    
    def __new__(cls, host='127.0.0.1', port=11111, readonly=True, account_id=None, portfolio_env=None, security_firm=None):
        """
        Singleton pattern - return existing instance for same config or create new one
        """
        # Create a key based on connection parameters
        key = f"{host}:{port}:{readonly}:{account_id}:{portfolio_env}:{security_firm}"
        
        with cls._instance_lock:
            if key not in cls._instances:
                # Create new instance
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instances[key] = instance
                logger.info(f"Created new MoomooConnection instance for {host}:{port}")
            else:
                logger.debug(f"Reusing existing MoomooConnection instance for {host}:{port}")
            
            return cls._instances[key]
    
    def __init__(self, host='127.0.0.1', port=11111, readonly=True, account_id=None, portfolio_env=None, security_firm=None):
        """
        Initialize the moomoo connection
        
        Args:
            host (str): OpenD host (default: 127.0.0.1)
            port (int): OpenD port (default: 11111)
            readonly (bool): Whether to operate in readonly mode (simulation)
        """
        # Prevent re-initialization of existing instances
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
        self.trading_password = os.environ.get('MOOMOO_TRADING_PASSWORD', '')
        self._connection_lock = threading.Lock()
        self._last_activity = None
        self._initialized = True
        
        # Rate limiting for API calls (moomoo allows max 10 requests per 30 seconds)
        # Using 8 to be conservative and account for burst scenarios
        self._request_timestamps = []
        self._rate_limit_lock = threading.Lock()
        self._max_requests_per_window = 8  # Conservative: 8 instead of 10
        self._rate_limit_window = 30  # seconds
        self._burst_threshold = 5  # requests in 5 seconds triggers burst protection
        self._burst_window = 5  # seconds for burst detection
        
        # Stock price cache (cache for 30 seconds)
        self._stock_price_cache = {}
        self._stock_price_cache_lock = threading.Lock()
        self._stock_price_ttl = 30  # seconds
        
        # Failed quote-rights ticker cache (skip for 5 minutes)
        self._failed_tickers = {}
        self._failed_tickers_lock = threading.Lock()
        self._failed_ticker_ttl = 300  # 5 minutes
        
        # Option chain cache (cache for 60 seconds)
        self._option_chain_cache = {}
        self._cache_lock = threading.Lock()
        self._cache_ttl = 60  # seconds
        
        # Pending request deduplication (prevents parallel identical requests)
        self._pending_requests = {}
        self._pending_requests_lock = threading.Lock()
        
        # Connection metrics
        self._connection_created_at = datetime.now()
        self._api_calls_count = 0
        self._rate_limit_waits = 0

    def _check_rate_limit(self):
        """
        Check and enforce rate limiting for API requests.
        Waits if necessary to stay within moomoo's rate limits.
        Includes burst detection to prevent rapid-fire requests.
        """
        with self._rate_limit_lock:
            now = time.time()
            
            # Remove timestamps older than the window
            self._request_timestamps = [
                ts for ts in self._request_timestamps 
                if now - ts < self._rate_limit_window
            ]
            
            # Check for burst: too many requests in short time
            recent_requests = [ts for ts in self._request_timestamps if now - ts < self._burst_window]
            if len(recent_requests) >= self._burst_threshold:
                # Burst detected, add extra cooldown
                burst_wait = 5.0  # 5 second cooldown for burst
                logger.warning(f"Burst detected ({len(recent_requests)} requests in {self._burst_window}s). Adding {burst_wait}s cooldown...")
                time.sleep(burst_wait)
                self._rate_limit_waits += 1
                
                # Recalculate after cooldown
                now = time.time()
                self._request_timestamps = [
                    ts for ts in self._request_timestamps 
                    if now - ts < self._rate_limit_window
                ]
            
            # If we've hit the limit, wait until we can make another request
            if len(self._request_timestamps) >= self._max_requests_per_window:
                # Calculate how long to wait
                oldest_request = min(self._request_timestamps)
                wait_time = self._rate_limit_window - (now - oldest_request) + 0.5  # larger buffer
                
                if wait_time > 0:
                    logger.warning(f"Rate limit reached ({len(self._request_timestamps)}/{self._max_requests_per_window}). Waiting {wait_time:.1f}s...")
                    time.sleep(wait_time)
                    self._rate_limit_waits += 1
                    
                    # Recalculate after waiting
                    now = time.time()
                    self._request_timestamps = [
                        ts for ts in self._request_timestamps 
                        if now - ts < self._rate_limit_window
                    ]
            
            # Add current request timestamp and increment counter
            self._request_timestamps.append(now)
            self._api_calls_count += 1
    
    def _get_cached_option_chain(self, symbol, expiration, right):
        """
        Get cached option chain if available and not expired.
        Returns None if not in cache or expired.
        """
        cache_key = f"{symbol}_{expiration}_{right}"
        with self._cache_lock:
            if cache_key in self._option_chain_cache:
                cached_data, timestamp = self._option_chain_cache[cache_key]
                if time.time() - timestamp < self._cache_ttl:
                    logger.debug(f"Using cached option chain for {cache_key}")
                    return cached_data
                else:
                    # Expired, remove from cache
                    del self._option_chain_cache[cache_key]
        return None
    
    def _cache_option_chain(self, symbol, expiration, right, data):
        """
        Cache option chain result.
        """
        cache_key = f"{symbol}_{expiration}_{right}"
        with self._cache_lock:
            self._option_chain_cache[cache_key] = (data, time.time())
            logger.debug(f"Cached option chain for {cache_key}")

    @classmethod
    def get_connection_pool_stats(cls):
        """
        Get statistics about the connection pool
        
        Returns:
            dict: Pool statistics including number of cached instances
        """
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
        Connect to moomoo OpenD with thread-safety and activity tracking
        """
        with self._connection_lock:
            # Double-check after acquiring lock
            if self._connected and self.is_connected():
                self._last_activity = datetime.now()
                return True
            
            try:
                logger.info(f"Connecting to moomoo OpenD at {self.host}:{self.port}")
                
                # Close existing contexts if they exist to avoid leaks
                self._safe_disconnect()
                
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
                self._last_activity = datetime.now()
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
                self._safe_disconnect()
                return False
    
    def _safe_disconnect(self):
        """
        Safely disconnect without logging (internal use)
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
    
    def disconnect(self):
        """
        Disconnect from moomoo OpenD
        """
        with self._connection_lock:
            self._safe_disconnect()
            self._account_cache = None
            logger.info("Disconnected from moomoo")
    
    def is_connected(self):
        """
        Check if connected to moomoo with connection health validation
        """
        if not self._connected or self.quote_ctx is None:
            return False

        try:
            ret, data = self.quote_ctx.get_global_state()
            if ret == RET_OK:
                self._last_activity = datetime.now()
                return True
            else:
                logger.debug(f"Connection check failed: {data}")
                return False
        except Exception as e:
            logger.debug(f"Connection health check failed: {e}")
            return False

    def get_connection_info(self):
        """
        Get connection status and statistics for debugging
        """
        idle_time = None
        if self._last_activity:
            idle_time = (datetime.now() - self._last_activity).total_seconds()
        
        uptime_seconds = None
        if self._connection_created_at:
            uptime_seconds = (datetime.now() - self._connection_created_at).total_seconds()
        
        # Get cache statistics
        with self._stock_price_cache_lock:
            stock_price_cache_size = len(self._stock_price_cache)
        with self._failed_tickers_lock:
            failed_tickers_count = len(self._failed_tickers)
        
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
            'api_calls_count': self._api_calls_count,
            'rate_limit_waits': self._rate_limit_waits,
            'stock_price_cache_size': stock_price_cache_size,
            'failed_tickers_count': failed_tickers_count,
            'rate_limit_config': {
                'max_requests_per_window': self._max_requests_per_window,
                'rate_limit_window': self._rate_limit_window,
                'burst_threshold': self._burst_threshold,
                'burst_window': self._burst_window,
            }
        }

    def _format_symbol(self, symbol):
        """Format symbol to moomoo format (e.g., US.AAPL)"""
        if '.' not in symbol:
            return f"US.{symbol}"
        return symbol

    def _get_cached_stock_price(self, symbol):
        """Get cached stock price if available and not expired."""
        with self._stock_price_cache_lock:
            if symbol in self._stock_price_cache:
                price, timestamp = self._stock_price_cache[symbol]
                if time.time() - timestamp < self._stock_price_ttl:
                    logger.debug(f"Using cached stock price for {symbol}: {price}")
                    return price
                else:
                    del self._stock_price_cache[symbol]
        return None
    
    def _cache_stock_price(self, symbol, price):
        """Cache stock price result."""
        with self._stock_price_cache_lock:
            self._stock_price_cache[symbol] = (price, time.time())
            logger.debug(f"Cached stock price for {symbol}: {price}")
    
    def _is_ticker_failed(self, symbol):
        """Check if ticker has failed quote rights recently."""
        with self._failed_tickers_lock:
            if symbol in self._failed_tickers:
                failure_time = self._failed_tickers[symbol]
                if time.time() - failure_time < self._failed_ticker_ttl:
                    logger.debug(f"Skipping {symbol} - failed quote rights (cached)")
                    return True
                else:
                    # Expired, remove from cache
                    del self._failed_tickers[symbol]
        return False
    
    def _mark_ticker_failed(self, symbol):
        """Mark ticker as failed due to quote rights."""
        with self._failed_tickers_lock:
            self._failed_tickers[symbol] = time.time()
            logger.info(f"Cached quote-rights failure for {symbol} (will skip for {self._failed_ticker_ttl}s)")
    
    def _get_or_create_pending_request(self, request_key):
        """
        Get an existing pending request or create a new one.
        Returns (event, is_new) tuple. If is_new is True, caller must complete the request.
        """
        with self._pending_requests_lock:
            if request_key in self._pending_requests:
                # Request already in progress, return existing event
                return self._pending_requests[request_key], False
            else:
                # Create new pending request
                event = threading.Event()
                self._pending_requests[request_key] = event
                return event, True
    
    def _complete_pending_request(self, request_key, result):
        """
        Complete a pending request and notify all waiters.
        """
        with self._pending_requests_lock:
            if request_key in self._pending_requests:
                event = self._pending_requests.pop(request_key)
                # Store result for waiters to retrieve (using a simple shared dict)
                self._pending_requests[f"{request_key}_result"] = result
                event.set()
    
    def _wait_for_pending_request(self, request_key, timeout=30):
        """
        Wait for a pending request to complete and return its result.
        """
        event, is_new = self._get_or_create_pending_request(request_key)
        
        if not is_new:
            # Wait for the existing request to complete
            logger.debug(f"Waiting for pending request: {request_key}")
            event.wait(timeout=timeout)
            
            # Get the result
            with self._pending_requests_lock:
                result_key = f"{request_key}_result"
                if result_key in self._pending_requests:
                    return self._pending_requests.pop(result_key)
            
            # Timeout or no result
            logger.warning(f"Timeout waiting for pending request: {request_key}")
            return None
        
        return None  # Caller should proceed with the request

    def get_stock_price(self, symbol):
        """
        Get the current price of a stock with caching, failure tracking, and request deduplication.
        """
        symbol = self._format_symbol(symbol)
        request_key = f"stock_price:{symbol}"
        
        # Check if ticker is in failure cache
        if self._is_ticker_failed(symbol):
            logger.debug(f"Skipping API call for {symbol} - quote rights failure cached")
            return None
        
        # Check cache first
        cached_price = self._get_cached_stock_price(symbol)
        if cached_price is not None:
            return cached_price
        
        # Check for pending duplicate request
        pending_result = self._wait_for_pending_request(request_key)
        if pending_result is not None:
            # Got result from pending request
            return pending_result
        
        # This thread will make the actual API call
        try:
            # Check rate limit before making API call
            self._check_rate_limit()
            
            if not self.is_connected():
                if not self.connect():
                    self._complete_pending_request(request_key, None)
                    return None
            
            ret, data = self.quote_ctx.get_market_snapshot([symbol])
            if ret == RET_OK and not data.empty:
                price = data.iloc[0].get('last_price')
                if price is None or price == 0:
                    price = data.iloc[0].get('prev_close_price')
                price = float(price)
                # Cache successful result
                self._cache_stock_price(symbol, price)
                # Complete pending request for other threads
                self._complete_pending_request(request_key, price)
                return price
            else:
                error_msg = str(data) if data else "Unknown error"
                logger.error(f"Failed to get price for {symbol}: {error_msg}")
                # Check if it's a quote rights error
                if "No right to get the quote" in error_msg or "quote right" in error_msg.lower():
                    self._mark_ticker_failed(symbol)
                self._complete_pending_request(request_key, None)
                return None
        except Exception as e:
            error_str = str(e)
            logger.error(f"Error getting {symbol} price: {error_str}")
            # Check if it's a quote rights error
            if "No right to get the quote" in error_str or "quote right" in error_str.lower():
                self._mark_ticker_failed(symbol)
            self._complete_pending_request(request_key, None)
            return None

    def get_option_expiration_dates(self, symbol):
        """
        Get available option expiration dates for a symbol.
        This method is rate-limited and deduplicated.
        
        Args:
            symbol: Stock symbol (e.g., 'AAPL' or 'US.AAPL')
            
        Returns:
            tuple: (ret_code, data) where data is a DataFrame with expiration dates
        """
        symbol = self._format_symbol(symbol)
        request_key = f"option_exp:{symbol}"
        
        # Check for pending duplicate request
        # Note: We use the same pattern but need to return tuple
        event, is_new = self._get_or_create_pending_request(request_key)
        
        if not is_new:
            # Wait for the existing request to complete
            logger.debug(f"Waiting for pending request: {request_key}")
            event.wait(timeout=30)
            
            # Get the result
            with self._pending_requests_lock:
                result_key = f"{request_key}_result"
                if result_key in self._pending_requests:
                    return self._pending_requests.pop(result_key)
            
            # Timeout or no result
            logger.warning(f"Timeout waiting for pending request: {request_key}")
            return RET_ERROR, None
        
        # This thread will make the actual API call
        try:
            # Check rate limit before making API call
            self._check_rate_limit()
            
            if not self.is_connected():
                if not self.connect():
                    result = (RET_ERROR, None)
                    self._complete_pending_request(request_key, result)
                    return result
            
            ret, data = self.quote_ctx.get_option_expiration_date(code=symbol)
            result = (ret, data)
            self._complete_pending_request(request_key, result)
            return result
        except Exception as e:
            logger.error(f"Error getting option expirations for {symbol}: {str(e)}")
            result = (RET_ERROR, None)
            self._complete_pending_request(request_key, result)
            return result

    def get_option_chain(self, symbol, expiration=None, right='C', target_strike=None):
        """
        Get option chain for a given symbol with caching and request deduplication.
        """
        symbol = self._format_symbol(symbol)
        cache_key = f"{symbol}_{expiration}_{right}"
        request_key = f"option_chain:{cache_key}"
        
        # Check cache first
        cached_result = self._get_cached_option_chain(symbol, expiration, right)
        if cached_result is not None:
            return cached_result
        
        # Check for pending duplicate request
        event, is_new = self._get_or_create_pending_request(request_key)
        
        if not is_new:
            # Wait for the existing request to complete
            logger.debug(f"Waiting for pending request: {request_key}")
            event.wait(timeout=30)
            
            # Get the result from cache (should be populated by the other thread)
            cached_result = self._get_cached_option_chain(symbol, expiration, right)
            if cached_result is not None:
                return cached_result
            
            # If not in cache, something went wrong
            logger.warning(f"Pending request completed but result not in cache: {request_key}")
            return None
        
        # This thread will make the actual API call
        try:
            # Check rate limit before making API call
            self._check_rate_limit()
            
            if not self.is_connected():
                if not self.connect():
                    self._complete_pending_request(request_key, None)
                    return None
            
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
                self._complete_pending_request(request_key, None)
                return None
            
            result = {
                'symbol': symbol.split('.')[-1],
                'expiration': expiration.replace('-', '') if expiration else '',
                'stock_price': None,
                'right': right,
                'options': []
            }
            
            if data.empty:
                # Cache empty result too
                self._cache_option_chain(symbol, expiration, right, result)
                self._complete_pending_request(request_key, result)
                return result

            # Filtering and getting snapshots
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
                        'implied_volatility': float(row.get('option_implied_volatility', 0)),
                        'delta': float(row.get('option_delta', 0)),
                        'gamma': float(row.get('option_gamma', 0)),
                        'theta': float(row.get('option_theta', 0)),
                        'vega': float(row.get('option_vega', 0))
                    }
                    result['options'].append(option_data)
            
            if not result['expiration'] and result['options']:
                result['expiration'] = result['options'][0]['expiration']

            # Cache the result before returning and complete pending request
            self._cache_option_chain(symbol, expiration, right, result)
            self._complete_pending_request(request_key, result)
            return result
        except Exception as e:
            logger.error(f"Error retrieving option chain for {symbol}: {str(e)}")
            logger.debug(traceback.format_exc())
            self._complete_pending_request(request_key, None)
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
