"""
Validation-focused tests for the Pydantic-backed API routes.
"""

import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

from api.routes.pop import bp as pop_bp
from api.routes.risk import bp as risk_bp
from api.routes.signals import bp as signals_bp


def _make_app():
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(risk_bp)
    app.register_blueprint(pop_bp)
    app.register_blueprint(signals_bp)
    return app


class TestRiskRouteValidation(unittest.TestCase):
    def test_get_sizing_normalizes_query_params(self):
        service = MagicMock()
        service.calculate_position_size.return_value = {'max_contracts': 2}

        with patch('api.routes.risk.get_risk_sizing_service', return_value=service):
            app = _make_app()
            with app.test_client() as client:
                resp = client.get(
                    '/api/risk/sizing',
                    query_string={
                        'ticker': ' aapl ',
                        'account_value': '50000',
                        'risk_pct': '0.02',
                        'atr_period': '21',
                    },
                )

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload['success'])
        service.calculate_position_size.assert_called_once_with(
            ticker='AAPL',
            account_value=50000.0,
            risk_pct=0.02,
            atr_period=21,
        )

    def test_get_sizing_rejects_missing_ticker(self):
        service = MagicMock()
        with patch('api.routes.risk.get_risk_sizing_service', return_value=service):
            app = _make_app()
            with app.test_client() as client:
                resp = client.get('/api/risk/sizing')

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()['success'])
        service.calculate_position_size.assert_not_called()

    def test_batch_sizing_normalizes_ticker_list(self):
        service = MagicMock()
        service.calculate_position_size.side_effect = lambda **kwargs: {'ticker': kwargs['ticker']}

        with patch('api.routes.risk.get_risk_sizing_service', return_value=service):
            app = _make_app()
            with app.test_client() as client:
                resp = client.post(
                    '/api/risk/sizing/batch',
                    json={
                        'tickers': [' aapl ', 'msft'],
                        'account_value': 100000,
                        'risk_pct': 0.015,
                    },
                )

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertEqual(payload['data']['AAPL']['ticker'], 'AAPL')
        self.assertEqual(payload['data']['MSFT']['ticker'], 'MSFT')


class TestPopRouteValidation(unittest.TestCase):
    def test_estimate_pop_normalizes_query_params(self):
        mock_get_pop = MagicMock(return_value={'pop': 0.82})

        with patch('api.routes.pop.get_pop', mock_get_pop):
            app = _make_app()
            with app.test_client() as client:
                resp = client.get(
                    '/api/pop/estimate',
                    query_string={
                        'ticker': ' aapl ',
                        'strike': '170',
                        'expiration': ' 20260530 ',
                        'type': 'put',
                        'delta': '-0.18',
                        'iv': '0.25',
                        'dte': '21',
                        'method': 'DELTA',
                    },
                )

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload['success'])
        mock_get_pop.assert_called_once_with('AAPL', 170.0, '20260530', 'PUT', -0.18, 0.25, 21, 'delta')

    def test_estimate_pop_rejects_missing_strike(self):
        with patch('api.routes.pop.get_pop') as mock_get_pop:
            app = _make_app()
            with app.test_client() as client:
                resp = client.get(
                    '/api/pop/estimate',
                    query_string={
                        'ticker': 'AAPL',
                        'expiration': '20260530',
                        'type': 'PUT',
                    },
                )

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()['success'])
        mock_get_pop.assert_not_called()


class TestSignalOverlayRouteValidation(unittest.TestCase):
    def test_overlay_route_normalizes_query_params(self):
        service = MagicMock()
        service.get_overlays.return_value = {
            'generated_at': '2026-06-04T12:00:00',
            'count': 1,
            'source_available': True,
            'overlays': {'AAPL': {'ticker': 'AAPL', 'verdict': 'confirming'}},
            'errors': [],
            'invalid_tickers': [],
            'elapsed_seconds': 0.1,
        }

        with patch('api.get_service', return_value=service):
            app = _make_app()
            with app.test_client() as client:
                resp = client.get(
                    '/api/signals/overlay',
                    query_string={'ticker': ' aapl ', 'refresh': 'true'},
                )

        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload['success'])
        service.get_overlays.assert_called_once_with(['AAPL'], refresh=True)

    def test_overlay_route_rejects_missing_ticker(self):
        with patch('api.get_service') as mock_get_service:
            app = _make_app()
            with app.test_client() as client:
                resp = client.get('/api/signals/overlay')

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()['success'])
        mock_get_service.assert_not_called()


if __name__ == '__main__':
    unittest.main()
