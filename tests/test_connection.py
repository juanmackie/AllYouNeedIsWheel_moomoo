"""
Tests for core/connection.py - MoomooConnection class and helper functions
"""

import unittest
from unittest.mock import Mock, patch, MagicMock, call
import threading
import time
from datetime import datetime, timedelta
import os

# Import the module under test
from core.connection import (
    MoomooConnection,
    _safe_close_context,
    _is_truthy_flag,
    _clean_account_id,
    _env_name,
    _normalize_trd_env,
    _normalize_security_firm,
    _infer_security_type_from_code,
    _parse_option_code_metadata,
    _safe_float,
    _first_non_zero,
    probe_opend_status,
    TrdEnv,
    SecurityFirm,
)
from moomoo import RET_OK, RET_ERROR


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
        self.assertTrue(_is_truthy_flag('true'))
        self.assertTrue(_is_truthy_flag('True'))
        self.assertTrue(_is_truthy_flag('YES'))
        self.assertTrue(_is_truthy_flag('yes'))
        self.assertTrue(_is_truthy_flag('Y'))
        self.assertTrue(_is_truthy_flag('ok'))
        self.assertTrue(_is_truthy_flag('connected'))
        self.assertTrue(_is_truthy_flag('ready'))
        self.assertFalse(_is_truthy_flag('false'))
        self.assertFalse(_is_truthy_flag(''))
        
        # None
        self.assertFalse(_is_truthy_flag(None))
    
    def test_clean_account_id(self):
        """Test _clean_account_id"""
        self.assertEqual(_clean_account_id(None), '')
        self.assertEqual(_clean_account_id(''), '')
        self.assertEqual(_clean_account_id('YOUR_MOOMOO_ACCOUNT_ID'), '')
        self.assertEqual(_clean_account_id('  123456  '), '123456')
        self.assertEqual(_clean_account_id(123456), '123456')
    
    def test_env_name(self):
        """Test _env_name"""
        self.assertEqual(_env_name(TrdEnv.SIMULATE), 'SIMULATE')
        self.assertEqual(_env_name(TrdEnv.REAL), 'REAL')
    
    def test_normalize_trd_env(self):
        """Test _normalize_trd_env"""
        default = TrdEnv.SIMULATE
        
        # Test with None
        self.assertEqual(_normalize_trd_env(None, default), default)
        
        # Test with valid enum values
        self.assertEqual(_normalize_trd_env(TrdEnv.SIMULATE, default), TrdEnv.SIMULATE)
        self.assertEqual(_normalize_trd_env(TrdEnv.REAL, default), TrdEnv.REAL)
        
        # Test with string values
        self.assertEqual(_normalize_trd_env('sim', default), TrdEnv.SIMULATE)
        self.assertEqual(_normalize_trd_env('SIMULATE', default), TrdEnv.SIMULATE)
        self.assertEqual(_normalize_trd_env('paper', default), TrdEnv.SIMULATE)
        self.assertEqual(_normalize_trd_env('REAL', default), TrdEnv.REAL)
        self.assertEqual(_normalize_trd_env('live', default), TrdEnv.REAL)
        
        # Test with invalid string
        self.assertEqual(_normalize_trd_env('invalid', default), default)
    
    def test_normalize_security_firm(self):
        """Test _normalize_security_firm"""
        default = SecurityFirm.FUTUSECURITIES
        
        # Test with None
        self.assertEqual(_normalize_security_firm(None, default), default)
        
        # Test with valid enum values
        self.assertEqual(
            _normalize_security_firm(SecurityFirm.FUTUSECURITIES, default),
            SecurityFirm.FUTUSECURITIES
        )
        
        # Test with string attribute name
        result = _normalize_security_firm('FUTUAU', default)
        self.assertEqual(result, SecurityFirm.FUTUAU)
        
        # Test with invalid string (should return default)
        result = _normalize_security_firm('INVALID', default)
        self.assertEqual(result, default)
    
    def test_infer_security_type_from_code(self):
        """Test _infer_security_type_from_code"""
        # Option code pattern
        self.assertEqual(_infer_security_type_from_code('US.AAPL230616C00150000'), 'OPT')
        self.assertEqual(_infer_security_type_from_code('US.TSLA240315P00200000'), 'OPT')
        
        # Stock code pattern
        self.assertEqual(_infer_security_type_from_code('US.AAPL'), 'STK')
        self.assertEqual(_infer_security_type_from_code('US.TSLA'), 'STK')
        
        # Invalid
        self.assertEqual(_infer_security_type_from_code(''), '')
        self.assertEqual(_infer_security_type_from_code(None), '')
        self.assertEqual(_infer_security_type_from_code('INVALID'), '')
    
    def test_parse_option_code_metadata(self):
        """Test _parse_option_code_metadata"""
        # Valid option code
        result = _parse_option_code_metadata('US.AAPL230616C00150000')
        self.assertIsNotNone(result)
        self.assertEqual(result['underlying'], 'AAPL')
        self.assertEqual(result['expiration'], '20230616')
        self.assertEqual(result['strike'], 150.0)
        self.assertEqual(result['option_type'], 'CALL')
        
        # Put option
        result = _parse_option_code_metadata('US.TSLA240315P00200000')
        self.assertIsNotNone(result)
        self.assertEqual(result['underlying'], 'TSLA')
        self.assertEqual(result['expiration'], '20240315')
        self.assertEqual(result['strike'], 200.0)
        self.assertEqual(result['option_type'], 'PUT')
        
        # Invalid codes
        self.assertIsNone(_parse_option_code_metadata(None))
        self.assertIsNone(_parse_option_code_metadata(''))
        self.assertIsNone(_parse_option_code_metadata('US.AAPL'))
    
    def test_safe_float(self):
        """Test _safe_float"""
        self.assertEqual(_safe_float(None), 0.0)
        self.assertEqual(_safe_float(''), 0.0)
        self.assertEqual(_safe_float('N/A'), 0.0)
        self.assertEqual(_safe_float('nan'), 0.0)
        self.assertEqual(_safe_float('NaN'), 0.0)
        self.assertEqual(_safe_float(42), 42.0)
        self.assertEqual(_safe_float(3.14), 3.14)
        self.assertEqual(_safe_float('3.14'), 3.14)
        self.assertEqual(_safe_float('invalid', default=99.0), 99.0)
    
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
        self.assertEqual(_first_non_zero('N/A', 3.14), 3.14)


class TestProbeOpenDStatus(unittest.TestCase):
    """Test the probe_opend_status function"""
    
    @patch('socket.socket')
    def test_probe_opend_reachable(self, mock_socket_class):
        """Test probing when OpenD is reachable"""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 0  # Success
        mock_socket_class.return_value = mock_socket
        
        result = probe_opend_status('127.0.0.1', 11111)
        
        self.assertTrue(result['reachable'])
        self.assertTrue(result['connected'])
        self.assertEqual(result['status'], 'connected')
        mock_socket.close.assert_called_once()
    
    @patch('socket.socket')
    def test_probe_opend_unreachable(self, mock_socket_class):
        """Test probing when OpenD is not reachable"""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 1  # Failure
        mock_socket_class.return_value = mock_socket
        
        result = probe_opend_status('127.0.0.1', 11111)
        
        self.assertFalse(result['reachable'])
        self.assertFalse(result['connected'])
        self.assertEqual(result['status'], 'unavailable')
        mock_socket.close.assert_called_once()
    
    @patch('socket.socket')
    def test_probe_opend_exception(self, mock_socket_class):
        """Test probing when an exception occurs"""
        mock_socket = MagicMock()
        mock_socket.connect_ex.side_effect = Exception("Network error")
        mock_socket_class.return_value = mock_socket
        
        result = probe_opend_status('127.0.0.1', 11111)
        
        self.assertFalse(result['reachable'])
        self.assertFalse(result['connected'])
        self.assertEqual(result['status'], 'error')


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
        conn1 = MoomooConnection(host='127.0.0.1', port=11111, readonly=True)
        conn2 = MoomooConnection(host='127.0.0.1', port=11111, readonly=True)
        self.assertIs(conn1, conn2)
    
    def test_different_instance_different_config(self):
        """Test that different config returns different instance"""
        conn1 = MoomooConnection(host='127.0.0.1', port=11111, readonly=True)
        conn2 = MoomooConnection(host='127.0.0.1', port=11111, readonly=False)
        self.assertIsNot(conn1, conn2)
    
    def test_singleton_key_format(self):
        """Test that singleton key is properly formatted"""
        conn = MoomooConnection(
            host='192.168.1.100',
            port=22222,
            readonly=False,
            account_id='12345',
            portfolio_env='REAL',
            security_firm='FUTUAU'
        )
        
        expected_key = '192.168.1.100:22222:False:12345:REAL:FUTUAU'
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
            self.assertEqual(conn.host, '127.0.0.1')
            self.assertEqual(conn.port, 11111)
            self.assertTrue(conn.readonly)
            self.assertEqual(conn.portfolio_env, TrdEnv.SIMULATE)
            self.assertEqual(conn.security_firm, SecurityFirm.FUTUSECURITIES)
            self.assertFalse(conn._connected)
    
    def test_init_custom_values(self):
        """Test initialization with custom values"""
        conn = MoomooConnection(
            host='192.168.1.100',
            port=22222,
            readonly=False,
            account_id='12345',
            portfolio_env='REAL',
            security_firm='FUTUAU'
        )
        self.assertEqual(conn.host, '192.168.1.100')
        self.assertEqual(conn.port, 22222)
        self.assertFalse(conn.readonly)
        self.assertEqual(conn.portfolio_env, TrdEnv.REAL)
        self.assertEqual(conn.security_firm, SecurityFirm.FUTUAU)
    
    def test_init_account_id_cleaning(self):
        """Test that account_id is cleaned during init"""
        conn = MoomooConnection(account_id='YOUR_MOOMOO_ACCOUNT_ID')
        self.assertEqual(conn.account_id, '')
        
        conn2 = MoomooConnection(account_id='  12345  ')
        self.assertEqual(conn2.account_id, '12345')
    
    def test_readonly_sets_simulate_env(self):
        """Test that readonly=True sets portfolio_env to SIMULATE"""
        conn = MoomooConnection(readonly=True)
        self.assertEqual(conn.portfolio_env, TrdEnv.SIMULATE)
    
    def test_no_readonly_sets_real_env(self):
        """Test that readonly=False sets portfolio_env to REAL"""
        conn = MoomooConnection(readonly=False)
        self.assertEqual(conn.portfolio_env, TrdEnv.REAL)


class TestMoomooConnectionMethods(unittest.TestCase):
    """Test MoomooConnection methods with mocking"""
    
    def setUp(self):
        MoomooConnection._instances.clear()
    
    def tearDown(self):
        MoomooConnection._instances.clear()
    
    @patch('core.context_factory.OpenQuoteContext')
    @patch('core.context_factory.OpenSecTradeContext')
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
        mock_quote_class.assert_called_once_with(host='127.0.0.1', port=11111)
        mock_trd_class.assert_called_once()
    
    @patch('core.context_factory.OpenQuoteContext')
    def test_connect_failure(self, mock_quote_class):
        """Test connection failure"""
        mock_quote_class.side_effect = Exception("Connection refused")
        
        conn = MoomooConnection()
        result = conn.connect()
        
        self.assertFalse(result)
        self.assertFalse(conn._connected)
    
    @patch('core.context_factory.OpenQuoteContext')
    def test_is_connected_true(self, mock_quote_class):
        """Test is_connected returns True when healthy"""
        mock_quote_ctx = MagicMock()
        mock_quote_ctx.get_global_state.return_value = (RET_OK, 'OK')
        mock_quote_class.return_value = mock_quote_ctx
        
        conn = MoomooConnection()
        conn._connected = True
        conn.quote_ctx = mock_quote_ctx
        
        self.assertTrue(conn.is_connected())
    
    @patch('core.context_factory.OpenQuoteContext')
    def test_is_connected_false_not_connected(self, mock_quote_class):
        """Test is_connected returns False when not connected"""
        conn = MoomooConnection()
        conn._connected = False
        
        self.assertFalse(conn.is_connected())
    
    @patch('core.context_factory.OpenQuoteContext')
    def test_is_connected_false_bad_health(self, mock_quote_class):
        """Test is_connected returns False when health check fails"""
        mock_quote_ctx = MagicMock()
        mock_quote_ctx.get_global_state.return_value = ('RET_ERROR', 'Not connected')
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
        self.assertEqual(conn._format_symbol('AAPL'), 'US.AAPL')
    
    def test_format_symbol_with_prefix(self):
        """Test _format_symbol doesn't duplicate prefix"""
        conn = MoomooConnection()
        self.assertEqual(conn._format_symbol('US.AAPL'), 'US.AAPL')
    
    def test_get_connection_info(self):
        """Test get_connection_info returns expected keys"""
        conn = MoomooConnection()
        info = conn.get_connection_info()
        
        expected_keys = [
            'connected', 'is_healthy', 'host', 'port',
            'last_activity', 'uptime_seconds',
            'has_quote_ctx', 'has_trd_ctx',
            'readonly', 'portfolio_env', 'security_firm'
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
        self.assertIsNone(conn._get_cached_stock_price('AAPL'))
        
        # Test cache set
        conn._cache_stock_price('AAPL', 150.0)
        
        # Test cache hit
        self.assertEqual(conn._get_cached_stock_price('AAPL'), 150.0)
        
        # Test cache expiry (manually set old timestamp)
        old_time = time.time() - 200  # Older than TTL (120s)
        with conn._ticker_cache._stock_price_cache_lock:
            conn._ticker_cache._stock_price_cache['AAPL'] = (150.0, old_time)
        
        # Should return None due to expiry
        self.assertIsNone(conn._get_cached_stock_price('AAPL'))
    
    def test_option_chain_cache(self):
        """Test option chain caching"""
        conn = MoomooConnection()
        
        mock_data = {'symbol': 'AAPL', 'options': []}
        
        # Test cache miss
        self.assertIsNone(conn._get_cached_option_chain('AAPL', '20230616', 'C'))
        
        # Test cache set
        conn._cache_option_chain('AAPL', '20230616', 'C', mock_data)
        
        # Test cache hit
        self.assertEqual(
            conn._get_cached_option_chain('AAPL', '20230616', 'C'),
            mock_data
        )


class TestMoomooConnectionLifecycle(unittest.TestCase):
    """Test connection lifecycle: reconnect, unlock, disconnect edge cases"""

    def setUp(self):
        MoomooConnection._instances.clear()

    def tearDown(self):
        MoomooConnection._instances.clear()

    @patch('core.context_factory.OpenQuoteContext')
    @patch('core.context_factory.OpenSecTradeContext')
    def test_reconnect_idempotent(self, mock_trd_class, mock_quote_class):
        conn = MoomooConnection()
        conn.connect()
        conn.connect()
        mock_quote_class.assert_called()

    @patch('core.context_factory.OpenQuoteContext')
    @patch('core.context_factory.OpenSecTradeContext')
    def test_connect_with_unlock_live(self, mock_trd_class, mock_quote_class):
        mock_trd_ctx = MagicMock()
        mock_trd_ctx.unlock_trade.return_value = (RET_OK, 'unlocked')
        mock_trd_class.return_value = mock_trd_ctx
        mock_quote_class.return_value = MagicMock()

        with patch.dict(os.environ, {'MOOMOO_TRADING_PASSWORD': 'secret'}, clear=True):
            conn = MoomooConnection(host='127.0.0.1', port=11111, readonly=False)
            result = conn.connect()

        self.assertTrue(result)
        mock_trd_ctx.unlock_trade.assert_called_once_with('secret')

    @patch('core.context_factory.OpenQuoteContext')
    @patch('core.context_factory.OpenSecTradeContext')
    def test_disconnect_when_none(self, mock_trd_class, mock_quote_class):
        conn = MoomooConnection()
        conn._connected = False
        conn.quote_ctx = None
        conn.trd_ctx = None
        conn.disconnect()
        self.assertFalse(conn._connected)

    @patch('core.context_factory.OpenQuoteContext')
    @patch('core.context_factory.OpenSecTradeContext')
    def test_connect_live_unlock_failure_logged(self, mock_trd_class, mock_quote_class):
        mock_trd_ctx = MagicMock()
        mock_trd_ctx.unlock_trade.return_value = (RET_ERROR, 'bad password')
        mock_trd_class.return_value = mock_trd_ctx
        mock_quote_class.return_value = MagicMock()

        with patch.dict(os.environ, {'MOOMOO_TRADING_PASSWORD': 'wrong'}, clear=True):
            conn = MoomooConnection(readonly=False)
            result = conn.connect()

        self.assertTrue(result)
        mock_trd_ctx.unlock_trade.assert_called_once_with('wrong')


class TestMoomooConnectionDataRetrieval(unittest.TestCase):
    """Test data retrieval methods: stock price, option chain, portfolio, orders"""

    def setUp(self):
        MoomooConnection._instances.clear()
        self.mock_patcher_quote = patch('core.context_factory.OpenQuoteContext')
        self.mock_patcher_trd = patch('core.context_factory.OpenSecTradeContext')
        self.mock_quote_class = self.mock_patcher_quote.start()
        self.mock_trd_class = self.mock_patcher_trd.start()

        self.mock_quote_ctx = MagicMock()
        self.mock_trd_ctx = MagicMock()
        self.mock_quote_class.return_value = self.mock_quote_ctx
        self.mock_trd_class.return_value = self.mock_trd_ctx
        self.mock_quote_ctx.get_global_state.return_value = (RET_OK, 'OK')

    def tearDown(self):
        MoomooConnection._instances.clear()
        self.mock_patcher_quote.stop()
        self.mock_patcher_trd.stop()

    def _make_connected_conn(self):
        conn = MoomooConnection()
        conn.connect()
        return conn

    def test_get_stock_price_cached(self):
        conn = self._make_connected_conn()
        conn._cache_stock_price('US.TEST', 150.0)
        price = conn.get_stock_price('TEST')
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

        price = conn.get_stock_price('AAPL')
        self.assertEqual(price, 155.5)

    def test_get_stock_price_no_data(self):
        conn = self._make_connected_conn()
        self.mock_quote_ctx.get_market_snapshot.return_value = (RET_ERROR, None)

        price = conn.get_stock_price('AAPL')
        self.assertIsNone(price)

    def test_get_option_chain(self):
        conn = self._make_connected_conn()
        mock_chain_df = MagicMock()
        mock_chain_df.empty = True
        self.mock_quote_ctx.get_option_chain.return_value = (RET_OK, mock_chain_df)

        result = conn.get_option_chain('US.AAPL', '20230616', 'C')
        self.assertIsNotNone(result)
        self.assertIn('options', result)

    def test_get_option_chain_fails(self):
        conn = self._make_connected_conn()
        self.mock_quote_ctx.get_option_chain.return_value = (RET_ERROR, None)

        result = conn.get_option_chain('US.AAPL', '20230616', 'C')
        self.assertIsNone(result)

    def test_get_portfolio_no_connection(self):
        conn = MoomooConnection()
        with patch.object(conn, 'connect', return_value=False):
            result = conn.get_portfolio()
        self.assertIsNone(result)

    def test_place_order(self):
        conn = self._make_connected_conn()
        mock_order_df = MagicMock()
        mock_order_df.iloc = MagicMock()
        mock_order_df.iloc.__getitem__.return_value = {'order_id': '12345'}
        self.mock_trd_ctx.place_order.return_value = (RET_OK, mock_order_df)

        result = conn.place_order('US.AAPL240315C00200000', 1, 'SELL', 2.50)
        self.assertIsNotNone(result)
        self.assertEqual(result['order_id'], '12345')

    def test_place_order_fails(self):
        conn = self._make_connected_conn()
        self.mock_trd_ctx.place_order.return_value = (RET_ERROR, 'Insufficient funds')

        result = conn.place_order('US.AAPL240315C00200000', 1, 'SELL', 2.50)
        self.assertIsNone(result)

    def test_check_order_status(self):
        conn = self._make_connected_conn()
        mock_df = MagicMock()
        mock_df.empty = False
        mock_order = MagicMock()
        mock_order.get.side_effect = lambda key, default=0: {
            'order_status': 'Filled',
            'dealt_qty': 100,
            'qty': 100,
            'dealt_avg_price': 2.45,
        }.get(key, default)
        mock_df.iloc = MagicMock()
        mock_df.iloc.__getitem__.return_value = mock_order
        self.mock_trd_ctx.order_list_query.return_value = (RET_OK, mock_df)

        result = conn.check_order_status('67890')
        self.assertIsNotNone(result)
        self.assertEqual(result['status'], 'Filled')

    def test_check_order_status_empty(self):
        conn = self._make_connected_conn()
        mock_df = MagicMock()
        mock_df.empty = True
        self.mock_trd_ctx.order_list_query.return_value = (RET_OK, mock_df)

        result = conn.check_order_status('99999')
        self.assertIsNone(result)

    def test_cancel_order(self):
        conn = self._make_connected_conn()
        self.mock_trd_ctx.modify_order.return_value = (RET_OK, None)

        result = conn.cancel_order('12345')
        self.assertTrue(result['success'])

    def test_cancel_order_fails(self):
        conn = self._make_connected_conn()
        self.mock_trd_ctx.modify_order.return_value = (RET_ERROR, 'Already filled')

        result = conn.cancel_order('12345')
        self.assertFalse(result['success'])

    def test_get_connection_info_with_connected(self):
        conn = self._make_connected_conn()
        info = conn.get_connection_info()
        self.assertTrue(info['connected'])
        self.assertIn('rate_limit_stats', info)
        self.assertIn('rate_limit_config', info)

    def test_get_connection_pool_stats(self):
        conn1 = MoomooConnection(host='127.0.0.1', port=11111)
        conn2 = MoomooConnection(host='127.0.0.2', port=11111)
        stats = MoomooConnection.get_connection_pool_stats()
        self.assertGreaterEqual(stats['cached_instances'], 2)

    def test_get_option_expiration_dates(self):
        conn = self._make_connected_conn()
        mock_df = MagicMock()
        self.mock_quote_ctx.get_option_expiration_date.return_value = (RET_OK, mock_df)

        result = conn.get_option_expiration_dates('US.AAPL')
        self.assertEqual(result, (RET_OK, mock_df))

    def test_create_option_contract_found(self):
        conn = self._make_connected_conn()
        mock_df = MagicMock()
        mock_df.empty = False
        mock_match = MagicMock()
        mock_match.empty = False
        mock_iloc = MagicMock()
        mock_iloc.__getitem__.return_value = {'code': 'US.AAPL240315C00200000'}
        mock_match.iloc = mock_iloc
        mock_df.__getitem__.return_value = mock_match
        self.mock_quote_ctx.get_option_chain.return_value = (RET_OK, mock_df)

        code = conn.create_option_contract('US.AAPL', '20240315', 200, 'CALL')
        self.assertEqual(code, 'US.AAPL240315C00200000')


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
            conn = MoomooConnection(host='127.0.0.1', port=11111)
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
        conn = MoomooConnection(account_id='12345', portfolio_env='REAL')
        accounts = [{'acc_id': '12345', 'trd_env': TrdEnv.REAL, 'security_firm': 'FUTU'}]
        conn.trd_ctx = self._make_mock_trd_ctx(accounts)

        trd_env, acc_id = conn._resolve_portfolio_account()
        self.assertEqual(trd_env, TrdEnv.REAL)
        self.assertEqual(acc_id, '12345')

    def test_resolve_portfolio_account_no_id_finds_by_env(self):
        conn = MoomooConnection(portfolio_env='REAL')
        accounts = [{'acc_id': '67890', 'trd_env': TrdEnv.REAL, 'security_firm': 'FUTU'}]
        conn.trd_ctx = self._make_mock_trd_ctx(accounts)

        trd_env, acc_id = conn._resolve_portfolio_account()
        self.assertEqual(trd_env, TrdEnv.REAL)
        self.assertEqual(acc_id, '67890')

    def test_resolve_portfolio_account_none_found(self):
        conn = MoomooConnection(portfolio_env='REAL')
        accounts = []
        conn.trd_ctx = self._make_mock_trd_ctx(accounts)

        trd_env, acc_id = conn._resolve_portfolio_account()
        self.assertEqual(trd_env, TrdEnv.REAL)
        self.assertEqual(acc_id, '')

    def test_resolve_order_account_with_id_matching_env(self):
        conn = MoomooConnection(readonly=False)
        accounts = [{'acc_id': '99999', 'trd_env': TrdEnv.REAL, 'security_firm': 'FUTU'}]
        conn.trd_ctx = self._make_mock_trd_ctx(accounts)
        conn.account_id = '99999'

        trd_env, acc_id = conn._resolve_order_account()
        self.assertEqual(trd_env, TrdEnv.REAL)
        self.assertEqual(acc_id, '99999')

    def test_resolve_order_account_no_accounts_falls_back_empty(self):
        conn = MoomooConnection(readonly=False)
        conn.trd_ctx = self._make_mock_trd_ctx([])

        trd_env, acc_id = conn._resolve_order_account()
        self.assertEqual(trd_env, TrdEnv.REAL)
        self.assertEqual(acc_id, '')

    @patch('core.context_factory.OpenQuoteContext')
    @patch('core.context_factory.OpenSecTradeContext')
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


if __name__ == '__main__':
    unittest.main()
