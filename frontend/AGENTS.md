<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# Frontend DOX

## Purpose

`frontend/` owns the local one-screen dashboard UI: Jinja templates, partials, CSS, images, and browser JavaScript.

## Ownership

- `templates/` owns server-rendered page and partial structure.
- `static/js/` owns browser behavior, API calls, state handling, rendering helpers, and UI events.
- Growth cockpit UI: `templates/partials/dashboard/growth_panel.html` + `static/js/dashboard/growth-panel.js` (equity curve, pace, ETA, journal stats from `/api/portfolio/history` and `/api/options/analytics/lifecycle`); `templates/partials/dashboard/outcome_panel.html` + `static/js/dashboard/outcome-panel.js` (broker-verified outcomes from `/api/options/analytics/outcomes`: quoted-vs-filled credit, net outcome after fees, capital-days, per-preset/DTE/ticker/event-tier summaries with sample size/coverage/unknown counts, and expandable supporting-fill drill-down; read-only ingest trigger via `POST /api/options/analytics/outcomes/ingest`); `templates/partials/dashboard/position_monitor.html` + the position table in `dashboard-init.js` (open short options with exit-playbook verdicts, live P&L, delta, earnings, roll pressure).
- Active-watchlist page foot: `templates/partials/dashboard/active_watchlist.html` + `static/js/dashboard/active-watchlist.js` renders the persisted snapshot's `active_watchlist` (Moomoo group scanned + status with distinct missing/empty/unreachable explanations, last-successful-sync stamp, per-ticker scan status, holdings checked for covered calls, unsupported symbols); driven from the single `renderRunStrip` sink so every `/api/run` poll refreshes it.
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
- Keep dashboard JS modular by feature (`dashboard/`, `utils/`).
- Use existing formatting/state helpers before adding new browser utilities.
- Maintain responsive behavior for tables, cards, modals, and navigation.
- Use `escapeHtml` from `utils/formatters.js` for any API-fed content injected into the DOM to prevent XSS. Dashboard modules that inject API-fed content (e.g. `dashboard-cash.js`, `weekly-income.js`, `growth-panel.js`, `outcome-panel.js`, `account.js`, `state-model.js`, `top-recommendations.js`) must use it on text-bearing inserts. `active-watchlist.js` builds nodes via `textContent` (never `innerHTML` of snapshot text) and is exempt, but must stay element-creation-based.
- Break cross-feature import cycles with lazy dynamic accessors (e.g. `getOptionsTableActions`) rather than direct static imports between feature modules.

## Verification

- Run affected frontend Vitest files under `tests/frontend/`.
- Run `npm test` when shared JS helpers or dashboard table behavior changes.
- Use the manual smoke checklist in `tests/README.md` for visible UI workflow changes.

## Child DOX Index

- `static/css/AGENTS.md` - Visual styling, responsive layout, theme tokens, and CSS compatibility shims.
- `static/js/AGENTS.md` - Browser JavaScript modules, API clients, state, rendering, and UI events.
- `templates/AGENTS.md` - Jinja pages, partials, and server-rendered UI structure.
