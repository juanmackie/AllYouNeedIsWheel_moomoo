<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# Frontend Templates DOX

## Purpose

`frontend/templates/` owns Jinja-rendered pages and partials for dashboard, portfolio, rollover, common tables, and modal components.

## Ownership

- Top-level templates own page composition.
- `partials/common/` owns shared tables and footer-level common UI.
- `partials/components/` owns reusable modal/component fragments.
- `partials/dashboard/` owns dashboard-specific panels and tables.

## Local Contracts

- Keep templates presentation-focused; route/service layers should provide data contracts.
- Preserve accessibility basics: labels, button text, modal semantics, and readable empty/error states.
- Do not remove warning/source/freshness fields from signal displays unless the backend contract is intentionally changed.
- Keep any plain-English interpretation/caveat slots for research-only signal cards stable when templates expose them.
- Keep script/style dependencies consistent with `base.html`.

## Work Guidance

- Favor partials for repeated markup, but avoid splitting one-off markup into indirection.
- Keep class/id names stable where JavaScript or tests query them.
- Update matching JavaScript when template structure changes affect selectors.

## Verification

- Run frontend tests for any affected JS/template selectors.
- For visible changes, run the relevant manual smoke checks in `tests/README.md`.

## Child DOX Index

No child DOX files yet.
