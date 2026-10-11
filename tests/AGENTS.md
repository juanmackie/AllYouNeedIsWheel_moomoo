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
- `test_app_startup.py` verifies recovery occurs only during explicit factory startup; `test_run_model.py` covers interrupted attempts, settled states, and preservation of an active refresh worker and immutable snapshots.
- `test_recommendations.py` verifies complete watchlist-union scanning and deterministic capital-return-on-deployed-capital ordering with executable-bid velocity as tie-break (quality/event tiers are display-only risk info and never order); infeasible unions publish `planning` rather than truncating.
- `test_full_watchlist_discovery.py` exercises the real broker adapter, SQLite directory, and scorer with synthetic query-only SDK responses: 69-symbol cold/warm/restart coverage, zero-cash review-only picks, fitting-first lane order, empty successful discovery, 400-code batches, date/window boundaries, over-budget planning, and the single retry for failed discovery or missing quotes (transient recovery, persistent failure with a named symbol and reason, no retry when every symbol fails). Unknown/stale prices never prune discovery. `test_portfolio_context.py` covers net cash provenance through live and cached contexts.
- Use Arrange-Act-Assert when conventional; cover success/failure/boundary; report skipped/flaky/blockers explicitly, alongside changes, verification, assumptions, and risks.
- Import-side-effect tests restore original module objects after forcing fresh imports, so already-imported scorers retain the same clock bindings patched by later tests.
- `test_raw_scan_replay.py` exercises all three preset profiles through settings, scan, immutable publication, and copy validation, including persisted Aggressive at service startup and cash/exposure limits.
- `test_run_model.py` verifies a blocked optional event provider cannot stall scan publication or duplicate in-flight ticker work across refreshes; service deadline, cancellation, and worker limits are covered in `test_iv_earnings_service.py`.
- `test_probe_option_chain_cost.py` verifies the manual discovery/quote capability gate using synthetic query-only frames: missing quote fields and broker failures cannot produce a go, and fewer than 400 codes leaves capacity unmeasured.

## Verification

- Python: `pytest tests/`
- Frontend: `npm test`
- Focus first, then broaden for shared contract changes.

## Child DOX Index

- `frontend/AGENTS.md` - Vitest browser-module tests.
- `e2e/AGENTS.md` - Browser smoke tests.
- `fixtures/AGENTS.md` - Shared deterministic scenarios.
