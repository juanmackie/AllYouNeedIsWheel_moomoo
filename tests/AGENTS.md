# Tests DOX

## Purpose

`tests/` owns verification for API routes, services, core decisions, database behavior, frontend modules, fixtures, and end-to-end smoke coverage.

## Ownership

- Top-level `test_*.py` files own Python unit and integration coverage.
- `tests/frontend/` owns Vitest coverage for browser JavaScript modules.
- `tests/e2e/` owns browser-level smoke checks.
- `tests/fixtures/` owns reusable deterministic test scenarios.
- `tests/README.md` owns the manual smoke checklist.

## Local Contracts

- Tests must stay deterministic and should not require live Moomoo/OpenD, real broker credentials, or paid market-data access unless explicitly marked/manual.
- Prefer fixtures and injected fakes over production mocks that leak into app behavior.
- Regression tests should pin scoring, signal, and route behavior that affects user trust or money-risk decisions.
- Keep frontend tests aligned with exported module functions and stable DOM contracts.

## Work Guidance

- Add focused tests near the feature or risk changed.
- Use `conftest.py` fixtures where shared app/database setup already exists.
- Keep scenario fixtures realistic but synthetic; do not include private account data.
- Update `tests/README.md` when manual smoke coverage changes.

## Verification

- Python: `pytest tests/`
- Frontend: `npm test`
- Focus first, then broaden for shared contract changes.

## Child DOX Index

- `frontend/AGENTS.md` - Vitest browser-module tests.
- `e2e/AGENTS.md` - Browser smoke tests.
- `fixtures/AGENTS.md` - Shared deterministic scenarios.

