"""
Tests for rate_limiter module.
"""

import time
import threading
import unittest
from core.rate_limiter import RateLimiter


class TestRateLimiter(unittest.TestCase):
    def test_basic_rate_limit(self):
        """Test that rate limiter enforces max requests per window."""
        limiter = RateLimiter(max_requests_per_window=2, rate_limit_window=10)
        start = time.time()
        # First two requests should pass immediately
        limiter.check_rate_limit()
        limiter.check_rate_limit()
        # Third request should wait (but we won't actually wait in test)
        # We'll just ensure no exception is raised (the method will sleep)
        # Since we don't want to block test, we'll just check that the call returns
        # (it will sleep for up to 10 seconds, but we'll mock time.sleep?)
        # For simplicity, we'll just test that the method doesn't raise.
        limiter.check_rate_limit()
        # Ensure counters are updated
        stats = limiter.get_stats()
        self.assertEqual(stats['api_calls_count'], 3)
        self.assertGreater(stats['rate_limit_waits'], 0)

    def test_burst_detection(self):
        """Test burst detection triggers extra cooldown."""
        limiter = RateLimiter(max_requests_per_window=10, rate_limit_window=30,
                              burst_threshold=2, burst_window=5)
        # Simulate burst: make two requests within burst_window
        limiter.check_rate_limit()
        time.sleep(0.1)
        limiter.check_rate_limit()
        # Third request should trigger burst detection (since we have 2 recent requests)
        # but we need to ensure we are within burst_window (5 seconds). We'll just call it.
        limiter.check_rate_limit()
        stats = limiter.get_stats()
        self.assertGreater(stats['rate_limit_waits'], 0)

    def test_thread_safety(self):
        """Test that rate limiter is thread-safe."""
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
        self.assertEqual(len(errors), 0, f"Errors occurred: {errors}")
        stats = limiter.get_stats()
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
