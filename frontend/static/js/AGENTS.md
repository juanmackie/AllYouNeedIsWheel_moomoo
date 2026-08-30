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

- `dashboard/` owns all dashboard widgets: run strip (`run-strip.js`, `api-run.js`), options table (`options-table-*.js`), top recommendations (`top-recommendations.js`), position monitor + account panels (`account.js`, `weekly-income.js`, `dashboard-cash.js`), growth panel (`growth-panel.js`), and watchlist panel (`watchlist-panel.js`).
- `utils/` owns shared formatting (`formatters.js` exports `escapeHtml`), alerts, sparklines, and state helpers.

## Local Contracts

- Treat API responses as the source for portfolio/options data; `localStorage` is only a UI preference/input cache.
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
