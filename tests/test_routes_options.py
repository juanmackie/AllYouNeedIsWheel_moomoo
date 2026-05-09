"""
Tests for api/routes/options.py — Flask route endpoints.

All endpoints are tested via Flask test client with mocked services,
database, and OpenD connection to avoid requiring live infrastructure.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock, call, PropertyMock
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from api.routes.options import bp, _options_service_instance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(**overrides):
    """Create a minimal Flask app with the options blueprint registered."""
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config.update({
        'connection_config': {
            'host': '127.0.0.1',
            'port': 11111,
        },
        **overrides,
    })
    app.register_blueprint(bp)
    return app


def _reset_service_global():
    """Reset the module-level service singleton between tests."""
    import api.routes.options as mod
    mod._options_service_instance = None


def _patch_service(mock_options_service=None):
    """Build a standard OptionsService mock with sensible defaults."""
    if mock_options_service is None:
        mock_options_service = MagicMock()
    return mock_options_service


def _make_mock_db():
    """Build a mock database with common methods."""
    db = MagicMock()
    db.save_order.return_value = 42
    db.get_pending_orders.return_value = []
    db.get_order.return_value = {'id': 1, 'status': 'pending', 'ticker': 'AAPL'}
    db.delete_order.return_value = True
    db.update_order_quantity.return_value = True
    return db


# ---------------------------------------------------------------------------
# Connection-status
# ---------------------------------------------------------------------------

class TestConnectionStatus(unittest.TestCase):
    """GET /api/options/connection-status"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service.connection = None

    @patch('api.routes.options.get_options_service')
    @patch('core.connection.MoomooConnection')
    def test_returns_pool_stats_and_service_info(self, mock_moomoo_cls, mock_get_svc):
        """Should return connection pool stats and service connection info."""
        mock_get_svc.return_value = self.mock_service
        mock_moomoo_cls.get_connection_pool_stats.return_value = {
            'pool_size': 1, 'active_connections': 0
        }

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/connection-status')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['connection_pool']['pool_size'], 1)
        self.assertFalse(data['service_initialized'])

    @patch('api.routes.options.get_options_service')
    @patch('core.connection.MoomooConnection')
    def test_includes_conn_info_when_initialized(self, mock_moomoo_cls, mock_get_svc):
        """Should include connection info when service connection exists."""
        mock_conn = MagicMock()
        mock_conn.get_connection_info.return_value = {
            'host': '127.0.0.1', 'port': 11111, 'connected': True
        }
        self.mock_service.connection = mock_conn
        mock_get_svc.return_value = self.mock_service
        mock_moomoo_cls.get_connection_pool_stats.return_value = {}

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/connection-status')
            data = resp.get_json()

        self.assertTrue(data['success'])
        self.assertTrue(data['service_initialized'])
        self.assertEqual(data['service_connection']['host'], '127.0.0.1')

    @patch('api.routes.options.get_options_service')
    @patch('core.connection.MoomooConnection')
    def test_handles_exception_gracefully(self, mock_moomoo_cls, mock_get_svc):
        """Should return error response on exception."""
        mock_get_svc.side_effect = RuntimeError('boom')

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/connection-status')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 500)
        self.assertFalse(data['success'])


# ---------------------------------------------------------------------------
# OTM options
# ---------------------------------------------------------------------------

class TestOtmOptions(unittest.TestCase):
    """GET /api/options/otm"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service.get_otm_options.return_value = {
            'status': 'success', 'options': []
        }

    def _make_request(self, client, **params):
        return client.get('/api/options/otm', query_string=params)

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    def test_returns_options_successfully(self, mock_probe, mock_get_svc):
        """Should return OTM options for valid parameters."""
        mock_probe.return_value = {'status': 'connected'}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = self._make_request(client, tickers='AAPL', otm='10')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data['status'], 'success')
        self.mock_service.get_otm_options.assert_called_once_with(
            ticker='AAPL', otm_percentage=10.0,
            option_type=None, expiration=None
        )

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    def test_passes_option_type_and_expiration(self, mock_probe, mock_get_svc):
        """Should forward optional option_type and expiration parameters."""
        mock_probe.return_value = {'status': 'connected'}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = self._make_request(client, tickers='AAPL', otm='10',
                                       optionType='PUT', expiration='20240510')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.mock_service.get_otm_options.assert_called_once_with(
            ticker='AAPL', otm_percentage=10.0,
            option_type='PUT', expiration='20240510'
        )

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    def test_validates_invalid_option_type(self, mock_probe, mock_get_svc):
        """Should reject invalid option_type."""
        mock_probe.return_value = {'status': 'connected'}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = self._make_request(client, tickers='AAPL',
                                       optionType='INVALID')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(data['success'])

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    def test_returns_503_when_opend_unavailable(self, mock_probe, mock_get_svc):
        """Should return 503 when OpenD is unavailable."""
        mock_probe.return_value = {
            'status': 'unavailable',
            'message': 'OpenD is not responding.'
        }
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = self._make_request(client, tickers='AAPL')

        self.assertEqual(resp.status_code, 503)
        self.mock_service.get_otm_options.assert_not_called()


# ---------------------------------------------------------------------------
# Stock price
# ---------------------------------------------------------------------------

class TestStockPrice(unittest.TestCase):
    """GET /api/options/stock-price"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service.get_stock_price.side_effect = lambda t: {
            'AAPL': 150.0, 'TSLA': 200.0
        }.get(t, 0)

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    def test_single_ticker(self, mock_probe, mock_get_svc):
        """Should return price for a single ticker."""
        mock_probe.return_value = {'status': 'connected'}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/stock-price',
                              query_string={'tickers': 'AAPL'})
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['data']['AAPL'], 150.0)

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    def test_multiple_tickers(self, mock_probe, mock_get_svc):
        """Should return prices for comma-separated tickers."""
        mock_probe.return_value = {'status': 'connected'}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/stock-price',
                              query_string={'tickers': 'AAPL,TSLA'})
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data['data']['AAPL'], 150.0)
        self.assertEqual(data['data']['TSLA'], 200.0)

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    def test_missing_tickers_returns_400(self, mock_probe, mock_get_svc):
        """Should return 400 when no tickers provided."""
        mock_probe.return_value = {'status': 'connected'}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/stock-price',
                              query_string={})
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(data['success'])

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    def test_opend_unavailable_503(self, mock_probe, mock_get_svc):
        """Should return 503 when OpenD is unavailable."""
        mock_probe.return_value = {
            'status': 'unavailable',
            'message': 'OpenD is not responding.'
        }
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/stock-price',
                              query_string={'tickers': 'AAPL'})

        self.assertEqual(resp.status_code, 503)


# ---------------------------------------------------------------------------
# Save order
# ---------------------------------------------------------------------------

class TestSaveOrder(unittest.TestCase):
    """POST /api/options/order"""

    def setUp(self):
        _reset_service_global()
        self.mock_db = _make_mock_db()
        self.mock_service = _patch_service()
        self.mock_service.db = self.mock_db

    @patch('api.routes.options.get_options_service')
    def test_saves_valid_order(self, mock_get_svc):
        """Should save order and return 201 with order_id."""
        mock_get_svc.return_value = self.mock_service

        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            payload = {
                'ticker': 'AAPL', 'option_type': 'PUT',
                'strike': 150.0, 'expiration': '20240510',
                'action': 'SELL', 'quantity': 1,
            }
            resp = client.post('/api/options/order',
                               data=json.dumps(payload),
                               content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 201)
        self.assertTrue(data['success'])
        self.assertEqual(data['order_id'], 42)
        self.mock_db.save_order.assert_called_once_with(payload)

    @patch('api.routes.options.get_options_service')
    def test_rejects_missing_fields(self, mock_get_svc):
        """Should return 400 when required fields are missing."""
        mock_get_svc.return_value = self.mock_service

        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            resp = client.post('/api/options/order',
                               data=json.dumps({'ticker': 'AAPL'}),
                               content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(data['success'])

    @patch('api.routes.options.get_options_service')
    def test_rejects_empty_body(self, mock_get_svc):
        """Should return 400 when no JSON body sent."""
        mock_get_svc.return_value = self.mock_service

        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            resp = client.post('/api/options/order',
                               data=json.dumps({}),
                               content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# Pending orders
# ---------------------------------------------------------------------------

class TestPendingOrders(unittest.TestCase):
    """GET /api/options/pending-orders"""

    def setUp(self):
        _reset_service_global()
        self.mock_db = _make_mock_db()
        self.mock_service = _patch_service()
        self.mock_service.db = self.mock_db

    @patch('api.routes.options.get_options_service')
    def test_returns_orders(self, mock_get_svc):
        """Should return pending orders from database."""
        self.mock_db.get_pending_orders.return_value = [
            {'id': 1, 'ticker': 'AAPL', 'status': 'pending'}
        ]
        mock_get_svc.return_value = self.mock_service

        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            resp = client.get('/api/options/pending-orders')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(data['orders']), 1)

    @patch('api.routes.options.get_options_service')
    def test_forwards_executed_param(self, mock_get_svc):
        """Should forward executed query parameter."""
        mock_get_svc.return_value = self.mock_service

        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            client.get('/api/options/pending-orders',
                       query_string={'executed': 'true'})

        self.mock_db.get_pending_orders.assert_called_once_with(
            executed=True, isRollover=None
        )

    @patch('api.routes.options.get_options_service')
    def test_forwards_is_rollover_param(self, mock_get_svc):
        """Should forward isRollover query parameter."""
        mock_get_svc.return_value = self.mock_service

        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            client.get('/api/options/pending-orders',
                       query_string={'isRollover': 'true'})

        self.mock_db.get_pending_orders.assert_called_once_with(
            executed=False, isRollover=True
        )


# ---------------------------------------------------------------------------
# Delete order
# ---------------------------------------------------------------------------

class TestDeleteOrder(unittest.TestCase):
    """DELETE /api/options/order/<order_id>"""

    def setUp(self):
        _reset_service_global()
        self.mock_db = _make_mock_db()

    def test_deletes_existing_order(self):
        """Should delete existing order and return 200."""
        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            resp = client.delete('/api/options/order/1')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.mock_db.get_order.assert_called_once_with(1)
        self.mock_db.delete_order.assert_called_once_with(1)

    def test_returns_404_for_missing_order(self):
        """Should return 404 when order not found."""
        self.mock_db.get_order.return_value = None

        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            resp = client.delete('/api/options/order/999')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 404)
        self.assertIn('error', data)

    def test_returns_500_when_db_not_configured(self):
        """Should return 500 when database not initialized."""
        app = _make_app()  # no database in config
        with app.test_client() as client:
            resp = client.delete('/api/options/order/1')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 500)
        self.assertIn('error', data)


# ---------------------------------------------------------------------------
# Execute order
# ---------------------------------------------------------------------------

class TestExecuteOrder(unittest.TestCase):
    """POST /api/options/execute/<order_id>"""

    def setUp(self):
        _reset_service_global()
        self.mock_db = _make_mock_db()
        self.mock_service = _patch_service()
        self.mock_service.execute_order.return_value = (
            {'success': True, 'execution_id': 'EX-001'}, 200
        )

    @patch('api.routes.options.get_options_service')
    def test_executes_order_successfully(self, mock_get_svc):
        """Should execute order and return result."""
        mock_get_svc.return_value = self.mock_service

        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            resp = client.post('/api/options/execute/1')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.mock_service.execute_order.assert_called_once_with(1, self.mock_db)

    @patch('api.routes.options.get_options_service')
    def test_returns_500_when_db_not_configured(self, mock_get_svc):
        """Should return 500 when database not initialized."""
        mock_get_svc.return_value = self.mock_service

        app = _make_app()  # no database
        with app.test_client() as client:
            resp = client.post('/api/options/execute/1')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 500)


# ---------------------------------------------------------------------------
# Check orders
# ---------------------------------------------------------------------------

class TestCheckOrders(unittest.TestCase):
    """POST /api/options/check-orders"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service.check_pending_orders.return_value = {
            'checked': 3, 'updated': 1
        }

    @patch('api.routes.options.get_options_service')
    def test_checks_orders(self, mock_get_svc):
        """Should check pending orders and return result."""
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.post('/api/options/check-orders')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data['checked'], 3)
        self.mock_service.check_pending_orders.assert_called_once()

    @patch('api.routes.options.get_options_service')
    def test_handles_exception(self, mock_get_svc):
        """Should return 500 on exception."""
        mock_get_svc.side_effect = RuntimeError('check failed')

        app = _make_app()
        with app.test_client() as client:
            resp = client.post('/api/options/check-orders')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 500)
        self.assertIn('error', data)


# ---------------------------------------------------------------------------
# Rollover
# ---------------------------------------------------------------------------

class TestRollover(unittest.TestCase):
    """POST /api/options/rollover"""

    def setUp(self):
        _reset_service_global()
        self.mock_db = _make_mock_db()
        self.mock_service = _patch_service()
        self.mock_service.db = self.mock_db

    def _valid_payload(self):
        return {
            'ticker': 'AAPL',
            'current_option_type': 'PUT',
            'current_strike': 150.0,
            'current_expiration': '20240510',
            'new_strike': 145.0,
            'new_expiration': '20240610',
            'quantity': 1,
        }

    @patch('api.routes.options.get_options_service')
    def test_creates_buy_and_sell_orders(self, mock_get_svc):
        """Should create buy-to-close and sell-to-open orders."""
        mock_get_svc.return_value = self.mock_service
        self.mock_db.save_order.side_effect = [101, 102]  # buy_id, sell_id

        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            resp = client.post('/api/options/rollover',
                               data=json.dumps(self._valid_payload()),
                               content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 201)
        self.assertTrue(data['success'])
        self.assertEqual(data['buy_order_id'], 101)
        self.assertEqual(data['sell_order_id'], 102)
        self.assertEqual(self.mock_db.save_order.call_count, 2)

    @patch('api.routes.options.get_options_service')
    def test_rejects_missing_fields(self, mock_get_svc):
        """Should return 400 when required fields are missing."""
        mock_get_svc.return_value = self.mock_service

        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            resp = client.post('/api/options/rollover',
                               data=json.dumps({'ticker': 'AAPL'}),
                               content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertIn('error', data)

    @patch('api.routes.options.get_options_service')
    def test_handles_save_failure(self, mock_get_svc):
        """Should return 500 if save_order fails."""
        mock_get_svc.return_value = self.mock_service
        self.mock_db.save_order.return_value = None

        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            resp = client.post('/api/options/rollover',
                               data=json.dumps(self._valid_payload()),
                               content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 500)
        self.assertIn('error', data)


# ---------------------------------------------------------------------------
# Cancel order
# ---------------------------------------------------------------------------

class TestCancelOrder(unittest.TestCase):
    """POST /api/options/cancel/<order_id>"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service.cancel_order.return_value = (
            {'success': True, 'cancelled': True}, 200
        )

    @patch('api.routes.options.get_options_service')
    def test_cancels_order(self, mock_get_svc):
        """Should cancel order and return result."""
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.post('/api/options/cancel/1')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.mock_service.cancel_order.assert_called_once_with(1)

    @patch('api.routes.options.get_options_service')
    def test_handles_exception(self, mock_get_svc):
        """Should return 500 on exception."""
        mock_get_svc.side_effect = RuntimeError('cancel failed')

        app = _make_app()
        with app.test_client() as client:
            resp = client.post('/api/options/cancel/1')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 500)


# ---------------------------------------------------------------------------
# Update order quantity
# ---------------------------------------------------------------------------

class TestUpdateOrderQuantity(unittest.TestCase):
    """PUT /api/options/order/<order_id>/quantity"""

    def setUp(self):
        _reset_service_global()
        self.mock_db = _make_mock_db()

    def test_updates_quantity(self):
        """Should update quantity for a pending order."""
        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            resp = client.put('/api/options/order/1/quantity',
                              data=json.dumps({'quantity': 5}),
                              content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['quantity'], 5)
        self.mock_db.update_order_quantity.assert_called_once_with(1, 5)

    def test_rejects_negative_quantity(self):
        """Should return 400 for invalid quantity."""
        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            resp = client.put('/api/options/order/1/quantity',
                              data=json.dumps({'quantity': -1}),
                              content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertIn('error', data)

    def test_rejects_missing_quantity(self):
        """Should return 400 when quantity not provided."""
        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            resp = client.put('/api/options/order/1/quantity',
                              data=json.dumps({}),
                              content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)

    def test_rejects_updating_non_pending_order(self):
        """Should reject quantity update for non-pending order."""
        self.mock_db.get_order.return_value = {
            'id': 1, 'status': 'executed'
        }

        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            resp = client.put('/api/options/order/1/quantity',
                              data=json.dumps({'quantity': 5}),
                              content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertIn('error', data)

    def test_returns_404_for_missing_order(self):
        """Should return 404 when order not found."""
        self.mock_db.get_order.return_value = None

        app = _make_app(database=self.mock_db)
        with app.test_client() as client:
            resp = client.put('/api/options/order/999/quantity',
                              data=json.dumps({'quantity': 5}),
                              content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 404)
        self.assertIn('error', data)

    def test_returns_500_when_db_not_configured(self):
        """Should return 500 when database not initialized."""
        app = _make_app()  # no database
        with app.test_client() as client:
            resp = client.put('/api/options/order/1/quantity',
                              data=json.dumps({'quantity': 5}),
                              content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 500)


# ---------------------------------------------------------------------------
# Expirations
# ---------------------------------------------------------------------------

class TestExpirations(unittest.TestCase):
    """GET /api/options/expirations"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service.get_option_expirations.return_value = {
            'ticker': 'AAPL', 'expirations': ['20240510', '20240610']
        }

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    def test_returns_expirations(self, mock_probe, mock_get_svc):
        """Should return option expirations for a ticker."""
        mock_probe.return_value = {'status': 'connected'}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/expirations',
                              query_string={'ticker': 'AAPL'})
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertIn('expirations', data)
        self.assertEqual(len(data['expirations']), 2)
        self.mock_service.get_option_expirations.assert_called_once_with('AAPL', None)

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    def test_forwards_option_type(self, mock_probe, mock_get_svc):
        """Should forward option_type parameter."""
        mock_probe.return_value = {'status': 'connected'}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            client.get('/api/options/expirations',
                       query_string={'ticker': 'AAPL', 'option_type': 'CALL'})

        self.mock_service.get_option_expirations.assert_called_once_with('AAPL', 'CALL')

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    def test_rejects_missing_ticker(self, mock_probe, mock_get_svc):
        """Should return 400 when ticker not provided."""
        mock_probe.return_value = {'status': 'connected'}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/expirations')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertIn('error', data)

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    def test_rejects_invalid_option_type(self, mock_probe, mock_get_svc):
        """Should return 400 for invalid option_type."""
        mock_probe.return_value = {'status': 'connected'}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/expirations',
                              query_string={'ticker': 'AAPL',
                                            'option_type': 'INVALID'})
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    def test_returns_404_on_service_error(self, mock_probe, mock_get_svc):
        """Should return 404 when service reports error."""
        mock_probe.return_value = {'status': 'connected'}
        self.mock_service.get_option_expirations.return_value = {
            'error': 'No options found'
        }
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/expirations',
                              query_string={'ticker': 'INVALID'})
            data = resp.get_json()

        self.assertEqual(resp.status_code, 404)
        self.assertIn('error', data)

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    def test_opend_unavailable_503(self, mock_probe, mock_get_svc):
        """Should return 503 when OpenD is unavailable."""
        mock_probe.return_value = {
            'status': 'unavailable', 'message': 'OpenD down.'
        }
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/expirations',
                              query_string={'ticker': 'AAPL'})

        self.assertEqual(resp.status_code, 503)


# ---------------------------------------------------------------------------
# Top recommendations
# ---------------------------------------------------------------------------

class TestTopRecommendations(unittest.TestCase):
    """GET /api/options/top-recommendations — most complex endpoint"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service.get_top_recommendations.return_value = {
            'recommendations': [{'ticker': 'AAPL', 'score': 85}]
        }
        self.mock_service._get_portfolio_context.return_value = {
            'cash_balance': 10000, 'positions': {}
        }

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    @patch('api.routes.options.recommendation_cache')
    def test_returns_recommendations_cache_miss(self, mock_cache, mock_probe,
                                                  mock_get_svc):
        """Should fetch fresh recommendations on cache miss."""
        mock_probe.return_value = {'status': 'connected'}
        mock_cache.get.return_value = (None, {'cache_status': 'MISS'})
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/top-recommendations')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertIn('recommendations', data)
        self.assertEqual(data['_cache']['cache_status'], 'MISS')
        self.mock_service.get_top_recommendations.assert_called_once()

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    @patch('api.routes.options.recommendation_cache')
    def test_returns_cached_recommendations(self, mock_cache, mock_probe,
                                              mock_get_svc):
        """Should return cached recommendations on cache hit."""
        mock_probe.return_value = {'status': 'connected'}
        cached_data = {
            'recommendations': [{'ticker': 'AAPL', 'score': 85}],
        }
        mock_cache.get.return_value = (
            cached_data,
            {'cache_status': 'HIT', 'cache_age_seconds': 10,
             'portfolio_changed': False, 'is_valid': True,
             'background_refresh_failed': False, 'cached_at': '2026-01-01'}
        )
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/top-recommendations')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data['_cache']['cache_status'], 'HIT')
        # Should NOT call get_top_recommendations (cache hit)
        self.mock_service.get_top_recommendations.assert_not_called()

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    @patch('api.routes.options.recommendation_cache')
    def test_stale_cache_triggers_background_refresh(self, mock_cache, mock_probe,
                                                      mock_get_svc):
        """Should serve stale cache and trigger background refresh."""
        mock_probe.return_value = {'status': 'connected'}
        cached_data = {
            'recommendations': [{'ticker': 'AAPL', 'score': 85}],
        }
        mock_cache.get.return_value = (
            cached_data,
            {'cache_status': 'STALE', 'cache_age_seconds': 400,
             'portfolio_changed': False, 'is_valid': True,
             'background_refresh_failed': False, 'cached_at': '2026-01-01'}
        )
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/top-recommendations')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data['_cache']['cache_status'], 'STALE')
        # Background refresh runs in a daemon thread; the main response
        # comes from cache. Store the call count for reference.
        self.assertEqual(data['recommendations'][0]['ticker'], 'AAPL')

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    @patch('api.routes.options.recommendation_cache')
    def test_manual_refresh_bypasses_cache(self, mock_cache, mock_probe,
                                            mock_get_svc):
        """Should bypass cache when refresh=true."""
        mock_probe.return_value = {'status': 'connected'}
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/top-recommendations',
                              query_string={'refresh': 'true'})
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data['_cache']['cache_status'], 'MISS')
        # Should call get_top_recommendations despite cache
        self.mock_service.get_top_recommendations.assert_called_once()

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    @patch('api.routes.options.recommendation_cache')
    def test_fallback_to_stale_on_failure(self, mock_cache, mock_probe,
                                           mock_get_svc):
        """Should return stale cache as fallback when fresh fetch fails.

        Flow: cache miss → fresh fetch fails with error → fallback to stale.
        """
        mock_probe.return_value = {'status': 'connected'}
        self.mock_service.get_top_recommendations.return_value = {
            'error': 'API timeout'
        }
        stale_data = {
            'recommendations': [{'ticker': 'AAPL', 'score': 80}],
        }
        # First call (from the main cache check) returns MISS
        # Second call (from the error fallback handler) returns stale data
        mock_cache.get.side_effect = [
            (None, {'cache_status': 'MISS', 'cache_age_seconds': 0,
                     'portfolio_changed': False, 'is_valid': True,
                     'background_refresh_failed': False, 'cached_at': ''}),
            (stale_data, {'cache_status': 'STALE', 'cache_age_seconds': 500,
                           'portfolio_changed': False, 'is_valid': True,
                           'background_refresh_failed': False,
                           'cached_at': '2026-01-01'}),
        ]
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/top-recommendations')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data['_cache']['cache_status'], 'STALE_FALLBACK')
        # The stale data should be served despite API failure
        self.assertEqual(data['recommendations'][0]['ticker'], 'AAPL')

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    @patch('api.routes.options.recommendation_cache')
    def test_enforces_limit_range(self, mock_cache, mock_probe, mock_get_svc):
        """Should clamp limit between 1 and 10."""
        mock_probe.return_value = {'status': 'connected'}
        mock_cache.get.return_value = (None, {'cache_status': 'MISS'})
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            client.get('/api/options/top-recommendations',
                       query_string={'limit': '100'})

        # Should clamp to 10
        self.mock_service.get_top_recommendations.assert_called_once_with(limit=10)

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    @patch('api.routes.options.recommendation_cache')
    def test_opend_unavailable_503(self, mock_cache, mock_probe, mock_get_svc):
        """Should return 503 when OpenD is unavailable."""
        mock_probe.return_value = {
            'status': 'unavailable', 'message': 'OpenD down.'
        }
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/top-recommendations')

        self.assertEqual(resp.status_code, 503)


# ---------------------------------------------------------------------------
# Cash status
# ---------------------------------------------------------------------------

class TestCashStatus(unittest.TestCase):
    """GET /api/options/cash-status"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service.config = {'cash_reserve_enabled': True}

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    @patch('api.services.portfolio_service.PortfolioService')
    def test_returns_cash_status(self, mock_portfolio_cls, mock_probe,
                                  mock_get_svc):
        """Should return cash balance and reserve info."""
        mock_probe.return_value = {'status': 'connected'}
        mock_get_svc.return_value = self.mock_service

        mock_portfolio = MagicMock()
        mock_portfolio.get_portfolio_summary.return_value = {
            'cash_balance': 50000
        }
        mock_portfolio.get_positions.return_value = []
        mock_portfolio_cls.return_value = mock_portfolio

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/cash-status')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['cash_balance'], 50000.0)
        self.assertEqual(data['cash_reserved'], 0.0)
        self.assertEqual(data['cash_available'], 50000.0)

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    @patch('api.services.portfolio_service.PortfolioService')
    def test_calculates_cash_reserved_for_short_puts(
            self, mock_portfolio_cls, mock_probe, mock_get_svc):
        """Should calculate cash reserved for short put positions."""
        mock_probe.return_value = {'status': 'connected'}
        mock_get_svc.return_value = self.mock_service

        mock_portfolio = MagicMock()
        mock_portfolio.get_portfolio_summary.return_value = {
            'cash_balance': 50000
        }
        mock_portfolio.get_positions.return_value = [
            {
                'symbol': 'US.AAPL',
                'position': -2,
                'option_type': 'PUT',
                'strike': 150.0,
                'expiration': '20240510',
            }
        ]
        mock_portfolio_cls.return_value = mock_portfolio

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/cash-status')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        # cash_reserved = 2 contracts * 150 strike * 100 = 30000
        self.assertEqual(data['cash_reserved'], 30000.0)
        self.assertEqual(data['cash_available'], 20000.0)
        self.assertEqual(len(data['open_puts']), 1)

    @patch('api.routes.options.get_options_service')
    @patch('api.routes.options.probe_opend_status')
    def test_opend_unavailable_503(self, mock_probe, mock_get_svc):
        """Should return 503 when OpenD is unavailable."""
        mock_probe.return_value = {
            'status': 'unavailable', 'message': 'OpenD down.'
        }
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/cash-status')

        self.assertEqual(resp.status_code, 503)


# ---------------------------------------------------------------------------
# VIX regime
# ---------------------------------------------------------------------------

class TestVixRegime(unittest.TestCase):
    """GET /api/options/vix-regime"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service._get_vix_regime.return_value = {
            'vix': 15.0, 'regime': 'low'
        }

    @patch('api.routes.options.get_options_service')
    def test_returns_vix_regime(self, mock_get_svc):
        """Should return VIX regime data."""
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/vix-regime')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['vix_regime']['vix'], 15.0)
        self.assertEqual(data['vix_regime']['regime'], 'low')


# ---------------------------------------------------------------------------
# Analytics: lifecycle
# ---------------------------------------------------------------------------

class TestTradeLifecycle(unittest.TestCase):
    """GET /api/options/analytics/lifecycle"""

    @patch('db.database.OptionsDatabase')
    @patch('api.services.config.get_config')
    def test_returns_lifecycle_events(self, mock_get_config, mock_db_cls):
        """Should return trade events and analytics."""
        mock_db = MagicMock()
        mock_db.get_trade_events.return_value = [
            {'id': 1, 'ticker': 'AAPL', 'event_type': 'entry'}
        ]
        mock_db.get_trade_analytics.return_value = {
            'win_rate': 0.6, 'total_exits': 10
        }
        mock_db_cls.return_value = mock_db
        mock_get_config.return_value = {'db_path': ':memory:'}

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/analytics/lifecycle')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(len(data['events']), 1)
        self.assertIn('analytics', data)

    @patch('db.database.OptionsDatabase')
    @patch('api.services.config.get_config')
    def test_forwards_query_params(self, mock_get_config, mock_db_cls):
        """Should forward ticker, event_type, limit params."""
        mock_db = MagicMock()
        mock_db.get_trade_events.return_value = []
        mock_db.get_trade_analytics.return_value = {}
        mock_db_cls.return_value = mock_db
        mock_get_config.return_value = {'db_path': ':memory:'}

        app = _make_app()
        with app.test_client() as client:
            client.get('/api/options/analytics/lifecycle',
                       query_string={
                           'ticker': 'AAPL',
                           'event_type': 'roll',
                           'limit': '50'
                       })

        mock_db.get_trade_events.assert_called_once_with(
            ticker='AAPL', event_type='roll', limit=50
        )


# ---------------------------------------------------------------------------
# Analytics: leakage
# ---------------------------------------------------------------------------

class TestLeakageAnalytics(unittest.TestCase):
    """GET /api/options/analytics/leakage"""

    @patch('db.database.OptionsDatabase')
    @patch('api.services.config.get_config')
    def test_returns_leakage_metrics(self, mock_get_config, mock_db_cls):
        """Should return leakage analytics."""
        mock_db = MagicMock()
        mock_db.get_trade_analytics.return_value = {
            'win_rate': 0.65,
            'avg_leakage': 0.02,
            'total_exits': 20,
            'wins': 13,
            'roll_count': 5,
            'per_symbol': [
                {'ticker': 'AAPL', 'leakage': 0.01}
            ]
        }
        mock_db_cls.return_value = mock_db
        mock_get_config.return_value = {'db_path': ':memory:'}

        app = _make_app()
        with app.test_client() as client:
            resp = client.get('/api/options/analytics/leakage')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['analytics']['win_rate'], 0.65)
        self.assertEqual(data['analytics']['total_exits'], 20)
        self.assertEqual(len(data['analytics']['per_symbol']), 1)


# ---------------------------------------------------------------------------
# Prefilled close order
# ---------------------------------------------------------------------------

class TestPrefilledClose(unittest.TestCase):
    """POST /api/options/prefilled-close"""

    def setUp(self):
        _reset_service_global()
        self.mock_service = _patch_service()
        self.mock_service._ensure_connection.return_value = MagicMock()

    def _valid_payload(self):
        return {
            'ticker': 'AAPL',
            'option_type': 'PUT',
            'strike': 150.0,
            'expiration': '20240510',
            'quantity': 1,
        }

    @patch('api.routes.options.get_options_service')
    @patch('api.services.options_service.OptionsService')
    def test_creates_prefilled_close(self, mock_options_cls, mock_get_svc):
        """Should return a prefilled close order quote."""
        mock_get_svc.return_value = self.mock_service
        mock_conn = MagicMock()
        mock_conn.get_option_chain.return_value = {
            'options': [{'strike': 150.0, 'bid': 0.45, 'ask': 0.55}]
        }
        self.mock_service._ensure_connection.return_value = mock_conn

        app = _make_app()
        with app.test_client() as client:
            resp = client.post('/api/options/prefilled-close',
                               data=json.dumps(self._valid_payload()),
                               content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['quote']['action'], 'BUY')
        # mid_price = (0.45 + 0.55) / 2 = 0.50
        self.assertEqual(data['quote']['limit_price'], 0.50)

    @patch('api.routes.options.get_options_service')
    @patch('api.services.options_service.OptionsService')
    def test_rejects_missing_fields(self, mock_options_cls, mock_get_svc):
        """Should return 400 when required fields are missing."""
        mock_get_svc.return_value = self.mock_service

        app = _make_app()
        with app.test_client() as client:
            resp = client.post('/api/options/prefilled-close',
                               data=json.dumps({'ticker': 'AAPL'}),
                               content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(data['success'])

    @patch('api.routes.options.get_options_service')
    @patch('api.services.options_service.OptionsService')
    def test_returns_503_when_connection_fails(self, mock_options_cls,
                                                mock_get_svc):
        """Should return 503 when connection to moomoo fails."""
        mock_get_svc.return_value = self.mock_service
        self.mock_service._ensure_connection.return_value = None

        app = _make_app()
        with app.test_client() as client:
            resp = client.post('/api/options/prefilled-close',
                               data=json.dumps(self._valid_payload()),
                               content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 503)
        self.assertFalse(data['success'])

    @patch('api.routes.options.get_options_service')
    @patch('api.services.options_service.OptionsService')
    def test_uses_provided_limit_price(self, mock_options_cls, mock_get_svc):
        """Should use provided limit_price instead of mid-price."""
        mock_get_svc.return_value = self.mock_service
        mock_conn = MagicMock()
        mock_conn.get_option_chain.return_value = {
            'options': [{'strike': 150.0, 'bid': 0.45, 'ask': 0.55}]
        }
        self.mock_service._ensure_connection.return_value = mock_conn

        payload = self._valid_payload()
        payload['limit_price'] = 0.75

        app = _make_app()
        with app.test_client() as client:
            resp = client.post('/api/options/prefilled-close',
                               data=json.dumps(payload),
                               content_type='application/json')
            data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        # Should use the provided limit_price (0.75), not calculated mid (0.50)
        self.assertEqual(data['quote']['limit_price'], 0.75)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    unittest.main()
