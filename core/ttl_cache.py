"""
Small TTL cache helper with an optional cachetools backend.

The app uses a few in-memory caches with identical TTL semantics. This module
keeps the public shape tiny while letting us swap to `cachetools.TTLCache`
whenever the dependency is available.
"""

from __future__ import annotations

from collections import OrderedDict
import time
from typing import Generic, Iterator, MutableMapping, Optional, TypeVar

K = TypeVar("K")
V = TypeVar("V")
_MISSING = object()

try:
    from cachetools import TTLCache as _CacheToolsTTLCache  # type: ignore
except ImportError:  # pragma: no cover - exercised implicitly when dependency is absent
    _CacheToolsTTLCache = None


class _FallbackTTLCache(MutableMapping[K, V], Generic[K, V]):
    """Very small TTL mapping used only when cachetools is unavailable."""

    def __init__(self, maxsize: int = 1024, ttl: int = 300):
        self.maxsize = maxsize
        self.ttl = ttl
        self._data: OrderedDict[K, tuple[V, float]] = OrderedDict()

    def _purge_expired(self) -> None:
        if not self._data:
            return
        now = time.time()
        expired = [key for key, (_, timestamp) in self._data.items() if now - timestamp >= self.ttl]
        for key in expired:
            self._data.pop(key, None)

    def _evict_if_needed(self) -> None:
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def __getitem__(self, key: K) -> V:
        self._purge_expired()
        value, _ = self._data[key]
        self._data.move_to_end(key)
        return value

    def __setitem__(self, key: K, value: V) -> None:
        self._purge_expired()
        self._data[key] = (value, time.time())
        self._data.move_to_end(key)
        self._evict_if_needed()

    def __delitem__(self, key: K) -> None:
        del self._data[key]

    def __iter__(self) -> Iterator[K]:
        self._purge_expired()
        return iter(self._data)

    def __len__(self) -> int:
        self._purge_expired()
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        self._purge_expired()
        return key in self._data

    def get(self, key: K, default: Optional[V] = None) -> Optional[V]:
        try:
            return self[key]
        except KeyError:
            return default

    def pop(self, key: K, default=_MISSING):  # type: ignore[override]
        self._purge_expired()
        if default is _MISSING:
            return self._data.pop(key)[0]
        value = self._data.pop(key, None)
        return default if value is None else value[0]

    def clear(self) -> None:
        self._data.clear()


def make_ttl_cache(maxsize: int = 1024, ttl: int = 300):
    """Return a TTL mapping with the best backend available."""
    if _CacheToolsTTLCache is not None:
        return _CacheToolsTTLCache(maxsize=maxsize, ttl=ttl)
    return _FallbackTTLCache(maxsize=maxsize, ttl=ttl)
