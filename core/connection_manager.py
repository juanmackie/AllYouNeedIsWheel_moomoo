"""
MoomooConnection - connection lifecycle and query-only portfolio retrieval.

This class exposes ONLY query operations (accounts, positions, quotes,
option chains, watchlist groups). There is no order/unlock surface;
readonly=False is rejected at construction (see core/broker_protocol.py).
"""

import math
import os
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    from moomoo import (
        RET_ERROR,
        RET_OK,
        Currency,
        OpenQuoteContext,
        OptionDataFilter,
        OptionType,
        OrderStatus,
        OrderType,
        SecurityFirm,
        TrdEnv,
        UserSecurityGroupType,
    )
except ImportError:
    # Allow graceful fallback during test collection / environments without full moomoo SDK
    OpenQuoteContext = None
    RET_OK = None
    RET_ERROR = None
    SecurityFirm = None
    TrdEnv = None

    OptionType = None
    OrderStatus = None
    OrderType = None
    UserSecurityGroupType = None
    OptionDataFilter = None
    Currency = None

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
)
from core.context_factory import create_contexts
from core.logging_config import get_logger
from core.quote_cache import OptionChainCache, PendingRequestCoordinator
from core.rate_limiter import RateLimiter
from core.ticker_utils import TickerCache, format_symbol
from core.utils import safe_float

logger = get_logger("ayniwheel.connection", "moomoo")


def _safe_str(value) -> str:
    """Convert a value to a UTF-8-safe string for logging, replacing non-ASCII chars that crash cp1252 consoles."""
    raw = str(value)
    try:
        raw.encode("cp1252")
        return raw
    except UnicodeEncodeError:
        return raw.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def _historical_volatility_result(window_sessions, reason):
    return {
        "status": "unavailable",
        "source": "moomoo_adjusted_daily_close",
        "window_sessions": max(int(window_sessions), 0),
        "sample_sessions": 0,
        "historical_volatility_pct": None,
        "as_of_date": None,
        "falling_knife": {
            "status": "unavailable",
            "decline_window_sessions": 20,
            "moving_average_sessions": 50,
            "decline_pct": None,
            "moving_average_price": None,
            "below_moving_average": None,
            "flagged": None,
        },
        "reason": reason,
    }


def _is_rate_limit_response(value) -> bool:
    """Recognize provider responses that indicate frequency pressure."""
    text = _safe_str(value).lower()
    return any(
        marker in text
        for marker in ("rate limit", "frequency limit", "too frequent", "quota exceeded", "request limit")
    )


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
        value = safe_float(acc_row.get(field), default=None)
        if value is not None and value > 0:
            return value, field
    return 0.0, "none"


def _select_buying_power_field(acc_row) -> tuple[float, str]:
    """Choose the Moomoo field that represents option cash power/capacity."""
    power_fields = (
        "usd_net_cash_power",
        "max_power_short",
        "power",
        "buying_power",
    )
    for field in power_fields:
        value = safe_float(acc_row.get(field), default=None)
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
        "max_power_short",
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
        self._session_calendar = None
        self._session_calendar_checked_at = 0.0

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
        self._historical_volatility_cache = {}
        self._historical_volatility_cache_lock = threading.Lock()

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
                matched_env = matched_account.get("trd_env", desired_env)
                if matched_env != desired_env:
                    raise ValueError(
                        f"Configured account_id {self.account_id!r} belongs to {_env_name(matched_env)}, "
                        f"not {_env_name(desired_env)}."
                    )
                return desired_env, matched_account.get("acc_id")
            if self.trd_ctx is None:
                # No trade context: the account list was never queried, so absence
                # from it proves nothing. Say that instead of blaming the account.
                raise ConnectionError(
                    "OpenD trade context is not connected; cannot verify configured "
                    f"account_id {self.account_id!r}. Connect to OpenD first."
                )
            if desired_env == TrdEnv.REAL:
                raise ValueError(f"Configured REAL account_id {self.account_id!r} is not available in OpenD.")
            return desired_env, self.account_id

        if desired_env == TrdEnv.REAL:
            raise ValueError("REAL portfolio reads require an explicitly configured account_id.")

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

    def get_price_snapshot(self, symbols):
        """Return broker quote fields for requested Moomoo codes in batches.

        The result is keyed by fully formatted Moomoo code (for example,
        ``US.AAPL``). Missing codes and missing broker fields remain ``None``;
        this method does not infer a security type or synthesize quote values.
        """
        if isinstance(symbols, str):
            symbols = [symbols]
        requested = []
        seen = set()
        for symbol in symbols or []:
            if not isinstance(symbol, str) or not symbol.strip():
                continue
            code = self._format_symbol(symbol.strip())
            if code not in seen:
                seen.add(code)
                requested.append(code)

        fields = (
            "last_price",
            "prev_close_price",
            "highest52weeks_price",
            "lowest52weeks_price",
            "dividend_ttm",
            "update_time",
            "security_type",
        )
        unknown = dict.fromkeys(fields)
        result = {code: unknown.copy() for code in requested}
        if not requested:
            return result

        try:
            if not self._ensure_quote_context():
                return result

            for offset in range(0, len(requested), 400):
                batch = requested[offset : offset + 400]
                self._rate_limiter.check_rate_limit()
                ret, data = self.quote_ctx.get_market_snapshot(batch)
                if ret != RET_OK or data is None or getattr(data, "empty", True):
                    continue

                for _, row in data.iterrows():
                    code = row.get("code")
                    if not isinstance(code, str) or code not in result:
                        continue

                    snapshot = {}
                    for field in fields[:-1]:
                        try:
                            parsed = safe_float(row.get(field), default=None)
                            snapshot[field] = parsed if parsed is not None and math.isfinite(parsed) else None
                        except (TypeError, ValueError):
                            snapshot[field] = None
                    update_time = row.get("update_time")
                    if isinstance(update_time, str):
                        snapshot["update_time"] = update_time.strip() or None
                    elif update_time is None:
                        snapshot["update_time"] = None
                    else:
                        try:
                            snapshot["update_time"] = None if update_time != update_time else str(update_time)
                        except (TypeError, ValueError):
                            snapshot["update_time"] = None
                    broker_type = next(
                        (
                            str(row.get(name) or "").strip().lower()
                            for name in ("stock_type", "security_type", "sec_type", "stock_class")
                            if str(row.get(name) or "").strip()
                        ),
                        "",
                    )
                    if broker_type in {"etf", "ie", "exchange_traded_fund"}:
                        snapshot["security_type"] = "etf"
                    elif broker_type in {"index", "idx", "ind"}:
                        snapshot["security_type"] = "index"
                    elif broker_type in {"stock", "equity"}:
                        snapshot["security_type"] = "stock"
                    else:
                        snapshot["security_type"] = None

                    result[code] = snapshot
                    last_price = snapshot["last_price"]
                    if last_price is not None and last_price > 0:
                        self._cache_stock_price(code, last_price)
        except Exception as exc:
            logger.error("Error getting price snapshot for %s: %s", requested, str(exc))

        return result

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

    def get_session_calendar(self):
        """Return a cached US session calendar, with a labeled weekday fallback."""
        from core.session_clock import MARKET_TIMEZONE, SessionCalendar

        now = datetime.now(MARKET_TIMEZONE)
        if self._session_calendar is not None and time.time() - self._session_calendar_checked_at < 86400:
            return self._session_calendar
        fallback = SessionCalendar.weekday_fallback()
        try:
            if not self._ensure_quote_context():
                self._session_calendar = fallback
                self._session_calendar_checked_at = time.time()
                return fallback
            start = (now.date() - timedelta(days=14)).isoformat()
            end = (now.date() + timedelta(days=370)).isoformat()
            self._rate_limiter.check_rate_limit()
            ret, data = self.quote_ctx.request_trading_days(market="US", start=start, end=end)
            if ret == RET_OK and data is not None:
                rows = [] if getattr(data, "empty", True) else data.to_dict("records")
                calendar = SessionCalendar.from_trading_days(rows)
                if calendar.trading_days:
                    self._session_calendar = calendar
                    self._session_calendar_checked_at = time.time()
                    if hasattr(self, "_quote_cache"):
                        self._quote_cache.session_calendar = calendar
                    return calendar
        except Exception as exc:
            logger.debug("OpenD trading-day calendar unavailable: %s", exc)
        self._session_calendar = fallback
        self._session_calendar_checked_at = time.time()
        if hasattr(self, "_quote_cache"):
            self._quote_cache.session_calendar = fallback
        return fallback

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

    def get_history_kl_quota(self, get_detail=False):
        """Return OpenD's historical-K-line quota summary (query-only)."""
        try:
            self._rate_limiter.check_rate_limit()
            if not self._ensure_quote_context():
                return RET_ERROR, None
            return self.quote_ctx.get_history_kl_quota(get_detail=get_detail)
        except Exception as e:
            logger.error(f"Error getting historical K-line quota: {str(e)}")
            return RET_ERROR, None

    def get_historical_volatility(self, symbol, window_sessions=20):
        """Return cached, Moomoo-close-derived annualized volatility for a US symbol.

        The latest daily bar is included only after that session's regular close.
        Missing quota, bars, or a complete return window remain unavailable.
        """
        from core.historical_volatility import annualized_historical_volatility, falling_knife_diagnostic
        from core.utils import market_now

        try:
            window = int(window_sessions)
        except (TypeError, ValueError):
            window = 0
        code = self._format_symbol(symbol)
        if window < 2 or window > 100 or not code.startswith("US."):
            return _historical_volatility_result(window, reason="invalid_request")

        cache_key = (code, window)
        now_epoch = time.time()
        with self._historical_volatility_cache_lock:
            cached = self._historical_volatility_cache.get(cache_key)
            if cached and now_epoch - cached["cached_at"] < cached["ttl"]:
                return dict(cached["result"])

        request_key = f"historical_volatility:{code}:{window}"
        pending = self._pending_requests.wait_for(request_key)
        if isinstance(pending, dict):
            return dict(pending)

        result = _historical_volatility_result(window, reason="provider_unavailable")
        try:
            quota = self.get_history_kl_quota()
            if not isinstance(quota, tuple) or len(quota) < 2 or quota[0] != RET_OK:
                result = _historical_volatility_result(window, reason="history_quota_unverified")
            elif not isinstance(quota[1], tuple) or len(quota[1]) < 2:
                result = _historical_volatility_result(window, reason="history_quota_unverified")
            else:
                try:
                    remaining = int(quota[1][1])
                except (TypeError, ValueError):
                    remaining = -1
                if remaining == 0:
                    result = _historical_volatility_result(window, reason="history_quota_exhausted")
                elif remaining < 0:
                    result = _historical_volatility_result(window, reason="history_quota_unverified")
                else:
                    try:
                        from moomoo import KL_FIELD, AuType, KLType
                    except ImportError:
                        result = _historical_volatility_result(window, reason="sdk_unavailable")
                    else:
                        ret, data, _page_req_key = self.get_history_kline(
                            code,
                            ktype=KLType.K_DAY,
                            autype=AuType.QFQ,
                            fields=[KL_FIELD.CLOSE],
                            max_count=max(window + 2, 52),
                        )
                        if ret == RET_OK and data is not None and not getattr(data, "empty", True):
                            rows = data.to_dict("records") if hasattr(data, "to_dict") else []
                            today = market_now().date()
                            calendar = self.get_session_calendar()
                            last_close = calendar.last_regular_close(market_now())
                            include_today = last_close is not None and last_close.date() == today
                            closes_by_date = {}
                            for row in rows:
                                try:
                                    session_date = datetime.strptime(
                                        str(row.get("time_key", ""))[:10], "%Y-%m-%d"
                                    ).date()
                                    close = safe_float(row.get("close"), default=None)
                                except (TypeError, ValueError):
                                    continue
                                if close is None or (session_date == today and not include_today):
                                    continue
                                if session_date <= today:
                                    closes_by_date[session_date] = close
                            ordered = sorted(closes_by_date.items())
                            closes = [close for _, close in ordered]
                            hv = annualized_historical_volatility(closes, window)
                            falling_knife = falling_knife_diagnostic(closes)
                            if hv is not None:
                                result = {
                                    "status": "available",
                                    "source": "moomoo_adjusted_daily_close",
                                    "window_sessions": window,
                                    "sample_sessions": window,
                                    "historical_volatility_pct": round(hv, 2),
                                    "as_of_date": ordered[-1][0].isoformat(),
                                    "falling_knife": falling_knife,
                                    "reason": None,
                                }
                            else:
                                result = _historical_volatility_result(window, reason="insufficient_history")
        except Exception as exc:
            logger.debug("Moomoo historical-volatility diagnostic unavailable for %s: %s", code, exc)
        finally:
            ttl = 86400 if result["status"] == "available" else 300
            with self._historical_volatility_cache_lock:
                self._historical_volatility_cache[cache_key] = {
                    "cached_at": time.time(),
                    "ttl": ttl,
                    "result": dict(result),
                }
            self._pending_requests.complete(request_key, result)
        return result

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

    def get_option_chain_range(self, symbol, right, start, end, band):
        """Fetch option quotes for a bounded date and strike range.

        This performs one chain query and one snapshot for the selected option
        codes. More than the broker's 400-code snapshot limit fails closed; no
        contracts are silently truncated.
        """
        symbol = self._format_symbol(symbol)
        right = str(right or "P").upper()
        if right not in {"P", "C"} or not isinstance(band, dict):
            return None
        cached_range = self._quote_cache.get_option_chain_range(symbol, right, start, end, band)
        if cached_range is not None:
            return cached_range
        try:
            min_strike = float(band.get("min_strike"))
            max_strike = float(band.get("max_strike"))
            if min_strike <= 0 or max_strike < min_strike:
                return {"symbol": symbol.split(".")[-1], "right": right, "options": []}
            self._option_chain_rate_limiter.check_rate_limit()
            if not self.is_connected() and not self.connect():
                return None
            opt_type = OptionType.PUT if right == "P" else OptionType.CALL
            self._acquire_option_chain_gate(f"range:{symbol}:{right}:{start}:{end}")
            try:
                ret, data = self.quote_ctx.get_option_chain(
                    code=symbol,
                    start=str(start),
                    end=str(end),
                    option_type=opt_type,
                )
            finally:
                MoomooConnection._option_chain_gate.release()
            if ret != RET_OK or data is None:
                return None
            if data.empty:
                result = {"symbol": symbol.split(".")[-1], "right": right, "options": []}
                self._quote_cache.cache_option_chain_range(symbol, right, start, end, band, result)
                return result

            strike_field = "strike_price" if "strike_price" in data.columns else "option_strike_price"
            if strike_field not in data.columns or "code" not in data.columns:
                return None
            data = data.copy()
            data[strike_field] = data[strike_field].apply(lambda value: safe_float(value, default=None))
            data = data[
                data[strike_field].notna() & (data[strike_field] >= min_strike) & (data[strike_field] <= max_strike)
            ]
            option_codes = list(dict.fromkeys(str(code) for code in data["code"].tolist() if code))
            if len(option_codes) > 400:
                logger.warning("Range chain for %s selected %d codes (snapshot cap 400)", symbol, len(option_codes))
                return None
            if not option_codes:
                result = {"symbol": symbol.split(".")[-1], "right": right, "options": []}
                self._quote_cache.cache_option_chain_range(symbol, right, start, end, band, result)
                return result

            self._rate_limiter.check_rate_limit()
            ret, snap_data = self.quote_ctx.get_market_snapshot(option_codes)
            if ret != RET_OK or snap_data is None:
                return None
            fetched_at = datetime.now(timezone.utc).isoformat()
            options = []
            for _, row in snap_data.iterrows():
                expiration = (
                    row.get("option_expiry_date", "") or row.get("strike_time", "") or row.get("strike_date", "")
                )
                expiration = str(expiration).replace("-", "")
                if len(expiration) != 8:
                    continue
                options.append(
                    {
                        "strike": safe_float(row.get("option_strike_price"), default=None),
                        "expiration": expiration,
                        "option_type": "CALL" if row.get("option_type") == "CALL" else "PUT",
                        "bid": safe_float(row.get("bid_price"), default=0),
                        "ask": safe_float(row.get("ask_price"), default=0),
                        "last": safe_float(row.get("last_price"), default=0),
                        "volume": int(safe_float(row.get("volume"), default=0) or 0),
                        "open_interest": int(
                            safe_float(row.get("option_open_interest", row.get("open_interest")), default=0) or 0
                        ),
                        "implied_volatility": _normalize_iv(row.get("option_implied_volatility", 0)),
                        "delta": safe_float(row.get("option_delta"), default=0),
                        "gamma": safe_float(row.get("option_gamma"), default=0),
                        "theta": safe_float(row.get("option_theta"), default=0),
                        "vega": safe_float(row.get("option_vega"), default=0),
                        "update_time": str(row.get("update_time", "") or ""),
                        "quote_fetched_at_utc": fetched_at,
                    }
                )

            grouped = {}
            for option in options:
                grouped.setdefault(option["expiration"], []).append(option)
            for expiration, expiry_options in grouped.items():
                self._quote_cache.cache_option_chain(
                    symbol,
                    expiration,
                    right,
                    {
                        "symbol": symbol.split(".")[-1],
                        "expiration": expiration,
                        "stock_price": None,
                        "right": right,
                        "options": expiry_options,
                    },
                )
            result = {
                "symbol": symbol.split(".")[-1],
                "right": right,
                "options": options,
                "quote_fetched_at_utc": fetched_at,
            }
            self._quote_cache.cache_option_chain_range(symbol, right, start, end, band, result)
            return result
        except Exception as exc:
            if _is_rate_limit_response(exc):
                self._option_chain_rate_limiter.record_rate_limit(_safe_str(exc))
            logger.error("Error retrieving option chain range for %s: %s", symbol, _safe_str(exc))
            return None

    def get_portfolio(self):
        if not self.is_connected():
            if not self.connect():
                return None

        try:
            trd_env, account_id = self._resolve_portfolio_account()
            ret, acc_data = self.trd_ctx.accinfo_query(
                trd_env=trd_env,
                acc_id=self._account_id_arg(account_id),
                currency=Currency.USD if Currency is not None else "USD",
            )
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
            if ret != RET_OK or pos_data is None:
                self.last_error = self._format_trade_error("position list query", pos_data, trd_env, account_id)
                logger.error(self.last_error)
                return None

            if not pos_data.empty:
                position_types = pos_data["code"].apply(_infer_security_type_from_code)
                option_positions = pos_data[position_types == "OPT"]
                if not option_positions.empty:
                    quote_fetched_at_utc = datetime.now(timezone.utc).isoformat()
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
                        "shares": safe_float(pos.get("qty", 0)),
                        "can_sell_qty": safe_float(pos.get("can_sell_qty"), None),
                        "avg_cost": safe_float(pos.get("average_cost", pos.get("cost_price", 0))),
                        "market_price": safe_float(pos.get("nominal_price", pos.get("last_price", 0))),
                        "market_value": safe_float(pos.get("market_val", 0)),
                        "unrealized_pnl": safe_float(pos.get("unrealized_pl", pos.get("pl_val", 0))),
                        "security_type": sec_type,
                    }

                    if sec_type == "OPT" and symbol in opt_snaps_dict:
                        snap = opt_snaps_dict[symbol]
                        pos_details.update(
                            {
                                "expiration": snap.get("option_expiry_date", "").replace("-", "")
                                or (option_metadata or {}).get("expiration", ""),
                                "strike": safe_float(
                                    snap.get("option_strike_price", (option_metadata or {}).get("strike", 0))
                                ),
                                "option_type": "CALL" if snap.get("option_type") == "CALL" else "PUT",
                                "bid": safe_float(snap.get("bid_price"), None),
                                "ask": safe_float(snap.get("ask_price"), None),
                                "last": safe_float(snap.get("last_price"), None),
                                "implied_volatility": _normalize_iv(snap.get("option_implied_volatility", 0)),
                                "delta": safe_float(snap.get("option_delta"), None),
                                "theta": safe_float(snap.get("option_theta"), None),
                                "quote_fetched_at_utc": quote_fetched_at_utc,
                                "quote_update_time": str(snap.get("update_time", "") or ""),
                            }
                        )
                    elif sec_type == "OPT" and option_metadata:
                        pos_details.update(
                            {
                                "expiration": option_metadata.get("expiration", ""),
                                "strike": safe_float(option_metadata.get("strike", 0)),
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

    def resolve_portfolio_identity(self):
        """Return (env_label, opaque_account_id) for account-scoped persistence.

        The opaque id is derived by ``core.wheel_runner.opaque_account_id`` —
        the single place account identities are hashed for storage (C04).
        Query-only. Connects on demand like every other broker read: the account
        list only exists once a trade context is established, so resolving the
        identity before connecting reports a configured account as missing.
        Raises ``ConnectionError`` when OpenD cannot be reached and ``ValueError``
        when the configured account genuinely is not exposed by OpenD.
        """
        from core.wheel_runner import opaque_account_id

        if not self.is_connected() and not self.connect():
            detail = self.last_error or "no broker connection"
            raise ConnectionError(f"OpenD is not connected, so the portfolio account cannot be resolved ({detail}).")
        trd_env, account_id = self._resolve_portfolio_account()
        return _env_name(trd_env), opaque_account_id(str(account_id or ""))

    def get_history_deals(self, start="", end=""):
        """Query historical fills (executions) for the resolved account.

        Read-only deal query (``history_deal_list_query``). The SDK caps each
        call to a 90-day window. Returns a list of raw row dicts, or None on
        failure (``last_error`` carries the reason). No fees are included —
        fees require ``get_order_fees`` keyed by order id.
        """
        if not self.is_connected() and not self.connect():
            return None
        try:
            self._rate_limiter.check_rate_limit()
            trd_env, account_id = self._resolve_portfolio_account()
            ret, data = self.trd_ctx.history_deal_list_query(
                start=start, end=end, trd_env=trd_env, acc_id=self._account_id_arg(account_id)
            )
            if ret != RET_OK:
                self.last_error = self._format_trade_error("history deal query", data, trd_env, account_id)
                logger.error(self.last_error)
                return None
            return [] if data.empty else data.to_dict("records")
        except Exception as e:
            self.last_error = f"Error querying history deals: {e}"
            logger.error(self.last_error)
            logger.debug(traceback.format_exc())
            return None

    def get_order_fees(self, order_ids):
        """Query fees for specific order ids (``order_fee_query``).

        Read-only; fees are ID-driven only (no date-range fee report).
        Returns [{order_id, fee_amount, fee_details}], or None on failure.
        """
        ids = [str(oid) for oid in (order_ids or []) if str(oid or "").strip()]
        if not ids:
            return []
        if not self.is_connected() and not self.connect():
            return None
        try:
            self._rate_limiter.check_rate_limit()
            trd_env, account_id = self._resolve_portfolio_account()
            ret, data = self.trd_ctx.order_fee_query(
                order_id_list=ids, trd_env=trd_env, acc_id=self._account_id_arg(account_id)
            )
            if ret != RET_OK:
                self.last_error = self._format_trade_error("order fee query", data, trd_env, account_id)
                logger.error(self.last_error)
                return None
            if data.empty:
                return []
            rows = data.to_dict("records")
            for row in rows:
                row["fee_amount"] = safe_float(row.get("fee_amount"), 0.0)
            return rows
        except Exception as e:
            self.last_error = f"Error querying order fees: {e}"
            logger.error(self.last_error)
            logger.debug(traceback.format_exc())
            return None

    def get_cash_flow(self, clearing_date):
        """Query one clearing date's cash movements (``get_acc_cash_flow``).

        Read-only. Securities accounts are keyed by a single ``clearing_date``
        per call (date-range bounds are crypto-only in the SDK). Returns rows
        [{cashflow_id, clearing_date, currency, cashflow_type,
        cashflow_direction, cashflow_amount, cashflow_remark}], or None on
        failure. ``cashflow_type`` is a raw server string passthrough.
        """
        clearing_date = str(clearing_date or "").strip()
        if not clearing_date:
            return []
        if not self.is_connected() and not self.connect():
            return None
        try:
            self._rate_limiter.check_rate_limit()
            trd_env, account_id = self._resolve_portfolio_account()
            ret, data = self.trd_ctx.get_acc_cash_flow(
                clearing_date=clearing_date, trd_env=trd_env, acc_id=self._account_id_arg(account_id)
            )
            if ret != RET_OK:
                self.last_error = self._format_trade_error("cash flow query", data, trd_env, account_id)
                logger.error(self.last_error)
                return None
            return [] if data.empty else data.to_dict("records")
        except Exception as e:
            self.last_error = f"Error querying cash flow: {e}"
            logger.error(self.last_error)
            logger.debug(traceback.format_exc())
            return None

    def get_open_orders(self):
        """Return active account orders without order IDs or other private fields.

        A successful empty result is ``[]``; ``None`` means OpenD could not
        establish the account's open-order state. Only rows with a positive
        remaining quantity and an active status are returned.
        """
        if not self.is_connected() and not self.connect():
            return None
        active_statuses = (
            "WAITING_SUBMIT",
            "SUBMITTING",
            "SUBMITTED",
            "FILLED_PART",
            "CANCELLING_PART",
            "CANCELLING_ALL",
        )
        try:
            self._rate_limiter.check_rate_limit()
            trd_env, account_id = self._resolve_portfolio_account()
            statuses = [getattr(OrderStatus, name, name) for name in active_statuses]
            ret, data = self.trd_ctx.order_list_query(
                status_filter_list=statuses,
                trd_env=trd_env,
                acc_id=self._account_id_arg(account_id),
            )
            if ret != RET_OK or data is None:
                self.last_error = "OpenD open-order query is unavailable for the configured account."
                return None
            rows = [] if getattr(data, "empty", True) else data.to_dict("records")
            result = []
            for row in rows:
                status = str(row.get("order_status", row.get("status", "")) or "").upper()
                qty = safe_float(row.get("qty"), default=None)
                dealt = safe_float(row.get("dealt_qty"), default=0.0)
                remaining = None if qty is None else max(qty - (dealt or 0.0), 0.0)
                if status not in active_statuses or remaining is None or remaining <= 0:
                    continue
                result.append(
                    {
                        "code": str(row.get("code", "") or ""),
                        "status": status,
                        "remaining_qty": remaining,
                        "trd_side": str(row.get("trd_side", "") or "").upper(),
                    }
                )
            return result
        except Exception as exc:
            self.last_error = "OpenD open-order query is unavailable for the configured account."
            logger.warning("OpenD open-order query unavailable (%s)", type(exc).__name__)
            return None

    def get_max_sellable_info(self, code, price):
        """Query Moomoo sell-capacity diagnostics for one security.

        This is display-only evidence; it never controls eligibility or
        quantity. Unknown fields remain ``None`` instead of being treated as
        zero. Callers should bound this to the visible shortlist.
        """
        code = self._format_symbol(code)
        try:
            quote = safe_float(price, default=None)
            if not code or quote is None or quote <= 0:
                return None
            if not self.is_connected() and not self.connect():
                return None
            self._rate_limiter.check_rate_limit()
            trd_env, account_id = self._resolve_portfolio_account()
            ret, data = self.trd_ctx.acctradinginfo_query(
                order_type=OrderType.NORMAL if OrderType is not None else "NORMAL",
                code=code,
                price=quote,
                trd_env=trd_env,
                acc_id=self._account_id_arg(account_id),
            )
            if ret != RET_OK or data is None or getattr(data, "empty", True):
                self.last_error = "OpenD sell-capacity query is unavailable for the configured account."
                return None
            row = data.iloc[0]
            return {
                field: safe_float(row.get(field), default=None)
                for field in ("max_position_sell", "max_sell_short", "max_buy_back")
            }
        except Exception as exc:
            self.last_error = "OpenD sell-capacity query is unavailable for the configured account."
            logger.warning("OpenD sell-capacity query unavailable (%s)", type(exc).__name__)
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
