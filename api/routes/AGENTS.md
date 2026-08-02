<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# API Routes DOX

## Purpose

`api/routes/` exposes Flask blueprints for portfolio, options, alerts, signals, earnings, macro, risk, ledger, roll-pressure, llm, and source-policy endpoints.

## Ownership

- Own HTTP method/path contracts, query/body validation, response status codes, and JSON shape.
- Delegate durable business logic to `api.services`, `core`, or `db` rather than duplicating calculations.

## Local Contracts

- Do not perform broker or market-data calls directly if a service already owns that workflow.
- Keep route error handling explicit and user-readable.
- Keep source-policy metadata visible for endpoints that combine Moomoo, watchlist, social, or third-party data.
- Do not add routes that imply autonomous order execution.
- `GET /api/options/top-recommendations` supports screener-override query params: `csp_min_otm_pct`, `csp_max_otm_pct`, `csp_min_dte`, `csp_max_dte`, `csp_target_delta`, `min_volatility_pct`, `min_csp_buying_power`. These are passed through as `screener_overrides` to the generation service and included in the cache key.

## Work Guidance

- Reuse helpers from `api/routes/utils.py` for common validation/formatting when applicable.
- Match existing response naming conventions in neighboring routes.
- Keep route modules import-safe; tests should be able to import them without starting background jobs or network calls.

## Verification

- Run the specific route test for changed endpoints (e.g. `tests/test_routes_earnings.py` for earnings routes).
- For validation changes, run `pytest tests/test_routes_validation.py`.
- For portfolio/options route behavior, run `pytest tests/test_routes_portfolio.py tests/test_routes_options.py`.

## Child DOX Index

No child DOX files yet.

