<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# Frontend CSS DOX

## Purpose

`frontend/static/css/` owns visual styling, responsive layout, theme tokens, and compatibility shims for the browser UI.

## Ownership

- `ft.css` owns the current FT-inspired redesign, layout primitives, Bootstrap-compatible class shims, dashboard/table/modal styling, and responsive behavior.
- `main.css` owns any minimal global CSS entrypoint behavior that remains outside the main redesign file.

## Local Contracts

- Preserve readable signal, source, confidence, warning, and freshness states.
- Keep tables, cards, modals, forms, and navigation usable on desktop and mobile.
- Do not remove Bootstrap-compatible class shims while templates or JavaScript still emit those class names.
- Keep class names stable when templates, JavaScript, or frontend tests depend on them.
- Avoid CSS-only behavior that hides disconnected, stale, or error states.

## Work Guidance

- Prefer existing `--ft-*` tokens for colors, typography, spacing, and state styling.
- Group new styles near the component or pattern they modify.
- Keep responsive overrides close to the base rule when practical.
- Coordinate selector changes with `frontend/templates/` and `frontend/static/js/`.

## Verification

- Run relevant frontend tests when selector-dependent behavior changes.
- Use the manual UI smoke checklist in `tests/README.md` for visible layout, table, modal, or navigation changes.

## Child DOX Index

No child DOX files yet.
