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
- The operational run strip renders published times in UTC and the browser's local zone; its next-US-open countdown uses `active_watchlist.market_session` from the OpenD calendar, and labels weekday fallback estimates.
- Keep calculations that affect displayed signal decisions aligned with backend services and tests.
- Prefer service-vetted executable-bid fields (`annualized_return`, `capital_velocity_per_day`, `bid_premium_per_contract`, `premium_velocity_per_day`) for signal display. Midpoint is a separately labelled, non-guaranteed limit target; do not recompute backend ranking in the browser.
- Preserve loading, empty, error, and stale states for networked widgets.
- Outcome panel reads `/api/options/analytics/outcomes` (local SQLite, no OpenD gate) and renders API values through text nodes; the only broker action is the explicit `Pull broker fills` button (query-only `POST /api/options/analytics/outcomes/ingest`). Its wheel-cycle table may show combined option/share P&L only when the backend reports fully evidenced `measured`; unknown cycle legs render as em dashes, never zero.
- Position-monitor roll targets must display and copy the backend fee provenance/sample counts. Missing net credit is rendered as unavailable, never as zero.
- The local holdings panel may show the persisted broker `can_sell_qty` for display-only covered-call capacity comparison; it must not turn that field into a browser eligibility gate.
- Empty states for signal panels should surface the dominant blockers or scan diagnostics when the payload provides them.
- A `RESTING ORDER` badge and Moomoo max-sell capacity note are display-only diagnostics for the bounded top-three shortlist; they must not alter copy eligibility, rank, or recommended quantity. Missing diagnostic values stay unavailable, not zero.
- Do not add hidden trading execution calls from UI controls.

## Work Guidance

- Prefer small named functions and module exports that Vitest can import.
- The optional `iv_vs_hv` card row renders backend IV, 20-session historical volatility, spread, and date through `textContent`; missing fields render unavailable and never change copy or rank behavior.
- The optional `.falling-knife-diagnostic` card row renders the backend 20-session/50-session display-only flag through `textContent`; missing history renders unavailable and never changes copy or rank behavior.
- Reuse existing formatting helpers for currency, percentages, dates, and alert display.
- Keep DOM selectors stable when tests depend on them.
- Avoid large cross-feature files; place behavior near the feature folder that owns it.
