# Core DOX

## Purpose

`core/` owns broker connection helpers, scoring primitives, option decision logic, risk/evidence gates, scheduler/background utilities, caches, logging, and shared pure utilities.

## Ownership

- Own business-critical trading logic that should not depend on Flask.
- Own reusable decision functions for Wheel strategy signals, scoring factors, Greeks, and evidence-gated advice.
- Own premium velocity calculation — the primary ranking axis for all surfaced signals.
- Own OpenD connection lifecycle helpers and shared runtime utilities.

## Local Contracts

- Keep `core` independent from `api.routes` and Flask request context.
- Preserve live-trading safety assumptions and source-of-truth boundaries.
- Scoring changes must preserve or deliberately update the methodology documented in `SCORING.md`.
- Connection logic must avoid leaking handles and should be safe under repeated route/service calls.
- Scheduler/background code must be idempotent enough for Flask debug reloads and test imports.

## Work Guidance

- Prefer pure functions and small data transformations for scoring/risk logic.
- Keep thresholds, weights, and profile choices explicit and covered by regression tests.
- Decision helpers for read-only panels should prefer plain-English blockers/rationale and preserve the ability to surface research-only outcomes.
- Avoid import-time network calls, scheduler starts, or DB writes.
- Reuse `ticker_utils`, `ttl_cache`, `rate_limiter`, and logging helpers instead of local one-off versions.
- Scheduler callbacks should be injected from `app.py` rather than imported directly from `api`. Use the factory pattern (`app.py` → `wired_scheduler_providers`) to keep `core` import-free from Flask layers.
- Greek calculations that need option-chain data should accept a `chain_fetcher` callable parameter to avoid importing `api` services at module level.

## Verification

- Scoring/decision changes: run `pytest tests/test_wheel_decision.py tests/test_score_regression.py`.
- Connection changes: run `pytest tests/test_connection.py tests/test_import_side_effects.py`.
- Scheduler/cache/rate-limit changes: run the matching focused tests such as `tests/test_rate_limiter.py` or `tests/test_scan_ledger.py`.

## Child DOX Index

No child DOX files yet.
