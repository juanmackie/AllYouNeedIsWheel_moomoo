"""
Rate limiting for Moomoo API calls.
"""

import logging
import threading
import time

logger = logging.getLogger("autotrader.rate_limiter")


class RateLimiter:
    """
    Enforces rate limits for API requests with burst detection.
    """

    _min_request_spacing = 0.1
    _burst_cooldown_seconds = 5.0

    def __init__(self, max_requests_per_window=8, rate_limit_window=30, burst_threshold=5, burst_window=5):
        """
        Initialize rate limiter.

        Args:
            max_requests_per_window: Maximum requests allowed per time window (default 8)
            rate_limit_window: Time window in seconds (default 30)
            burst_threshold: Number of requests in burst_window that triggers burst protection
            burst_window: Time window for burst detection in seconds
        """
        self.max_requests_per_window = max_requests_per_window
        self.rate_limit_window = rate_limit_window
        self.burst_threshold = burst_threshold
        self.burst_window = burst_window

        self._request_timestamps = []
        self._lock = threading.Lock()
        self._rate_limit_waits = 0
        self._api_calls_count = 0

    def check_rate_limit(self):
        """
        Check and enforce rate limiting for API requests.
        Waits if necessary to stay within moomoo's rate limits.
        Includes burst detection to prevent rapid-fire requests.
        """
        wait_time = 0.0
        wait_reason = None

        with self._lock:
            now = time.time()
            self._request_timestamps = [ts for ts in self._request_timestamps if now - ts < self.rate_limit_window]

            scheduled_time = now

            recent_requests = [ts for ts in self._request_timestamps if now - ts < self.burst_window]
            if len(recent_requests) >= self.burst_threshold:
                scheduled_time = max(scheduled_time, now + self._burst_cooldown_seconds)
                wait_reason = (
                    f"Burst detected ({len(recent_requests)} requests in {self.burst_window}s). "
                    f"Adding {self._burst_cooldown_seconds}s cooldown..."
                )

            if len(self._request_timestamps) >= self.max_requests_per_window:
                oldest_request = min(self._request_timestamps)
                rate_limit_time = oldest_request + self.rate_limit_window + 0.5
                if rate_limit_time > scheduled_time:
                    scheduled_time = rate_limit_time
                    wait_reason = (
                        f"Rate limit reached ({len(self._request_timestamps)}/{self.max_requests_per_window}). "
                        f"Waiting for the window to clear..."
                    )

            if self._request_timestamps:
                min_next_time = max(self._request_timestamps) + self._min_request_spacing
                if min_next_time > scheduled_time:
                    scheduled_time = min_next_time
                    wait_reason = (
                        f"Spacing requests by at least {self._min_request_spacing:.1f}s to avoid API collisions..."
                    )

            self._request_timestamps.append(scheduled_time)
            self._api_calls_count += 1
            wait_time = scheduled_time - now
            if wait_time > 0:
                self._rate_limit_waits += 1

        if wait_time > 0:
            if wait_reason:
                logger.warning(f"{wait_reason} Waiting {wait_time:.1f}s...")
            time.sleep(wait_time)

    def get_stats(self):
        """Return rate limiting statistics."""
        with self._lock:
            return {
                "api_calls_count": self._api_calls_count,
                "rate_limit_waits": self._rate_limit_waits,
                "current_queue_length": len(self._request_timestamps),
                "max_requests_per_window": self.max_requests_per_window,
                "rate_limit_window": self.rate_limit_window,
            }
