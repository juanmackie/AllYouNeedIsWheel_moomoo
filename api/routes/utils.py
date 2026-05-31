"""
Route utility helpers — standardized API response envelopes.

All route endpoints should use these helpers to produce consistent
response shapes across the application.

Error envelope:   {'success': False, 'error': 'message'}
Success envelope: {'success': True,  ...merged(data)...}
"""

import os
import logging
import sqlite3
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from flask import jsonify

from api.services.utils import validate_ticker, clean_yfinance_ticker

_logger = logging.getLogger('api.routes.utils')

_SANITIZE_ERRORS = os.environ.get('SANITIZE_ERRORS', 'true').lower() in ('1', 'true', 'yes')
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_INIT_LOCK = threading.Lock()
_RATE_LIMIT_STORE_PATH = Path(__file__).resolve().parents[2] / '_tmp' / 'route_rate_limits.db'
_RATE_LIMIT_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _clear_rate_limit_store() -> None:
    try:
        with sqlite3.connect(str(_RATE_LIMIT_STORE_PATH), timeout=5) as conn:
            conn.execute('DELETE FROM route_rate_limits')
            conn.commit()
    except sqlite3.Error:
        pass


def _ensure_rate_limit_store() -> None:
    with _RATE_LIMIT_INIT_LOCK:
        with sqlite3.connect(str(_RATE_LIMIT_STORE_PATH), timeout=5) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA busy_timeout=3000')
            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS route_rate_limits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    route_name TEXT NOT NULL,
                    client_key TEXT NOT NULL,
                    ts REAL NOT NULL
                )
                '''
            )
            conn.execute(
                '''
                CREATE INDEX IF NOT EXISTS idx_route_rate_limits_lookup
                ON route_rate_limits(route_name, client_key, ts)
                '''
            )
            conn.commit()


class _RateLimitBucketCache(defaultdict):
    def __init__(self):
        super().__init__(deque)

    def clear(self):
        super().clear()
        _clear_rate_limit_store()


_RATE_LIMIT_BUCKETS: dict[str, deque[float]] = _RateLimitBucketCache()


def normalize_ticker_list(tickers_param: str) -> tuple[list[str], list[str]]:
    """Parse, clean, and validate a comma-separated ticker list.

    Returns a tuple of ``(valid_tickers, invalid_tickers)``. Valid tickers are
    normalized for downstream yfinance/moomoo use while invalid tickers are
    returned in their cleaned form for error reporting.
    """
    valid_tickers: list[str] = []
    invalid_tickers: list[str] = []

    if not tickers_param:
        return valid_tickers, invalid_tickers

    seen = set()
    for raw_ticker in tickers_param.split(','):
        ticker = clean_yfinance_ticker(raw_ticker.strip())
        if not ticker:
            continue
        if not validate_ticker(raw_ticker.strip()):
            invalid_tickers.append(ticker)
            continue
        if ticker not in seen:
            seen.add(ticker)
            valid_tickers.append(ticker)

    return valid_tickers, invalid_tickers


def enforce_route_rate_limit(route_name: str, client_key: str, max_requests: int = 60, window_seconds: int = 60) -> tuple[bool, int]:
    """Return ``(allowed, retry_after_seconds)`` for a route/client bucket."""
    now = time.time()
    key = f"{route_name}:{client_key}"
    with _RATE_LIMIT_LOCK:
        try:
            _ensure_rate_limit_store()
            cutoff = now - window_seconds
            with sqlite3.connect(str(_RATE_LIMIT_STORE_PATH), timeout=5, isolation_level=None) as conn:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('PRAGMA busy_timeout=3000')
                conn.execute('BEGIN IMMEDIATE')
                conn.execute('DELETE FROM route_rate_limits WHERE ts < ?', (cutoff,))

                row = conn.execute(
                    '''
                    SELECT COUNT(*), MIN(ts)
                    FROM route_rate_limits
                    WHERE route_name = ? AND client_key = ?
                    ''',
                    (route_name, client_key),
                ).fetchone()
                count = int(row[0] or 0) if row else 0
                oldest_ts = float(row[1]) if row and row[1] is not None else None

                if count >= max_requests and oldest_ts is not None:
                    retry_after = max(1, int(window_seconds - (now - oldest_ts)))
                    conn.rollback()
                    return False, retry_after

                conn.execute(
                    'INSERT INTO route_rate_limits (route_name, client_key, ts) VALUES (?, ?, ?)',
                    (route_name, client_key, now),
                )
                conn.commit()
                return True, 0
        except sqlite3.Error as exc:
            _logger.debug("Persistent rate limit store unavailable, using in-memory fallback: %s", exc)
            bucket = _RATE_LIMIT_BUCKETS[key]
            while bucket and now - bucket[0] > window_seconds:
                bucket.popleft()
            if len(bucket) >= max_requests:
                retry_after = max(1, int(window_seconds - (now - bucket[0])))
                return False, retry_after
            bucket.append(now)
            return True, 0


def error_response(message, status_code=500, **extra):
    """Return a standardized error JSON response.

    In production (SANITIZE_ERRORS=true), internal error details are replaced
    with a generic message to avoid leaking implementation details to clients.
    The full error is always logged server-side.

    Args:
        message: Human-readable error message string.
        status_code: HTTP status code (default 500).
        **extra: Additional key-value pairs merged into the response body.

    Returns:
        Flask response tuple (Response, status_code).
    """
    if _SANITIZE_ERRORS and status_code >= 500:
        _logger.warning(f"Sanitized internal error (original: {message})")
        body = {'success': False, 'error': 'Internal server error'}
    else:
        body = {'success': False, 'error': message}
    if extra:
        body.update(extra)
    return jsonify(body), status_code


def success_response(data=None, status_code=200):
    """Return a standardized success JSON response.

    Args:
        data: Optional dict to merge into the response body.
              Scalars are wrapped as {'data': value}.
        status_code: HTTP status code (default 200).

    Returns:
        Flask response tuple (Response, status_code).
    """
    body = {'success': True}
    if data is not None:
        if isinstance(data, dict):
            body.update(data)
        else:
            body['data'] = data
    return jsonify(body), status_code


def opend_unavailable_response(probe_result):
    """Return a standardized OpenD unavailable error response.

    Builds a 503 response with a recognizable contract so the frontend
    can display broker-unavailable states consistently across all routes.

    Args:
        probe_result: Dict returned by probe_opend_status(). Must contain
                      at least 'status' and 'message' keys.

    Returns:
        Flask response tuple (Response, 503).
    """
    status = probe_result.get('status', 'unavailable')
    message = probe_result.get('message', 'OpenD is unavailable.')
    error_code = (
        'opend_login_required' if status == 'login_required' else 'opend_unavailable'
    )
    return error_response(
        message,
        status_code=503,
        error_code=error_code,
        opend_status=probe_result,
    )
