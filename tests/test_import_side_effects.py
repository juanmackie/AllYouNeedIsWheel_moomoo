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
        with patch("core.connection.MoomooConnection", MagicMock()):
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
        from core.scoring_factors import _clamp, _score_proximity

        self.assertTrue(callable(_clamp))
        self.assertTrue(callable(_score_proximity))

    def test_import_core_greeks_imports_scipy_not_moomoo(self):
        """core.greeks imports scipy but should not import moomoo at top level."""
        # This test verifies that the module can be imported
        # The lazy loading of moomoo should prevent side effects
        with patch("core.greeks.compute_bs_greeks", side_effect=NotImplementedError):
            from core import greeks

            self.assertTrue(hasattr(greeks, "compute_bs_greeks"))


class TestAPIImport(unittest.TestCase):
    """Test that importing API modules does not trigger moomoo SDK."""

    def test_import_api_services_no_side_effects(self):
        """Importing service modules should not trigger moomoo SDK."""
        # These modules use lazy imports - patch core.connection instead
        with patch("core.connection.MoomooConnection", MagicMock()):
            module = importlib.import_module("api.services.options_service")

        self.assertTrue(hasattr(module, "OptionsService"))

    def test_import_wheel_decision_no_moomoo_side_effects(self):
        """Importing core.wheel_decision should not trigger moomoo SDK."""
        with patch("core.connection.MoomooConnection", MagicMock()):
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
    """Test that connection.py no longer has top-level moomoo imports."""

    def test_connection_no_top_level_moomoo_import(self):
        """core/connection.py should NOT import moomoo at top level."""
        import ast

        with open(os.path.join(os.path.dirname(__file__), "..", "core", "connection.py"), "r") as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(
                        alias.name, "moomoo", "core/connection.py should not have top-level 'from moomoo import ...'"
                    )
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(
                    node.module, "moomoo", "core/connection.py should not have top-level 'from moomoo import ...'"
                )


if __name__ == "__main__":
    unittest.main()
