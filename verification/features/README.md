# AllYouNeedIsWheel verification map

Maintained by the `verify-app` skill. The control is `verification/run.sh`; it
drives the real Flask app through `tests/e2e/fixture_server.py` (broker I/O
stubbed at the service boundary only) and exits non-zero on regression. No
review or handoff claim is valid while `verification/run.sh` is red.

## Features

- [failed-refresh-retains-last-published-run.md](failed-refresh-retains-last-published-run.md) — **driven by the control**: a refresh whose broker portfolio read fails keeps the last published run, with the attempt recorded as failed and the error surfaced.
- [honest-unknown-outcomes.md](honest-unknown-outcomes.md) — mapped, not yet driven: outcome summaries report unmeasured totals as unknown, never `$0.00`, and the Outcomes headline names realized option-leg net.

## Driving notes

- Closed-market session is fixed via `/__e2e/clock` so every run is deterministic.
- The moomoo SDK writes to `%APPDATA%` at import time, which the sandbox denies; the control redirects `APPDATA`/`TEMP`/`WHEEL_LOG_DIR` to `_tmp/verification-scratch/` (ignored by git).
- Run the control with `bash verification/run.sh`; override the port with `VERIFY_PORT=NNNN`.