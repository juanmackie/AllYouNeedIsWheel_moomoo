"""
Tests for rate_limiter module.
"""

import time
import threading
import unittest
from unittest.mock import patch
from core.rate_limiter import RateLimiter


class FakeClock:
    def __init__(self, start=0.0):
        self.now = start
        self._lock = threading.Lock()

    def time(self):
        with self._lock:
            return self.now

    def sleep(self, seconds):
        with self._lock:
            self.now += seconds


class TestRateLimiter(unittest.TestCase):
    def test_basic_rate_limit(self):
        """Test that rate limiter enforces max requests per window."""
        clock = FakeClock()
        with patch('core.rate_limiter.time.time', side_effect=clock.time), \
             patch('core.rate_limiter.time.sleep', side_effect=clock.sleep):
            limiter = RateLimiter(max_requests_per_window=2, rate_limit_window=10)
            # First two requests should pass immediately.
            limiter.check_rate_limit()
            limiter.check_rate_limit()
            # Third request should reserve a future slot without blocking the test.
            limiter.check_rate_limit()
            stats = limiter.get_stats()

        self.assertEqual(stats['api_calls_count'], 3)
        self.assertGreater(stats['rate_limit_waits'], 0)

    def test_burst_detection(self):
        """Test burst detection triggers extra cooldown."""
        clock = FakeClock()
        with patch('core.rate_limiter.time.time', side_effect=clock.time), \
             patch('core.rate_limiter.time.sleep', side_effect=clock.sleep):
            limiter = RateLimiter(max_requests_per_window=10, rate_limit_window=30,
                                  burst_threshold=2, burst_window=5)
            # Simulate burst: make two requests within burst_window.
            limiter.check_rate_limit()
            clock.sleep(0.1)
            limiter.check_rate_limit()
            # Third request should trigger burst detection.
            limiter.check_rate_limit()
            stats = limiter.get_stats()

        self.assertGreater(stats['rate_limit_waits'], 0)

    def test_thread_safety(self):
        """Test that rate limiter is thread-safe."""
        clock = FakeClock()
        with patch('core.rate_limiter.time.time', side_effect=clock.time), \
             patch('core.rate_limiter.time.sleep', side_effect=clock.sleep):
            limiter = RateLimiter(max_requests_per_window=5, rate_limit_window=10)
            errors = []

            def worker():
                try:
                    for _ in range(10):
                        limiter.check_rate_limit()
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            stats = limiter.get_stats()

        self.assertEqual(len(errors), 0, f"Errors occurred: {errors}")
        self.assertEqual(stats['api_calls_count'], 50)

    def test_get_stats(self):
        """Test stats return correct structure."""
        limiter = RateLimiter()
        stats = limiter.get_stats()
        self.assertIn('api_calls_count', stats)
        self.assertIn('rate_limit_waits', stats)
        self.assertIn('current_queue_length', stats)
        self.assertIn('max_requests_per_window', stats)
        self.assertIn('rate_limit_window', stats)


if __name__ == '__main__':
    unittest.main()
