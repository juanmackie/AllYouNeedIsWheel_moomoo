<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. Updated 2026-08-02: Telemetry archetype edits (base.html markers + theme toggle). -->

# Frontend Templates DOX

## Purpose

`frontend/templates/` owns Jinja-rendered pages and partials for the one-screen dashboard (base layout, dashboard partials, common tables).

## Ownership

- `base.html` owns the masthead, theme-toggle hook (`data-theme`), navigation shell, and global markup.
- `dashboard.html` owns the dashboard page structure.
- `partials/common/` owns shared tables and footer-level common UI.
- `partials/components/` owns reusable modal/component fragments.
- `partials/dashboard/` owns dashboard-specific panels and tables.

## Theme & Archetype Contract (binding)

- `base.html` sets `<html data-theme="auto">`. Manual override button (`onclick="cycleTheme()"`) is in the right masthead meta.
- Masthead framing: `[ DATE · LOCAL ]`, `[ CASH-SECURED PUTS // COVERED CALLS ] · OPEN D · LIVE SESSION`, `UNIT / D-01 · REV 2.6`, `v2.0 · REV 2.6 · © 2026`.
- `AYNIWHEEL` wordmark remains the macro-typography anchor; the live-connection dot uses the single-use terminal green (`--ft-green`).
- No structural changes to `#global-error-boundary`, `#opend-status-banner`, `.content-container`, `.app-flash-shell`, or partial class names.

## Local Contracts

- Keep templates presentation-focused; route/service layers should provide data contracts.
- Preserve accessibility basics: labels, button text, modal semantics, and readable empty/error states.
- Signal cards must keep broker source/freshness, quality/event tiers, spread/liquidity, bid velocity, recommended quantity, and review-only/copy caveats visible.
- Keep script/style dependencies consistent with `base.html`.
- Do not alter class names that frontend tests (`tests/frontend/`) depend on (e.g. `.recommendation-card`, `.ticker-badge`, `.table-responsive`).

## Work Guidance

- Favor partials for repeated markup.
- Update matching JavaScript (`main.js`) when new interactive elements (e.g. theme toggle) are added.
- Keep all Bootstrap shim class names intact (`.badge`, `.alert-*`, `.btn`, `.table`, `.d-none`).
