# Database DOX

## Purpose

`db/` owns SQLite schema creation, migrations, connection pooling, and repositories for persisted recommendations, IV history, earnings, trade events, scan ledger, and playbook data.

## Ownership

- `schema.py` owns `SCHEMA_VERSION`, table creation, and migrations.
- `database.py` owns the higher-level database facade.
- Repository modules own focused persistence behavior for their table/domain.
- `sqlite_pool.py` owns pooled connection behavior.

## Local Contracts

- Every schema change must be backward-compatible through `migrate_database` and increment `SCHEMA_VERSION`.
- Migrations must be idempotent for existing local databases.
- Do not store secrets, broker credentials, or raw sensitive account snapshots unnecessarily.
- Repository methods should avoid hiding broad business decisions that belong in services or `core`.
- SQLite write paths must commit intentionally and keep connections scoped.

## Work Guidance

- Prefer repository methods over ad hoc SQL in services when the behavior is durable.
- Keep JSON-in-text columns documented by their repository/service usage.
- Avoid destructive migrations unless there is an explicit product decision and tests cover the transition.

## Verification

- Run `pytest tests/test_database.py` for schema, migration, or repository changes.
- Run feature tests that depend on the changed repository.
- For scan ledger persistence, run `pytest tests/test_scan_ledger.py`.

## Child DOX Index

No child DOX files yet.

