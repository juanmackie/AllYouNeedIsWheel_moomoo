"""
MoomooConnection - connection lifecycle and query-only portfolio retrieval.

This class exposes ONLY query operations (accounts, positions, quotes,
option chains, watchlist groups). There is no order/unlock surface;
readonly=False is rejected at construction (see core/broker_protocol.py).
"""

import os
import threading
import time
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

try:
    from moomoo import (
        RET_ERROR,
        RET_OK,
        OpenQuoteContext,
        OpenSecTradeContext,
        OptionDataFilter,
        OptionType,
        SecurityFirm,
        TrdEnv,
        UserSecurityGroupType,
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
    UserSecurityGroupType = None
    OptionDataFilter = None

from core.broker_protocol import FORBIDDEN_SDK_MEMBERS  # noqa: F401 - documented surface for tests
from core.connection_constants import (
    _clean_account_id,
    _env_name,
    _first_non_zero,
    _infer_security_type_from_code,
    _normalize_iv,
    _normalize_security_firm,
    _normalize_trd_env,
    _parse_option_code_metadata,
    _safe_close_context,
    _safe_float,
)
from core.context_factory import create_contexts
from core.logging_config import get_logger
from core.quote_cache import OptionChainCache, PendingRequestCoordinator
from core.rate_limiter import RateLimiter
from core.ticker_utils import TickerCache, format_symbol

logger = get_logger("ayniwheel.connection", "moomoo")


def _safe_str(value) -> str:
    """Convert a value to a UTF-8-safe string for logging, replacing non-ASCII chars that crash cp1252 consoles."""
    raw = str(value)
    try:
        raw.encode("cp1252")
        return raw
    except UnicodeEncodeError:
        return raw.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def _is_rate_limit_response(value) -> bool:
    """Recognize provider responses that indicate frequency pressure."""
    text = _safe_str(value).lower()
    return any(
        marker in text
        for marker in ("rate limit", "frequency limit", "too frequent", "quota exceeded", "request limit")
    )


def _safe_cash_value(value) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric:
        return None
    return numeric


def _select_withdrawable_cash_field(acc_row) -> tuple[float, str]:
    """Choose withdrawable/cash-like USD fields, not margin buying power."""
    cash_fields = (
        "us_avl_withdrawal_cash",
        "available_funds",
        "avl_withdrawal_cash",
        "us_cash",
        "cash",
    )
    for field in cash_fields:
        value = _safe_cash_value(acc_row.get(field))
        if value is not None and value > 0:
            return value, field
    return 0.0, "none"


def _select_buying_power_field(acc_row) -> tuple[float, str]:
    """Choose the Moomoo field that represents option cash power/capacity."""
    power_fields = (
        "usd_net_cash_power",
        "max_power_short_sell",
        "power",
        "buying_power",
    )
    for field in power_fields:
        value = _safe_cash_value(acc_row.get(field))
        if value is not None and value > 0:
            return value, field
    return 0.0, "none"


def _select_account_cash_field(acc_row) -> tuple[float, str]:
    return _select_withdrawable_cash_field(acc_row)


def _cash_diagnostics_from_acc_row(acc_row) -> dict:
    fields = [
        "us_avl_withdrawal_cash",
        "us_cash",
        "usd_net_cash_power",
        "max_power_short_sell",
        "power",
        "buying_power",
        "cash",
        "available_funds",
        "avl_withdrawal_cash",
    ]
    cash, source = _select_account_cash_field(acc_row)
    buying_power, buying_power_source = _select_buying_power_field(acc_row)
    return {
        "selected_cash": cash,
        "selected_cash_source": source,
        "selected_buying_power": buying_power,
        "selected_buying_power_source": buying_power_source,
        "fields": {field: acc_row.get(field) for field in fields},
    }


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
    _option_chain_rate_limiter = None
    _option_chain_rate_lock = threading.Lock()
    _market_timezone = ZoneInfo(os.environ.get("MARKET_TIMEZONE", "America/New_York"))

    def __new__(
        cls,
        host="127.0.0.1",
        port=11111,
        readonly=True,
        account_id=None,
        portfolio_env=None,
        security_firm=None,
        broker_cache_after_hours=True,
        chain_rate_limit_max_requests=10,
        chain_rate_limit_window_sec=30,
        chain_min_request_spacing_sec=3.0,
    ):
        cleaned_account_id = _clean_account_id(account_id)

        if TrdEnv is not None:
            default_portfolio_env = TrdEnv.SIMULATE if readonly else TrdEnv.REAL
            normalized_portfolio_env = _normalize_trd_env(portfolio_env, default_portfolio_env)
        else:
            normalized_portfolio_env = (
                str(portfolio_env).strip().upper() if portfolio_env else ("SIMULATE" if readonly else "REAL")
            )

        if SecurityFirm is not None:
            normalized_security_firm = _normalize_security_firm(
                security_firm or os.environ.get("MOOMOO_SECURITY_FIRM"),
                SecurityFirm.FUTUSECURITIES,
            )
        else:
            normalized_security_firm = (
                str(security_firm or os.environ.get("MOOMOO_SECURITY_FIRM") or "").strip().upper()
            )

        normalized_portfolio_env_key = getattr(
            normalized_portfolio_env, "name", str(normalized_portfolio_env).strip().upper()
        )
        normalized_security_firm_key = getattr(
            normalized_security_firm, "name", str(normalized_security_firm).strip().upper()
        )
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

    def __init__(
        self,
        host="127.0.0.1",
        port=11111,
        readonly=True,
        account_id=None,
        portfolio_env=None,
        security_firm=None,
        broker_cache_after_hours=True,
        chain_rate_limit_max_requests=10,
        chain_rate_limit_window_sec=30,
        chain_min_request_spacing_sec=3.0,
    ):
        if not readonly:
            raise ValueError(
                "readonly=False is not supported: the wheel app is structurally "
                "query-only (see core/broker_protocol.py). There is no execution surface."
            )
        self._broker_cache_after_hours = broker_cache_after_hours
        if self._initialized:
            return

        self.host = host
        self.port = port
        self.readonly = readonly
        self.account_id = _clean_account_id(account_id)
        self.portfolio_env = _normalize_trd_env(portfolio_env, TrdEnv.SIMULATE if readonly else TrdEnv.REAL)
        self.security_firm = _normalize_security_firm(
            security_firm or os.environ.get("MOOMOO_SECURITY_FIRM"), SecurityFirm.FUTUSECURITIES
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
            max_requests_per_window=45, rate_limit_window=60, burst_threshold=20, burst_window=10
        )
        with MoomooConnection._option_chain_rate_lock:
            if MoomooConnection._option_chain_rate_limiter is None:
                oc_rl = RateLimiter(
                    max_requests_per_window=chain_rate_limit_max_requests,
                    rate_limit_window=chain_rate_limit_window_sec,
                    # The quota/window is the chain burst boundary; the minimum
                    # spacing already prevents an unsafe request burst.
                    burst_threshold=chain_rate_limit_max_requests,
                    burst_window=chain_rate_limit_window_sec,
                    min_request_spacing=chain_min_request_spacing_sec,
                )
                MoomooConnection._option_chain_rate_limiter = oc_rl
            else:
                MoomooConnection._option_chain_rate_limiter.configure(
                    max_requests_per_window=chain_rate_limit_max_requests,
                    rate_limit_window=chain_rate_limit_window_sec,
                    min_request_spacing=chain_min_request_spacing_sec,
                    burst_threshold=chain_rate_limit_max_requests,
                    burst_window=chain_rate_limit_window_sec,
                )
        self._ticker_cache = TickerCache(price_ttl=120, failed_ttl=300)

        # Quote caches + single-flight coordination live in dedicated modules.
        self._quote_cache = OptionChainCache(
            chain_ttl=180,
            expiration_ttl=300,
            broker_cache_after_hours=self._broker_cache_after_hours,
        )
        self._pending_requests = PendingRequestCoordinator(result_ttl_seconds=1.0)

        self._connection_created_at = datetime.now()
        self._cash_diagnostics_logged = False

    @classmethod
    def get_connection_pool_stats(cls):
        with cls._instance_lock:
            return {"cached_instances": len(cls._instances), "instance_keys": list(cls._instances.keys())}

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
            if ret != RET_OK or data is None or getattr(data, "empty", True):
                self._account_cache = []
                return self._account_cache

            accounts = []
            for record in data.to_dict("records"):
                accounts.append(
                    {
                        "acc_id": str(record.get("acc_id", "")).strip(),
                        "trd_env": record.get("trd_env", TrdEnv.SIMULATE),
                        "security_firm": record.get("security_firm", ""),
                    }
                )

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
            if account.get("acc_id") == str(account_id):
                return account
        return None

    def _find_account_by_env(self, trd_env):
        for account in self._get_available_accounts():
            if account.get("trd_env") == trd_env:
                return account
        return None

    def _resolve_portfolio_account(self):
        desired_env = self.portfolio_env
        if self.account_id:
            matched_account = self._find_account_by_id(self.account_id)
            if matched_account:
                return matched_account.get("trd_env", TrdEnv.REAL), matched_account.get("acc_id")
            return desired_env, self.account_id
        fallback_account = self._find_account_by_env(desired_env)
        return desired_env, fallback_account.get("acc_id") if fallback_account else ""

    def _format_trade_error(self, action, data, trd_env, account_id=""):
        details = str(data)
        env_label = _env_name(trd_env)
        account_label = f" account {account_id}" if account_id else " account"
        available_accounts = self._get_available_accounts()
        available_accounts_text = ", ".join(
            f"{account.get('acc_id')} ({_env_name(account.get('trd_env'))})"
            for account in available_accounts
            if account.get("acc_id")
        )

        if "No available real accounts" in details or "Nonexisting acc_id" in details:
            suffix = ""
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

    def health_check(self):
        """
        Perform a lightweight health check and reconnect if needed.
        Returns True if connection is healthy after the check.
        """
        if self.is_connected():
            return True
        logger.warning("Connection health check failed, attempting reconnect")
        return self.connect()

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
            logger.debug(f"Connection health check failed: {data}")
            self._connected = False
            return False
        except Exception as e:
            logger.debug(f"Connection health check failed: {e}")
            self._connected = False
            return False

    def get_connection_info(self):
        idle_time = None
        if self._last_activity:
            idle_time = (datetime.now() - self._last_activity).total_seconds()
        uptime_seconds = None
        if self._connection_created_at:
            uptime_seconds = (datetime.now() - self._connection_created_at).total_seconds()

        price_size, failed_size = self._ticker_cache.get_cache_stats()
        chain_limiter = MoomooConnection._option_chain_rate_limiter

        return {
            "connected": self._connected,
            "is_healthy": self.is_connected(),
            "host": self.host,
            "port": self.port,
            "last_activity": self._last_activity.isoformat() if self._last_activity else None,
            "idle_seconds": idle_time,
            "uptime_seconds": uptime_seconds,
            "has_quote_ctx": self.quote_ctx is not None,
            "has_trd_ctx": self.trd_ctx is not None,
            "readonly": self.readonly,
            "portfolio_env": _env_name(self.portfolio_env),
            "security_firm": str(self.security_firm),
            "account_id": self.account_id if self.account_id else "auto",
            "stock_price_cache_size": price_size,
            "failed_tickers_count": failed_size,
            "rate_limit_stats": self._rate_limiter.get_stats(),
            "rate_limit_config": {
                "max_requests_per_window": self._rate_limiter.max_requests_per_window,
                "rate_limit_window": self._rate_limiter.rate_limit_window,
                "burst_threshold": self._rate_limiter.burst_threshold,
                "burst_window": self._rate_limiter.burst_window,
            },
            "option_chain_rate_limit_stats": chain_limiter.get_stats() if chain_limiter else None,
            "option_chain_rate_limit_config": {
                "max_requests_per_window": chain_limiter.max_requests_per_window,
                "rate_limit_window": chain_limiter.rate_limit_window,
                "min_request_spacing": chain_limiter.get_stats().get("min_request_spacing"),
            }
            if chain_limiter
            else None,
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

    def get_option_expiration_dates(self, symbol):
        symbol = self._format_symbol(symbol)
        request_key = f"option_expirations:{symbol}"

        cached_result = self._quote_cache.get_option_expirations(symbol)
        if cached_result is not None:
            return cached_result

        pending_result = self._pending_requests.wait_for(request_key)
        if pending_result is not None:
            cached_result = self._quote_cache.get_option_expirations(symbol)
            return cached_result if cached_result is not None else pending_result

        try:
            self._rate_limiter.check_rate_limit()
            if not self.is_connected():
                if not self.connect():
                    result = (RET_ERROR, None)
                    self._pending_requests.complete(request_key, result)
                    return result
            ret, data = self.quote_ctx.get_option_expiration_date(code=symbol)
            result = (ret, data)
            self._quote_cache.cache_option_expirations(symbol, result)
            self._pending_requests.complete(request_key, result)
            return result
        except Exception as e:
            logger.error(f"Error getting option expirations for {symbol}: {str(e)}")
            result = (RET_ERROR, None)
            self._pending_requests.complete(request_key, result)
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

        pending_result = self._pending_requests.wait_for(request_key)
        if pending_result is not None:
            return pending_result

        try:
            self._rate_limiter.check_rate_limit()
            if not self.is_connected():
                if not self.connect():
                    self._pending_requests.complete(request_key, None)
                    return None

            ret, data = self.quote_ctx.get_market_snapshot([symbol])
            if ret != RET_OK or data is None or data.empty:
                logger.error(f"Failed to get stock price for {symbol}: {data}")
                self._pending_requests.complete(request_key, None)
                return None

            price = float(data.iloc[0].get("last_price", 0))
            self._cache_stock_price(symbol, price)
            self._pending_requests.complete(request_key, price)
            return price
        except Exception as e:
            logger.error(f"Error getting stock price for {symbol}: {str(e)}")
            self._pending_requests.complete(request_key, None)
            return None

    def _ensure_quote_context(self):
        if self.quote_ctx is not None and self.is_connected():
            return True
        return self.connect() and self.quote_ctx is not None

    def get_market_snapshot(self, symbols):
        if isinstance(symbols, str):
            symbols = [symbols]
        symbols = [self._format_symbol(symbol) for symbol in (symbols or []) if symbol]
        if not symbols:
            return RET_ERROR, None
        try:
            self._rate_limiter.check_rate_limit()
            if not self._ensure_quote_context():
                return RET_ERROR, None
            return self.quote_ctx.get_market_snapshot(symbols)
        except Exception as e:
            logger.error(f"Error getting market snapshot for {symbols}: {str(e)}")
            return RET_ERROR, None

    def get_capital_distribution(self, symbol):
        symbol = self._format_symbol(symbol)
        try:
            self._rate_limiter.check_rate_limit()
            if not self._ensure_quote_context():
                return RET_ERROR, None
            return self.quote_ctx.get_capital_distribution(symbol)
        except Exception as e:
            logger.error(f"Error getting capital distribution for {symbol}: {str(e)}")
            return RET_ERROR, None

    def get_capital_flow(self, symbol, period_type=None, start=None, end=None):
        symbol = self._format_symbol(symbol)
        try:
            self._rate_limiter.check_rate_limit()
            if not self._ensure_quote_context():
                return RET_ERROR, None
            if period_type is None:
                return self.quote_ctx.get_capital_flow(symbol, start=start, end=end)
            return self.quote_ctx.get_capital_flow(symbol, period_type=period_type, start=start, end=end)
        except Exception as e:
            logger.error(f"Error getting capital flow for {symbol}: {str(e)}")
            return RET_ERROR, None

    def get_broker_queue(self, symbol):
        symbol = self._format_symbol(symbol)
        try:
            self._rate_limiter.check_rate_limit()
            if not self._ensure_quote_context():
                return RET_ERROR, None, None
            return self.quote_ctx.get_broker_queue(symbol)
        except Exception as e:
            logger.error(f"Error getting broker queue for {symbol}: {str(e)}")
            return RET_ERROR, None, None

    def get_history_kline(
        self,
        symbol,
        start=None,
        end=None,
        ktype=None,
        autype=None,
        fields=None,
        max_count=120,
        page_req_key=None,
        extended_time=False,
        session=None,
    ):
        symbol = self._format_symbol(symbol)
        try:
            self._rate_limiter.check_rate_limit()
            if not self._ensure_quote_context():
                return RET_ERROR, None, None
            kwargs = {
                "code": symbol,
                "start": start,
                "end": end,
                "max_count": max_count,
                "page_req_key": page_req_key,
                "extended_time": extended_time,
            }
            if ktype is not None:
                kwargs["ktype"] = ktype
            if autype is not None:
                kwargs["autype"] = autype
            if fields is not None:
                kwargs["fields"] = fields
            if session is not None:
                kwargs["session"] = session
            return self.quote_ctx.request_history_kline(**kwargs)
        except Exception as e:
            logger.error(f"Error getting history kline for {symbol}: {str(e)}")
            return RET_ERROR, None, None

    def get_cur_kline(self, symbol, num, ktype=None, autype=None):
        symbol = self._format_symbol(symbol)
        try:
            self._rate_limiter.check_rate_limit()
            if not self._ensure_quote_context():
                return RET_ERROR, None
            kwargs = {"code": symbol, "num": num}
            if ktype is not None:
                kwargs["ktype"] = ktype
            if autype is not None:
                kwargs["autype"] = autype
            return self.quote_ctx.get_cur_kline(**kwargs)
        except Exception as e:
            logger.error(f"Error getting current kline for {symbol}: {str(e)}")
            return RET_ERROR, None

    def get_owner_plate(self, symbol):
        symbol = self._format_symbol(symbol)
        try:
            self._rate_limiter.check_rate_limit()
            if not self._ensure_quote_context():
                return RET_ERROR, None
            return self.quote_ctx.get_owner_plate([symbol])
        except Exception as e:
            logger.error(f"Error getting owner plate for {symbol}: {str(e)}")
            return RET_ERROR, None

    def get_option_chain(
        self, symbol, expiration=None, right="C", target_strike=None, data_filter=None, force_refresh=False
    ):
        symbol = self._format_symbol(symbol)
        cache_key = f"{symbol}_{expiration}_{right}"
        request_key = f"option_chain:{cache_key}"

        if data_filter is None and not force_refresh:
            cached_result = self._quote_cache.get_option_chain(symbol, expiration, right)
            if cached_result is not None:
                return cached_result

            pending_result = self._pending_requests.wait_for(request_key)
            if pending_result is not None:
                cached_result = self._quote_cache.get_option_chain(symbol, expiration, right)
                if cached_result is not None:
                    return cached_result
                return pending_result

        try:
            self._option_chain_rate_limiter.check_rate_limit()
            if not self.is_connected():
                if not self.connect():
                    self._pending_requests.complete(request_key, None)
                    return None

            opt_type = OptionType.CALL if right == "C" else OptionType.PUT
            start_date = None
            end_date = None
            if expiration:
                if len(expiration) == 8:
                    start_date = f"{expiration[0:4]}-{expiration[4:6]}-{expiration[6:8]}"
                    end_date = start_date

            result = {
                "symbol": symbol.split(".")[-1],
                "expiration": expiration.replace("-", "") if expiration else "",
                "stock_price": None,
                "right": right,
                "options": [],
            }

            if data_filter is not None and OptionDataFilter is None:
                logger.warning("OptionDataFilter not available in this SDK version, ignoring filter")

            self._acquire_option_chain_gate(request_key)
            try:
                chain_kwargs = dict(code=symbol, start=start_date, end=end_date, option_type=opt_type)
                if data_filter is not None and OptionDataFilter is not None:
                    chain_kwargs["data_filter"] = data_filter
                ret, data = self.quote_ctx.get_option_chain(**chain_kwargs)

                if ret != RET_OK:
                    if _is_rate_limit_response(data):
                        self._option_chain_rate_limiter.record_rate_limit(_safe_str(data))
                    logger.error(f"Failed to get option chain for {symbol}: {_safe_str(data)}")
                    self._pending_requests.complete(request_key, None)
                    return None

                if data.empty:
                    if data_filter is None:
                        self._quote_cache.cache_option_chain(symbol, expiration, right, result)
                    self._pending_requests.complete(request_key, result)
                    return result

                if target_strike:
                    data["strike_diff"] = (data["strike_price"] - float(target_strike)).abs()
                    data = data.sort_values("strike_diff").head(20)

                option_codes = data["code"].tolist()
                if not option_codes:
                    if data_filter is None:
                        self._quote_cache.cache_option_chain(symbol, expiration, right, result)
                    self._pending_requests.complete(request_key, result)
                    return result

                ret, snap_data = self.quote_ctx.get_market_snapshot(option_codes)
                quote_fetched_at_utc = datetime.now(timezone.utc).isoformat()
                if ret == RET_OK:
                    for _, row in snap_data.iterrows():
                        opt_expiry = row.get("option_expiry_date", "") or row.get("strike_time", "")
                        if opt_expiry:
                            opt_expiry = opt_expiry.replace("-", "")

                        option_data = {
                            "strike": float(row.get("option_strike_price", 0)),
                            "expiration": opt_expiry,
                            "option_type": "CALL" if row.get("option_type") == "CALL" else "PUT",
                            "bid": float(row.get("bid_price", 0)),
                            "ask": float(row.get("ask_price", 0)),
                            "last": float(row.get("last_price", 0)),
                            "volume": int(row.get("volume", 0)),
                            "open_interest": int(row.get("option_open_interest", row.get("open_interest", 0)) or 0),
                            "implied_volatility": _normalize_iv(row.get("option_implied_volatility", 0)),
                            "delta": float(row.get("option_delta", 0)),
                            "gamma": float(row.get("option_gamma", 0)),
                            "theta": float(row.get("option_theta", 0)),
                            "vega": float(row.get("option_vega", 0)),
                            # Broker quote timestamp — preserved verbatim so the
                            # decision layer can fail closed on stale quotes.
                            "update_time": str(row.get("update_time", "") or ""),
                            "quote_fetched_at_utc": quote_fetched_at_utc,
                        }
                        result["options"].append(option_data)
            finally:
                MoomooConnection._option_chain_gate.release()

            if not result["expiration"] and result["options"]:
                result["expiration"] = result["options"][0]["expiration"]

            if data_filter is None:
                self._quote_cache.cache_option_chain(symbol, expiration, right, result)
            self._pending_requests.complete(request_key, result)
            return result
        except Exception as e:
            if _is_rate_limit_response(e):
                self._option_chain_rate_limiter.record_rate_limit(_safe_str(e))
            logger.error(f"Error retrieving option chain for {symbol}: {_safe_str(e)}")
            logger.debug(traceback.format_exc())
            self._pending_requests.complete(request_key, None)
            return None

    def get_portfolio(self):
        if not self.is_connected():
            if not self.connect():
                return None

        try:
            trd_env, account_id = self._resolve_portfolio_account()
            ret, acc_data = self.trd_ctx.accinfo_query(trd_env=trd_env, acc_id=self._account_id_arg(account_id))
            if ret != RET_OK:
                self.last_error = self._format_trade_error("get account info", acc_data, trd_env, account_id)
                logger.error(self.last_error)
                return None

            acc = acc_data.iloc[0]
            if not self._cash_diagnostics_logged:
                diagnostics = _cash_diagnostics_from_acc_row(acc)
                logger.warning(
                    "Account cash field selection diagnostics: %s",
                    _safe_str(diagnostics),
                )
                self._cash_diagnostics_logged = True
            available_cash, available_cash_source = _select_account_cash_field(acc)
            buying_power, buying_power_source = _select_buying_power_field(acc)
            account_value = _first_non_zero(acc.get("usd_assets"), acc.get("us_cash"), acc.get("total_assets", 0))
            excess_liquidity = _first_non_zero(
                acc.get("usd_net_cash_power"),
                acc.get("us_avl_withdrawal_cash"),
                acc.get("available_funds"),
                acc.get("avl_withdrawal_cash", 0),
            )
            initial_margin = _first_non_zero(
                acc.get("initial_margin"),
                acc.get("margin_call_margin"),
                acc.get("maintenance_margin"),
                acc.get("frozen_cash", 0),
            )

            account_info = {
                "account_id": str(acc.get("acc_id", account_id or "")),
                "trading_env": _env_name(trd_env),
                "available_cash": available_cash,
                "available_cash_source": available_cash_source,
                "buying_power": buying_power,
                "buying_power_source": buying_power_source,
                "account_value": account_value,
                "excess_liquidity": excess_liquidity,
                "initial_margin": initial_margin,
                "currency": "USD",
                "leverage_percentage": 0,
                "positions": {},
                "is_frozen": False,
            }

            ret, pos_data = self.trd_ctx.position_list_query(trd_env=trd_env, acc_id=self._account_id_arg(account_id))
            if ret == RET_OK and not pos_data.empty:
                position_types = pos_data["code"].apply(_infer_security_type_from_code)
                option_positions = pos_data[position_types == "OPT"]
                if not option_positions.empty:
                    opt_ret, opt_snaps = self.quote_ctx.get_market_snapshot(option_positions["code"].tolist())
                    if opt_ret == RET_OK:
                        opt_snaps_dict = opt_snaps.set_index("code").to_dict("index")
                    else:
                        opt_snaps_dict = {}
                else:
                    opt_snaps_dict = {}

                for _, pos in pos_data.iterrows():
                    symbol = pos.get("code", "")
                    sec_type = _infer_security_type_from_code(symbol)
                    option_metadata = _parse_option_code_metadata(symbol) if sec_type == "OPT" else None

                    pos_key = symbol
                    pos_details = {
                        "shares": _safe_float(pos.get("qty", 0)),
                        "avg_cost": _safe_float(pos.get("average_cost", pos.get("cost_price", 0))),
                        "market_price": _safe_float(pos.get("nominal_price", pos.get("last_price", 0))),
                        "market_value": _safe_float(pos.get("market_val", 0)),
                        "unrealized_pnl": _safe_float(pos.get("unrealized_pl", pos.get("pl_val", 0))),
                        "security_type": sec_type,
                    }

                    if sec_type == "OPT" and symbol in opt_snaps_dict:
                        snap = opt_snaps_dict[symbol]
                        pos_details.update(
                            {
                                "expiration": snap.get("option_expiry_date", "").replace("-", "")
                                or (option_metadata or {}).get("expiration", ""),
                                "strike": _safe_float(
                                    snap.get("option_strike_price", (option_metadata or {}).get("strike", 0))
                                ),
                                "option_type": "CALL" if snap.get("option_type") == "CALL" else "PUT",
                            }
                        )
                    elif sec_type == "OPT" and option_metadata:
                        pos_details.update(
                            {
                                "expiration": option_metadata.get("expiration", ""),
                                "strike": _safe_float(option_metadata.get("strike", 0)),
                                "option_type": option_metadata.get("option_type", ""),
                            }
                        )

                    account_info["positions"][pos_key] = pos_details

            return account_info
        except Exception as e:
            self.last_error = f"Error getting portfolio: {str(e)}"
            logger.error(f"Error getting portfolio: {str(e)}")
            logger.debug(traceback.format_exc())
            return None

    def get_user_security_group(self, group_type=None):
        try:
            self._rate_limiter.check_rate_limit()
            if not self._ensure_quote_context():
                return RET_ERROR, None
            kwargs = {}
            if group_type is not None:
                kwargs["group_type"] = group_type
            return self.quote_ctx.get_user_security_group(**kwargs)
        except Exception as e:
            logger.error(f"Error getting user security groups: {e}")
            logger.debug(traceback.format_exc())
            return RET_ERROR, None

    def get_user_security(self, group_name):
        try:
            self._rate_limiter.check_rate_limit()
            if not self._ensure_quote_context():
                return RET_ERROR, None
            return self.quote_ctx.get_user_security(group_name)
        except Exception as e:
            logger.error(f"Error getting securities for group '{group_name}': {e}")
            logger.debug(traceback.format_exc())
            return RET_ERROR, None

    @staticmethod
    def _normalize_security_type_row(row):
        """Normalize only broker-provided underlying classification."""
        for field in ("stock_type", "security_type", "sec_type", "stock_class"):
            value = str(row.get(field, "") or "").strip().lower()
            if value in ("etf", "ie", "exchange_traded_fund"):
                return "etf"
            if value in ("index", "idx", "ind"):
                return "index"
        name = str(row.get("name", "") or "").lower()
        if "etf" in name or name.endswith(" etf"):
            return "etf"
        return "stock"

    def get_security_types(self, symbols):
        """Return broker-verified underlying types for symbols in one snapshot."""
        requested = [self._format_symbol(symbol) for symbol in (symbols or []) if symbol]
        if not requested:
            return {}
        try:
            if not self._ensure_quote_context():
                return {symbol.split(".")[-1]: "stock" for symbol in requested}
            ret, data = self.quote_ctx.get_market_snapshot(requested)
            if ret != RET_OK or data is None or getattr(data, "empty", True):
                return {symbol.split(".")[-1]: "stock" for symbol in requested}
            result = {symbol.split(".")[-1]: "stock" for symbol in requested}
            for _, row in data.iterrows():
                code = str(row.get("code", "") or row.get("symbol", "") or "")
                if code:
                    result[code.split(".")[-1]] = self._normalize_security_type_row(row)
            return result
        except Exception as exc:
            logger.debug("get_security_types failed: %s", exc)
            return {symbol.split(".")[-1]: "stock" for symbol in requested}

    def get_security_type(self, symbol):
        """Return one broker-verified underlying type."""
        normalized = self._format_symbol(symbol).split(".")[-1]
        return self.get_security_types([symbol]).get(normalized, "stock")

    def query_subscription(self, is_all_conn=True):
        try:
            if not self._ensure_quote_context():
                return RET_ERROR, None
            return self.quote_ctx.query_subscription(is_all_conn=is_all_conn)
        except Exception as e:
            logger.error(f"Error querying subscription: {e}")
            logger.debug(traceback.format_exc())
            return RET_ERROR, None

    @staticmethod
    def _get_sdk_version():
        try:
            import moomoo as _m

            return getattr(_m, "__version__", "unknown")
        except Exception:
            return "N/A"

    def get_opend_diagnostics(self):
        sdk_version = self._get_sdk_version()
        if not self._ensure_quote_context():
            return {
                "connected": False,
                "sdk_available": OpenQuoteContext is not None,
                "sdk_version": sdk_version,
                "security_firm": str(self.security_firm) if self.security_firm else "N/A",
                "portfolio_env": _env_name(self.portfolio_env),
                "readonly": self.readonly,
            }
        try:
            ret, sub_data = self.query_subscription()
            sub_info = None
            if ret == RET_OK and sub_data is not None:
                sub_info = {
                    "total_used": int(sub_data.get("total_used", 0)),
                    "remain": int(sub_data.get("remain", 0)),
                    "own_used": int(sub_data.get("own_used", 0)),
                }
            return {
                "connected": True,
                "sdk_available": True,
                "sdk_version": sdk_version,
                "security_firm": str(self.security_firm) if self.security_firm else "N/A",
                "portfolio_env": _env_name(self.portfolio_env),
                "readonly": self.readonly,
                "subscription": sub_info,
                "option_data_filter_available": OptionDataFilter is not None,
                "host": self.host,
                "port": self.port,
            }
        except Exception as e:
            logger.error(f"Error getting OpenD diagnostics: {e}")
            logger.debug(traceback.format_exc())
            return {
                "connected": True,
                "sdk_available": OpenQuoteContext is not None,
                "sdk_version": sdk_version,
                "error": str(e),
            }

    def create_option_contract(self, symbol, expiry, strike, option_type):
        symbol = self._format_symbol(symbol)
        opt_type = OptionType.CALL if option_type.upper() in ["C", "CALL"] else OptionType.PUT

        moomoo_expiry = f"{expiry[0:4]}-{expiry[4:6]}-{expiry[6:8]}" if len(expiry) == 8 else expiry

        ret, data = self.quote_ctx.get_option_chain(
            code=symbol, start=moomoo_expiry, end=moomoo_expiry, option_type=opt_type
        )
        if ret == RET_OK:
            match = data[data["strike_price"] == float(strike)]
            if not match.empty:
                return match.iloc[0]["code"]

        return None

    def get_option_volatility(self, code, query_time_period=None, hv_time_period=None):
        try:
            if not self._ensure_quote_context():
                return RET_ERROR, None
            kwargs = {}
            if query_time_period is not None:
                kwargs["query_time_period"] = query_time_period
            if hv_time_period is not None:
                kwargs["hv_time_period"] = hv_time_period
            return self.quote_ctx.get_option_volatility(code, **kwargs)
        except AttributeError:
            logger.warning("get_option_volatility not available in this SDK version (requires upgrade)")
            return RET_ERROR, None
        except Exception as e:
            logger.error(f"Error getting option volatility: {e}")
            logger.debug(traceback.format_exc())
            return RET_ERROR, None

    def get_option_exercise_probability(self, code):
        try:
            if not self._ensure_quote_context():
                return RET_ERROR, None
            return self.quote_ctx.get_option_exercise_probability(code)
        except AttributeError:
            logger.warning("get_option_exercise_probability not available in this SDK version (requires upgrade)")
            return RET_ERROR, None
        except Exception as e:
            logger.error(f"Error getting exercise probability: {e}")
            logger.debug(traceback.format_exc())
            return RET_ERROR, None

    def get_option_screen(self, screen_request):
        try:
            if not self._ensure_quote_context():
                return RET_ERROR, None
            return self.quote_ctx.get_option_screen(screen_request)
        except AttributeError:
            logger.warning("get_option_screen not available in this SDK version (requires upgrade)")
            return RET_ERROR, None
        except Exception as e:
            logger.error(f"Error running option screen: {e}")
            logger.debug(traceback.format_exc())
            return RET_ERROR, None

    def get_short_interest(self, code, next_key=None, num=None):
        try:
            if not self._ensure_quote_context():
                return RET_ERROR, None, None
            kwargs = {}
            if next_key is not None:
                kwargs["next_key"] = next_key
            if num is not None:
                kwargs["num"] = num
            return self.quote_ctx.get_short_interest(code, **kwargs)
        except AttributeError:
            logger.warning("get_short_interest not available in this SDK version (requires upgrade)")
            return RET_ERROR, None, None
        except Exception as e:
            logger.error(f"Error getting short interest: {e}")
            logger.debug(traceback.format_exc())
            return RET_ERROR, None, None

    def get_financials_earnings_price_move(self, code, period_count=None):
        try:
            if not self._ensure_quote_context():
                return RET_ERROR, None
            kwargs = {}
            if period_count is not None:
                kwargs["period_count"] = period_count
            return self.quote_ctx.get_financials_earnings_price_move(code, **kwargs)
        except AttributeError:
            logger.warning("get_financials_earnings_price_move not available in this SDK version (requires upgrade)")
            return RET_ERROR, None
        except Exception as e:
            logger.error(f"Error getting earnings price move: {e}")
            logger.debug(traceback.format_exc())
            return RET_ERROR, None

    def get_financials_earnings_price_history(self, code):
        try:
            if not self._ensure_quote_context():
                return RET_ERROR, None
            api = getattr(self.quote_ctx, "get_financials_earnings_price_history", None)
            if not callable(api):
                logger.warning(
                    "get_financials_earnings_price_history not available in this SDK version (requires upgrade)"
                )
                return RET_ERROR, None
            return api(code)
        except AttributeError:
            logger.warning("get_financials_earnings_price_history not available in this SDK version (requires upgrade)")
            return RET_ERROR, None
        except Exception as e:
            logger.error(f"Error getting earnings price history: {e}")
            logger.debug(traceback.format_exc())
            return RET_ERROR, None
