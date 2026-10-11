<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# E2E Tests DOX

## Purpose

`tests/e2e/` owns browser-level smoke checks for the running local app.

## Ownership

- Verify that key pages and workflows load and interact at the browser level.
- Keep coverage focused on user-critical flows, not every implementation detail.

## Local Contracts

- E2E tests should not require live trading credentials.
- Keep tests tolerant of external-provider unavailability where the UI is expected to degrade gracefully.
- Do not encode private account data.

## Work Guidance

- Prefer smoke tests for the dashboard one-screen flow: run strip, top recommendations, position monitor, and OpenD-state visibility.
- Keep selectors stable and user-oriented.
- The dashboard smoke opens the collapsed Options area before asserting options-table visibility.
- The long-refresh journey verifies named stage and advancing elapsed time over retained results without relying on a stage-weight percentage; progress details hide on completion.
- `preset-and-refresh.spec.js` verifies failure, quota, API outage, and rejected-refresh warnings remain visible above retained results and clear on recovery, including a narrow viewport. `fixture_server.py` supplies persisted test-only run scenes (`quota_partial` retains a covered call with incomplete coverage); production data paths stay unmodified.
- The same journey holds `/api/portfolio` open to verify saved cards, run status, and a manually usable refresh control load independently; page startup never starts a scan.

## Verification

- Run `npm run test:e2e` (Playwright, chromium; app must be running on 127.0.0.1:8000 — set `E2E_BASE_URL` to override).

## Child DOX Index

No child DOX files yet.
