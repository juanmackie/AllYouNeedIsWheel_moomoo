# Frontend JavaScript DOX

## Purpose

`frontend/static/js/` owns browser-side behavior for dashboard, portfolio, rollover, API calls, state models, rendering, calculations, alerts, and small visual helpers.

## Ownership

- `dashboard/` owns dashboard widgets, options table behavior, recommendations, earnings-vol signals, Catalyst Watch, macro/regime display, and account panels.
- `rollover/` owns rollover state, API calls, calculations, and UI rendering.
- `portfolio/` owns portfolio page interactions.
- `utils/` owns shared formatting, alerts, sparklines, and state helpers.

## Local Contracts

- Treat API responses as the source for portfolio/options data; localStorage is only a UI preference/input cache.
- Keep calculations that affect displayed signal decisions aligned with backend services and tests.
- Preserve loading, empty, error, and stale states for networked widgets.
- Do not add hidden trading execution calls from UI controls.

## Work Guidance

- Prefer small named functions and module exports that Vitest can import.
- Reuse existing formatting helpers for currency, percentages, dates, and alert display.
- Keep DOM selectors stable when tests depend on them.
- Avoid large cross-feature files; place behavior near the feature folder that owns it.

## Verification

- Run the matching `tests/frontend/*.test.js` file for changed modules.
- Run `npm test` for shared utility or options-table changes.

## Child DOX Index

No child DOX files yet.

