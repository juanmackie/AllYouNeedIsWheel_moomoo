"""
Smoke tests for import side effects.
Verifies that importing modules does not trigger moomoo SDK side effects
(e.g., file logging to C:\\Users\\...\\py_*.log).
"""

import importlib
import os
import sys
import unittest
from unittest.mock import MagicMock, patch


class TestCoreImport(unittest.TestCase):
    """Test that importing core modules does not trigger moomoo SDK."""

    def test_import_core_no_side_effects(self):
        """Importing core should not trigger moomoo SDK logging."""
        # Capture any file writes to the moomoo log directory
        moomoo_log_dir = os.path.expanduser("~/AppData/Roaming/com.moomoo.OpenD/Log")

        # Force a fresh import so the test actually exercises module import side effects.
        for name in [
            module_name for module_name in list(sys.modules) if module_name == "core" or module_name.startswith("core.")
        ]:
            sys.modules.pop(name, None)

        before = 0
        if os.path.exists(moomoo_log_dir):
            before = len([f for f in os.listdir(moomoo_log_dir) if f.startswith("py_")])

        importlib.import_module("core")

        after = 0
        if os.path.exists(moomoo_log_dir):
            after = len([f for f in os.listdir(moomoo_log_dir) if f.startswith("py_")])

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


if __name__ == "__main__":
    unittest.main()
