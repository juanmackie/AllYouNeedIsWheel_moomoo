"""
In-process scheduler for earnings updater and warm cache scan.

Schedule:
  - Earnings updater: every 6 hours
  - Warm cache scan: every 30 minutes (override via WARM_CACHE_INTERVAL_MINUTES)

One-click start: python run_api.py
"""

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from core.ticker_utils import earnings_underlying_ticker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lock helpers — single-owner for multi-worker safety
# ---------------------------------------------------------------------------

_LOCK_FILE = None
_LOCK_FILE_PATH: Optional[Path] = None
_DEFAULT_LOCK_STALE_TIMEOUT = int(os.environ.get('SCHEDULER_LOCK_STALE_TIMEOUT_SECONDS', '1800'))


def _lock_path() -> Path:
    global _LOCK_FILE_PATH
    if _LOCK_FILE_PATH is None:
        _LOCK_FILE_PATH = Path.home() / ".wheel" / "scheduler.lock"
    _LOCK_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _LOCK_FILE_PATH


def _acquire_lock() -> bool:
    global _LOCK_FILE
    lock_path = _lock_path()
    try:
        _LOCK_FILE = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        pid = os.getpid()
        os.write(_LOCK_FILE, str(pid).encode())
        logger.info("Scheduler lock acquired (pid=%s)", pid)
        return True
    except FileExistsError:
        try:
            mtime = lock_path.stat().st_mtime
            age = (datetime.now() - datetime.fromtimestamp(mtime)).total_seconds()
            if age > _DEFAULT_LOCK_STALE_TIMEOUT:
                logger.warning("Removing stale scheduler lock (age=%.0fs)", age)
                lock_path.unlink(missing_ok=True)
                return _acquire_lock()
        except OSError:
            pass
        logger.debug("Scheduler lock held by another process")
        return False
    except Exception as e:
        logger.warning("Failed to acquire scheduler lock: %s", e)
        return False


def _release_lock() -> None:
    global _LOCK_FILE
    if _LOCK_FILE is not None:
        try:
            os.close(_LOCK_FILE)
        except OSError:
            pass
        _LOCK_FILE = None
    lock_path = _lock_path()
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Earnings updater helpers
# ---------------------------------------------------------------------------


def _resolve_earnings_db(db=None):
    if db is not None:
        return db

    try:
        from db.database import OptionsDatabase
        return OptionsDatabase()
    except Exception as e:
        logger.error("Cannot resolve earnings database: %s", e)
        return None


def _collect_earnings_update_tickers(earnings_ticker_provider=None):
    tickers = set()

    try:
        if earnings_ticker_provider is None:
            return []

        for ticker in earnings_ticker_provider() or []:
            normalized = earnings_underlying_ticker(str(ticker).strip())
            if normalized:
                tickers.add(normalized)
    except Exception as e:
        logger.debug("Could not collect earnings update tickers: %s", e)

    return sorted(tickers)


def _run_earnings_updater_job(db=None, earnings_ticker_provider=None, earnings_service_provider=None) -> None:
    resolved_db = _resolve_earnings_db(db)
    if resolved_db is None:
        logger.error("Earnings updater database not available")
        return

    try:
        if earnings_service_provider is None:
            logger.debug("Earnings updater skipped: no service provider configured")
            return

        service = earnings_service_provider(resolved_db)
        tickers = _collect_earnings_update_tickers(earnings_ticker_provider)
        if tickers:
            logger.info("Scheduled earnings updater: refreshing %d tickers", len(tickers))
            result = service.batch_update_earnings(tickers)
            logger.info("Scheduled earnings updater complete: %s", result)
        else:
            logger.info("Scheduled earnings updater: no tickers found")
        service.purge_old_data()
    except Exception as e:
        logger.error("Scheduled earnings updater failed: %s", e)


def _earnings_database_needs_initialization(db) -> bool:
    if db is None:
        return False

    db_path = getattr(db, 'db_path', None)
    if not db_path:
        return False

    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute('SELECT COUNT(*) FROM earnings_calendar').fetchone()
            return not row or row[0] == 0
    except Exception as e:
        logger.warning("Could not inspect earnings database state: %s", e)
        return True


def _start_earnings_initializer(db) -> None:
    if not _earnings_database_needs_initialization(db):
        return

    thread = threading.Thread(
        target=_run_earnings_updater_job,
        kwargs={'db': db},
        daemon=True,
        name='earnings_initializer',
    )
    thread.start()
    logger.info("Started one-time earnings initialization in background")


# ---------------------------------------------------------------------------
# Warm cache scan — periodic pre-fetch so the dashboard loads instantly
# ---------------------------------------------------------------------------

_WARM_CACHE_JOB_ID = "warm_cache_scan"
_WARM_CACHE_INTERVAL_MINUTES = int(os.environ.get('WARM_CACHE_INTERVAL_MINUTES', '30'))


def _run_warm_cache_job(warm_cache_service_provider=None) -> None:
    try:
        if warm_cache_service_provider is None:
            logger.debug("Warm cache scan skipped: no service provider configured")
            return

        service = warm_cache_service_provider()
        logger.info("Warm cache scan: fetching top recommendations...")
        t0 = time.time()
        result = service.get_top_recommendations(limit=5)
        elapsed = time.time() - t0
        signal_count = len(result.get('signals', []) if isinstance(result, dict) else [])
        error = result.get('error') if isinstance(result, dict) else None
        if error:
            logger.warning("Warm cache scan completed with error (%.1fs): %s", elapsed, error)
        else:
            logger.info("Warm cache scan completed (%.1fs, %d signals cached)", elapsed, signal_count)
    except Exception as e:
        logger.warning("Warm cache scan failed: %s", e)


def get_scheduler_info():
    """Return info about whether the in-process scheduler is running."""
    global _scheduler
    running = _scheduler is not None and getattr(_scheduler, 'running', False)
    return {
        'running': running,
        'state': {},
    }


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

_scheduler: Optional[BackgroundScheduler] = None
_scheduler_lock = threading.Lock()
_scheduler_started = False


def start_scheduler(db=None, app=None, earnings_ticker_provider=None, earnings_service_provider=None, warm_cache_service_provider=None) -> bool:
    """Start the APScheduler background scheduler with jobs.

    Args:
        db: Optional OptionsDatabase instance used by the earnings updater.
        app: Optional Flask app (unused, kept for backwards compatibility).

    Returns True if this process became the scheduler owner, False otherwise.
    Only one process per host will actually register the schedule.
    """
    global _scheduler, _scheduler_started

    if _scheduler_started:
        return True

    if not _acquire_lock():
        return False

    with _scheduler_lock:
        if _scheduler_started:
            return True

        _scheduler = BackgroundScheduler(
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 900,
            }
        )

        _scheduler.add_job(
            _run_earnings_updater_job,
            trigger=IntervalTrigger(hours=6),
            id="earnings_updater_6h",
            name="Earnings updater (every 6h)",
            kwargs={"db": db, "earnings_ticker_provider": earnings_ticker_provider, "earnings_service_provider": earnings_service_provider},
            replace_existing=True,
        )

        _scheduler.add_job(
            _run_warm_cache_job,
            trigger=IntervalTrigger(minutes=_WARM_CACHE_INTERVAL_MINUTES),
            id=_WARM_CACHE_JOB_ID,
            name=f"Warm cache scan (every {_WARM_CACHE_INTERVAL_MINUTES}m)",
            kwargs={"warm_cache_service_provider": warm_cache_service_provider},
            replace_existing=True,
        )

        _scheduler.start()
        _scheduler_started = True
        logger.info("Scheduler started: earnings updater every 6h, warm cache scan every %dm", _WARM_CACHE_INTERVAL_MINUTES)

        if db is not None:
            _start_earnings_initializer(db)

        return True


def stop_scheduler() -> None:
    global _scheduler, _scheduler_started
    with _scheduler_lock:
        if _scheduler:
            _scheduler.shutdown(wait=False)
        _scheduler_started = False
        _release_lock()
        logger.info("Scheduler stopped")


def run_with_retry(fn, max_retries=3, retry_delay=300, name="job"):
    """
    Execute a function with retry logic.

    If the function raises an exception, it will be retried up to max_retries
    times with retry_delay seconds between attempts.
    """
    for attempt in range(1, max_retries + 1):
        try:
            result = fn()
            return result
        except Exception as e:
            logger.warning(f"{name} failed (attempt {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                logger.info(f"Retrying {name} in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                logger.error(f"{name} failed after {max_retries} attempts")
                raise
