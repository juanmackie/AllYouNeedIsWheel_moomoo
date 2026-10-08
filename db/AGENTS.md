<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# Database DOX

## Purpose

`db/` owns SQLite schema creation, migrations, connection pooling, and repositories for persisted earnings, IV history, trade events, option chains, portfolio snapshots, and recommendation data.

## Ownership

- `schema.py` owns `SCHEMA_VERSION`, table creation, and migrations. Schema v10 added `option_fills` and `account_cash_flows` (additive, idempotent). Schema v12 added `iv_history.strike` (additive, nullable). Schema v13 adds durable `wheel_cycles`, `run_candidates`, and `shadow_outcomes` tables. `option_fills` and `account_cash_flows` are retained for attribution and are exempt from automatic pruning; `run_metadata`, `portfolio_snapshots`, and `trade_events` retain 1,200 days.
- `database.py` owns the higher-level database facade.
- Repository modules own focused persistence behavior for their table/domain.
- `portfolio_snapshots_repository.py` owns one-snapshot-per-run equity history (`portfolio_snapshots`, schema v7); rows join to `run_metadata` via `run_id` and feed `/api/portfolio/history` plus position-diff trade-event inference.
- `fills_repository.py` owns broker-verified outcome evidence (`option_fills`, `account_cash_flows`, schema v10): fills are keyed by the broker deal id (`fill_id UNIQUE`) for idempotent re-ingest, cash flows by `cashflow_id`; all rows are scoped by `env`/`account_id` via the shared `_identity_where` convention. `fees` is NULL until the order-level fee query resolves it — unknown fees are distinct from zero fees.
- `account_cash_flows` also stores owner-declared goal deposits/withdrawals under `owner:<uuid>` IDs. Their deletion must require the active `env` and `account_id`; broker rows remain untouched by owner-flow editing.
- `wheel_cycles_repository.py` owns the account-scoped materialized wheel fold. Writes are idempotent by `cycle_id`; reads use the same environment/account identity scope as fills and trade events.
- `run_candidates_repository.py` archives hard-gate-passing per-run candidates atomically with snapshot publish; `shadow_outcomes_repository.py` stores immutable settled observations joined by candidate ID. Neither store mutates the published snapshot JSON.
- `database.py::get_run_snapshots` is the read-only outcome-history view over `run_metadata` (newest first, identity-scoped); run snapshots are publish-once immutable, so historical recommendations are never relabeled.
- `sqlite_pool.py` owns pooled connection behavior.
- `backup.py` owns WAL-safe SQLite database backups, daily/monthly retention,
  and archive integrity checks. The API layer invokes it only after a run
  snapshot has been successfully published; backup failure is logged and does
  not relabel or invalidate that published run. Default archives live in the
  ignored `backups/` directory beside the configured database.

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
- For backup behavior, run `pytest tests/test_database.py -k backup`.

## Child DOX Index

No child DOX files yet.

