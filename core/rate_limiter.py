"""
Rate limiting for Moomoo API calls.
"""

import threading
import time
import logging

logger = logging.getLogger('autotrader.rate_limiter')


class RateLimiter:
    """
    Enforces rate limits for API requests with burst detection.
    """
    
    def __init__(self, max_requests_per_window=8, rate_limit_window=30,
                 burst_threshold=5, burst_window=5):
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
        with self._lock:
            now = time.time()
            
            # Remove timestamps older than the window
            self._request_timestamps = [
                ts for ts in self._request_timestamps 
                if now - ts < self.rate_limit_window
            ]
            
            # Check for burst: too many requests in short time
            recent_requests = [ts for ts in self._request_timestamps if now - ts < self.burst_window]
            if len(recent_requests) >= self.burst_threshold:
                # Burst detected, add extra cooldown
                burst_wait = 5.0  # 5 second cooldown for burst
                logger.warning(f"Burst detected ({len(recent_requests)} requests in {self.burst_window}s). Adding {burst_wait}s cooldown...")
                time.sleep(burst_wait)
                self._rate_limit_waits += 1
                
                # Recalculate after cooldown
                now = time.time()
                self._request_timestamps = [
                    ts for ts in self._request_timestamps 
                    if now - ts < self.rate_limit_window
                ]
            
            # If we've hit the limit, wait until we can make another request
            if len(self._request_timestamps) >= self.max_requests_per_window:
                # Calculate how long to wait
                oldest_request = min(self._request_timestamps)
                wait_time = self.rate_limit_window - (now - oldest_request) + 0.5  # larger buffer
                
                if wait_time > 0:
                    logger.warning(f"Rate limit reached ({len(self._request_timestamps)}/{self.max_requests_per_window}). Waiting {wait_time:.1f}s...")
                    time.sleep(wait_time)
                    self._rate_limit_waits += 1
                    
                    # Recalculate after waiting
                    now = time.time()
                    self._request_timestamps = [
                        ts for ts in self._request_timestamps 
                        if now - ts < self.rate_limit_window
                    ]
            
            # Add current request timestamp and increment counter
            self._request_timestamps.append(now)
            self._api_calls_count += 1
            
    def get_stats(self):
        """Return rate limiting statistics."""
        with self._lock:
            return {
                'api_calls_count': self._api_calls_count,
                'rate_limit_waits': self._rate_limit_waits,
                'current_queue_length': len(self._request_timestamps),
                'max_requests_per_window': self.max_requests_per_window,
                'rate_limit_window': self.rate_limit_window,
            }
