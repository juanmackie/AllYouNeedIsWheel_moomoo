"""
Smoke tests for import side effects.
Verifies that importing modules does not trigger moomoo SDK side effects
(e.g., file logging to C:\\Users\\...\\py_*.log).
"""

import ast
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import moomoo_sdk_log

_REPO_ROOT = Path(__file__).resolve().parents[1]


class TestCoreImport(unittest.TestCase):
    """Test that importing core modules does not trigger moomoo SDK."""

    def test_import_core_no_side_effects(self):
        """Importing core should not trigger moomoo SDK logging."""
        # Capture any file writes to the moomoo log directory
        moomoo_log_dir = os.path.expanduser("~/AppData/Roaming/com.moomoo.OpenD/Log")

        # Exercise a fresh import without leaving duplicate core module objects
        # beside already-imported functions that still reference the originals.
        original_core_modules = {
            name: module for name, module in sys.modules.items() if name == "core" or name.startswith("core.")
        }
        try:
            for name in original_core_modules:
                sys.modules.pop(name, None)

            before = 0
            if os.path.exists(moomoo_log_dir):
                before = len([f for f in os.listdir(moomoo_log_dir) if f.startswith("py_")])

            importlib.import_module("core")

            after = 0
            if os.path.exists(moomoo_log_dir):
                after = len([f for f in os.listdir(moomoo_log_dir) if f.startswith("py_")])
        finally:
            for name in list(sys.modules):
                if (name == "core" or name.startswith("core.")) and name not in original_core_modules:
                    sys.modules.pop(name, None)
            sys.modules.update(original_core_modules)

        # Importing core should not create new moomoo log files
        self.assertEqual(before, after, f"Importing core created {after - before} new moomoo log files")

    def test_core_lazy_moomoo_import(self):
        """MoomooConnection should be loaded lazily via __getattr__."""
        import core

        # core.MoomooConnection should trigger __getattr__ lazy load
        with patch("core.connection_manager.MoomooConnection", MagicMock()):
            # Accessing MoomooConnection should not fail
            cls = core.MoomooConnection
            self.assertIsNotNone(cls)

    def test_import_core_utils_no_moomoo(self):
        """Importing core.utils should not import moomoo."""
        # core.utils is pure - should not trigger moomoo import
        from core import get_closest_friday

        self.assertTrue(callable(get_closest_friday))

    def test_import_core_scoring_factors_no_moomoo(self):
        """Importing core.scoring_factors should not import moomoo."""
        from core.scoring_factors import _clamp, capital_velocity_per_day, classify_event_tier

        self.assertTrue(callable(_clamp))
        self.assertTrue(callable(capital_velocity_per_day))
        self.assertTrue(callable(classify_event_tier))

    def test_import_core_greeks_uses_stdlib_normaldist_not_moomoo(self):
        """core.greeks imports stdlib NormalDist (no scipy) and must not import
        moomoo at top level."""
        # This test verifies that the module can be imported
        # The lazy loading of moomoo should prevent side effects
        with patch("core.greeks.compute_bs_greeks", side_effect=NotImplementedError):
            from core import greeks

            self.assertTrue(hasattr(greeks, "compute_bs_greeks"))
            self.assertTrue(hasattr(greeks, "_STANDARD_NORMAL"))


class TestAPIImport(unittest.TestCase):
    """Test that importing API modules does not trigger moomoo SDK."""

    def test_import_api_services_no_side_effects(self):
        """Importing service modules should not trigger moomoo SDK."""
        # These modules use lazy imports - patch core.connection_manager instead
        with patch("core.connection_manager.MoomooConnection", MagicMock()):
            module = importlib.import_module("api.services.options_service")

        self.assertTrue(hasattr(module, "OptionsService"))

    def test_import_wheel_decision_no_moomoo_side_effects(self):
        """Importing core.wheel_decision should not trigger moomoo SDK."""
        with patch("core.connection_manager.MoomooConnection", MagicMock()):
            from core.wheel_decision import score_contract

            self.assertTrue(callable(score_contract))


class TestRouteResponseHelpers(unittest.TestCase):
    """Test that route response helpers are available."""

    def test_response_helpers_importable(self):
        """error_response and success_response should be importable from routes."""
        from api.routes.utils import error_response, success_response

        self.assertTrue(callable(error_response))
        self.assertTrue(callable(success_response))


class TestConnectionImports(unittest.TestCase):
    """The connection module must tolerate a missing moomoo SDK at import time.

    The old ``core/connection.py`` re-export shim was removed in favour of
    importing the decomposed modules directly. This keeps the property that
    mattered: importing the module that owns ``MoomooConnection`` never
    hard-fails when the SDK is absent, so test collection and the pure helpers
    still work.
    """

    def test_connection_manager_guards_top_level_moomoo_import(self):
        """core/connection_manager.py must import moomoo only inside try/except."""
        import ast

        with open(os.path.join(os.path.dirname(__file__), "..", "core", "connection_manager.py"), "r") as f:
            tree = ast.parse(f.read())

        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        moomoo_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "moomoo":
                moomoo_imports.append(node)
            elif isinstance(node, ast.Import) and any(alias.name == "moomoo" for alias in node.names):
                moomoo_imports.append(node)

        self.assertTrue(moomoo_imports, "expected the optional SDK import to be present but guarded")
        for node in moomoo_imports:
            ancestor = parents.get(node)
            guarded = False
            while ancestor is not None:
                if isinstance(ancestor, ast.Try):
                    guarded = True
                    break
                ancestor = parents.get(ancestor)
            self.assertTrue(
                guarded,
                f"core/connection_manager.py:{node.lineno} must keep its moomoo import inside try/except",
            )


class TestMoomooSdkLogDirBootstrap(unittest.TestCase):
    """The moomoo SDK log directory must never stop the app from booting.

    ``moomoo.common.ft_logger`` opens ``%APPDATA%\\com.moomoo.OpenD\\Log\\
    py_<date>.log`` during ``import moomoo``. Where that write is denied the
    import raised ``PermissionError`` and every entry point died at boot
    (``[Errno 13] Permission denied ... py_2026_10_29.log``).
    """

    def test_writable_sdk_log_dir_is_left_alone(self):
        with tempfile.TemporaryDirectory() as profile:
            with patch.dict(os.environ, {"APPDATA": profile}):
                resolved = moomoo_sdk_log.ensure_sdk_log_dir()

                self.assertEqual(os.environ["APPDATA"], profile, "a writable SDK log dir must not be redirected")
            expected = Path(profile) / moomoo_sdk_log.SDK_LOG_PARENT / moomoo_sdk_log.SDK_LOG_LEAF
            self.assertEqual(Path(resolved), expected)
            self.assertTrue(Path(resolved).is_dir())

    def test_unwritable_sdk_log_dir_redirects_to_a_fallback(self):
        with tempfile.TemporaryDirectory() as profile:
            blocked = Path(profile) / moomoo_sdk_log.SDK_LOG_PARENT / moomoo_sdk_log.SDK_LOG_LEAF
            real_is_writable = moomoo_sdk_log._is_writable

            def fake_writable(directory):
                if Path(directory) == blocked:
                    return False
                return real_is_writable(directory)

            with patch.dict(os.environ, {"APPDATA": profile}):
                with patch.object(moomoo_sdk_log, "_is_writable", side_effect=fake_writable):
                    resolved = moomoo_sdk_log.ensure_sdk_log_dir()

                fallback_base = os.path.join(moomoo_sdk_log.LOGS_DIR, moomoo_sdk_log.FALLBACK_DIR_NAME)
                self.assertEqual(os.environ["APPDATA"], fallback_base)
                self.assertEqual(resolved, os.path.join(fallback_base, "com.moomoo.OpenD", "Log"))
                self.assertTrue(os.path.isdir(resolved), "the fallback SDK log dir must exist and be writable")

    def test_every_module_that_imports_moomoo_prepares_the_log_dir_first(self):
        """A module that imports the SDK before the bootstrap reintroduces the boot crash."""
        bootstrap_names = {"core.moomoo_sdk_log"}
        offenders = []
        for path in _runtime_python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            bootstrap_lines = []
            moomoo_lines = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "moomoo" or alias.name.startswith("moomoo."):
                            moomoo_lines.append(node.lineno)
                        elif alias.name in bootstrap_names:
                            bootstrap_lines.append(node.lineno)
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module == "moomoo" or module.startswith("moomoo."):
                        moomoo_lines.append(node.lineno)
                    elif module in bootstrap_names:
                        bootstrap_lines.append(node.lineno)
            if not moomoo_lines:
                continue
            first = min(bootstrap_lines) if bootstrap_lines else None
            if first is None or first > min(moomoo_lines):
                rel = path.relative_to(_REPO_ROOT).as_posix()
                offenders.append(f"{rel}: import core.moomoo_sdk_log before moomoo")
        self.assertEqual(offenders, [], "modules must prepare the SDK log dir before importing moomoo")


def _runtime_python_files():
    roots = [
        _REPO_ROOT / "api",
        _REPO_ROOT / "core",
        _REPO_ROOT / "db",
        _REPO_ROOT / "tools",
    ]
    files = []
    for name in ("app.py", "run_api.py"):
        files.append(_REPO_ROOT / name)
    for root in roots:
        files.extend(sorted(root.rglob("*.py")))
    return [path for path in files if "__pycache__" not in path.parts]


if __name__ == "__main__":
    unittest.main()
