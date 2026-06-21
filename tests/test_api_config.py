import os
import unittest
from unittest.mock import patch

from api import create_app, _resolve_secret_key, _supports_credentialed_cors


class TestApiConfig(unittest.TestCase):
    def test_create_app_uses_non_static_secret_key(self):
        with patch.dict(os.environ, {}, clear=True), patch('api._register_services'):
            app = create_app({'TESTING': True})

        self.assertNotEqual(app.config['SECRET_KEY'], 'dev')
        self.assertTrue(app.config['SECRET_KEY'])

    def test_resolve_secret_key_preserves_explicit_secret(self):
        with patch.dict(os.environ, {'SECRET_KEY': 'explicit-secret'}, clear=True):
            self.assertEqual(_resolve_secret_key(), 'explicit-secret')

    def test_credentialed_cors_is_limited_to_trusted_local_origins(self):
        self.assertTrue(
            _supports_credentialed_cors(['http://localhost:8000', 'http://127.0.0.1:3000'])
        )
        self.assertFalse(_supports_credentialed_cors(['https://example.com']))


if __name__ == '__main__':
    unittest.main()
