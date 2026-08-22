<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# Database DOX

## Purpose

`db/` owns SQLite schema creation, migrations, connection pooling, and repositories for persisted earnings, IV history, trade events, option chains, portfolio snapshots, and recommendation data.

## Ownership

- `schema.py` owns `SCHEMA_VERSION`, table creation, and migrations.
- `database.py` owns the higher-level database facade.
- Repository modules own focused persistence behavior for their table/domain.
- `portfolio_snapshots_repository.py` owns one-snapshot-per-run equity history (`portfolio_snapshots`, schema v7); rows join to `run_metadata` via `run_id` and feed `/api/portfolio/history` plus position-diff trade-event inference.
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

