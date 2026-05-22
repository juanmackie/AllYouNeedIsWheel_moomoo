"""
In-process scheduler for evaluator and calibrator runs.

Uses APScheduler with a host-level single-owner lock so only one process
schedules jobs under multi-worker launches.  Persists last-run state in
a local SQLite database so the dashboard always knows when jobs ran.

Schedule:
  - Evaluator: daily at 8:00 AM Australia/Brisbane time
  - Calibrator: weekly on Sunday at 8:15 AM Australia/Brisbane time
"""

import json
import logging
import os
import socket
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

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
    """Acquire a host-level lock. Returns True if this process owns the lock."""
    global _LOCK_FILE
    lock_path = _lock_path()
    try:
        _LOCK_FILE = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        pid = os.getpid()
        os.write(_LOCK_FILE, str(pid).encode())
        logger.info("Scheduler lock acquired (pid=%s)", pid)
        return True
    except FileExistsError:
        # Check if the lock is stale (>5 minutes)
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
    """Release the host-level lock."""
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
# State persistence
# ---------------------------------------------------------------------------

STATE_DB_PATH: Optional[Path] = None


def _state_db() -> sqlite3.Connection:
    global STATE_DB_PATH
    if STATE_DB_PATH is None:
        STATE_DB_PATH = Path.home() / ".wheel" / "evaluator" / "scheduler_state.db"
    STATE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(STATE_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scheduler_state (
            name        TEXT PRIMARY KEY,
            last_run    TEXT,
            last_status TEXT,
            last_message TEXT
        )
    """)
    return conn


def _get_state(name: str) -> Optional[dict]:
    conn = _state_db()
    try:
        row = conn.execute(
            "SELECT * FROM scheduler_state WHERE name=?", (name,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _set_state(name: str, status: str, message: str = "") -> None:
    conn = _state_db()
    try:
        now = datetime.now().isoformat()
        conn.execute("""
            INSERT INTO scheduler_state (name, last_run, last_status, last_message)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                last_run=excluded.last_run,
                last_status=excluded.last_status,
                last_message=excluded.last_message
        """, (name, now, status, message))
        conn.commit()
    finally:
        conn.close()


def get_scheduler_state() -> dict:
    """Return scheduler state for all named jobs, for dashboard use."""
    conn = _state_db()
    try:
        rows = conn.execute(
            "SELECT * FROM scheduler_state ORDER BY name"
        ).fetchall()
        return {r["name"]: dict(r) for r in rows}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Job functions
# ---------------------------------------------------------------------------

def _run_evaluator_job() -> None:
    """Run the evaluator cycle and record state."""
    from core.evaluator import run_evaluation_cycle
    try:
        result = run_evaluation_cycle()
        resolved = result.get("resolved", 0)
        errors = result.get("errors", 0)
        _set_state(
            "evaluator",
            "ok" if errors == 0 else "partial",
            f"Checked {result.get('checked', 0)}, resolved {resolved}, errors {errors}",
        )
        logger.info("Scheduled evaluator: %s", result)
    except Exception as e:
        _set_state("evaluator", "error", str(e))
        logger.error("Scheduled evaluator failed: %s", e)


def _run_calibrator_job() -> None:
    """Run the calibrator cycle and record state."""
    from core.calibrator import run_calibration_cycle
    try:
        result = run_calibration_cycle()
        if result.get("success"):
            _set_state(
                "calibrator",
                "ok",
                f"Cycle {result.get('cycle', '?')}, loss={result.get('loss', '?'):.4f}, samples={result.get('samples', 0)}",
            )
        else:
            _set_state("calibrator", "skipped", result.get("message", "unknown"))
        logger.info("Scheduled calibrator: %s", result)
    except Exception as e:
        _set_state("calibrator", "error", str(e))
        logger.error("Scheduled calibrator failed: %s", e)


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

_scheduler: Optional[BackgroundScheduler] = None
_scheduler_lock = threading.Lock()
_scheduler_started = False


def start_scheduler() -> bool:
    """Start the APScheduler background scheduler with jobs.

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

        brisbane_tz = pytz.timezone("Australia/Brisbane")

        _scheduler = BackgroundScheduler(
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 900,
            }
        )

        # Daily evaluator at 8:00 AM Brisbane time
        _scheduler.add_job(
            _run_evaluator_job,
            trigger=CronTrigger(hour=8, minute=0, timezone=brisbane_tz),
            id="evaluator_daily",
            name="Evaluator (daily 8am Brisbane)",
            replace_existing=True,
        )

        # Weekly calibrator on Sunday at 8:15 AM Brisbane time
        _scheduler.add_job(
            _run_calibrator_job,
            trigger=CronTrigger(day_of_week="sun", hour=8, minute=15, timezone=brisbane_tz),
            id="calibrator_weekly",
            name="Calibrator (weekly Sun 8:15am Brisbane)",
            replace_existing=True,
        )

        _scheduler.start()
        _scheduler_started = True
        logger.info(
            "Scheduler started: evaluator daily 8:00am, calibrator weekly Sun 8:15am (Brisbane time)"
        )
        return True


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    global _scheduler, _scheduler_started
    with _scheduler_lock:
        if _scheduler:
            _scheduler.shutdown(wait=False)
            _scheduler = None
        _scheduler_started = False
        _release_lock()
        logger.info("Scheduler stopped")


def get_scheduler_info() -> dict:
    """Return scheduler metadata for the dashboard."""
    global _scheduler
    state = get_scheduler_state()
    jobs_info = []
    if _scheduler and _scheduler.running:
        for job in _scheduler.get_jobs():
            jobs_info.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            })
    return {
        "running": _scheduler is not None and _scheduler.running,
        "has_lock": _LOCK_FILE is not None,
        "jobs": jobs_info,
        "state": state,
    }
