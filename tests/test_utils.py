import ast
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core import utils


def _call_path(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_path(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return f"{_call_path(node.func)}()"
    return ""


def _datetime_aliases(tree):
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "datetime":
            for imported in node.names:
                if imported.name in {"date", "datetime"}:
                    aliases[imported.asname or imported.name] = imported.name
        elif isinstance(node, ast.Import):
            for imported in node.names:
                if imported.name == "datetime":
                    aliases[imported.asname or "datetime"] = "datetime"
    return aliases


def test_application_code_uses_market_date_instead_of_host_local_date():
    repository_root = Path(__file__).resolve().parents[1]
    violations = []
    for source_root in ("api", "core", "db"):
        for path in (repository_root / source_root).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            aliases = _datetime_aliases(tree)
            local_now_names = set()
            for node in ast.walk(tree):
                value = node.value if isinstance(node, ast.Assign) else None
                is_naive_now = (
                    isinstance(value, ast.Call)
                    and _call_path(value.func).replace("()", "") in {"datetime.now", "dt.datetime.now"}
                    and not value.args
                    and not value.keywords
                )
                if is_naive_now:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            local_now_names.add(target.id)
                if not isinstance(node, ast.Call):
                    continue
                parts = _call_path(node.func).replace("()", "").split(".")
                if parts and parts[0] in aliases:
                    parts[0] = aliases[parts[0]]
                call_path = ".".join(parts)
                midnight_local_now = call_path.endswith("datetime.now.replace") and any(
                    keyword.arg == "hour" and isinstance(keyword.value, ast.Constant) and keyword.value.value == 0
                    for keyword in node.keywords
                )
                receiver_is_local_now = (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in local_now_names
                )
                local_calendar_method = receiver_is_local_now and node.func.attr in {"date", "weekday"}
                local_midnight_method = (
                    receiver_is_local_now
                    and node.func.attr == "replace"
                    and any(
                        keyword.arg == "hour" and isinstance(keyword.value, ast.Constant) and keyword.value.value == 0
                        for keyword in node.keywords
                    )
                )
                if (
                    call_path.endswith("date.today")
                    or call_path.endswith("datetime.now.date")
                    or call_path.endswith("datetime.now.weekday")
                    or midnight_local_now
                    or local_calendar_method
                    or local_midnight_method
                ):
                    violations.append(f"{path.relative_to(repository_root)}:{node.lineno}: {call_path}")

    assert not violations, "Use core.utils.market_now() for market dates:\n" + "\n".join(violations)


def test_get_closest_friday_uses_us_market_date():
    market_datetime = datetime(2026, 12, 18, 15, 30, tzinfo=ZoneInfo("America/New_York"))
    with patch.object(utils, "market_now", return_value=market_datetime) as market_clock:
        result = utils.get_closest_friday()

    assert result == date(2026, 12, 18)
    market_clock.assert_called_once_with()


def test_get_next_monthly_expiration_uses_us_market_date():
    market_datetime = datetime(2026, 12, 18, 15, 30, tzinfo=ZoneInfo("America/New_York"))
    with patch.object(utils, "market_now", return_value=market_datetime) as market_clock:
        result = utils.get_next_monthly_expiration()

    assert result == "20261218"
    market_clock.assert_called_once_with()


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
