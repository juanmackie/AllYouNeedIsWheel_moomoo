"""Executable architecture contract for the wheel desk.

The capability map lives in ``docs/architecture/capabilities.json``. This
script keeps the map, dependency direction, complexity budgets, and the
generated human documentation honest.

Commands:
  check   Validate boundaries, budgets, capability coverage, and docs.
  docs    Regenerate the capability section in docs/architecture/README.md.
  impact  Map changed files to the capabilities and focused tests they touch.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "docs" / "architecture" / "capabilities.json"
DOC_PATH = ROOT / "docs" / "architecture" / "README.md"
DOC_START = "<!-- capability-map:start -->"
DOC_END = "<!-- capability-map:end -->"
RUNTIME_ROOTS = ("api", "core", "db")
RUNTIME_FILES = ("app.py", "config.py", "run_api.py")
FRONTEND_JS = "frontend/static/js"
FRONTEND_TESTS = Path("tests/frontend")
EXCLUDED_DIRS = {"__pycache__"}


@dataclass(frozen=True)
class ImportRef:
    source: str
    target: str
    name: str
    lineno: int


@dataclass(frozen=True)
class FunctionSize:
    qualname: str
    lines: int


def runtime_files() -> list[Path]:
    files: list[Path] = []
    for root_name in RUNTIME_ROOTS:
        root = ROOT / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if not any(part in EXCLUDED_DIRS for part in path.parts):
                files.append(path)
    for name in RUNTIME_FILES:
        path = ROOT / name
        if path.is_file():
            files.append(path)
    return files


def module_name(path: Path) -> tuple[str, bool]:
    rel = path.relative_to(ROOT)
    parts = list(rel.with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return ".".join(parts), is_package


def module_map() -> dict[str, Path]:
    return {module_name(path)[0]: path for path in runtime_files()}


def _absolute_import(module: str, is_package: bool, level: int, imported: str | None) -> str:
    if level == 0:
        return imported or ""
    parts = module.split(".")
    base = parts if is_package else parts[:-1]
    if level > 1:
        base = base[: len(base) - (level - 1)]
    if imported:
        base.extend(imported.split("."))
    return ".".join(part for part in base if part)


def import_refs(path: Path, module_names: set[str]) -> list[ImportRef]:
    source, is_package = module_name(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    refs: list[ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in module_names:
                    refs.append(ImportRef(source, alias.name, "", node.lineno))
        elif isinstance(node, ast.ImportFrom):
            base = _absolute_import(source, is_package, node.level, node.module)
            if not base:
                continue
            for alias in node.names:
                candidate = f"{base}.{alias.name}"
                if candidate in module_names:
                    refs.append(ImportRef(source, candidate, alias.name, node.lineno))
                elif base in module_names:
                    refs.append(ImportRef(source, base, alias.name, node.lineno))
    return refs


def collect_imports(modules: dict[str, Path]) -> list[ImportRef]:
    names = set(modules)
    refs: list[ImportRef] = []
    for path in modules.values():
        refs.extend(import_refs(path, names))
    return refs


def _matches(namespace: str, module: str) -> bool:
    return module == namespace or module.startswith(namespace + ".")


def boundary_violations(refs: list[ImportRef], boundaries: list[dict]) -> list[str]:
    errors = []
    for ref in refs:
        for rule in boundaries:
            source_ns = rule["from"]
            target_ns = rule["to"]
            if not _matches(source_ns, ref.source):
                continue
            if not _matches(target_ns, ref.target):
                continue
            if _matches(source_ns, ref.target):
                continue
            errors.append(
                f"{ref.source} -> {ref.target} (line {ref.lineno}) violates {source_ns} -> {target_ns}: {rule['reason']}"
            )
    return errors


def private_import_violations(refs: list[ImportRef]) -> list[str]:
    errors = []
    for ref in refs:
        if not ref.name.startswith("_") or ref.name == "__future__":
            continue
        if ref.source.split(".")[0] == ref.target.split(".")[0]:
            continue
        errors.append(f"{ref.source} imports private symbol {ref.name} from {ref.target} (line {ref.lineno})")
    return errors


def module_loc(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def function_sizes(path: Path) -> dict[str, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sizes: dict[str, int] = {}
    stack: list[str] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                stack.append(child.name)
                walk(child)
                stack.pop()
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = ".".join(stack + [child.name])
                end = child.end_lineno or child.lineno
                sizes[qualname] = end - child.lineno + 1
                stack.append(child.name)
                walk(child)
                stack.pop()
            else:
                walk(child)

    walk(tree)
    return sizes


def budget_violations(modules: dict[str, Path], budgets: dict, root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    module_cap = int(budgets["module_loc"])
    function_cap = int(budgets["function_loc"])
    module_baseline = {str(k): int(v) for k, v in budgets.get("module_baseline", {}).items()}
    function_baseline = {str(k): int(v) for k, v in budgets.get("function_baseline", {}).items()}

    observed_modules = {path.relative_to(root).as_posix(): module_loc(path) for path in modules.values()}
    for rel, size in sorted(observed_modules.items()):
        if rel in module_baseline:
            if size > module_baseline[rel]:
                errors.append(f"{rel}: {size} lines grew past baseline {module_baseline[rel]}")
            elif size <= module_cap:
                errors.append(
                    f"{rel}: {size} lines is within the {module_cap}-line cap; remove the stale baseline entry"
                )
        elif size > module_cap:
            errors.append(f"{rel}: {size} lines exceeds the {module_cap}-line module cap")
    for rel in sorted(set(module_baseline) - set(observed_modules)):
        errors.append(f"{rel}: baseline entry points at a missing module")

    observed_functions: dict[str, int] = {}
    for path in modules.values():
        rel = path.relative_to(root).as_posix()
        for qualname, size in function_sizes(path).items():
            observed_functions[f"{rel}::{qualname}"] = size
    for key, size in sorted(observed_functions.items()):
        if key in function_baseline:
            if size > function_baseline[key]:
                errors.append(f"{key}: {size} lines grew past baseline {function_baseline[key]}")
            elif size <= function_cap:
                errors.append(
                    f"{key}: {size} lines is within the {function_cap}-line cap; remove the stale baseline entry"
                )
        elif size > function_cap:
            errors.append(f"{key}: {size} lines exceeds the {function_cap}-line function cap")
    for key in sorted(set(function_baseline) - set(observed_functions)):
        errors.append(f"{key}: baseline entry points at a missing function")
    return errors


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def validate_manifest(manifest: dict, modules: dict[str, Path]) -> list[str]:
    errors: list[str] = []
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        return ["manifest has no capabilities list"]

    rel_paths = {path.relative_to(ROOT).as_posix() for path in modules.values()}
    seen_ids: set[str] = set()
    owners: dict[str, str] = {}
    for cap in capabilities:
        cap_id = str(cap.get("id", "")).strip()
        if not cap_id:
            errors.append("capability with empty id")
            continue
        if cap_id in seen_ids:
            errors.append(f"duplicate capability id: {cap_id}")
        seen_ids.add(cap_id)
        if not str(cap.get("summary", "")).strip():
            errors.append(f"{cap_id}: missing summary")
        owns = cap.get("owns") or []
        tests = cap.get("tests") or []
        if not isinstance(owns, list) or not owns:
            errors.append(f"{cap_id}: owns must be a non-empty list")
        if not isinstance(tests, list) or not tests:
            errors.append(f"{cap_id}: tests must be a non-empty list")
        for rel in owns:
            if rel not in rel_paths:
                errors.append(f"{cap_id}: owns unknown runtime module {rel}")
                continue
            if rel in owners:
                errors.append(f"{rel}: owned by both {owners[rel]} and {cap_id}")
            owners[rel] = cap_id
        for rel in tests:
            test_path = ROOT / rel
            if not test_path.is_file():
                errors.append(f"{cap_id}: test file does not exist: {rel}")
            elif not rel.startswith("tests/"):
                errors.append(f"{cap_id}: test path must live under tests/: {rel}")

    for rel in sorted(rel_paths - set(owners)):
        errors.append(f"runtime module has no capability owner: {rel}")

    for rel in sorted(manifest.get("budgets", {}).get("module_baseline", {})):
        if rel not in rel_paths:
            errors.append(f"module baseline points at an unknown module: {rel}")
    for key in sorted(manifest.get("budgets", {}).get("function_baseline", {})):
        rel = key.split("::", 1)[0]
        if rel not in rel_paths:
            errors.append(f"function baseline points at an unknown module: {key}")
    return errors


def capability_by_id(manifest: dict) -> dict[str, dict]:
    return {cap["id"]: cap for cap in manifest["capabilities"]}


def module_owners(manifest: dict) -> dict[str, str]:
    return {rel: cap["id"] for cap in manifest["capabilities"] for rel in cap["owns"]}


def render_capability_section(manifest: dict) -> str:
    lines = [
        "## Capability map",
        "",
        "Generated from `docs/architecture/capabilities.json` by `scripts/architecture.py docs`.",
        "Do not edit this section by hand.",
        "",
    ]
    for cap in manifest["capabilities"]:
        lines.append(f"### `{cap['id']}`")
        lines.append("")
        lines.append(str(cap["summary"]))
        lines.append("")
        lines.append("- **Owned modules:** " + ", ".join(f"`{rel}`" for rel in cap["owns"]))
        if cap.get("entry_points"):
            lines.append("- **Entry points:** " + ", ".join(f"`{item}`" for item in cap["entry_points"]))
        lines.append("- **Focused tests:** " + ", ".join(f"`{rel}`" for rel in cap["tests"]))
        command = "uv run python scripts/ci_pytest.py " + " ".join(cap["tests"]) + " -q"
        lines.append(f"- **Verify:** `{command}`")
        lines.append("")
    return "\n".join(lines)


def doc_section() -> str:
    text = DOC_PATH.read_text(encoding="utf-8")
    if DOC_START not in text or DOC_END not in text:
        raise ValueError(f"{DOC_PATH} is missing {DOC_START} / {DOC_END} markers")
    start = text.index(DOC_START) + len(DOC_START)
    end = text.index(DOC_END)
    return text[start:end]


def write_doc(manifest: dict) -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    if DOC_START not in text or DOC_END not in text:
        raise ValueError(f"{DOC_PATH} is missing {DOC_START} / {DOC_END} markers")
    start = text.index(DOC_START) + len(DOC_START)
    end = text.index(DOC_END)
    replacement = "\n" + render_capability_section(manifest) + "\n"
    DOC_PATH.write_text(text[:start] + replacement + text[end:], encoding="utf-8", newline="\n")


def check_docs(manifest: dict) -> list[str]:
    try:
        actual = doc_section()
    except ValueError as exc:
        return [str(exc)]
    expected = "\n" + render_capability_section(manifest) + "\n"
    if actual != expected:
        return ["docs/architecture/README.md capability map is stale; run: uv run python scripts/architecture.py docs"]
    return []


def check() -> int:
    manifest = load_manifest()
    modules = module_map()
    errors: list[str] = []
    errors.extend(validate_manifest(manifest, modules))
    refs = collect_imports(modules)
    errors.extend(boundary_violations(refs, manifest.get("boundaries", [])))
    errors.extend(private_import_violations(refs))
    errors.extend(budget_violations(modules, manifest.get("budgets", {})))
    errors.extend(check_docs(manifest))
    if errors:
        print("architecture contract: FAILED", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(
        f"architecture contract: OK ({len(modules)} runtime modules, "
        f"{len(manifest['capabilities'])} capabilities, {len(refs)} internal imports)"
    )
    return 0


def _git_lines(args: list[str]) -> list[str]:
    try:
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)
    except OSError:
        return []
    if result.returncode != 0:
        print(f"warning: {' '.join(args)} failed; continuing without that diff", file=sys.stderr)
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def changed_files(base: str | None) -> list[str]:
    if base:
        files = _git_lines(["git", "diff", "--name-only", "--diff-filter=ACMRT", f"{base}...HEAD"])
    else:
        files = _git_lines(["git", "diff", "--name-only", "HEAD"])
        files.extend(_git_lines(["git", "ls-files", "--others", "--exclude-standard"]))
    normalized = []
    for rel in files:
        rel = rel.replace("\\", "/")
        if rel.startswith("./"):
            rel = rel[2:]
        normalized.append(rel)
    return sorted(set(normalized))


def _reverse_graph(refs: list[ImportRef]) -> dict[str, set[str]]:
    reverse: dict[str, set[str]] = {}
    for ref in refs:
        reverse.setdefault(ref.target, set()).add(ref.source)
    return reverse


def impact(base: str | None, explicit: list[str] | None) -> int:
    manifest = load_manifest()
    modules = module_map()
    path_to_module = {path.relative_to(ROOT).as_posix(): name for name, path in modules.items()}
    module_to_path = {name: path.relative_to(ROOT).as_posix() for name, path in modules.items()}
    owners = module_owners(manifest)
    caps = capability_by_id(manifest)
    reverse = _reverse_graph(collect_imports(modules))

    files = explicit or changed_files(base)
    if not files:
        print("No changed files detected. Nothing to map.")
        return 0

    impacted: set[str] = set()
    python_tests: set[str] = set()
    frontend_tests: set[str] = set()
    full_suite = False

    for rel in files:
        if rel.startswith("tests/") and rel.endswith(".py"):
            python_tests.add(rel)
        if rel.startswith("tests/frontend/") and rel.endswith(".test.js"):
            frontend_tests.add(rel)
        if rel in path_to_module:
            impacted.add(path_to_module[rel])
        if rel.startswith(FRONTEND_JS) and rel.endswith(".js"):
            stem = Path(rel).stem
            candidate = FRONTEND_TESTS / f"{stem}.test.js"
            if (ROOT / candidate).is_file():
                frontend_tests.add(candidate.as_posix())
            else:
                full_suite = True
        if rel.startswith("frontend/templates/") or rel.startswith("frontend/static/css/"):
            full_suite = True
        if rel in {
            "docs/architecture/capabilities.json",
            "docs/architecture/README.md",
            "scripts/architecture.py",
            "pyproject.toml",
            "uv.lock",
            "package.json",
            "package-lock.json",
            ".github/workflows/ci.yml",
        }:
            full_suite = True

    direct = set(impacted)
    for module in direct:
        impacted |= reverse.get(module, set())
    impacted |= direct

    affected_caps: set[str] = set()
    for module in impacted:
        cap_id = owners.get(module_to_path.get(module, ""))
        if cap_id:
            affected_caps.add(cap_id)
    for rel in files:
        for cap in manifest["capabilities"]:
            if rel in cap["tests"]:
                affected_caps.add(cap["id"])

    for cap_id in sorted(affected_caps):
        python_tests.update(caps[cap_id]["tests"])

    print("Architecture impact")
    print("-------------------")
    print("Changed files: " + (", ".join(files) if files else "(none)"))
    print("Affected capabilities: " + (", ".join(sorted(affected_caps)) if affected_caps else "(none)"))
    print("")

    if python_tests:
        command = "uv run python scripts/ci_pytest.py " + " ".join(sorted(python_tests)) + " -q"
        print("Focused Python verification:")
        print(f"  {command}")
    else:
        print("Focused Python verification: no mapped tests; inspect the capability map.")
    if frontend_tests:
        print("Focused frontend verification:")
        print(f"  npm test -- {' '.join(sorted(frontend_tests))}")
    if full_suite or not python_tests:
        print("Broad verification:")
        print("  uv run python scripts/ci_pytest.py tests/ -q")
        print("  npm test")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = [
            "## Test impact",
            "",
            "**Changed files:** " + (", ".join(f"`{rel}`" for rel in files) if files else "(none)"),
            "",
            "**Affected capabilities:** " + (", ".join(f"`{cap}`" for cap in sorted(affected_caps)) or "(none)"),
            "",
        ]
        if python_tests:
            lines.append("**Focused Python verification:**")
            lines.append("")
            lines.append("```bash")
            lines.append(command)
            lines.append("```")
        if frontend_tests:
            lines.append("**Focused frontend verification:**")
            lines.append("")
            lines.append("```bash")
            lines.append(f"npm test -- {' '.join(sorted(frontend_tests))}")
            lines.append("```")
        if full_suite or not python_tests:
            lines.append("**Broad verification:**")
            lines.append("")
            lines.append("```bash")
            lines.append("uv run python scripts/ci_pytest.py tests/ -q")
            lines.append("npm test")
            lines.append("```")
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="validate the architecture contract")
    sub.add_parser("docs", help="regenerate the capability section in the architecture README")
    impact_parser = sub.add_parser("impact", help="map changed files to capabilities and focused tests")
    impact_parser.add_argument("--base", help="git revision to diff against (e.g. HEAD^1)")
    impact_parser.add_argument("--files", nargs="+", help="explicit changed file paths")
    args = parser.parse_args(argv)

    if args.command == "check":
        return check()
    if args.command == "docs":
        manifest = load_manifest()
        write_doc(manifest)
        print(f"updated {DOC_PATH.relative_to(ROOT).as_posix()}")
        return 0
    if args.command == "impact":
        return impact(args.base, args.files)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
