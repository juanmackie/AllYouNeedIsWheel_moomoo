# API Services DOX

## Purpose

`api/services/` owns application-level workflows: Moomoo-backed portfolio/options data, recommendations, watchlists, signal overlays, market/macro enrichment, LLM advice, and external-provider integration.

## Ownership

- Own orchestration and adaptation between external data sources, `core` decision helpers, repositories, and route responses.
- Keep durable calculations in `core` when they are independent of Flask or external provider plumbing.

## Local Contracts

- Moomoo/OpenD remains authoritative for portfolio, positions, cash, account state, and option-chain truth.
- Optional providers such as yfinance, FRED, Alpha Vantage, TradingView, Ape Wisdom, OpenBB, or LLMs may enrich or widen context, but must not silently replace broker truth.
- Services must degrade gracefully when external providers fail, rate-limit, or return partial data.
- Keep caches and TTLs explicit so stale market data is not presented as fresh.
- Do not introduce production mocks or simulated account data.

## Work Guidance

- Prefer dependency injection for config, database, and provider clients in code that needs isolated tests.
- Preserve source metadata and warnings when signals are widened or enriched by non-Moomoo sources.
- Keep recommendation, signal, and risk outputs deterministic enough for regression tests.
- Avoid circular imports back into route modules.

## Verification

- Run the feature-specific service tests for changed files.
- For recommendation/scoring-adjacent service changes, run `pytest tests/test_recommendations.py tests/test_score_regression.py`.
- For source expansion or Catalyst Watch, run `pytest tests/test_catalyst_flow.py tests/test_catalyst_watch_route.py tests/test_apewisdom_service.py`.

## Child DOX Index

No child DOX files yet.

