<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# Frontend Tests DOX

## Purpose

`tests/frontend/` owns Vitest coverage for browser JavaScript modules under `frontend/static/js/`.

## Ownership

- Test exported calculation, formatting, rendering, and state helpers without requiring a running Flask server.
- Use jsdom where DOM behavior matters.

## Local Contracts

- Keep tests deterministic and independent from live APIs.
- Match stable selectors and exports from the frontend modules.
- Cover loading, empty, error, and malformed-data cases when changing UI rendering paths.

## Work Guidance

- Prefer focused module tests over broad brittle DOM snapshots.
- Add regression tests for any bug that could hide, mis-rank, or mislabel a signal.
- Active-refresh tests verify stage and elapsed feedback with and without a saved snapshot, and elapsed time advancing while the same discovery progress persists.
- `section-navigation.test.js` verifies native anchor defaults, selected-link state, disclosure opening, keyboard focus, and direct hash navigation without load focus theft.
- `run-strip.test.js` exercises visible failure/quota warnings, first-refresh failure, safe reason text, rejected refresh requests, broker-data failure banners (blocking, saved-run fallback reason, non-blocking covered call), and recovery with a real jsdom DOM. `run-notifier.test.js` covers communication warnings without duplicate polls or publish events.
- `top-recommendations.test.js` covers the visible copy-block reason (`.copy-status`) for read-time review-only cards and copy-time blocks, including literal rendering of API text.
- `dashboard-safety.test.js` verifies that API-fed content is rendered through `escapeHtml` instead of raw `innerHTML` assignment.
- `outcome-panel.test.js` covers the outcome panel rendering (totals/groups/records, empty + error states, XSS escaping of contract/strategy/group text, expandable supporting-fills drill-down, and the read-only ingest trigger).
- Outcome disclosure checks verify named native controls, valid table-row semantics, expansion/collapse, and no empty disclosure for contracts without fills.

## Verification

- Run `npm test` or the relevant Vitest file.

## Child DOX Index

No child DOX files yet.
