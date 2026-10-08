"""WAL-safe, local SQLite backups for the wheel history database."""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_DAILY_NAME = re.compile(r"^wheel-(\d{8})\.sqlite3$")
_MONTHLY_NAME = re.compile(r"^wheel-(\d{6})\.sqlite3$")


def _write_sqlite_backup(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.tmp")
    if temporary.exists():
        temporary.unlink()
    source_conn = sqlite3.connect(str(source), timeout=30)
    backup_conn = sqlite3.connect(str(temporary), timeout=30)
    try:
        source_conn.backup(backup_conn)
        check = backup_conn.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise sqlite3.DatabaseError("SQLite backup failed quick_check")
        backup_conn.commit()
    except Exception:
        backup_conn.close()
        source_conn.close()
        temporary.unlink(missing_ok=True)
        raise
    else:
        backup_conn.close()
        source_conn.close()
    os.replace(temporary, destination)


def _prune(directory: Path, pattern: re.Pattern, keep: int) -> None:
    files = []
    for candidate in directory.iterdir():
        match = pattern.fullmatch(candidate.name)
        if candidate.is_file() and match:
            files.append((match.group(1), candidate))
    files.sort(reverse=True)
    for _, stale in files[max(1, int(keep)) :]:
        stale.unlink()


def backup_database(
    database_path,
    backup_root=None,
    now: datetime | None = None,
    daily_keep: int = 14,
    monthly_keep: int = 36,
) -> dict:
    """Create one daily and one first-of-month copy after a successful publish.

    Backups use ``sqlite3.Connection.backup`` so committed WAL contents are
    included. Existing date targets are left untouched, making repeated calls
    on one day idempotent. By default, backups live beside the configured DB.
    """
    source = Path(database_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Database not found: {source}")
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)

    root = Path(backup_root).resolve() if backup_root is not None else source.parent / "backups"
    if source == root or root in source.parents:
        raise ValueError("Backup directory cannot contain the source database")
    daily_dir = root / "daily"
    monthly_dir = root / "monthly"
    daily_dir.mkdir(parents=True, exist_ok=True)
    monthly_dir.mkdir(parents=True, exist_ok=True)

    daily = daily_dir / f"wheel-{moment:%Y%m%d}.sqlite3"
    monthly = monthly_dir / f"wheel-{moment:%Y%m}.sqlite3"
    daily_created = not daily.exists()
    monthly_created = not monthly.exists()
    if daily_created:
        _write_sqlite_backup(source, daily)
    if monthly_created:
        _write_sqlite_backup(source, monthly)

    _prune(daily_dir, _DAILY_NAME, daily_keep)
    _prune(monthly_dir, _MONTHLY_NAME, monthly_keep)
    return {
        "daily_backup": str(daily),
        "monthly_backup": str(monthly),
        "daily_created": daily_created,
        "monthly_created": monthly_created,
    }
