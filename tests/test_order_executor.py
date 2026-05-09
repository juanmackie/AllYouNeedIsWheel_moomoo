"""
Tests for api/services/order_executor.py — OrderExecutor class
"""

import unittest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.order_executor import OrderExecutor


class TestOrderExecutor(unittest.TestCase):
    """Test OrderExecutor with mocked service context."""

    def setUp(self):
        self.mock_context = MagicMock()
        self.mock_db = MagicMock()
        self.mock_context.db = self.mock_db
        self.executor = OrderExecutor(self.mock_context, self.mock_db, self.mock_context)

    def test_execute_order_not_found(self):
        self.mock_db.get_order.return_value = None

        result, status = self.executor.execute_order(999, self.mock_db)

        self.assertEqual(status, 404)
        self.assertFalse(result['success'])

    def test_execute_order_wrong_status(self):
        self.mock_db.get_order.return_value = {
            'id': 1, 'status': 'executed', 'ticker': 'AAPL'
        }

        result, status = self.executor.execute_order(1, self.mock_db)

        self.assertEqual(status, 400)
        self.assertFalse(result['success'])

    def test_execute_order_no_connection(self):
        self.mock_db.get_order.return_value = {
            'id': 1, 'status': 'pending', 'ticker': 'AAPL',
            'quantity': 1, 'action': 'SELL', 'expiration': '20240315',
            'strike': 200, 'option_type': 'CALL',
            'bid': 2.50, 'ask': 2.60, 'last': 2.55,
        }
        self.mock_context._ensure_connection.return_value = None

        result, status = self.executor.execute_order(1, self.mock_db)

        self.assertEqual(status, 500)
        self.assertFalse(result['success'])

    def test_execute_order_missing_option_details(self):
        self.mock_db.get_order.return_value = {
            'id': 1, 'status': 'pending', 'ticker': 'AAPL',
            'quantity': 1, 'action': 'SELL',
            'expiration': None, 'strike': None, 'option_type': None,
        }
        self.mock_context._ensure_connection.return_value = MagicMock()

        result, status = self.executor.execute_order(1, self.mock_db)

        self.assertEqual(status, 400)
        self.assertFalse(result['success'])

    def test_execute_order_contract_not_found(self):
        mock_conn = MagicMock()
        mock_conn.create_option_contract.return_value = None
        self.mock_context._ensure_connection.return_value = mock_conn
        self.mock_db.get_order.return_value = {
            'id': 1, 'status': 'pending', 'ticker': 'AAPL',
            'quantity': 1, 'action': 'SELL', 'expiration': '20240315',
            'strike': 200, 'option_type': 'CALL',
            'bid': 2.50, 'ask': 2.60, 'last': 2.55,
        }

        result, status = self.executor.execute_order(1, self.mock_db)

        self.assertEqual(status, 400)
        self.assertFalse(result['success'])

    @patch('moomoo.RET_OK', 'OK')
    def test_execute_order_success(self):
        mock_conn = MagicMock()
        mock_conn.create_option_contract.return_value = 'US.AAPL240315C00200000'
        mock_conn.place_order.return_value = {
            'order_id': '67890', 'status': 'Submitted',
            'filled': 0, 'remaining': 1, 'avg_fill_price': 0,
        }
        self.mock_context._ensure_connection.return_value = mock_conn
        self.mock_context.portfolio_service = MagicMock()
        self.mock_db.get_order.return_value = {
            'id': 1, 'status': 'pending', 'ticker': 'AAPL',
            'quantity': 1, 'action': 'SELL', 'expiration': '20240315',
            'strike': 200, 'option_type': 'CALL',
            'bid': 2.50, 'ask': 2.60, 'last': 2.55,
            'isRollover': False,
        }

        result, status = self.executor.execute_order(1, self.mock_db)

        self.assertEqual(status, 200)
        self.assertTrue(result['success'])
        mock_conn.place_order.assert_called_once()
        self.mock_db.update_order_status.assert_called_once()
        self.mock_context.portfolio_service.invalidate_cache.assert_called_once()

    def test_check_pending_orders_no_orders(self):
        self.mock_db.get_orders.return_value = []

        result = self.executor.check_pending_orders()

        self.assertTrue(result['success'])
        self.assertEqual(result['updated_orders'], [])

    def test_check_pending_orders_updates(self):
        self.mock_db.get_orders.return_value = [
            {'id': 1, 'status': 'processing', 'moomoo_order_id': '111'},
        ]
        mock_conn = MagicMock()
        mock_conn.check_order_status.return_value = {
            'status': 'Filled', 'filled': 1, 'remaining': 0,
            'avg_fill_price': 2.50,
        }
        self.mock_context._ensure_connection.return_value = mock_conn

        result = self.executor.check_pending_orders()

        self.assertTrue(result['success'])
        self.assertEqual(len(result['updated_orders']), 1)

    def test_cancel_order_not_found(self):
        self.mock_db.get_order.return_value = None

        result, status = self.executor.cancel_order(999)

        self.assertEqual(status, 404)
        self.assertFalse(result['success'])

    def test_cancel_order_success(self):
        self.mock_db.get_order.return_value = {
            'id': 1, 'status': 'pending',
        }

        result, status = self.executor.cancel_order(1)

        self.assertEqual(status, 200)
        self.assertTrue(result['success'])

    def test_cancel_order_with_moomoo(self):
        mock_conn = MagicMock()
        mock_conn.cancel_order.return_value = {'success': True}
        self.mock_context._ensure_connection.return_value = mock_conn
        self.mock_db.get_order.return_value = {
            'id': 1, 'status': 'processing', 'moomoo_order_id': '111',
        }

        result, status = self.executor.cancel_order(1)

        self.assertEqual(status, 200)
        self.assertTrue(result['success'])
        mock_conn.cancel_order.assert_called_once_with('111')


if __name__ == '__main__':
    unittest.main()
