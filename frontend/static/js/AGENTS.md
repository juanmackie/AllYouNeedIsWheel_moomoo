<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# Frontend JavaScript DOX

## Purpose

`frontend/static/js/` owns browser-side behavior for dashboard, portfolio, rollover, API calls, state models, rendering, calculations, alerts, and small visual helpers.

## Ownership

- `dashboard/` owns dashboard widgets, options table behavior (`options-table-actions.js`), recommendations, earnings-vol signals, Catalyst Watch, macro/regime display, and account panels.
- `rollover/` owns rollover state, API calls (`rollover-api.js`), calculations, and UI rendering (`rollover-ui.js`). Uses a handler-registration pattern (`registerRolloverUiHandlers`) to expose DOM-render functions to `rollover-api.js` without a direct import cycle.
- `portfolio/` owns portfolio page interactions.
- `utils/` owns shared formatting (`formatters.js` exports `escapeHtml`), alerts, sparklines, and state helpers.

## Local Contracts

- Treat API responses as the source for portfolio/options data; localStorage is only a UI preference/input cache.
- Keep calculations that affect displayed signal decisions aligned with backend services and tests.
- Prefer service-vetted premium fields (`premium_per_contract`, `mid_price`) for display and totals; do not infer premium from one-sided quotes when the value is unknown.
- Preserve loading, empty, error, and stale states for networked widgets.
- Empty states for signal panels should surface the dominant blockers or scan diagnostics when the payload provides them.
- Do not add hidden trading execution calls from UI controls.

## Work Guidance

- Prefer small named functions and module exports that Vitest can import.
- Reuse existing formatting helpers for currency, percentages, dates, and alert display.
- Keep DOM selectors stable when tests depend on them.
- Avoid large cross-feature files; place behavior near the feature folder that owns it.
- Break circular dependencies between `dashboard/` and `rollover/` with a handler-registration pattern: the UI module exports a registration function, the API module stores references internally via a local `rolloverUiHandlers` map. Tests must import the UI module (which triggers registration) before calling the API entrypoint.
- Use `escapeHtml` from `utils/formatters.js` for any API-fed `innerHTML` assignment; never interpolate raw strings fetched over HTTP.
- `top-recommendations.js` exports `getScreenerOverrides()` which reads 7 screener inputs from the DOM (`screener-otm-min`, `screener-otm-max`, `screener-dte-min`, `screener-dte-max`, `screener-delta-target`, `screener-min-volatility`, `screener-min-buying-power`). Apply/Reset buttons trigger `loadTopRecommendations()` with the override values.

## Verification

- Run the matching `tests/frontend/*.test.js` file for changed modules.
- Run `npm test` for shared utility or options-table changes.

## Child DOX Index

No child DOX files yet.
