"""Repository scan proving no order-capable runtime surface remains.

An AST scan over runtime source (api/, core/, db/, app.py, config.py,
run_api.py) asserts that no forbidden SDK member (unlock/order/cancel/modify)
is ever called, imported, or aliased. Tests are intentionally excluded from
the scan; they are allowed to *reference* the names for assertions.
"""

import ast
import unittest
from pathlib import Path

from core.broker_protocol import FORBIDDEN_SDK_MEMBERS, QUERY_SDK_MEMBERS

REPO_ROOT = Path(__file__).parent.parent
SCAN_PATHS = [
    REPO_ROOT / "api",
    REPO_ROOT / "core",
    REPO_ROOT / "db",
    REPO_ROOT / "app.py",
    REPO_ROOT / "config.py",
    REPO_ROOT / "run_api.py",
]
EXCLUDED_DIRS = {"__pycache__"}


def _iter_source_files():
    for path in SCAN_PATHS:
        if path.is_file() and path.suffix == ".py":
            yield path
        elif path.is_dir():
            for py in sorted(path.rglob("*.py")):
                if not any(part in EXCLUDED_DIRS for part in py.parts):
                    yield py


def _scan_for_forbidden():
    findings = []
    for path in _iter_source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            findings.append(f"{path}: SYNTAX ERROR {exc}")
            continue

        for node in ast.walk(tree):
            # Attribute access: ctx.place_order(...) or any member mention
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_SDK_MEMBERS:
                findings.append(f"{path}:{node.lineno}: member access .{node.attr}")
            # Imported names: from moomoo import place_order / import alias
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    imported = alias.name.split(".")[-1]
                    if imported in FORBIDDEN_SDK_MEMBERS:
                        findings.append(f"{path}:{node.lineno}: import of {alias.name}")
    return findings


class TestNoExecutionSurface(unittest.TestCase):
    def test_runtime_source_has_no_forbidden_member_calls(self):
        findings = _scan_for_forbidden()
        self.assertEqual(findings, [], "order/unlock surface found in runtime source:\n" + "\n".join(findings))

    def test_forbidden_members_are_defined(self):
        # Sanity: the forbidden list is non-empty and the scan actually
        # traverses files.
        self.assertTrue(FORBIDDEN_SDK_MEMBERS)
        files = list(_iter_source_files())
        self.assertGreater(len(files), 10)
        self.assertTrue(any(f.name == "connection_manager.py" for f in files))

    def test_connection_context_calls_are_documented_queries(self):
        path = REPO_ROOT / "core" / "connection_manager.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        used_members = set()

        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if isinstance(node.value, ast.Attribute) and node.value.attr in {"quote_ctx", "trd_ctx"}:
                used_members.add(node.attr)
            elif isinstance(node.value, ast.Name) and node.value.id in {"quote_ctx", "trd_ctx"}:
                used_members.add(node.attr)

        undocumented = sorted(used_members - set(QUERY_SDK_MEMBERS))
        self.assertEqual(
            undocumented,
            [],
            "connection_manager accesses SDK context members missing from QUERY_SDK_MEMBERS: "
            + ", ".join(undocumented),
        )

    def test_new_order_mutation_members_are_forbidden(self):
        self.assertTrue({"cancel_all_order", "change_order"}.issubset(FORBIDDEN_SDK_MEMBERS))


if __name__ == "__main__":
    unittest.main()
