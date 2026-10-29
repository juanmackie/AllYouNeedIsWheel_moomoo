# AYNIW review -> punchlist (pilot cycle, 2026-10-25)

Gate state before this review: `verification/run.sh` GREEN
(evidence `verification/evidence/ayniw-verify-20261025-201700/`).

## Punchlist

1. **[env/robustness] RESOLVED — Standalone boot no longer dies on the moomoo SDK log dir.**
   The SDK writes `%APPDATA%\com.moomoo.OpenD\Log\py_*.log` at import time; a
   denied write raised `PermissionError` and the app never started. Fixed at the
   factory, not the harness: `core/moomoo_sdk_log.py` probes that directory and
   redirects the SDK's `APPDATA`/`HOME` lookup to `logs/moomoo_sdk/` only when the
   SDK cannot write its own location. Every module that imports `moomoo` imports
   it first; `tests/test_import_side_effects.py` fails if that order regresses and
   covers the writable (no redirect) and unwritable (redirect) paths. Evidence:
   `python run_api.py` from a profile-unwritable process now serves
   `GET /health` (`degraded`, OpenD down) and `GET /` returns 200 where it
   previously exited with `[Errno 13]`. The harness redirects in
   `verification/run.sh` and `tests/conftest.py` stay as deliberate isolation.

2. **[gate coverage] honest-unknown-outcomes not yet driven by the control.**
   `verification/features/honest-unknown-outcomes.md` is mapped but the drive
   (assert `unknown` for unmeasured totals, no profit-green gross yield) needs
   pinned frontend selectors. Add behind a stage flag in `verification/run.sh`.

3. **[process] Review->punchlist handoffs are now gated but the muscle is new.**
   The user's Claude logs show the "review the repo, punchlist everything"
   recurrence across projects. The convention now: a punchlist handoff is only
   valid with a green control + evidence dir. First use of the gate in anger
   is this cycle; watch for the first "I claim done without green" and refuse
   it by the rule, not by mood.

## Refused handoffs (gate in action, this cycle)

- "The refresh published a fresh run after the failed broker read" — refused:
  `idle.json` shows run_id retained and attempt failed; seeded run
  `e2e-fdd072713842` is still the published one.
- Simulated old behavior ($0-cash snapshot): the control's snapshot-equality
  assertion trips (red) — the check demonstrably catches the CHANGELOG
  2026-08-30 mistake class.