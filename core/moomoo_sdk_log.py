"""
Keep the moomoo SDK client log writable before the SDK is imported.

``moomoo.common.ft_logger`` opens
``%APPDATA%\\com.moomoo.OpenD\\Log\\py_<date>.log`` (or
``$HOME/.com.moomoo.OpenD/Log/...``) as a side effect of ``import moomoo`` and
lets ``PermissionError`` escape. A process that cannot create files in that
profile directory — a sandboxed or low-integrity launcher, a locked file, a
read-only profile — therefore cannot import the SDK at all, and every entry
point dies during boot with ``[Errno 13] Permission denied``.

Importing this module probes the directory the SDK will use:

- writable: nothing changes, the SDK keeps its normal log location;
- not writable: the environment variable the SDK reads (``APPDATA`` on
  Windows, ``HOME`` elsewhere) is pointed at a writable fallback base, so the
  SDK writes ``.../com.moomoo.OpenD/Log/py_<date>.log`` under the application
  log directory (or the temp directory) and the app boots.

Import it before any ``import moomoo``. The redirect is process-wide and only
applied when the SDK's own location is unusable; the application itself never
reads ``APPDATA``/``HOME``.
"""

import os
import platform
import tempfile

from core.logging_config import LOGS_DIR, get_logger

logger = get_logger("ayniwheel.sdk_log", "moomoo")

SDK_LOG_PARENT = "com.moomoo.OpenD"
SDK_LOG_LEAF = "Log"
FALLBACK_DIR_NAME = "moomoo_sdk"


def _is_windows():
    return platform.system() == "Windows"


def _home_env_var():
    """Environment variable the SDK joins its log path with."""
    return "APPDATA" if _is_windows() else "HOME"


def _sdk_log_dir_for_base(base):
    """Path the SDK builds from ``base`` (see moomoo.common.ft_logger.FTLog)."""
    prefix = "" if _is_windows() else "."
    return os.path.join(base, prefix + SDK_LOG_PARENT, SDK_LOG_LEAF)


def sdk_log_dir():
    """Directory the SDK will use for its client log right now."""
    base = os.environ.get(_home_env_var())
    if not base:
        base = tempfile.gettempdir() if _is_windows() else os.path.expanduser("~")
    return _sdk_log_dir_for_base(base)


def _is_writable(directory):
    """True when a probe file can be created and removed in ``directory``."""
    probe = os.path.join(directory, f".write-probe-{os.getpid()}.tmp")
    try:
        os.makedirs(directory, exist_ok=True)
        with open(probe, "w", encoding="utf-8"):
            pass
        return True
    except OSError:
        return False
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass


def _fallback_bases():
    fallback = os.path.join(LOGS_DIR, FALLBACK_DIR_NAME)
    yield fallback
    yield os.path.join(tempfile.gettempdir(), FALLBACK_DIR_NAME)
    if not _is_windows():
        yield os.path.expanduser("~")


def ensure_sdk_log_dir():
    """Return the SDK log directory, redirecting it only when it is unusable."""
    current = sdk_log_dir()
    if _is_writable(current):
        return current

    for base in _fallback_bases():
        if not base or base == os.environ.get(_home_env_var()):
            continue
        candidate = _sdk_log_dir_for_base(base)
        if _is_writable(candidate):
            os.environ[_home_env_var()] = base
            logger.warning(
                "moomoo SDK log directory %s is not writable; SDK client logs go to %s",
                current,
                candidate,
            )
            return candidate

    # Nothing writable: leave the environment alone and let the SDK report its
    # own error, exactly as before this module existed.
    logger.warning("moomoo SDK log directory %s is not writable and no fallback worked", current)
    return current


ensure_sdk_log_dir()
