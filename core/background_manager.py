"""
Background Task Manager

Manages background tasks with health monitoring and auto-restart capabilities.
Designed to handle multiple tasks with automatic failure recovery.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("autotrader.background")


@dataclass
class TaskConfig:
    """Configuration for a background task."""

    name: str
    worker_fn: Callable[[], None]
    restart_on_failure: bool = True
    max_restart_attempts: int = 5
    restart_delay_seconds: int = 60
    health_check_interval_seconds: int = 300


@dataclass
class TaskState:
    """Runtime state of a background task."""

    thread: Optional[threading.Thread] = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    restart_count: int = 0
    last_start_time: Optional[datetime] = None
    last_error: Optional[str] = None
    is_healthy: bool = True


class BackgroundTaskManager:
    """
    Manages background tasks with health monitoring and auto-restart.

    Usage:
        manager = BackgroundTaskManager()

        def my_worker():
            # do work
            pass

        manager.register(TaskConfig(name='my_task', worker_fn=my_worker))
        manager.start('my_task')

        # Check health
        status = manager.get_status('my_task')
    """

    def __init__(self):
        self._tasks: Dict[str, TaskConfig] = {}
        self._states: Dict[str, TaskState] = {}
        self._lock = threading.Lock()
        self._health_check_thread: Optional[threading.Thread] = None
        self._running = False

    def register(self, config: TaskConfig) -> None:
        """Register a background task."""
        with self._lock:
            self._tasks[config.name] = config
            self._states[config.name] = TaskState()
            logger.info(f"Registered background task: {config.name}")

    def start(self, name: str) -> bool:
        """Start a registered background task."""
        with self._lock:
            if name not in self._tasks:
                logger.error(f"Cannot start unknown task: {name}")
                return False

            self._tasks[name]
            state = self._states[name]

            if state.thread and state.thread.is_alive():
                logger.warning(f"Task {name} is already running")
                return True

            # Reset state
            state.stop_event.clear()
            state.last_error = None
            state.is_healthy = True

            # Create thread
            thread = threading.Thread(target=self._worker_wrapper, args=(name,), daemon=True, name=f"bg_task_{name}")
            state.thread = thread
            state.last_start_time = datetime.now()

            thread.start()
            logger.info(f"Started background task: {name}")
            return True

    def stop(self, name: str, timeout: float = 10.0) -> bool:
        """Stop a background task."""
        with self._lock:
            if name not in self._states:
                return False

            state = self._states[name]
            state.stop_event.set()

            if state.thread:
                state.thread.join(timeout=timeout)
                logger.info(f"Stopped background task: {name}")
            return True

    def stop_all(self, timeout: float = 10.0) -> None:
        """Stop all background tasks."""
        with self._lock:
            for name in list(self._tasks.keys()):
                self.stop(name, timeout)

    def restart(self, name: str) -> bool:
        """Manually restart a task."""
        self.stop(name)
        return self.start(name)

    def get_status(self, name: str) -> Optional[Dict[str, Any]]:
        """Get status of a background task."""
        with self._lock:
            if name not in self._tasks:
                return None

            config = self._tasks[name]
            state = self._states[name]

            is_alive = state.thread.is_alive() if state.thread else False

            return {
                "name": name,
                "running": is_alive,
                "healthy": state.is_healthy,
                "restart_count": state.restart_count,
                "last_start_time": state.last_start_time.isoformat() if state.last_start_time else None,
                "last_error": state.last_error,
                "restart_on_failure": config.restart_on_failure,
                "max_restart_attempts": config.max_restart_attempts,
            }

    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all registered tasks."""
        return {name: self.get_status(name) for name in self._tasks.keys()}

    def _worker_wrapper(self, name: str) -> None:
        """Internal wrapper that handles restarts and error tracking."""
        config = self._tasks[name]
        state = self._states[name]

        while not state.stop_event.is_set():
            try:
                logger.debug(f"Task {name} executing")
                config.worker_fn()

                # If worker returns (normal exit), check if we should restart
                if not state.stop_event.is_set() and config.restart_on_failure:
                    logger.info(f"Task {name} completed, restarting...")
                    time.sleep(config.restart_delay_seconds)
                    continue
                else:
                    break

            except Exception as e:
                error_msg = f"Task {name} failed: {str(e)}"
                logger.error(error_msg)
                state.last_error = str(e)
                state.is_healthy = False

                if config.restart_on_failure and state.restart_count < config.max_restart_attempts:
                    state.restart_count += 1
                    delay = config.restart_delay_seconds * (2 ** min(state.restart_count, 5))  # exponential backoff
                    logger.info(f"Task {name} restart {state.restart_count}/{config.max_restart_attempts} in {delay}s")
                    time.sleep(delay)
                else:
                    logger.error(f"Task {name} giving up after {state.restart_count} restart attempts")
                    break

        logger.info(f"Task {name} worker exited")

    def start_health_monitor(self, interval: int = 60) -> None:
        """Start periodic health check thread."""
        if self._running:
            return

        self._running = True

        def health_check_loop():
            while self._running:
                try:
                    self._perform_health_check()
                except Exception as e:
                    logger.error(f"Health check error: {e}")

                time.sleep(interval)

        self._health_check_thread = threading.Thread(target=health_check_loop, daemon=True, name="health_monitor")
        self._health_check_thread.start()
        logger.info("Health monitor started")

    def stop_health_monitor(self) -> None:
        """Stop the health check thread."""
        self._running = False
        if self._health_check_thread:
            self._health_check_thread.join(timeout=5)

    def _perform_health_check(self) -> None:
        """Check all tasks and auto-restart failed ones."""
        for name, config in self._tasks.items():
            if not config.restart_on_failure:
                continue

            state = self._states[name]

            if not state.thread or not state.thread.is_alive():
                if state.restart_count < config.max_restart_attempts:
                    logger.warning(f"Task {name} not running, auto-restarting...")
                    self.start(name)
