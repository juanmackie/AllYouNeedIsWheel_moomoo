import logging
import os
import queue
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger('db.sqlite_pool')

_POOLS = {}
_POOL_HANDLES = {}
_POOLS_LOCK = threading.Lock()
_DEFAULT_POOL_SIZE = max(1, int(os.environ.get('SQLITE_POOL_SIZE', '4')))


class SQLiteConnectionPool:
    def __init__(self, db_path, maxsize=_DEFAULT_POOL_SIZE):
        self.db_path = str(Path(db_path).resolve())
        self.maxsize = maxsize
        self._available = queue.LifoQueue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._created = 0
        self._borrowed = 0

    def _create_connection(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        conn.execute("PRAGMA foreign_keys=ON")
        self._created += 1
        logger.debug("Created pooled SQLite connection for %s", self.db_path)
        return conn

    def acquire(self, row_factory=None):
        try:
            conn = self._available.get_nowait()
            logger.debug("Reusing pooled SQLite connection for %s", self.db_path)
        except queue.Empty:
            conn = self._create_connection()
        with self._lock:
            self._borrowed += 1
        if row_factory is not None:
            conn.row_factory = row_factory
        return conn

    def release(self, conn):
        if conn is None:
            return

        try:
            if conn.in_transaction:
                conn.rollback()
        except Exception:
            pass

        try:
            conn.row_factory = None
        except Exception:
            pass

        with self._lock:
            self._borrowed = max(0, self._borrowed - 1)

        try:
            self._available.put_nowait(conn)
        except queue.Full:
            try:
                conn.close()
            except Exception:
                pass

    def close_all(self):
        closed = 0
        while True:
            try:
                conn = self._available.get_nowait()
            except queue.Empty:
                break
            try:
                conn.close()
                closed += 1
            except Exception:
                pass
        logger.debug("Closed %d pooled SQLite connections for %s", closed, self.db_path)
        return closed

    def stats(self):
        with self._lock:
            return {
                'db_path': self.db_path,
                'pool_size': self._available.qsize(),
                'created': self._created,
                'borrowed': self._borrowed,
                'maxsize': self.maxsize,
                'handle_count': _POOL_HANDLES.get(self.db_path, 0),
            }


def get_sqlite_pool(db_path):
    resolved = str(Path(db_path).resolve())
    with _POOLS_LOCK:
        pool = _POOLS.get(resolved)
        if pool is None:
            pool = SQLiteConnectionPool(resolved)
            _POOLS[resolved] = pool
        return pool


def register_pool_handle(db_path):
    resolved = str(Path(db_path).resolve())
    with _POOLS_LOCK:
        _POOL_HANDLES[resolved] = _POOL_HANDLES.get(resolved, 0) + 1
        return _POOL_HANDLES[resolved]


def release_pool_handle(db_path):
    resolved = str(Path(db_path).resolve())
    with _POOLS_LOCK:
        current = _POOL_HANDLES.get(resolved, 0)
        if current <= 1:
            _POOL_HANDLES.pop(resolved, None)
            pool = _POOLS.pop(resolved, None)
        else:
            _POOL_HANDLES[resolved] = current - 1
            pool = None
    if pool is not None:
        pool.close_all()
    return current - 1 if current > 0 else 0


@contextmanager
def pooled_connection(db_path, row_factory=None):
    pool = get_sqlite_pool(db_path)
    conn = pool.acquire(row_factory=row_factory)
    try:
        yield conn
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        pool.release(conn)


def close_connection_pool(db_path):
    resolved = str(Path(db_path).resolve())
    with _POOLS_LOCK:
        pool = _POOLS.pop(resolved, None)
        _POOL_HANDLES.pop(resolved, None)
    if pool is not None:
        pool.close_all()


def get_connection_pool_stats(db_path):
    resolved = str(Path(db_path).resolve())
    with _POOLS_LOCK:
        pool = _POOLS.get(resolved)
        handle_count = _POOL_HANDLES.get(resolved, 0)
        return pool.stats() if pool is not None else {
            'db_path': resolved,
            'pool_size': 0,
            'created': 0,
            'borrowed': 0,
            'maxsize': _DEFAULT_POOL_SIZE,
            'handle_count': handle_count,
        }
