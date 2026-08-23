<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# GitHub Workflow DOX

## Purpose

`.github/` owns repository automation, currently GitHub Actions CI and secret scanning.

## Ownership

- `workflows/ci.yml` owns the single Windows job: dependency sync (uv), Ruff check + format check, pytest run via `scripts/ci_pytest.py`, frontend Vitest (`npm ci` + `npm test`), generated-artifact hygiene scans, and a fresh-clone import smoke.

## Local Contracts

- CI must remain deterministic without live Moomoo/OpenD connectivity, broker credentials, paid market-data accounts, or local databases.
- Keep secret scanning active and redact output.
- Do not commit or reference real credentials, account identifiers, or private local paths in workflows.
- Keep workflow checks aligned with `requirements.txt`, `pyproject.toml`, and `package.json` when those contracts change.

## Work Guidance

- Prefer narrow CI additions that catch real regressions without making local iteration painful.
- If frontend tests are added to CI, install Node dependencies from `package-lock.json` and run the same `npm test` contract used locally.
- Preserve branch triggers unless the repository release flow intentionally changes.

## Verification

- For workflow edits, inspect YAML and run the closest local equivalent: `pytest tests/`, `ruff check . --select=F`, or `npm test` as relevant.
- If available, use GitHub Actions status after pushing to confirm the workflow path.

## Child DOX Index

No child DOX files yet.
