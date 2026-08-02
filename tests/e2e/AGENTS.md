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

- Prefer smoke tests for dashboard, portfolio, rollover, and OpenD-state visibility.
- Keep selectors stable and user-oriented.

## Verification

- Run the configured e2e command when available; otherwise document manual smoke coverage in `tests/README.md`.

## Child DOX Index

No child DOX files yet.

