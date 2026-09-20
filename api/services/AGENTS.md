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
- `RecommendationEngine` is the backend shortlist authority: watchlist coverage is complete-or-planning, executable-return-on-deployed-capital ranking with executable-bid premium velocity as tie-break (quality/event tiers are display-only risk info and never order), and candidates carry Moomoo quote evidence plus safe recommended quantity. `/api/run` publishes the immutable result.
- Closed-market CSP and covered-call scans request the freshest available Moomoo/OpenD last-session chain first, then use persisted broker snapshots only as fallback. Closed results remain planning/staged and never bypass freshness or read-only gates.
- `fills_service.py` owns read-only ingestion of broker fills, order fees, and account cash flows for outcome attribution. It only queries (`get_history_deals` / `get_order_fees` / `get_cash_flow`), stamps rows with the opaque account identity from `resolve_portfolio_identity`, persists idempotently via `db.fills_repository`, and allocates order fees across fills proportionally to gross fill value. Attribution math stays in pure `core.outcome_attribution`; fills are journal/display evidence only and never gate or reorder recommendations.
- `iv_earnings_service.py` owns the IV environment read: rank and percentile both come from a one-sample-per-day closest-to-ATM series over a 1-year window (`IV_RANK_WINDOW_DAYS`, minimum `IV_RANK_MIN_DAYS` daily samples), never from the raw per-contract rows a scan writes. Fewer samples than the minimum reports `insufficient_history` and applies a zero adjustment rather than a fabricated neutral rank. Both values are display-only and must never gate or reorder candidates. The service also derives `days_to_ex_dividend` (market clock, per C13) for the exit playbook, and `record_iv_data` deliberately does not write the scoring cache — seeding it would publish one contract's rank as the whole ticker's environment for the cache TTL.
- `outcome_service.py` serves broker-verified outcome summaries on top of ingested fills plus published run snapshots: quoted (recommended) credit vs. filled credit, net-of-fee results, capital-days, and net P&L / capital-day via the pure `core.outcome_attribution` helpers, grouped by preset, DTE bucket, ticker, and event tier. Every aggregate exposes sample size, coverage %, and unknown-outcome count; each outcome record carries its supporting fills for drill-down. Matching keys on (ticker, expiration, option_type, numeric strike) so string-formatting drift cannot split one contract. Unknown fees mean an unknown outcome (never zero), unmatched fills are surfaced without a fabricated quoted price, and tiers remain display-only — this service never gates or reorders anything.

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
