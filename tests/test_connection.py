"""
Tests for core/connection.py - MoomooConnection class and helper functions
"""

import os
import threading
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

import pandas as pd
from moomoo import RET_ERROR, RET_OK

# Import the module under test
from core.connection import (
    MoomooConnection,
    SecurityFirm,
    TrdEnv,
    _clean_account_id,
    _env_name,
    _first_non_zero,
    _infer_security_type_from_code,
    _is_truthy_flag,
    _normalize_security_firm,
    _normalize_trd_env,
    _parse_option_code_metadata,
    _safe_close_context,
    _safe_float,
    probe_opend_status,
)
from core.connection_constants import _normalize_iv


class TestHelperFunctions(unittest.TestCase):
    """Test the standalone helper functions"""

    def test_safe_close_context(self):
        """Test _safe_close_context handles None and exceptions"""
        # Test with None
        _safe_close_context(None)  # Should not raise

        # Test with mock that raises
        mock_ctx = Mock()
        mock_ctx.close.side_effect = Exception("Close failed")
        _safe_close_context(mock_ctx)  # Should not raise

        # Test with mock that works
        mock_ctx2 = Mock()
        _safe_close_context(mock_ctx2)
        mock_ctx2.close.assert_called_once()

    def test_is_truthy_flag(self):
        """Test _is_truthy_flag with various inputs"""
        # Boolean values
        self.assertTrue(_is_truthy_flag(True))
        self.assertFalse(_is_truthy_flag(False))

        # Numeric values
        self.assertTrue(_is_truthy_flag(1))
        self.assertTrue(_is_truthy_flag(42))
        self.assertFalse(_is_truthy_flag(0))
        self.assertTrue(_is_truthy_flag(3.14))

        # String values
        self.assertTrue(_is_truthy_flag("true"))
        self.assertTrue(_is_truthy_flag("True"))
        self.assertTrue(_is_truthy_flag("YES"))
        self.assertTrue(_is_truthy_flag("yes"))
        self.assertTrue(_is_truthy_flag("Y"))
        self.assertTrue(_is_truthy_flag("ok"))
        self.assertTrue(_is_truthy_flag("connected"))
        self.assertTrue(_is_truthy_flag("ready"))
        self.assertFalse(_is_truthy_flag("false"))
        self.assertFalse(_is_truthy_flag(""))

        # None
        self.assertFalse(_is_truthy_flag(None))

    def test_clean_account_id(self):
        """Test _clean_account_id"""
        self.assertEqual(_clean_account_id(None), "")
        self.assertEqual(_clean_account_id(""), "")
        self.assertEqual(_clean_account_id("YOUR_MOOMOO_ACCOUNT_ID"), "")
        self.assertEqual(_clean_account_id("  123456  "), "123456")
        self.assertEqual(_clean_account_id(123456), "123456")

    def test_env_name(self):
        """Test _env_name"""
        self.assertEqual(_env_name(TrdEnv.SIMULATE), "SIMULATE")
        self.assertEqual(_env_name(TrdEnv.REAL), "REAL")

    def test_normalize_trd_env(self):
        """Test _normalize_trd_env"""
        default = TrdEnv.SIMULATE

        # Test with None
        self.assertEqual(_normalize_trd_env(None, default), default)

        # Test with valid enum values
        self.assertEqual(_normalize_trd_env(TrdEnv.SIMULATE, default), TrdEnv.SIMULATE)
        self.assertEqual(_normalize_trd_env(TrdEnv.REAL, default), TrdEnv.REAL)

        # Test with string values
        self.assertEqual(_normalize_trd_env("sim", default), TrdEnv.SIMULATE)
        self.assertEqual(_normalize_trd_env("SIMULATE", default), TrdEnv.SIMULATE)
        self.assertEqual(_normalize_trd_env("paper", default), TrdEnv.SIMULATE)
        self.assertEqual(_normalize_trd_env("REAL", default), TrdEnv.REAL)
        self.assertEqual(_normalize_trd_env("live", default), TrdEnv.REAL)

        # Test with invalid string
        self.assertEqual(_normalize_trd_env("invalid", default), default)

    def test_normalize_security_firm(self):
        """Test _normalize_security_firm"""
        default = SecurityFirm.FUTUSECURITIES

        # Test with None
        self.assertEqual(_normalize_security_firm(None, default), default)

        # Test with valid enum values
        self.assertEqual(_normalize_security_firm(SecurityFirm.FUTUSECURITIES, default), SecurityFirm.FUTUSECURITIES)

        # Test with string attribute name
        result = _normalize_security_firm("FUTUAU", default)
        self.assertEqual(result, SecurityFirm.FUTUAU)

        # Test with invalid string (should return default)
        result = _normalize_security_firm("INVALID", default)
        self.assertEqual(result, default)

    def test_infer_security_type_from_code(self):
        """Test _infer_security_type_from_code"""
        # Option code pattern
        self.assertEqual(_infer_security_type_from_code("US.AAPL230616C00150000"), "OPT")
        self.assertEqual(_infer_security_type_from_code("US.TSLA240315P00200000"), "OPT")

        # Stock code pattern
        self.assertEqual(_infer_security_type_from_code("US.AAPL"), "STK")
        self.assertEqual(_infer_security_type_from_code("US.TSLA"), "STK")

        # Invalid
        self.assertEqual(_infer_security_type_from_code(""), "")
        self.assertEqual(_infer_security_type_from_code(None), "")
        self.assertEqual(_infer_security_type_from_code("INVALID"), "")

    def test_parse_option_code_metadata(self):
        """Test _parse_option_code_metadata"""
        # Valid option code
        result = _parse_option_code_metadata("US.AAPL230616C00150000")
        self.assertIsNotNone(result)
        self.assertEqual(result["underlying"], "AAPL")
        self.assertEqual(result["expiration"], "20230616")
        self.assertEqual(result["strike"], 150.0)
        self.assertEqual(result["option_type"], "CALL")

        # Put option
        result = _parse_option_code_metadata("US.TSLA240315P00200000")
        self.assertIsNotNone(result)
        self.assertEqual(result["underlying"], "TSLA")
        self.assertEqual(result["expiration"], "20240315")
        self.assertEqual(result["strike"], 200.0)
        self.assertEqual(result["option_type"], "PUT")

        # Invalid codes
        self.assertIsNone(_parse_option_code_metadata(None))
        self.assertIsNone(_parse_option_code_metadata(""))
        self.assertIsNone(_parse_option_code_metadata("US.AAPL"))

    def test_safe_float(self):
        """Test _safe_float"""
        self.assertEqual(_safe_float(None), 0.0)
        self.assertEqual(_safe_float(""), 0.0)
        self.assertEqual(_safe_float("N/A"), 0.0)
        self.assertEqual(_safe_float("nan"), 0.0)
        self.assertEqual(_safe_float("NaN"), 0.0)
        self.assertEqual(_safe_float(42), 42.0)
        self.assertEqual(_safe_float(3.14), 3.14)
        self.assertEqual(_safe_float("3.14"), 3.14)
        self.assertEqual(_safe_float("invalid", default=99.0), 99.0)

    def test_first_non_zero(self):
        """Test _first_non_zero"""
        # Test with valid values
        self.assertEqual(_first_non_zero(0, 0, 5, 0), 5)
        self.assertEqual(_first_non_zero(0, 3.14, 5), 3.14)
        self.assertEqual(_first_non_zero(10, 0, 0), 10)

        # Test with all zeros
        self.assertEqual(_first_non_zero(0, 0, 0), 0.0)
        self.assertEqual(_first_non_zero(), 0.0)

        # Test with None values
        self.assertEqual(_first_non_zero(None, 0, 5), 5)
        self.assertEqual(_first_non_zero("N/A", 3.14), 3.14)

    # -- _normalize_iv tests ----------------------------------------------

    def test_normalize_iv_moomoo_percentage(self):
        """_normalize_iv converts Moomoo percentage (48.0) to decimal (0.48)"""
        self.assertEqual(_normalize_iv(48.0), 0.48)
        self.assertEqual(_normalize_iv(50.0), 0.50)
        self.assertEqual(_normalize_iv(120.0), 1.20)
        self.assertEqual(_normalize_iv(200.0), 2.00)

    def test_normalize_iv_yfinance_decimal(self):
        """_normalize_iv passes through yfinance decimal (0.48) unchanged"""
        self.assertEqual(_normalize_iv(0.48), 0.48)
        self.assertEqual(_normalize_iv(0.30), 0.30)
        self.assertEqual(_normalize_iv(1.20), 1.20)
        self.assertEqual(_normalize_iv(0.0), 0.0)

    def test_normalize_iv_edge_cases(self):
        """_normalize_iv handles boundary values correctly"""
        # Exactly 3.0 boundary (300% IV) - passes through as decimal
        self.assertEqual(_normalize_iv(3.0), 3.0)
        # Just above 3.0 - treated as percentage
        self.assertEqual(_normalize_iv(3.01), 0.0301)
        # None and empty
        self.assertEqual(_normalize_iv(None), 0.0)
        self.assertEqual(_normalize_iv(""), 0.0)
        # Negative
        self.assertEqual(_normalize_iv(-1.0), -1.0)

    def test_normalize_iv_broker_zero_delta_preserved(self):
        """_normalize_iv does not affect values already in decimal"""
        # Typical values: 0.15-0.60 for wheel strategy
        for val in [0.15, 0.25, 0.30, 0.45, 0.60, 0.75]:
            normalized = _normalize_iv(val)
            self.assertEqual(normalized, val, f"IV {val} should pass through unchanged, got {normalized}")


class TestProbeOpenDStatus(unittest.TestCase):
    """Test the probe_opend_status function"""

    @patch("socket.socket")
    def test_probe_opend_reachable(self, mock_socket_class):
        """Test probing when OpenD is reachable"""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 0  # Success
        mock_socket_class.return_value = mock_socket

        result = probe_opend_status("127.0.0.1", 11111)

        self.assertTrue(result["reachable"])
        self.assertTrue(result["connected"])
        self.assertEqual(result["status"], "connected")
        mock_socket.close.assert_called_once()

    @patch("socket.socket")
    def test_probe_opend_unreachable(self, mock_socket_class):
        """Test probing when OpenD is not reachable"""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 1  # Failure
        mock_socket_class.return_value = mock_socket

        result = probe_opend_status("127.0.0.1", 11111)

        self.assertFalse(result["reachable"])
        self.assertFalse(result["connected"])
        self.assertEqual(result["status"], "unavailable")
        mock_socket.close.assert_called_once()

    @patch("socket.socket")
    def test_probe_opend_exception(self, mock_socket_class):
        """Test probing when an exception occurs"""
        mock_socket = MagicMock()
        mock_socket.connect_ex.side_effect = Exception("Network error")
        mock_socket_class.return_value = mock_socket

        result = probe_opend_status("127.0.0.1", 11111)

        self.assertFalse(result["reachable"])
        self.assertFalse(result["connected"])
        self.assertEqual(result["status"], "error")


class TestMoomooConnectionSingleton(unittest.TestCase):
    """Test the singleton pattern of MoomooConnection"""

    def setUp(self):
        """Clear the singleton cache before each test"""
        MoomooConnection._instances.clear()

    def tearDown(self):
        """Clear the singleton cache after each test"""
        MoomooConnection._instances.clear()

    def test_singleton_same_config(self):
        """Test that same config returns same instance"""
        conn1 = MoomooConnection(host="127.0.0.1", port=11111, readonly=True)
        conn2 = MoomooConnection(host="127.0.0.1", port=11111, readonly=True)
        self.assertIs(conn1, conn2)

    def test_different_instance_different_config(self):
        """Test that different config returns different instance"""
        conn1 = MoomooConnection(host="127.0.0.1", port=11111, readonly=True)
        conn2 = MoomooConnection(host="127.0.0.1", port=22222, readonly=True)
        self.assertIsNot(conn1, conn2)

    def test_readonly_false_rejected(self):
        """readonly=False is not a supported configuration (query-only app)"""
        with self.assertRaises(ValueError):
            MoomooConnection(readonly=False)

    def test_singleton_key_format(self):
        """Test that singleton key is properly formatted"""
        MoomooConnection(
            host="192.168.1.100",
            port=22222,
            readonly=True,
            account_id="12345",
            portfolio_env="REAL",
            security_firm="FUTUAU",
        )

        expected_key = "192.168.1.100:22222:True:12345:REAL:FUTUAU"
        self.assertIn(expected_key, MoomooConnection._instances)


class TestMoomooConnectionInit(unittest.TestCase):
    """Test MoomooConnection initialization"""

    def setUp(self):
        MoomooConnection._instances.clear()

    def tearDown(self):
        MoomooConnection._instances.clear()

    def test_init_default_values(self):
        """Test initialization with default values"""
        with patch.dict(os.environ, {}, clear=True):
            conn = MoomooConnection()
            self.assertEqual(conn.host, "127.0.0.1")
            self.assertEqual(conn.port, 11111)
            self.assertTrue(conn.readonly)
            self.assertEqual(conn.portfolio_env, TrdEnv.SIMULATE)
            self.assertEqual(conn.security_firm, SecurityFirm.FUTUSECURITIES)
            self.assertFalse(conn._connected)

    def test_init_custom_values(self):
        """Test initialization with custom values"""
        conn = MoomooConnection(
            host="192.168.1.100",
            port=22222,
            readonly=True,
            account_id="12345",
            portfolio_env="REAL",
            security_firm="FUTUAU",
        )
        self.assertEqual(conn.host, "192.168.1.100")
        self.assertEqual(conn.port, 22222)
        self.assertTrue(conn.readonly)
        self.assertEqual(conn.portfolio_env, TrdEnv.REAL)
        self.assertEqual(conn.security_firm, SecurityFirm.FUTUAU)

    def test_init_account_id_cleaning(self):
        """Test that account_id is cleaned during init"""
        conn = MoomooConnection(account_id="YOUR_MOOMOO_ACCOUNT_ID")
        self.assertEqual(conn.account_id, "")

        conn2 = MoomooConnection(account_id="  12345  ")
        self.assertEqual(conn2.account_id, "12345")

    def test_readonly_sets_simulate_env(self):
        """Test that readonly=True sets portfolio_env to SIMULATE"""
        conn = MoomooConnection(readonly=True)
        self.assertEqual(conn.portfolio_env, TrdEnv.SIMULATE)

    def test_no_readonly_sets_real_env(self):
        """readonly=False is rejected; REAL selection is explicit via portfolio_env."""
        with self.assertRaises(ValueError):
            MoomooConnection(readonly=False)
        conn = MoomooConnection(readonly=True, portfolio_env="REAL")
        self.assertEqual(conn.portfolio_env, TrdEnv.REAL)


class TestMoomooConnectionMethods(unittest.TestCase):
    """Test MoomooConnection methods with mocking"""

    def setUp(self):
        MoomooConnection._instances.clear()

    def tearDown(self):
        MoomooConnection._instances.clear()

    @patch("core.context_factory.OpenQuoteContext")
    @patch("core.context_factory.OpenSecTradeContext")
    def test_connect_success(self, mock_trd_class, mock_quote_class):
        """Test successful connection"""
        mock_quote_ctx = MagicMock()
        mock_trd_ctx = MagicMock()
        mock_quote_class.return_value = mock_quote_ctx
        mock_trd_class.return_value = mock_trd_ctx

        conn = MoomooConnection()
        result = conn.connect()

        self.assertTrue(result)
        self.assertTrue(conn._connected)
        mock_quote_class.assert_called_once_with(host="127.0.0.1", port=11111)
        mock_trd_class.assert_called_once()

    @patch("core.context_factory.OpenQuoteContext")
    def test_connect_failure(self, mock_quote_class):
        """Test connection failure"""
        mock_quote_class.side_effect = Exception("Connection refused")

        conn = MoomooConnection()
        result = conn.connect()

        self.assertFalse(result)
        self.assertFalse(conn._connected)

    @patch("core.context_factory.OpenQuoteContext")
    def test_is_connected_true(self, mock_quote_class):
        """Test is_connected returns True when healthy"""
        mock_quote_ctx = MagicMock()
        mock_quote_ctx.get_global_state.return_value = (RET_OK, "OK")
        mock_quote_class.return_value = mock_quote_ctx

        conn = MoomooConnection()
        conn._connected = True
        conn.quote_ctx = mock_quote_ctx

        self.assertTrue(conn.is_connected())

    @patch("core.context_factory.OpenQuoteContext")
    def test_is_connected_false_not_connected(self, mock_quote_class):
        """Test is_connected returns False when not connected"""
        conn = MoomooConnection()
        conn._connected = False

        self.assertFalse(conn.is_connected())

    @patch("core.context_factory.OpenQuoteContext")
    def test_is_connected_false_bad_health(self, mock_quote_class):
        """Test is_connected returns False when health check fails"""
        mock_quote_ctx = MagicMock()
        mock_quote_ctx.get_global_state.return_value = ("RET_ERROR", "Not connected")
        mock_quote_class.return_value = mock_quote_ctx

        conn = MoomooConnection()
        conn._connected = True
        conn.quote_ctx = mock_quote_ctx

        self.assertFalse(conn.is_connected())

    def test_disconnect(self):
        """Test disconnect method"""
        conn = MoomooConnection()
        mock_quote = MagicMock()
        mock_trd = MagicMock()
        conn.quote_ctx = mock_quote
        conn.trd_ctx = mock_trd
        conn._connected = True

        conn.disconnect()

        self.assertFalse(conn._connected)
        self.assertIsNone(conn.quote_ctx)
        self.assertIsNone(conn.trd_ctx)
        mock_quote.close.assert_called_once()
        mock_trd.close.assert_called_once()

    def test_format_symbol_no_prefix(self):
        """Test _format_symbol adds US. prefix"""
        conn = MoomooConnection()
        self.assertEqual(conn._format_symbol("AAPL"), "US.AAPL")

    def test_format_symbol_with_prefix(self):
        """Test _format_symbol doesn't duplicate prefix"""
        conn = MoomooConnection()
        self.assertEqual(conn._format_symbol("US.AAPL"), "US.AAPL")

    def test_get_connection_info(self):
        """Test get_connection_info returns expected keys"""
        conn = MoomooConnection()
        info = conn.get_connection_info()

        expected_keys = [
            "connected",
            "is_healthy",
            "host",
            "port",
            "last_activity",
            "uptime_seconds",
            "has_quote_ctx",
            "has_trd_ctx",
            "readonly",
            "portfolio_env",
            "security_firm",
        ]
        for key in expected_keys:
            self.assertIn(key, info)


class TestMoomooConnectionCaching(unittest.TestCase):
    """Test caching mechanisms in MoomooConnection"""

    def setUp(self):
        MoomooConnection._instances.clear()

    def tearDown(self):
        MoomooConnection._instances.clear()

    def test_stock_price_cache(self):
        """Test stock price caching"""
        conn = MoomooConnection()

        # Test cache miss
        self.assertIsNone(conn._get_cached_stock_price("AAPL"))

        # Test cache set
        conn._cache_stock_price("AAPL", 150.0)

        # Test cache hit
        self.assertEqual(conn._get_cached_stock_price("AAPL"), 150.0)

        # Test cache expiry (manually set old timestamp)
        old_time = time.time() - 200  # Older than TTL (120s)
        with conn._ticker_cache._stock_price_cache_lock:
            conn._ticker_cache._stock_price_cache["AAPL"] = (150.0, old_time)

        # Should return None due to expiry
        self.assertIsNone(conn._get_cached_stock_price("AAPL"))

    def test_option_chain_cache(self):
        """Test option chain caching"""
        conn = MoomooConnection()

        mock_data = {"symbol": "AAPL", "options": []}

        # Test cache miss
        self.assertIsNone(conn._get_cached_option_chain("AAPL", "20230616", "C"))

        # Test cache set
        conn._cache_option_chain("AAPL", "20230616", "C", mock_data)

        # Test cache hit
        self.assertEqual(conn._get_cached_option_chain("AAPL", "20230616", "C"), mock_data)

    def test_option_chain_cache_bypassed_when_data_filter_provided(self):
        """Test that option chain cache is bypassed when data_filter is provided"""
        MoomooConnection._instances.clear()
        conn = MoomooConnection()

        mock_chain_df = MagicMock()
        mock_chain_df.empty = True
        conn.quote_ctx = MagicMock()
        conn.is_connected = MagicMock(return_value=True)
        conn.quote_ctx.get_option_chain.return_value = (RET_OK, mock_chain_df)

        conn._cache_option_chain("AAPL", "20230616", "C", {"cached": True})

        from moomoo import OptionDataFilter

        f = OptionDataFilter()
        f.delta_min = 0.15

        result = conn.get_option_chain("US.AAPL", "20230616", "C", data_filter=f)

        self.assertIsNotNone(result)
        self.assertNotIn("cached", result)
        conn.quote_ctx.get_option_chain.assert_called()

    def test_broker_cache_after_hours_disabled(self):
        """Test that broker_cache_after_hours=False disables after-hours cache serving"""
        MoomooConnection._instances.clear()
        conn = MoomooConnection(broker_cache_after_hours=False)
        conn._cache_option_chain("AAPL", "20230616", "C", {"chain": "data"})

        old_time = time.time() - 1000
        with conn._cache_lock:
            cache_key = "AAPL_20230616_C"
            if cache_key in conn._option_chain_cache:
                data, _ = conn._option_chain_cache[cache_key]
                conn._option_chain_cache[cache_key] = (data, old_time)

        with patch("core.connection_manager.is_market_open", return_value=False):
            result = conn._get_cached_option_chain("AAPL", "20230616", "C")
        self.assertIsNone(result)

    def test_broker_cache_after_hours_enabled(self):
        """Test that broker_cache_after_hours=True serves stale cache when market closed"""
        MoomooConnection._instances.clear()
        conn = MoomooConnection(broker_cache_after_hours=True)
        conn._cache_option_chain("AAPL", "20230616", "C", {"chain": "data"})

        old_time = time.time() - 10000
        with conn._cache_lock:
            cache_key = "AAPL_20230616_C"
            data, _ = conn._option_chain_cache[cache_key]
            conn._option_chain_cache[cache_key] = (data, old_time)

        with patch("core.connection_manager.is_market_open", return_value=False):
            result = conn._get_cached_option_chain("AAPL", "20230616", "C")
        self.assertIsNotNone(result)
        self.assertEqual(result, {"chain": "data"})


class TestMoomooConnectionExpirationCaching(unittest.TestCase):
    """Test option-expiration caching and pending-request reuse."""

    def setUp(self):
        MoomooConnection._instances.clear()

    def tearDown(self):
        MoomooConnection._instances.clear()

    def test_option_expiration_dates_are_cached(self):
        conn = MoomooConnection()
        conn._rate_limiter = MagicMock()
        conn._rate_limiter.check_rate_limit.return_value = None
        conn.quote_ctx = MagicMock()
        conn.is_connected = MagicMock(return_value=True)
        conn.connect = MagicMock(return_value=True)

        expected = (RET_OK, pd.DataFrame({"expiration_date": ["20260529"]}))
        conn.quote_ctx.get_option_expiration_date.return_value = expected

        first = conn.get_option_expiration_dates("AAPL")
        second = conn.get_option_expiration_dates("AAPL")

        self.assertEqual(first[0], expected[0])
        self.assertEqual(second[0], expected[0])
        pd.testing.assert_frame_equal(first[1], expected[1])
        pd.testing.assert_frame_equal(second[1], expected[1])
        conn.quote_ctx.get_option_expiration_date.assert_called_once_with(code="US.AAPL")

    def test_option_expiration_dates_reuse_pending_result(self):
        conn = MoomooConnection()
        conn._rate_limiter = MagicMock()
        conn._rate_limiter.check_rate_limit.return_value = None
        conn.quote_ctx = MagicMock()
        conn.is_connected = MagicMock(return_value=True)
        conn.connect = MagicMock(return_value=True)

        pending_result = (RET_OK, pd.DataFrame({"expiration_date": ["20260619"]}))
        conn._wait_for_pending_request = MagicMock(return_value=pending_result)

        result = conn.get_option_expiration_dates("MSFT")

        self.assertEqual(result[0], pending_result[0])
        pd.testing.assert_frame_equal(result[1], pending_result[1])
        conn.quote_ctx.get_option_expiration_date.assert_not_called()

    def test_pending_request_result_is_reusable_across_waiters(self):
        conn = MoomooConnection()
        conn._pending_result_ttl_seconds = 60

        request_key = "stock_price:US.AAPL"
        entry, is_new = conn._get_or_create_pending_request(request_key)
        self.assertTrue(is_new)

        expected = 123.45
        conn._complete_pending_request(request_key, expected)

        first = conn._wait_for_pending_request(request_key)
        second = conn._wait_for_pending_request(request_key)

        self.assertEqual(first, expected)
        self.assertEqual(second, expected)


class TestMoomooConnectionLifecycle(unittest.TestCase):
    """Test connection lifecycle: reconnect, unlock, disconnect edge cases"""

    def setUp(self):
        MoomooConnection._instances.clear()

    def tearDown(self):
        MoomooConnection._instances.clear()

    @patch("core.context_factory.OpenQuoteContext")
    @patch("core.context_factory.OpenSecTradeContext")
    def test_reconnect_idempotent(self, mock_trd_class, mock_quote_class):
        conn = MoomooConnection()
        conn.connect()
        conn.connect()
        mock_quote_class.assert_called()

    # unlock_trade removed — execution subsystem deleted

    @patch("core.context_factory.OpenQuoteContext")
    @patch("core.context_factory.OpenSecTradeContext")
    def test_disconnect_when_none(self, mock_trd_class, mock_quote_class):
        conn = MoomooConnection()
        conn._connected = False
        conn.quote_ctx = None
        conn.trd_ctx = None
        conn.disconnect()
        self.assertFalse(conn._connected)

    # unlock_trade removed — execution subsystem deleted


class TestMoomooConnectionDataRetrieval(unittest.TestCase):
    """Test data retrieval methods: stock price, option chain, portfolio, orders"""

    def setUp(self):
        MoomooConnection._instances.clear()
        self.mock_patcher_quote = patch("core.context_factory.OpenQuoteContext")
        self.mock_patcher_trd = patch("core.context_factory.OpenSecTradeContext")
        self.mock_quote_class = self.mock_patcher_quote.start()
        self.mock_trd_class = self.mock_patcher_trd.start()

        self.mock_quote_ctx = MagicMock()
        self.mock_trd_ctx = MagicMock()
        self.mock_quote_class.return_value = self.mock_quote_ctx
        self.mock_trd_class.return_value = self.mock_trd_ctx
        self.mock_quote_ctx.get_global_state.return_value = (RET_OK, "OK")

    def tearDown(self):
        MoomooConnection._instances.clear()
        self.mock_patcher_quote.stop()
        self.mock_patcher_trd.stop()

    def _make_connected_conn(self):
        conn = MoomooConnection()
        conn.connect()
        return conn

    def _make_acc_row(self, values):
        class AccRow:
            def __init__(self, data):
                self.data = data

            def get(self, key, default=None):
                return self.data.get(key, default)

        return AccRow(values)

    def test_get_portfolio_keeps_cash_and_buying_power_separate(self):
        conn = self._make_connected_conn()
        acc_data = MagicMock()
        acc_data.empty = False
        acc_data.iloc.__getitem__.return_value = self._make_acc_row(
            {
                "acc_id": "acct-123",
                "us_avl_withdrawal_cash": 0,
                "us_cash": 40000,
                "usd_net_cash_power": 25000,
                "cash": 0,
            }
        )
        empty_positions = MagicMock()
        empty_positions.empty = True
        empty_positions.to_dict.return_value = []
        self.mock_trd_ctx.accinfo_query.return_value = (RET_OK, acc_data)
        self.mock_trd_ctx.position_list_query.return_value = (RET_OK, empty_positions)

        result = conn.get_portfolio()

        self.assertEqual(result["available_cash"], 40000)
        self.assertEqual(result["available_cash_source"], "us_cash")
        self.assertEqual(result["buying_power"], 25000)
        self.assertEqual(result["buying_power_source"], "usd_net_cash_power")

    def test_get_portfolio_falls_back_to_us_cash_when_no_usd_cash_field_exists(self):
        conn = self._make_connected_conn()
        acc_data = MagicMock()
        acc_data.empty = False
        acc_data.iloc.__getitem__.return_value = self._make_acc_row(
            {
                "acc_id": "acct-123",
                "us_avl_withdrawal_cash": 0,
                "us_cash": 40000,
                "usd_net_cash_power": 0,
                "cash": 0,
            }
        )
        empty_positions = MagicMock()
        empty_positions.empty = True
        empty_positions.to_dict.return_value = []
        self.mock_trd_ctx.accinfo_query.return_value = (RET_OK, acc_data)
        self.mock_trd_ctx.position_list_query.return_value = (RET_OK, empty_positions)

        result = conn.get_portfolio()

        self.assertEqual(result["available_cash"], 40000)
        self.assertEqual(result["available_cash_source"], "us_cash")
        self.assertEqual(result["buying_power"], 0.0)
        self.assertEqual(result["buying_power_source"], "none")

    def test_get_stock_price_cached(self):
        conn = self._make_connected_conn()
        conn._cache_stock_price("US.TEST", 150.0)
        price = conn.get_stock_price("TEST")
        self.assertEqual(price, 150.0)
        self.mock_quote_ctx.get_market_snapshot.assert_not_called()

    def test_get_stock_price_fetch(self):
        conn = self._make_connected_conn()
        mock_df = MagicMock()
        mock_df.empty = False
        mock_row = MagicMock()
        mock_row.get.return_value = 155.5
        mock_df.iloc = MagicMock()
        mock_df.iloc.__getitem__.return_value = mock_row
        self.mock_quote_ctx.get_market_snapshot.return_value = (RET_OK, mock_df)

        price = conn.get_stock_price("AAPL")
        self.assertEqual(price, 155.5)

    def test_get_stock_price_no_data(self):
        conn = self._make_connected_conn()
        self.mock_quote_ctx.get_market_snapshot.return_value = (RET_ERROR, None)

        price = conn.get_stock_price("AAPL")
        self.assertIsNone(price)

    def test_get_option_chain(self):
        conn = self._make_connected_conn()
        mock_chain_df = MagicMock()
        mock_chain_df.empty = True
        self.mock_quote_ctx.get_option_chain.return_value = (RET_OK, mock_chain_df)

        result = conn.get_option_chain("US.AAPL", "20230616", "C")
        self.assertIsNotNone(result)
        self.assertIn("options", result)

    def test_get_option_chain_uses_shared_gate(self):
        conn = self._make_connected_conn()
        mock_chain_df = MagicMock()
        mock_chain_df.empty = True
        self.mock_quote_ctx.get_option_chain.return_value = (RET_OK, mock_chain_df)

        with patch.object(conn, "_acquire_option_chain_gate", wraps=conn._acquire_option_chain_gate) as acquire_gate:
            result = conn.get_option_chain("US.AAPL", "20230616", "C")

        self.assertIsNotNone(result)
        acquire_gate.assert_called_once()

    def test_get_option_chain_coalesces_duplicate_inflight_requests(self):
        conn = self._make_connected_conn()
        conn._option_chain_rate_limiter.check_rate_limit = MagicMock()

        entered = threading.Event()
        release = threading.Event()
        call_count = 0
        errors = []
        results = []

        mock_chain_df = pd.DataFrame({"code": ["US.AAPL230616C00150000"], "strike_price": [150.0]})
        mock_snap_df = pd.DataFrame(
            [
                {
                    "option_expiry_date": "2023-06-16",
                    "option_type": "CALL",
                    "option_strike_price": 150.0,
                    "bid_price": 1.0,
                    "ask_price": 1.2,
                    "last_price": 1.1,
                    "volume": 10,
                    "option_open_interest": 100,
                    "option_implied_volatility": 0.25,
                    "option_delta": 0.2,
                    "option_gamma": 0.01,
                    "option_theta": -0.02,
                    "option_vega": 0.03,
                }
            ]
        )

        def chain_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            entered.set()
            release.wait(timeout=2)
            return (RET_OK, mock_chain_df)

        self.mock_quote_ctx.get_option_chain.side_effect = chain_side_effect
        self.mock_quote_ctx.get_market_snapshot.return_value = (RET_OK, mock_snap_df)

        def worker():
            try:
                results.append(conn.get_option_chain("US.AAPL", "20230616", "C"))
            except Exception as exc:
                errors.append(exc)

        first = threading.Thread(target=worker)
        second = threading.Thread(target=worker)
        first.start()
        self.assertTrue(entered.wait(timeout=2))
        second.start()
        time.sleep(0.1)
        release.set()
        first.join(timeout=2)
        second.join(timeout=2)

        self.assertFalse(errors)
        self.assertEqual(call_count, 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result and result.get("options") for result in results))

    def test_get_option_chain_fails(self):
        conn = self._make_connected_conn()
        self.mock_quote_ctx.get_option_chain.return_value = (RET_ERROR, None)

        result = conn.get_option_chain("US.AAPL", "20230616", "C")
        self.assertIsNone(result)

    def test_get_portfolio_no_connection(self):
        conn = MoomooConnection()
        with patch.object(conn, "connect", return_value=False):
            result = conn.get_portfolio()
        self.assertIsNone(result)

    def test_get_connection_info_with_connected(self):
        conn = self._make_connected_conn()
        info = conn.get_connection_info()
        self.assertTrue(info["connected"])
        self.assertIn("rate_limit_stats", info)
        self.assertIn("rate_limit_config", info)

    def test_get_connection_pool_stats(self):
        MoomooConnection(host="127.0.0.1", port=11111)
        MoomooConnection(host="127.0.0.2", port=11111)
        stats = MoomooConnection.get_connection_pool_stats()
        self.assertGreaterEqual(stats["cached_instances"], 2)

    def test_get_user_security_group(self):
        conn = self._make_connected_conn()
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.to_dict.return_value = [{"group_id": 1, "group_name": "My Watchlist"}]
        self.mock_quote_ctx.get_user_security_group.return_value = (RET_OK, mock_df)

        ret, data = conn.get_user_security_group()

        self.assertEqual(ret, RET_OK)
        self.assertIsNotNone(data)
        self.mock_quote_ctx.get_user_security_group.assert_called_once()

    def test_get_user_security_group_with_group_type(self):
        conn = self._make_connected_conn()
        mock_df = MagicMock()
        mock_df.empty = False
        self.mock_quote_ctx.get_user_security_group.return_value = (RET_OK, mock_df)

        from moomoo import UserSecurityGroupType

        ret, data = conn.get_user_security_group(group_type=UserSecurityGroupType.CUSTOM)

        self.assertEqual(ret, RET_OK)
        self.mock_quote_ctx.get_user_security_group.assert_called_once_with(group_type=UserSecurityGroupType.CUSTOM)

    def test_get_user_security_group_failure(self):
        conn = self._make_connected_conn()
        self.mock_quote_ctx.get_user_security_group.return_value = (RET_ERROR, None)

        ret, data = conn.get_user_security_group()

        self.assertEqual(ret, RET_ERROR)

    def test_get_user_security(self):
        conn = self._make_connected_conn()
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.to_dict.return_value = [{"code": "US.AAPL", "name": "Apple Inc."}]
        self.mock_quote_ctx.get_user_security.return_value = (RET_OK, mock_df)

        ret, data = conn.get_user_security("My Watchlist")

        self.assertEqual(ret, RET_OK)
        self.assertIsNotNone(data)
        self.mock_quote_ctx.get_user_security.assert_called_once_with("My Watchlist")

    def test_get_user_security_failure(self):
        conn = self._make_connected_conn()
        self.mock_quote_ctx.get_user_security.return_value = (RET_ERROR, None)

        ret, data = conn.get_user_security("My Watchlist")

        self.assertEqual(ret, RET_ERROR)

    def test_get_option_expiration_dates(self):
        conn = self._make_connected_conn()
        mock_df = MagicMock()
        self.mock_quote_ctx.get_option_expiration_date.return_value = (RET_OK, mock_df)

        result = conn.get_option_expiration_dates("US.AAPL")
        self.assertEqual(result, (RET_OK, mock_df))

    def test_get_option_chain_with_data_filter(self):
        conn = self._make_connected_conn()
        mock_chain_df = MagicMock()
        mock_chain_df.empty = True
        self.mock_quote_ctx.get_option_chain.return_value = (RET_OK, mock_chain_df)

        from moomoo import OptionDataFilter

        f = OptionDataFilter()
        f.delta_min = 0.15
        f.delta_max = 0.35
        result = conn.get_option_chain("US.AAPL", "20230616", "C", data_filter=f)

        self.assertIsNotNone(result)
        call_kwargs = self.mock_quote_ctx.get_option_chain.call_args[1]
        self.assertIn("data_filter", call_kwargs)
        self.assertEqual(call_kwargs["data_filter"].delta_min, 0.15)

    def test_query_subscription(self):
        conn = self._make_connected_conn()
        self.mock_quote_ctx.query_subscription.return_value = (RET_OK, {"total_used": 5, "remain": 95, "own_used": 3})

        ret, data = conn.query_subscription()

        self.assertEqual(ret, RET_OK)
        self.assertEqual(data["total_used"], 5)
        self.mock_quote_ctx.query_subscription.assert_called_once()

    def test_query_subscription_failure(self):
        conn = self._make_connected_conn()
        self.mock_quote_ctx.query_subscription.return_value = (RET_ERROR, None)

        ret, data = conn.query_subscription()

        self.assertEqual(ret, RET_ERROR)

    def test_get_opend_diagnostics_connected(self):
        conn = self._make_connected_conn()
        self.mock_quote_ctx.query_subscription.return_value = (RET_OK, {"total_used": 3, "remain": 97, "own_used": 3})

        diag = conn.get_opend_diagnostics()

        self.assertTrue(diag["connected"])
        self.assertTrue(diag["sdk_available"])
        self.assertIn("sdk_version", diag)
        self.assertIn("subscription", diag)
        self.assertTrue(diag["option_data_filter_available"])

    def test_get_opend_diagnostics_disconnected(self):
        conn = MoomooConnection()
        with patch.object(conn, "_ensure_quote_context", return_value=False):
            diag = conn.get_opend_diagnostics()

        self.assertFalse(diag["connected"])
        self.assertIn("sdk_version", diag)

    def test_create_option_contract_found(self):
        conn = self._make_connected_conn()
        mock_df = MagicMock()
        mock_df.empty = False
        mock_match = MagicMock()
        mock_match.empty = False
        mock_iloc = MagicMock()
        mock_iloc.__getitem__.return_value = {"code": "US.AAPL240315C00200000"}
        mock_match.iloc = mock_iloc
        mock_df.__getitem__.return_value = mock_match
        self.mock_quote_ctx.get_option_chain.return_value = (RET_OK, mock_df)

        code = conn.create_option_contract("US.AAPL", "20240315", 200, "CALL")
        self.assertEqual(code, "US.AAPL240315C00200000")

    def test_get_option_volatility_delegates_to_sdk(self):
        conn = self._make_connected_conn()
        self.mock_quote_ctx.get_option_volatility.return_value = (RET_OK, {"iv": 0.35})
        ret, data = conn.get_option_volatility("US.AAPL", query_time_period=2, hv_time_period=30)
        self.assertEqual(ret, RET_OK)
        self.assertEqual(data["iv"], 0.35)
        call_args = self.mock_quote_ctx.get_option_volatility.call_args
        self.assertEqual(call_args[0][0], "US.AAPL")
        self.assertEqual(call_args[1]["query_time_period"], 2)
        self.assertEqual(call_args[1]["hv_time_period"], 30)

    def test_get_option_volatility_graceful_fallback(self):
        conn = self._make_connected_conn()
        del self.mock_quote_ctx.get_option_volatility
        ret, data = conn.get_option_volatility("US.AAPL")
        self.assertEqual(ret, RET_ERROR)

    def test_get_option_exercise_probability_delegates_to_sdk(self):
        conn = self._make_connected_conn()
        self.mock_quote_ctx.get_option_exercise_probability.return_value = (RET_OK, {"probability": 0.72})
        ret, data = conn.get_option_exercise_probability("US.AAPL")
        self.assertEqual(ret, RET_OK)
        self.assertEqual(data["probability"], 0.72)

    def test_get_option_exercise_probability_graceful_fallback(self):
        conn = self._make_connected_conn()
        del self.mock_quote_ctx.get_option_exercise_probability
        ret, data = conn.get_option_exercise_probability("US.AAPL")
        self.assertEqual(ret, RET_ERROR)

    def test_get_option_screen_delegates_to_sdk(self):
        conn = self._make_connected_conn()
        self.mock_quote_ctx.get_option_screen.return_value = (RET_OK, {"matches": ["AAPL240315C00200000"]})
        ret, data = conn.get_option_screen(None)
        self.assertEqual(ret, RET_OK)
        self.assertIn("matches", data)

    def test_get_option_screen_graceful_fallback(self):
        conn = self._make_connected_conn()
        del self.mock_quote_ctx.get_option_screen
        ret, data = conn.get_option_screen(None)
        self.assertEqual(ret, RET_ERROR)

    def test_get_short_interest_delegates_to_sdk(self):
        conn = self._make_connected_conn()
        mock_us_df = MagicMock()
        mock_hk_df = MagicMock()
        self.mock_quote_ctx.get_short_interest.return_value = (RET_OK, mock_us_df, mock_hk_df)
        ret, us_df, hk_df = conn.get_short_interest("US.AAPL")
        self.assertEqual(ret, RET_OK)
        self.assertIs(us_df, mock_us_df)
        self.assertIs(hk_df, mock_hk_df)

    def test_get_short_interest_passes_pagination_params(self):
        conn = self._make_connected_conn()
        mock_us_df = MagicMock()
        mock_hk_df = MagicMock()
        self.mock_quote_ctx.get_short_interest.return_value = (RET_OK, mock_us_df, mock_hk_df)
        ret, us_df, hk_df = conn.get_short_interest("US.AAPL", next_key="abc", num=10)
        self.assertEqual(ret, RET_OK)
        call_kwargs = self.mock_quote_ctx.get_short_interest.call_args
        self.assertEqual(call_kwargs[0][0], "US.AAPL")
        self.assertEqual(call_kwargs[1]["next_key"], "abc")
        self.assertEqual(call_kwargs[1]["num"], 10)

    def test_get_short_interest_graceful_fallback(self):
        conn = self._make_connected_conn()
        del self.mock_quote_ctx.get_short_interest
        ret, us_df, hk_df = conn.get_short_interest("US.AAPL")
        self.assertEqual(ret, RET_ERROR)
        self.assertIsNone(us_df)
        self.assertIsNone(hk_df)

    def test_get_financials_earnings_price_move_delegates(self):
        conn = self._make_connected_conn()
        self.mock_quote_ctx.get_financials_earnings_price_move.return_value = (RET_OK, {"rows": []})
        ret, data = conn.get_financials_earnings_price_move("US.AAPL", period_count=2)
        self.assertEqual(ret, RET_OK)
        self.assertIn("rows", data)
        call_args = self.mock_quote_ctx.get_financials_earnings_price_move.call_args
        self.assertEqual(call_args[0][0], "US.AAPL")
        self.assertEqual(call_args[1]["period_count"], 2)

    def test_get_financials_earnings_price_move_graceful_fallback(self):
        conn = self._make_connected_conn()
        del self.mock_quote_ctx.get_financials_earnings_price_move
        ret, data = conn.get_financials_earnings_price_move("US.AAPL")
        self.assertEqual(ret, RET_ERROR)

    def test_get_financials_earnings_price_history_delegates(self):
        conn = self._make_connected_conn()
        self.mock_quote_ctx.get_financials_earnings_price_history.return_value = (RET_OK, {"history": []})
        ret, data = conn.get_financials_earnings_price_history("US.AAPL")
        self.assertEqual(ret, RET_OK)
        self.assertIn("history", data)

    def test_get_financials_earnings_price_history_graceful_fallback(self):
        conn = self._make_connected_conn()
        self.mock_quote_ctx.get_financials_earnings_price_history = None
        ret, data = conn.get_financials_earnings_price_history("US.AAPL")
        self.assertEqual(ret, RET_ERROR)


class TestMoomooConnectionThreadSafety(unittest.TestCase):
    """Test thread safety of MoomooConnection"""

    def setUp(self):
        MoomooConnection._instances.clear()

    def tearDown(self):
        MoomooConnection._instances.clear()

    def test_singleton_thread_safety(self):
        """Test that singleton creation is thread-safe"""
        instances = []
        barrier = threading.Barrier(5, timeout=5)

        def create_instance():
            barrier.wait()  # synchronize start
            conn = MoomooConnection(host="127.0.0.1", port=11111)
            instances.append(conn)
            return conn

        threads = []
        for _ in range(5):
            t = threading.Thread(target=create_instance)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=5)

        self.assertTrue(all(inst is instances[0] for inst in instances))


class TestMoomooConnectionAccountResolution(unittest.TestCase):
    """Test account resolution for portfolio and order operations"""

    def setUp(self):
        MoomooConnection._instances.clear()

    def tearDown(self):
        MoomooConnection._instances.clear()

    def _make_mock_trd_ctx(self, accounts=None):
        """Create a mock trade context with get_acc_list support."""
        mock_trd = MagicMock()
        mock_data = MagicMock()
        if accounts is None:
            mock_trd.get_acc_list.return_value = (RET_OK, mock_data)
            mock_data.empty = True
        else:
            mock_data.empty = False
            mock_data.to_dict.return_value = accounts
            mock_trd.get_acc_list.return_value = (RET_OK, mock_data)
        return mock_trd

    def test_resolve_portfolio_account_with_id_matched(self):
        conn = MoomooConnection(account_id="12345", portfolio_env="REAL")
        accounts = [{"acc_id": "12345", "trd_env": TrdEnv.REAL, "security_firm": "FUTU"}]
        conn.trd_ctx = self._make_mock_trd_ctx(accounts)

        trd_env, acc_id = conn._resolve_portfolio_account()
        self.assertEqual(trd_env, TrdEnv.REAL)
        self.assertEqual(acc_id, "12345")

    def test_resolve_portfolio_account_no_id_finds_by_env(self):
        conn = MoomooConnection(portfolio_env="REAL")
        accounts = [{"acc_id": "67890", "trd_env": TrdEnv.REAL, "security_firm": "FUTU"}]
        conn.trd_ctx = self._make_mock_trd_ctx(accounts)

        trd_env, acc_id = conn._resolve_portfolio_account()
        self.assertEqual(trd_env, TrdEnv.REAL)
        self.assertEqual(acc_id, "67890")

    def test_resolve_portfolio_account_none_found(self):
        conn = MoomooConnection(portfolio_env="REAL")
        accounts = []
        conn.trd_ctx = self._make_mock_trd_ctx(accounts)

        trd_env, acc_id = conn._resolve_portfolio_account()
        self.assertEqual(trd_env, TrdEnv.REAL)
        self.assertEqual(acc_id, "")

    @patch("core.context_factory.OpenQuoteContext")
    @patch("core.context_factory.OpenSecTradeContext")
    def test_safe_disconnect_handles_exception_in_close(self, mock_trd_class, mock_quote_class):
        mock_quote = MagicMock()
        mock_quote.close.side_effect = Exception("Close blew up")
        mock_trd = MagicMock()
        mock_trd.close.side_effect = RuntimeError("TRD close broke")
        mock_quote_class.return_value = mock_quote
        mock_trd_class.return_value = mock_trd

        conn = MoomooConnection()
        conn.connect()
        conn._safe_disconnect()

        self.assertIsNone(conn.quote_ctx)
        self.assertIsNone(conn.trd_ctx)
        self.assertFalse(conn._connected)


class TestBrokerEvidence(unittest.TestCase):
    def setUp(self):
        MoomooConnection._instances.clear()

    def tearDown(self):
        MoomooConnection._instances.clear()

    def test_security_types_are_batched_and_broker_derived(self):
        conn = MoomooConnection()
        conn.quote_ctx = MagicMock()
        conn.is_connected = MagicMock(return_value=True)
        conn.quote_ctx.get_market_snapshot.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {"code": "US.SPY", "stock_type": "ETF"},
                    {"code": "US.AAPL", "stock_type": "STOCK"},
                ]
            ),
        )

        result = conn.get_security_types(["SPY", "AAPL"])

        self.assertEqual(result, {"SPY": "etf", "AAPL": "stock"})
        conn.quote_ctx.get_market_snapshot.assert_called_once()
        self.assertEqual(conn.get_security_type("SPY"), "etf")

    def test_option_chain_carries_broker_time_and_fetch_time(self):
        conn = MoomooConnection()
        conn.quote_ctx = MagicMock()
        conn.is_connected = MagicMock(return_value=True)
        conn.quote_ctx.get_option_chain.return_value = (
            RET_OK,
            pd.DataFrame([{"code": "US.AAPL240101P00100000"}]),
        )
        conn.quote_ctx.get_market_snapshot.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "option_expiry_date": "2024-01-01",
                        "option_type": "PUT",
                        "option_strike_price": 100,
                        "bid_price": 2,
                        "ask_price": 2.1,
                        "last_price": 2.05,
                        "volume": 10,
                        "option_open_interest": 20,
                        "option_implied_volatility": 30,
                        "option_delta": -0.2,
                        "option_gamma": 0.01,
                        "option_theta": -0.02,
                        "option_vega": 0.1,
                        "update_time": "2024-01-01 10:00:00",
                    }
                ]
            ),
        )

        result = conn.get_option_chain("AAPL", "20240101", "P")
        option = result["options"][0]
        self.assertEqual(option["update_time"], "2024-01-01 10:00:00")
        self.assertTrue(option["quote_fetched_at_utc"])


if __name__ == "__main__":
    unittest.main()
