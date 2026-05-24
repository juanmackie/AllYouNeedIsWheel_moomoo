"""
In-process scheduler for evaluator and calibrator runs.

Uses app's OptionsDatabase for state persistence instead of independent SQLite.
State is stored in 'evaluator_scheduler_state' table.

Schedule:
  - Evaluator: daily at 8:00 AM Australia/Brisbane time
  - Calibrator: weekly on Sunday at 8:15 AM Australia/Brisbane time

One-click start: python run_api.py
"""

import json
import logging
import os
import socket
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lock helpers — single-owner for multi-worker safety
# ---------------------------------------------------------------------------

_LOCK_FILE = None
_LOCK_FILE_PATH: Optional[Path] = None


def _lock_path() -> Path:
    global _LOCK_FILE_PATH
    if _LOCK_FILE_PATH is None:
        _LOCK_FILE_PATH = Path.home() / ".wheel" / "evaluator" / "scheduler.lock"
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
            if age > 300:
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
# Global evaluator_repo accessor — set once by start_scheduler
# ---------------------------------------------------------------------------

_evaluator_repo = None


def _resolve_earnings_db(db=None):
    if db is not None:
        return db

    try:
        from flask import current_app
        app_db = current_app.config.get('database')
        if app_db is not None:
            return app_db
    except Exception:
        logger.warning("Could not resolve database from Flask app config", exc_info=True)

    try:
        from db.database import OptionsDatabase
        from api.services.config import get_config
        cfg = get_config()
        db_path = cfg.get('db_path', 'options.db') if cfg else 'options.db'
        return OptionsDatabase(db_path)
    except Exception as e:
        logger.error("Cannot resolve earnings database: %s", e)
        return None


def _collect_earnings_update_tickers():
    tickers = set()

    try:
        from api import get_service
        portfolio_service = get_service('portfolio')
        positions = portfolio_service.get_positions() or []
        for position in positions:
            symbol = str(position.get('symbol', '') or '').strip()
            if symbol:
                tickers.add(symbol.replace('US.', '').upper())
    except Exception as e:
        logger.debug("Could not collect portfolio tickers for earnings update: %s", e)

    try:
        from api.services.config import get_config
        from api.services.watchlist_manager import WatchlistManager

        cfg = get_config()
        watchlist_manager = WatchlistManager(cfg)
        growth_mode = cfg.get('growth_mode', {}) if cfg else {}
        for ticker in watchlist_manager.get_effective_watchlist(growth_mode_config=growth_mode) or []:
            clean_ticker = str(ticker).strip().upper()
            if clean_ticker:
                tickers.add(clean_ticker)
    except Exception as e:
        logger.debug("Could not collect watchlist tickers for earnings update: %s", e)

    return sorted(tickers)


def _run_earnings_updater_job(db=None) -> None:
    resolved_db = _resolve_earnings_db(db)
    if resolved_db is None:
        logger.error("Earnings updater database not available")
        return

    from api.services.iv_earnings_service import IVEarningsService

    try:
        service = IVEarningsService(resolved_db)
        tickers = _collect_earnings_update_tickers()
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


def get_scheduler_info():
    """Return info about the scheduler and both job states.

    Checks in-process APScheduler state; falls back to app DB for run history.
    """
    global _scheduler, _evaluator_repo
    running = _scheduler is not None and getattr(_scheduler, 'running', False)
    state_map = {}
    if _evaluator_repo is not None:
        try:
            states = _evaluator_repo.get_all_scheduler_states()
            for s in states:
                state_map[s['name']] = {
                    'last_run': s.get('last_run', ''),
                    'last_status': s.get('last_status', ''),
                    'last_message': s.get('last_message', ''),
                }
        except Exception:
            logger.warning("Could not fetch scheduler states", exc_info=True)
    return {
        'running': running,
        'state': state_map,
    }


# ---------------------------------------------------------------------------
# Job functions
# ---------------------------------------------------------------------------


def _resolve_evaluator_repo(db=None):
    """Resolve evaluator_repo if not already set (fallback for background jobs)."""
    global _evaluator_repo
    if _evaluator_repo is not None:
        return _evaluator_repo

    if db is not None:
        try:
            _evaluator_repo = db.evaluator
            return _evaluator_repo
        except Exception as e:
            logger.error("Cannot resolve evaluator repo from provided database: %s", e)

    try:
        from flask import current_app
        db = current_app.config.get('database')
        if db:
            _evaluator_repo = db.evaluator
            return _evaluator_repo
    except Exception:
        logger.warning("Could not resolve evaluator repo from Flask app config", exc_info=True)
    try:
        from db.database import OptionsDatabase
        from api.services.config import get_config
        cfg = get_config() if get_config else {}
        db_path = cfg.get('db_path', 'options.db')
        _evaluator_repo = OptionsDatabase(db_path).evaluator
        return _evaluator_repo
    except Exception as e:
        logger.error("Cannot resolve evaluator repo: %s", e)
        return None


def _run_evaluator_job(db=None) -> None:
    repo = _resolve_evaluator_repo(db)
    if repo is None:
        logger.error("Evaluator repo not available for scheduled evaluator job")
        return

    from api.services.config import get_config

    try:
        cfg = get_config() if get_config else {}
        ev_cfg = cfg.get('evaluator', {})

        if not ev_cfg.get('enabled', True):
            logger.info("Scheduled evaluator: disabled via config")
            return

        portfolio_service = None
        try:
            import api
            portfolio_service = api.get_service('portfolio')
        except Exception:
            logger.warning("Portfolio service not available for evaluator")

        result = _resolve_outcomes(repo, portfolio_service, ev_cfg)
        resolved = result.get('resolved', 0)
        errors = result.get('errors', 0)

        repo.set_scheduler_state(
            "evaluator",
            "ok" if errors == 0 else "partial",
            f"Checked {result.get('checked', 0)}, resolved {resolved}, errors {errors}",
        )
        logger.info("Scheduled evaluator: %s", result)
    except Exception as e:
        logger.error("Scheduled evaluator failed: %s", e)


def _resolve_outcomes(evaluator_repo, portfolio_service, config):
    """Thin wrapper that resolves expired signals."""
    from core.evaluator import run_evaluation_cycle
    return run_evaluation_cycle(evaluator_repo, portfolio_service, config)


def _run_calibrator_job(db=None) -> None:
    repo = _resolve_evaluator_repo(db)
    if repo is None:
        logger.error("Evaluator repo not available for calibrator job")
        return

    from api.services.config import get_config

    try:
        cfg = get_config() if get_config else {}
        ev_cfg = cfg.get('evaluator', {})

        result = _run_calibration(repo, ev_cfg)
        if result.get("success"):
            repo.set_scheduler_state(
                "calibrator",
                "ok",
                f"Cycle {result.get('cycle', '?')}, loss={result.get('loss', '?'):.4f}, samples={result.get('samples', 0)}",
            )
        else:
            repo.set_scheduler_state(
                "calibrator", "skipped", result.get("message", "unknown")
            )
        logger.info("Scheduled calibrator: %s", result)
    except Exception as e:
        logger.error("Scheduled calibrator failed: %s", e)


def _run_calibration(evaluator_repo, config):
    """Thin wrapper that runs calibration cycle."""
    from core.calibrator import run_calibration_cycle
    return run_calibration_cycle(evaluator_repo, config=config)


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

_scheduler: Optional[BackgroundScheduler] = None
_scheduler_lock = threading.Lock()
_scheduler_started = False


def start_scheduler(db=None, app=None) -> bool:
    """Start the APScheduler background scheduler with jobs.

    Args:
        db: Optional OptionsDatabase instance. If provided, the evaluator repo
            is created immediately from it. Otherwise resolves from Flask app
            config on job run.

    Returns True if this process became the scheduler owner, False otherwise.
    Only one process per host will actually register the schedule.
    """
    global _scheduler, _scheduler_started, _evaluator_repo

    if _scheduler_started:
        return True

    if not _acquire_lock():
        return False

    with _scheduler_lock:
        if _scheduler_started:
            return True

        # Resolve evaluator_repo from app DB or direct argument
        if db is not None:
            _evaluator_repo = db.evaluator
            logger.info("Scheduler using provided DB for evaluator repo")
        else:
            try:
                from flask import current_app
                if current_app:
                    db = current_app.config.get('database')
                    if db:
                        _evaluator_repo = db.evaluator
            except Exception:
                logger.warning("Could not resolve evaluator_repo from Flask app, will init on job run")

        brisbane_tz = pytz.timezone("Australia/Brisbane")

        _scheduler = BackgroundScheduler(
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 900,
            }
        )

        _scheduler.add_job(
            _run_evaluator_job,
            trigger=CronTrigger(hour=8, minute=0, timezone=brisbane_tz),
            id="evaluator_daily",
            name="Evaluator (daily 8am Brisbane)",
            kwargs={"db": db},
            replace_existing=True,
        )

        _scheduler.add_job(
            _run_calibrator_job,
            trigger=CronTrigger(day_of_week="sun", hour=8, minute=15, timezone=brisbane_tz),
            id="calibrator_weekly",
            name="Calibrator (weekly Sun 8:15am Brisbane)",
            kwargs={"db": db},
            replace_existing=True,
        )

        _scheduler.add_job(
            _run_earnings_updater_job,
            trigger=IntervalTrigger(hours=6),
            id="earnings_updater_6h",
            name="Earnings updater (every 6h)",
            kwargs={"db": db},
            replace_existing=True,
        )

        _scheduler.start()
        _scheduler_started = True
        logger.info(
            "Scheduler started: evaluator daily 8:00am, calibrator weekly Sun 8:15am, earnings updater every 6h"
        )

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
