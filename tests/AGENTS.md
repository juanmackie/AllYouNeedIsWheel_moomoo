<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# Tests DOX

## Purpose

`tests/` owns verification for API routes, services, core decisions, database behavior, frontend modules, fixtures, and end-to-end smoke coverage.

## Ownership

- Top-level `test_*.py` files own Python unit and integration coverage.
- `tests/frontend/` owns Vitest coverage for browser JavaScript modules (including `dashboard-safety.test.js` for XSS-safe rendering paths).
- `tests/e2e/` owns browser-level smoke checks.
- `tests/fixtures/` owns reusable deterministic test scenarios.
- `tests/README.md` owns the manual smoke checklist.

## Local Contracts

- Tests must stay deterministic and should not require live Moomoo/OpenD, real broker credentials, or paid market-data access unless explicitly marked/manual.
- Prefer fixtures and injected fakes over production mocks that leak into app behavior.
- Regression tests should pin scoring, signal, and route behavior that affects user trust or money-risk decisions.
- Keep frontend tests aligned with exported module functions and stable DOM contracts.

## Work Guidance

- Add focused tests near the feature or risk changed (e.g. `tests/test_api_config.py` for secret-key/CORS hardening, `tests/test_routes_earnings.py` for earnings routes, `tests/test_outcome_attribution.py` for pure outcome/capital-days attribution, `tests/test_fills_repository.py` for idempotent account-scoped fill/cash-flow persistence and the schema v10 migration, `tests/test_outcome_service.py` for outcome summary matching/aggregation, `tests/test_outcome_integration.py` for the no-broker end-to-end path (fake query-only broker connection → fills ingestion → real SQLite → outcome service → `/analytics/outcomes` routes, incl. the 6/min ingest rate limit), and `TestOutcomeAnalytics` in `tests/test_routes_options.py` for the `/analytics/outcomes` routes).
- Use `conftest.py` fixtures where shared app/database setup already exists.
- Keep scenario fixtures realistic but synthetic; do not include private account data.
- Update `tests/README.md` when manual smoke coverage changes.
- `test_recommendations.py` verifies complete watchlist-union scanning and deterministic capital-return-on-deployed-capital ordering with executable-bid velocity as tie-break (quality/event tiers are display-only risk info and never order); infeasible unions publish `planning` rather than truncating.
- Testing approach aligns with V10 §5: Arrange-Act-Assert when conventional; cover success/failure/boundary; report skipped/flaky/blockers explicitly; close with brief changed/verified/assumptions/risks (V10 §9).

## Verification

- Python: `pytest tests/`
- Frontend: `npm test`
- Focus first, then broaden for shared contract changes.

## Child DOX Index

- `frontend/AGENTS.md` - Vitest browser-module tests.
- `e2e/AGENTS.md` - Browser smoke tests.
- `fixtures/AGENTS.md` - Shared deterministic scenarios.

