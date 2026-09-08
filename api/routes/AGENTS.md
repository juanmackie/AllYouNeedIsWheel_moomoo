<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# API Routes DOX

## Purpose

`api/routes/` exposes Flask blueprints for run, settings, watchlist, options, portfolio (incl. alerts), roll-pressure, earnings, ledger, and source-policy endpoints.

## Ownership

- Own HTTP method/path contracts, query/body validation, response status codes, and JSON shape.
- Delegate durable business logic to `api.services`, `core`, or `db` rather than duplicating calculations.

## Local Contracts

- Do not perform broker or market-data calls directly if a service already owns that workflow.
- Keep route error handling explicit and user-readable.
- Keep source-policy metadata visible for endpoints that combine Moomoo, watchlist, or optional third-party data.
- Do not add routes that imply autonomous order execution.
- `/api/run` and `/api/run/refresh` are the sole dashboard shortlist workflow. `GET /api/run` returns the immutable last snapshot plus a read-time effective `tradeable`/`stale` view; refresh is serialized and never overwrites the last good snapshot.
- `GET /api/options/analytics/outcomes` serves broker-verified outcome summaries (totals + groups by preset/DTE bucket/ticker/event tier, each with sample size, coverage %, unknown count, and per-outcome fill drill-down) from local SQLite only — it never gates on live OpenD. `POST /api/options/analytics/outcomes/ingest` is the only broker-backed endpoint of the pair: it gates on the shared OpenD probe, rate-limits tightly (6/min; days ≤ 90 per the SDK history window), and only triggers query-only ingestion.

## Work Guidance

- Reuse helpers from `api/routes/utils.py` for common validation/formatting when applicable.
- Match existing response naming conventions in neighboring routes.
- Keep route modules import-safe; tests should be able to import them without starting background jobs or network calls.

## Verification

- Run the specific route test for changed endpoints (e.g. `tests/test_routes_earnings.py` for earnings routes).
- For validation changes, run the affected `tests/test_routes_*.py` files.
- For portfolio/options route behavior, run `pytest tests/test_routes_portfolio.py tests/test_routes_options.py`.

## Child DOX Index

No child DOX files yet.

