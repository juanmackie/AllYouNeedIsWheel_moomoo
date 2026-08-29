"""
Rate limiting for Moomoo API calls.
"""

import logging
import threading
import time

logger = logging.getLogger("ayniwheel.rate_limiter")


class RateLimiter:
    """
    Enforces rate limits for API requests with burst detection.
    """

    _min_request_spacing = 0.1
    _burst_cooldown_seconds = 5.0

    def __init__(
        self,
        max_requests_per_window=8,
        rate_limit_window=30,
        burst_threshold=5,
        burst_window=5,
        min_request_spacing=None,
        min_effective_requests=2,
    ):
        """
        Initialize rate limiter.

        Args:
            max_requests_per_window: Maximum requests allowed per time window (default 8)
            rate_limit_window: Time window in seconds (default 30)
            burst_threshold: Number of requests in burst_window that triggers burst protection
            burst_window: Time window for burst detection in seconds
        """
        self.rate_limit_window = max(0.1, float(rate_limit_window))
        self.burst_threshold = max(1, int(burst_threshold))
        self.burst_window = max(0.1, float(burst_window))
        self._configured_max_requests = max(1, int(max_requests_per_window))
        self._configured_min_spacing = max(
            0.0, float(self._min_request_spacing if min_request_spacing is None else min_request_spacing)
        )
        self._min_effective_requests = max(1, int(min_effective_requests))
        self.max_requests_per_window = self._configured_max_requests
        self._min_request_spacing = self._configured_min_spacing

        self._request_timestamps = []
        self._lock = threading.Lock()
        self._rate_limit_waits = 0
        self._api_calls_count = 0
        self._rate_limit_events = 0
        self._last_rate_limit_at = None

    def configure(
        self,
        max_requests_per_window=None,
        rate_limit_window=None,
        min_request_spacing=None,
        burst_threshold=None,
        burst_window=None,
    ):
        """Apply a new base quota and discard any temporary adaptation."""
        with self._lock:
            if max_requests_per_window is not None:
                self._configured_max_requests = max(1, int(max_requests_per_window))
            if rate_limit_window is not None:
                self.rate_limit_window = max(0.1, float(rate_limit_window))
            if burst_threshold is not None:
                self.burst_threshold = max(1, int(burst_threshold))
            if burst_window is not None:
                self.burst_window = max(0.1, float(burst_window))
            if min_request_spacing is not None:
                self._configured_min_spacing = max(0.0, float(min_request_spacing))
            self.max_requests_per_window = self._configured_max_requests
            self._min_request_spacing = self._configured_min_spacing
            self._last_rate_limit_at = None

    def _restore_after_clean_window(self, now):
        if self._last_rate_limit_at is not None and now - self._last_rate_limit_at >= self.rate_limit_window:
            self.max_requests_per_window = self._configured_max_requests
            self._min_request_spacing = self._configured_min_spacing
            self._last_rate_limit_at = None
            logger.info("Rate limiter restored to configured quota after a clean window")

    def record_rate_limit(self, reason="provider rate limit"):
        """Reduce the effective quota after a provider rate-limit response."""
        with self._lock:
            floor = min(self._min_effective_requests, self._configured_max_requests)
            reduced = max(floor, self.max_requests_per_window // 2)
            self.max_requests_per_window = reduced
            if reduced:
                self._min_request_spacing = max(self._configured_min_spacing, self.rate_limit_window / reduced)
            self._last_rate_limit_at = time.time()
            self._rate_limit_events += 1
            logger.warning(
                "%s; adapting quota to %d requests/%gs (spacing %.2fs)",
                reason,
                self.max_requests_per_window,
                self.rate_limit_window,
                self._min_request_spacing,
            )

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
            self._restore_after_clean_window(now)
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
                "configured_max_requests_per_window": self._configured_max_requests,
                "configured_min_request_spacing": self._configured_min_spacing,
                "min_request_spacing": self._min_request_spacing,
                "rate_limit_events": self._rate_limit_events,
                "adapted": self._last_rate_limit_at is not None,
            }
