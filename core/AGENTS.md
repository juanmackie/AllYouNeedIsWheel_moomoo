<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# Core DOX

## Purpose

`core/` owns broker connection helpers, scoring primitives, option decision logic, risk/evidence gates, the wheel runner and its immutable run model, caches, logging, and shared pure utilities.

## Ownership

- Own business-critical trading logic that should not depend on Flask.
- Own reusable decision functions for Wheel strategy signals, scoring factors, Greeks, and evidence-gated advice.
- Own executable-bid capital-return and premium-velocity calculations plus quote-evidence gates; quality/event tiers precede stable capital-return ranking, with bid velocity as a tie-break for surfaced signals.
- Own OpenD connection lifecycle helpers and shared runtime utilities.
- Own the growth cockpit pure logic: `exit_playbook` (HOLD/TAKE_PROFIT/ROLL/CLOSE verdicts), `position_diff` + `portfolio_snapshot` (per-run snapshots and trade-event inference), `sizing` (exposure/concentration arithmetic), and `growth_mode.growth_pace` (path-to-target math). All are pure and broker-free.

## Local Contracts

- Keep `core` free of `api` imports entirely (any direction): api-layer composition is injected into `WheelRunner` as callables (see `api/services/roll_diagnostics.py`).
- Preserve live-trading safety assumptions and source-of-truth boundaries.
- Scoring changes must preserve or deliberately update the methodology documented in `SCORING.md`; midpoint is display-only and never a ranking basis.
- Connection logic must avoid leaking handles and should be safe under repeated route/service calls. `MoomooConnection.get_option_chain(..., force_refresh=True)` is the explicit seam for closed-market last-session reads; it must remain query-only.

## Work Guidance

- Prefer pure functions and small data transformations for scoring/risk logic.
- Keep thresholds, weights, and profile choices explicit and covered by regression tests.
- Decision helpers for read-only panels should prefer plain-English blockers/rationale and preserve the ability to surface research-only outcomes.
- Avoid import-time network calls, thread starts, or DB writes.
- Reuse `ticker_utils`, `rate_limiter`, and logging helpers instead of local one-off versions.
- Cross-layer composition (e.g. roll diagnostics needing registered services) is injected as a provider callable at factory time (`api/__init__.py` → `WheelRunner(roll_diagnostics_provider=...)`); never import `api` from `core`.

## Verification

- Scoring/decision changes: run `pytest tests/test_wheel_decision.py tests/test_score_regression.py`.
- Connection changes: run `pytest tests/test_connection.py tests/test_import_side_effects.py`.
- Cache/rate-limit changes: run the matching focused tests such as `tests/test_rate_limiter.py` or `tests/test_scan_ledger.py`.
- Before significant edits: question requirements, delete dead weight, then simplify (V10 §3); compatibility needs concrete consumer evidence (V10 §3); structural readonly (`core/broker_protocol.py`) is non-negotiable (V10 §6). After edits, apply the brief changed/verified/assumptions/risks closeout (V10 §9) and note any DOX files intentionally unchanged.

## Child DOX Index

No child DOX files yet.
