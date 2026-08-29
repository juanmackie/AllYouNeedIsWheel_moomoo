<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# Frontend JavaScript DOX

## Purpose

`frontend/static/js/` owns browser-side behavior for the one-screen dashboard: API clients, state models, rendering, calculations, alerts, and small visual helpers.

## Ownership

- `dashboard/` owns all dashboard widgets: run strip (`run-strip.js`, `api-run.js`), options table (`options-table-*.js`), top recommendations/preset selector/copy tickets (`top-recommendations.js`), position monitor + account panels (`account.js`, `weekly-income.js`, `dashboard-cash.js`), growth panel (`growth-panel.js`), and watchlist panel (`watchlist-panel.js`).
- `utils/` owns shared formatting (`formatters.js` exports `escapeHtml`), alerts, sparklines, and state helpers.

## Local Contracts

- Treat API responses as the source for portfolio/options data; localStorage is only a UI preference/input cache.
- Keep calculations that affect displayed signal decisions aligned with backend services and tests.
- Prefer service-vetted executable-bid fields (`annualized_return`, `capital_velocity_per_day`, `bid_premium_per_contract`, `premium_velocity_per_day`) for signal display. Midpoint is a separately labelled, non-guaranteed limit target; do not recompute backend ranking in the browser.
- Preserve loading, empty, error, and stale states for networked widgets.
- Empty states for signal panels should surface the dominant blockers or scan diagnostics when the payload provides them.
- Do not add hidden trading execution calls from UI controls.

## Work Guidance

- Prefer small named functions and module exports that Vitest can import.
- Reuse existing formatting helpers for currency, percentages, dates, and alert display.
- Keep DOM selectors stable when tests depend on them.
- Avoid large cross-feature files; place behavior near the feature folder that owns it.
- `api.js` re-exports the API surface from `api-core.js` / `api-options.js` / `api-portfolio.js` / `api-run.js`; import feature modules through it or directly, but keep cross-feature imports acyclic.
- Use `escapeHtml` from `utils/formatters.js` for any API-fed `innerHTML` assignment; never interpolate raw strings fetched over HTTP.
- `top-recommendations.js` consumes the immutable `/api/run` snapshot. Any `copy_eligible` candidate (qualified or marginal broker-sourced signal with positive capacity) can copy a manual ticket; when the run is not live-tradeable the ticket is staged for US open with last-quote labelling and event-risk warnings. The parallel cached recommendation endpoint and browser-side rank math are retired.

## Verification

- Run the matching `tests/frontend/*.test.js` file for changed modules.
- Run `npm test` for shared utility or options-table changes.

## Child DOX Index

No child DOX files yet.
