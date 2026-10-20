"""The architecture contract must be executable and self-testing.

These tests prove the checker reaches the intended sources: a known-good tree
passes, a synthetic forbidden dependency fails, and budget/staleness rules fire.
"""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "architecture.py"
SPEC = importlib.util.spec_from_file_location("architecture_contract", SCRIPT)
architecture = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = architecture
SPEC.loader.exec_module(architecture)


def test_manifest_covers_every_runtime_module():
    errors = architecture.validate_manifest(architecture.load_manifest(), architecture.module_map())
    assert errors == []


def test_generated_capability_map_is_current():
    assert architecture.check_docs(architecture.load_manifest()) == []


def test_boundary_checker_rejects_a_core_to_db_import():
    refs = [architecture.ImportRef("core.scan_ledger", "db.sqlite_pool", "", 10)]
    rules = [{"from": "core", "to": "db", "reason": "persistence is injected"}]
    errors = architecture.boundary_violations(refs, rules)
    assert errors and "core.scan_ledger -> db.sqlite_pool" in errors[0]


def test_boundary_checker_allows_the_declared_direction():
    refs = [
        architecture.ImportRef("api.services.recommendations", "core.wheel_decision", "score_contract", 1),
        architecture.ImportRef("db.fills_repository", "core.utils", "safe_float", 1),
        architecture.ImportRef("api.services.options_service", "api.services.recommendations", "", 1),
    ]
    rules = architecture.load_manifest()["boundaries"]
    assert architecture.boundary_violations(refs, rules) == []


def test_private_imports_are_rejected_across_packages_only():
    cross = [architecture.ImportRef("db.iv_repository", "core.utils", "_normalize_iv", 1)]
    internal = [architecture.ImportRef("core.wheel_decision", "core.utils", "_internal", 1)]
    assert architecture.private_import_violations(cross)
    assert architecture.private_import_violations(internal) == []


def test_module_budget_rejects_a_new_oversized_module(tmp_path):
    path = tmp_path / "big.py"
    path.write_text("value = 1\n" * 901, encoding="utf-8")
    errors = architecture.budget_violations(
        {"big": path},
        {"module_loc": 900, "function_loc": 150},
        root=tmp_path,
    )
    assert any("big.py" in error and "exceeds" in error for error in errors)


def test_module_budget_rejects_a_stale_baseline(tmp_path):
    path = tmp_path / "small.py"
    path.write_text("value = 1\n" * 10, encoding="utf-8")
    errors = architecture.budget_violations(
        {"small": path},
        {"module_loc": 900, "function_loc": 150, "module_baseline": {"small.py": 20}},
        root=tmp_path,
    )
    assert any("small.py" in error and "stale" in error for error in errors)


def test_function_budget_rejects_a_new_oversized_function(tmp_path):
    path = tmp_path / "func.py"
    path.write_text("def long_function():\n" + "    value = 1\n" * 151, encoding="utf-8")
    errors = architecture.budget_violations(
        {"func": path},
        {"module_loc": 900, "function_loc": 150},
        root=tmp_path,
    )
    assert any("long_function" in error and "exceeds" in error for error in errors)
