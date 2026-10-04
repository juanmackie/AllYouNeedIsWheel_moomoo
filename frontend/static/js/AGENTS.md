<!-- As-built 2026-08-02 consolidation. Updated 2026-08-02: theme preference contract. -->

# Frontend JavaScript DOX

## Purpose

`frontend/static/js/` owns browser-side behavior for the one-screen dashboard: API clients, state models, rendering, calculations, alerts, and small visual helpers.

## Theme Preference Contract (binding)

- `main.js` defines `window.cycleTheme()` (cycles `auto` → `dark` → `light` → `auto`) and writes `localStorage.setItem('ui-theme', ...)`.
- `main.js` initializes theme from `localStorage('ui-theme')` at `DOMContentLoaded` and sets `document.documentElement.setAttribute('data-theme', ...)`.
- Only `main.js` owns the theme preference logic. No other JS module should read/write `localStorage('ui-theme')` or manipulate `data-theme` directly.
- `base.html` includes `<button onclick="cycleTheme()">` in the right masthead meta; it must remain functional with zero additional dependencies.

## Ownership

- `dashboard/` owns all dashboard widgets: run strip (`run-strip.js`, `api-run.js`), active-watchlist foot (`active-watchlist.js`), options table (`options-table-*.js`), top recommendations (`top-recommendations.js`), position monitor + account panels (`account.js`, `weekly-income.js`, `dashboard-cash.js`), growth & outcome panels (`growth-panel.js`, `outcome-panel.js`), and watchlist panel (`watchlist-panel.js`).
- `utils/` owns shared formatting (`formatters.js` exports `escapeHtml`), alerts, sparklines, and state helpers.

## Local Contracts

- Treat API responses as the source for portfolio/options data; `localStorage` is only a UI preference/input cache.
- Keep calculations that affect displayed signal decisions aligned with backend services and tests.
- Prefer service-vetted executable-bid fields (`annualized_return`, `capital_velocity_per_day`, `bid_premium_per_contract`, `premium_velocity_per_day`) for signal display. Midpoint is a separately labelled, non-guaranteed limit target; do not recompute backend ranking in the browser.
- Preserve loading, empty, error, and stale states for networked widgets.
- `dashboard/run-strip.js` owns the visible run warning: failed attempts (including no snapshot), rejected refresh requests, API communication failures, and persisted `scan_infeasible` quota diagnostics. Render API reasons with `textContent`; retain previous results and clear warnings when their failure recovers. Complete closed-market planning alone is not an error.
- `dashboard/run-notifier.js` feeds both status and communication warnings from its single shared poll; warning rendering must not trigger another scan or an extra status request.
- Outcome panel reads `/api/options/analytics/outcomes` (local SQLite, no OpenD gate) and renders it through `escapeHtml`; the only broker action is the explicit `Pull broker fills` button (query-only `POST /api/options/analytics/outcomes/ingest`).
- Empty states for signal panels should surface the dominant blockers or scan diagnostics when the payload provides them.
- CSP cash-fit warnings are display-only: every underlying is assessed, unaffordable picks are review-only, and quota warnings refer to the contract-discovery budget rather than quote freshness.
- Do not add hidden trading execution calls from UI controls.

## Work Guidance

- Prefer small named functions and module exports that Vitest can import.
- Reuse existing formatting helpers for currency, percentages, dates, and alert display.
- Keep DOM selectors stable when tests depend on them.
- Avoid large cross-feature files; place behavior near the feature folder that owns it.
