<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# Frontend DOX

## Purpose

`frontend/` owns the local dashboard UI: Jinja templates, partials, CSS, images, and browser JavaScript for dashboard, portfolio, rollover, and shared components.

## Ownership

- `templates/` owns server-rendered page and partial structure.
- `static/js/` owns browser behavior, API calls, state handling, rendering helpers, and UI events.
- `static/css/` owns visual styling and responsive layout.
- `static/img/` owns static visual assets.

## Local Contracts

- Keep the UI signals-only. Buttons and modals may support review workflows, not autonomous broker execution.
- Preserve clear OpenD connection/login state and stale-data warnings.
- Do not hide source, warning, confidence, or freshness metadata needed to judge a signal.
- Browser state in localStorage must remain scoped to user preferences or UI inputs, not authoritative portfolio truth.
- Templates should avoid duplicating calculations already provided by services or JS helpers.

## Work Guidance

- Keep templates semantic and partials focused on repeated UI blocks.
- Keep dashboard JS modular by feature (`dashboard/`, `rollover/`, `portfolio/`, `utils/`).
- Use existing formatting/state helpers before adding new browser utilities.
- Maintain responsive behavior for tables, cards, modals, and navigation.
- Use `escapeHtml` from `utils/formatters.js` for any API-fed content injected into the DOM to prevent XSS. Dashboard modules (`dashboard-cash.js`, `dashboard-regime.js`, `state-model.js`, `top-recommendations.js`) must use it on text-bearing inserts.
- Break cross-feature import cycles with lazy dynamic accessors (e.g. `getOptionsTableActions`, `getRolloverActions`) and a registration pattern (`registerRolloverUiHandlers`) rather than direct static imports between `rollover/` and `dashboard/` modules.

## Verification

- Run affected frontend Vitest files under `tests/frontend/`.
- Run `npm test` when shared JS helpers or dashboard table behavior changes.
- Use the manual smoke checklist in `tests/README.md` for visible UI workflow changes.

## Child DOX Index

- `static/css/AGENTS.md` - Visual styling, responsive layout, theme tokens, and CSS compatibility shims.
- `static/js/AGENTS.md` - Browser JavaScript modules, API clients, state, rendering, and UI events.
- `templates/AGENTS.md` - Jinja pages, partials, and server-rendered UI structure.
