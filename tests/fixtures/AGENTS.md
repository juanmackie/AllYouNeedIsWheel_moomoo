<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# Test Fixtures DOX

## Purpose

`tests/fixtures/` owns reusable deterministic scenarios for tests.

## Ownership

- Provide synthetic but realistic option, portfolio, scoring, and signal inputs.
- Keep private account data and live broker responses out of fixtures.

## Local Contracts

- Fixtures should be explicit enough that tests read like trading scenarios.
- Do not make fixtures depend on network, current market prices, wall-clock timing, or local user config.

## Work Guidance

- Add fixtures only when they reduce duplication across tests or clarify a meaningful scenario.
- Keep scenario names tied to the behavior under test.

## Verification

- Run tests that consume changed fixtures.

## Child DOX Index

No child DOX files yet.

