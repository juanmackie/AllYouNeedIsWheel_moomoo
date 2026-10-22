<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# Database DOX

## Purpose

`db/` owns SQLite schema creation, migrations, connection pooling, and repositories for persisted earnings, IV history, trade events, option chains, portfolio snapshots, and recommendation data.

## Ownership

- `schema.py` owns `SCHEMA_VERSION`, table creation, and migrations. Schema v10 added `option_fills` and `account_cash_flows` (additive, idempotent). Schema v12 added `iv_history.strike` (additive, nullable) so IV rank/percentile can read a one-sample-per-day closest-to-ATM series instead of every scored contract; a NULL strike means moneyness unknown and the reader falls back to that day's median IV.
- `database.py` owns the higher-level database facade.
- Repository modules own focused persistence behavior for their table/domain.
- Schema v13 adds `option_contracts`; `OptionChainRepository.save_contracts/get_contracts` persist successfully discovered ranges (including empty ones), keyed by symbol, exact US market date, and inclusive window. Reads require complete range coverage and never reuse a previous date. Retention is seven days; retained historical rows are not current discovery evidence.
- `portfolio_snapshots_repository.py` owns one-snapshot-per-run equity history (`portfolio_snapshots`, schema v7); rows join to `run_metadata` via `run_id` and feed `/api/portfolio/history` plus position-diff trade-event inference.
- `fills_repository.py` owns broker-verified outcome evidence (`option_fills`, `account_cash_flows`, schema v10): fills are keyed by the broker deal id (`fill_id UNIQUE`) for idempotent re-ingest, cash flows by `cashflow_id`; all rows are scoped by `env`/`account_id` via the shared `_identity_where` convention. `fees` is NULL until the order-level fee query resolves it — unknown fees are distinct from zero fees. `database.py::get_cash_flow_coverage` / `set_cash_flow_coverage` keep the contiguous cash-flow backfill span (`earliest`/`latest` clearing dates) per opaque identity in the settings table, next to the fill coverage.
- `database.py::get_run_snapshots` is the read-only outcome-history view over `run_metadata` (newest first, identity-scoped); run snapshots are publish-once immutable, so historical recommendations are never relabeled.
- `sqlite_pool.py` owns pooled connection behavior.
- `scan_ledger_repository.py` owns scan-ledger table reads/writes; `core.scan_ledger` owns the pure entry shape and hashes. The repository may import `core`, never the reverse.

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
- For the fills/cash-flow repositories, run `pytest tests/test_fills_repository.py`.
- Run feature tests that depend on the changed repository.
- For scan ledger persistence, run `pytest tests/test_scan_ledger.py`.

## Child DOX Index

No child DOX files yet.


- Financial evidence is not age-deleted: `option_fills`, `account_cash_flows`, `portfolio_snapshots`, `run_metadata`, and `trade_events` (including owner-taken links) survive startup and cleanup. Only caches and operational diagnostics expire. This preserves attribution and the equity baseline, not just early credits. Database size can grow; keep private backups, never commit the database. Already-pruned records cannot be recovered by this policy change.
- Account-scoped fill coverage and the durable growth baseline use existing settings; snapshot schema 3 requires no database migration.
