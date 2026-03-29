"""
Recommendation Cache Manager
Provides intelligent caching for top recommendations with portfolio change detection.
"""

import threading
import time
import hashlib
import json
from typing import Optional, Dict, Any, Tuple
from datetime import datetime
import logging

logger = logging.getLogger('autotrader.cache')


class CacheEntry:
    """Represents a cached recommendation result"""
    def __init__(self, data: Any, portfolio_hash: str, timestamp: float, all_scored_options: list = None):
        self.data = data
        self.portfolio_hash = portfolio_hash
        self.timestamp = timestamp
        self.all_scored_options = all_scored_options or []
        self.is_valid = True
        self.background_refresh_failed = False


class RecommendationCache:
    """
    Thread-safe cache for top recommendations with portfolio change detection.
    
    Features:
    - 5-minute TTL for cached data
    - Portfolio hash comparison for immediate invalidation on any position change
    - Stale data serving with background refresh trigger
    - Invalidation on background refresh failure
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern - only one cache instance"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, ttl_seconds: int = 300):
        """
        Initialize the cache
        
        Args:
            ttl_seconds: Time to live in seconds (default: 300 = 5 minutes)
        """
        if self._initialized:
            return
            
        self._ttl = ttl_seconds
        self._cache: Dict[str, CacheEntry] = {}
        self._cache_lock = threading.RLock()
        self._initialized = True
        
        logger.info(f"RecommendationCache initialized with TTL={ttl_seconds}s")
    
    @staticmethod
    def calculate_portfolio_hash(portfolio_context: Dict) -> str:
        """
        Calculate a hash of the portfolio state for change detection.
        ANY change to positions or cash invalidates the cache.
        
        Args:
            portfolio_context: Dict with 'positions' and 'cash_balance'
            
        Returns:
            MD5 hash string (16 chars)
        """
        positions = portfolio_context.get('positions', {})
        cash_balance = portfolio_context.get('cash_balance', 0)
        
        # Create deterministic string of all positions
        # Format: symbol:quantity:avg_cost|symbol:quantity:avg_cost...
        position_items = []
        for symbol, pos in sorted(positions.items()):
            qty = float(pos.get('position', 0) or 0)
            avg_cost = float(pos.get('avg_cost', 0) or 0)
            position_items.append(f"{symbol}:{qty}:{avg_cost}")
        
        # Include short positions (covered calls/puts)
        short_calls = portfolio_context.get('short_calls', {})
        short_puts = portfolio_context.get('short_puts', {})
        
        for symbol, contracts in sorted(short_calls.items()):
            position_items.append(f"SHORT_CALL:{symbol}:{contracts}")
        
        for symbol, contracts in sorted(short_puts.items()):
            position_items.append(f"SHORT_PUT:{symbol}:{contracts}")
        
        portfolio_str = "|".join(position_items)
        portfolio_str += f"|CASH:{cash_balance}"
        
        # Generate hash
        hash_obj = hashlib.md5(portfolio_str.encode())
        return hash_obj.hexdigest()[:16]
    
    def get(self, key: str, current_portfolio_hash: str) -> Tuple[Optional[Any], Dict[str, Any]]:
        """
        Get cached data with intelligent staleness detection.
        
        Args:
            key: Cache key (typically endpoint + params)
            current_portfolio_hash: Hash of current portfolio state
            
        Returns:
            Tuple of (data, metadata) where metadata contains:
                - cache_status: 'HIT', 'STALE', 'MISS', or 'INVALIDATED'
                - cache_age_seconds: How old the cache is
                - portfolio_changed: Whether portfolio changed since cache
                - is_valid: Whether cached data is still valid
                - background_refresh_failed: Whether last background refresh failed
        """
        with self._cache_lock:
            if key not in self._cache:
                return None, {
                    'cache_status': 'MISS',
                    'cache_age_seconds': 0,
                    'portfolio_changed': False,
                    'is_valid': True,
                    'background_refresh_failed': False
                }
            
            entry = self._cache[key]
            now = time.time()
            age = now - entry.timestamp
            
            # Check if portfolio changed (any single share change invalidates)
            portfolio_changed = entry.portfolio_hash != current_portfolio_hash
            
            # Check if cache is stale (exceeded TTL)
            is_stale = age > self._ttl
            
            # Check if background refresh previously failed
            refresh_failed = entry.background_refresh_failed
            
            # Determine cache status
            if refresh_failed:
                # Mark as invalid if background refresh failed
                entry.is_valid = False
                cache_status = 'INVALIDATED'
            elif portfolio_changed:
                # Portfolio changed - invalidate immediately
                entry.is_valid = False
                cache_status = 'INVALIDATED'
            elif is_stale:
                # Stale but still valid for serving while refreshing
                cache_status = 'STALE'
            else:
                # Fresh cache hit
                cache_status = 'HIT'
            
            # Return data if valid, otherwise None
            data = entry.data if entry.is_valid else None
            
            metadata = {
                'cache_status': cache_status,
                'cache_age_seconds': int(age),
                'portfolio_changed': portfolio_changed,
                'is_valid': entry.is_valid,
                'background_refresh_failed': refresh_failed,
                'cached_at': datetime.fromtimestamp(entry.timestamp).isoformat()
            }
            
            logger.debug(f"Cache {cache_status} for key={key[:50]}... age={int(age)}s portfolio_changed={portfolio_changed}")
            
            return data, metadata
    
    def set(self, key: str, data: Any, portfolio_hash: str, all_scored_options: list = None):
        """
        Store data in cache.
        
        Args:
            key: Cache key
            data: Data to cache
            portfolio_hash: Hash of current portfolio state
            all_scored_options: Optional list of all scored options (not just top N)
        """
        with self._cache_lock:
            self._cache[key] = CacheEntry(
                data=data,
                portfolio_hash=portfolio_hash,
                timestamp=time.time(),
                all_scored_options=all_scored_options
            )
            logger.info(f"Cache SET for key={key[:50]}... portfolio_hash={portfolio_hash}")
    
    def mark_background_refresh_failed(self, key: str):
        """Mark cache entry as having failed background refresh"""
        with self._cache_lock:
            if key in self._cache:
                self._cache[key].background_refresh_failed = True
                self._cache[key].is_valid = False
                logger.warning(f"Cache INVALIDATED due to background refresh failure: {key[:50]}...")
    
    def invalidate(self, key: str):
        """Explicitly invalidate a cache entry"""
        with self._cache_lock:
            if key in self._cache:
                self._cache[key].is_valid = False
                logger.info(f"Cache INVALIDATED for key={key[:50]}...")
    
    def clear(self):
        """Clear all cache entries"""
        with self._cache_lock:
            self._cache.clear()
            logger.info("Cache CLEARED")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self._cache_lock:
            total_entries = len(self._cache)
            valid_entries = sum(1 for e in self._cache.values() if e.is_valid)
            stale_entries = sum(1 for e in self._cache.values() 
                              if e.is_valid and (time.time() - e.timestamp) > self._ttl)
            
            return {
                'total_entries': total_entries,
                'valid_entries': valid_entries,
                'stale_entries': stale_entries,
                'invalidated_entries': total_entries - valid_entries,
                'ttl_seconds': self._ttl
            }


# Global cache instance
recommendation_cache = RecommendationCache()
