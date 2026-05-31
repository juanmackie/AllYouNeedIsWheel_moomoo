"""
Tests for api/services/openbb_service.py — optional enrichment wrapper.
"""

import unittest
from unittest.mock import patch
from datetime import datetime


class TestOpenBBService(unittest.TestCase):
    """Optional enrichment service initialization and caching."""

    def setUp(self):
        from api.services.openbb_service import OpenBBService
        self.service = OpenBBService()

    def test_initial_state(self):
        self.assertIsNone(self.service._obb)
        self.assertFalse(self.service._initialized)
        self.assertEqual(self.service._cache, {})

    def test_ensure_initialized_returns_false_when_not_installed(self):
        with patch('api.services.openbb_service.OpenBBService._ensure_initialized', return_value=False):
            result = self.service._ensure_initialized()
            self.assertFalse(result)

    def test_ensure_initialized_returns_true(self):
        with patch('api.services.openbb_service.OpenBBService._ensure_initialized', return_value=True):
            result = self.service._ensure_initialized()
            self.assertTrue(result)

    def test_cache_miss_returns_none(self):
        result = self.service._get_cache('nonexistent', 300)
        self.assertIsNone(result)

    def test_cache_hit_returns_data(self):
        self.service._cache['test_key'] = {'data': {'value': 42}, '_timestamp': datetime.now().timestamp()}
        result = self.service._get_cache('test_key', 300)
        self.assertEqual(result, {'value': 42})

    def test_cache_expired_returns_none(self):
        from datetime import timedelta
        self.service._cache['old_key'] = {
            'data': {'value': 42},
            '_timestamp': (datetime.now() - timedelta(seconds=600)).timestamp()
        }
        result = self.service._get_cache('old_key', 300)
        self.assertIsNone(result)

    def test_set_cache_stores_data(self):
        self.service._set_cache('new_key', {'value': 99})
        self.assertIn('new_key', self.service._cache)
        self.assertEqual(self.service._cache['new_key']['data'], {'value': 99})

    def test_safe_fetch_returns_cached(self):
        self.service._cache['cached'] = {'data': {'cached': True}, '_timestamp': datetime.now().timestamp()}
        result = self.service._safe_fetch('cached', 300, lambda: {'fresh': True})
        self.assertEqual(result, {'cached': True})

    def test_safe_fetch_calls_func_on_miss(self):
        result = self.service._safe_fetch('fresh', 300, lambda: {'fresh': True})
        self.assertEqual(result, {'fresh': True})
        self.assertIn('fresh', self.service._cache)

    def test_safe_fetch_handles_exception(self):
        def broken():
            raise ValueError('fail')

        result = self.service._safe_fetch('broken', 300, broken)
        self.assertIsNone(result)

    def test_unusual_options_returns_none_when_not_initialized(self):
        with patch.object(self.service, '_ensure_initialized', return_value=False):
            result = self.service.get_unusual_options('AAPL')
            self.assertIsNone(result)

    def test_vix_returns_none_when_not_initialized(self):
        with patch.object(self.service, '_ensure_initialized', return_value=False):
            result = self.service.get_vix()
            self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
