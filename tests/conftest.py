"""
Pytest/Unittest configuration.

1. Adds project root to sys.path so tests can import modules from api/, core/, db/.
2. Suppresses moomoo SDK file logging during test collection by redirecting
   the SDK's log directory to a temp path before any moomoo import occurs.
3. Cleans .pytest_cache on session start to avoid stale permission issues.
"""

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_test_appdata_root = PROJECT_ROOT / "_tmp" / "appdata"
_test_appdata_root.mkdir(parents=True, exist_ok=True)
os.environ["APPDATA"] = str(_test_appdata_root)
os.environ["appdata"] = str(_test_appdata_root)

_test_temp_root = PROJECT_ROOT / "_tmp" / "pytest"
_test_temp_root.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = str(_test_temp_root)
os.environ["TMP"] = str(_test_temp_root)
os.environ["TMPDIR"] = str(_test_temp_root)
tempfile.tempdir = str(_test_temp_root)

# Route application logging away from the repository tree (import-time safety)
os.environ.setdefault("WHEEL_LOG_DIR", str(_test_temp_root / "logs"))


def _safe_mkdtemp(suffix="", prefix="tmp", dir=None):
    base_dir = Path(dir) if dir else _test_temp_root
    base_dir.mkdir(parents=True, exist_ok=True)
    for _ in range(1000):
        candidate = base_dir / f"{prefix}{uuid.uuid4().hex[:8]}{suffix}"
        try:
            candidate.mkdir(parents=False)
            return str(candidate)
        except FileExistsError:
            continue
    raise FileExistsError("Unable to allocate a temporary directory")


tempfile.mkdtemp = _safe_mkdtemp

# Redirect moomoo SDK log output away from Roaming to avoid PermissionError
# during test collection when moomoo imports trigger file-logging init.
_moomoo_log_root = Path.home() / "AppData" / "Roaming" / "com.moomoo.OpenD" / "Log"
if _moomoo_log_root.exists():
    _test_log_dir = PROJECT_ROOT / "_tmp" / "moomoo_logs"
    _test_log_dir.mkdir(parents=True, exist_ok=True)
    # Attempt to redirect by patching the env before any moomoo import
    os.environ.setdefault("MOOMOO_LOG_PATH", str(_test_log_dir))

# Clean stale .pytest_cache to avoid permission issues from prior runs
_pycache = PROJECT_ROOT / ".pytest_cache"
if _pycache.exists():
    try:
        shutil.rmtree(str(_pycache), ignore_errors=True)
    except Exception:
        pass
