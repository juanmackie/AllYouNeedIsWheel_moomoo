<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. Updated 2026-08-02: Tactical Telemetry dual-mode contract. -->

# Frontend CSS DOX

## Purpose

`frontend/static/css/` owns visual styling, responsive layout, theme tokens, and compatibility shims for the browser UI. The current file (`ft.css`) operates in **Tactical Telemetry** mode (mono-dominant typography, rigid grid, zero radius, hard dividing rules, single accent red, one terminal-green element only).

## Ownership

- `ft.css` owns the telemetry design system: dual-mode tokens (dark default + light print), typography atoms, layout primitives, component shims (tables, badges, alerts, buttons), responsive behavior.

## Theme Mode Contract (binding)

- **Dark telemetry** (`#0A0A0A` substrate, `#EAEAEA` phosphor ink) is the default at `:root`.
- `prefers-color-scheme: light` activates the light print mode automatically (`#F4F4F0` / `#111111`).
- Manual override: `data-theme="dark"` / `data-theme="light"` / `data-theme="auto"` on `<html>` wins over media query. JS (`main.js`) sets this from `localStorage` (`ui-theme` key) and provides `window.cycleTheme()`.
- Token variables (`--ft-paper`, `--ft-ink`, `--ft-rule-*`, `--ft-signal`, etc.) must resolve in both modes. Component rules must never hard-code a substrate-specific value.
- **Single-use terminal green (`--ft-green` / `#4AF626` in dark, `#2A7A12` in light):** reserved exclusively for the live OpenD connection indicator (`.ft-masthead__dot`). No other UI element may use this color.

## Typography Architecture (binding)

- **Data / telemetry tier:** `var(--ft-font-mono)` — JetBrains Mono. Used for all tables, badges, metadata, buttons, navigation, unit IDs, section footers.
- **Macro / structural tier:** `var(--ft-font-display)` — IBM Plex Sans 600 (no heavier weights vendored). Deployed with `clamp()`, `text-transform: uppercase`, tight negative tracking (`-0.04em`), compressed line-height (`0.92`–`0.95`).
- **Source Serif 4 removed** from `--ft-font-display`; it is not used in the telemetry archetype. Do not reintroduce unless the archetype changes.

## Layout Contract (binding)

- Zero border-radius everywhere (`--ft-radius: 0`). All corners exactly 90°.
- Visible compartmentalization via 1px / 2px solid rules (`--ft-rule`, `--ft-rule-soft`).
- `display: grid; gap: 1px;` hairline technique used for table/card borders.
- No gradients, no soft drop shadows, no translucency beyond token soft backgrounds.
- No analog textures: no scanlines, no mechanical noise, no halftone filters, no CRT overlays. Clean grid only.

## Component Rules

- **Tables (`.ft-table`):** mono font, uppercase, 12px, tight tracking (`0.03em`). Header rules via `border-top` / `border-bottom`. Hover row must stay subtle in both substrates.
- **Badges / pills (`.ft-pill`, `.badge.*` shims):** mono, uppercase, 10–11px, tight tracking, 1px solid border, transparent background with `-soft` token fill for state colors.
- **Buttons (`.ft-btn`):** mono, uppercase, 11px, 0.1em tracking, 1px solid ink border, no radius, hover inverts to ink-on-paper.
- **State colors (`--ft-signal`, `--ft-warn`, `--ft-down`, `--ft-info`):** must remain readable on both substrates. Read them against both `#0A0A0A` and `#F4F4F0` backgrounds; contrast ratios must stay compliant.
- **Bootstrap shims preserved:** `.badge`, `.alert-*`, `.btn`, `.table`, `.d-none`, `.fade`, `.show` must not be removed while templates/JS emit those classes.

## Safety / Contract Constraints

- Preserve readable signal, source, confidence, warning, and freshness states in both modes.
- Preserve all class names and selectors that frontend tests (`tests/frontend/`) depend on.
- Preserve the OpenD connection banner (`#opend-status-banner`), error boundary (`#global-error-boundary`), and all visibility states.
- Do not hide stale-data, disconnected, or error states with CSS-only rules.

## Verification

- `npm test`: all 28 frontend Vitest files must pass.
- Manual smoke checklist (`tests/README.md`) must cover both dark and light modes.
- Confirm terminal green is visible exactly once on the connection dot and never elsewhere.
