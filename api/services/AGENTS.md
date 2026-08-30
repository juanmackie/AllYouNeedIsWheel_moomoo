<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# API Services DOX

## Purpose

`api/services/` owns application-level workflows: Moomoo-backed portfolio/options data, the recommendation engine (backend shortlist authority), watchlists, IV/earnings enrichment (`iv_earnings_service` + `alpha_vantage_provider`), portfolio scoring/context, and roll diagnostics (`roll_diagnostics.py`).

## Ownership

- Own orchestration and adaptation between external data sources, `core` decision helpers, repositories, and route responses.
- Keep durable calculations in `core` when they are independent of Flask or external provider plumbing.

## Local Contracts

- Moomoo/OpenD remains authoritative for portfolio, positions, cash, account state, and option-chain truth.
- Optional providers such as yfinance (fallback quotes) and Alpha Vantage (bulk earnings calendar) may enrich or widen context, but must not silently replace broker truth.
- Services must degrade gracefully when external providers fail, rate-limit, or return partial data.
- Keep caches and TTLs explicit so stale market data is not presented as fresh.
- Do not introduce production mocks or simulated account data.
- When a signal can remain visible only as research-only data, keep that label and caveat explicit rather than silently dropping it.
- Scanner services should return blocker/diagnostic counts when useful so the UI can explain empty panels.
- `RecommendationEngine` is the backend shortlist authority: watchlist coverage is complete-or-planning, quality/event tiers precede executable-bid premium velocity, and candidates carry Moomoo quote evidence plus safe recommended quantity. `/api/run` publishes the immutable result.
- Closed-market CSP and covered-call scans request the freshest available Moomoo/OpenD last-session chain first, then use persisted broker snapshots only as fallback. Closed results remain planning/staged and never bypass freshness or read-only gates.

## Work Guidance

- Prefer dependency injection for config, database, and provider clients in code that needs isolated tests.
- Preserve source metadata and warnings when signals are widened or enriched by non-Moomoo sources.
- Keep recommendation, signal, and risk outputs deterministic enough for regression tests.
- Avoid circular imports back into route modules.

## Verification

- Run the feature-specific service tests for changed files.
- For recommendation/scoring-adjacent service changes, run `pytest tests/test_recommendations.py tests/test_score_regression.py`.
- For provider changes, run `pytest tests/test_routes_earnings.py` plus the matching provider test if one exists.

## Child DOX Index

No child DOX files yet.
