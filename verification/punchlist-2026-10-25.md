# AYNIW review -> punchlist (pilot cycle, 2026-10-25)

Gate state before this review: `verification/run.sh` GREEN
(evidence `verification/evidence/ayniw-verify-20261025-201700/`).

## Punchlist

1. **[env/robustness] Standalone boot still dies on the moomoo SDK log dir.**
   The SDK writes `%APPDATA%\com.moomoo.OpenD\Log\py_*.log` at import time; in
   this sandbox that raises `PermissionError` and the app never starts.
   Evidence: first boot attempt traceback; the redirect exists only in tests
   (`tests/conftest.py:25`, `tests/README.md:70`), not in the app or fixture
   server. `verification/run.sh` works around it. Fix at the right level: move
   the redirection into the fixture server / app boot so any harness boots
   cleanly (matches the "fix the factory, not the agent" rule).

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