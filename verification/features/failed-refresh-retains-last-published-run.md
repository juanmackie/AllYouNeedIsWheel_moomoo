# Failed refresh keeps the last published run

User-visible behavior: when a refresh fails (broker portfolio read error, engine
failure, quota), the dashboard keeps the last published run and its snapshot
intact, and the attempt is recorded as `failed` with the error surfaced. It
must never publish a `$0-cash` run or an empty book in place of the last good
one. This is the fail-closed contract; before it existed (CHANGELOG 2026-08-30)
a failed read published a zero-NAV run and logged phantom exits and re-entries.

## Sub-features

- Failed refresh keeps last published run (user-visible and persisted)
- Failed attempt visible to the user, not silent
- No phantom snapshot or zero-cash replacement

## How to get to it (user POV)

1. The dashboard shows the last published planning snapshot.
2. Start a refresh (`#run-refresh-btn` or the scheduled evening flow).
3. The refresh fails before completing.
4. The dashboard still shows the same snapshot — plus the failure.

## Driving it with the control

The control (`verification/run.sh`) reproduces the scenario deterministically
without touching a live broker:

1. Boot the real app via `tests/e2e/fixture_server.py`.
2. Fix the session to closed market (`/__e2e/clock`).
3. Seed a published run (`/__e2e/publish-run` scene `complete_closed`).
4. Record `GET /api/run` snapshot.
5. Set the refresh to fail (`/__e2e/refresh` mode `fail` with a named message).
6. POST `/api/run/refresh` (the app's real route).
7. Wait for idle (`/__e2e/wait-refresh-idle`).
8. Assert: attempt `failed` with the error; `run_id` unchanged; snapshot
   byte-for-byte identical to step 4.

## Gotchas

- The moomoo SDK writes logs under `%APPDATA%` at import time; the control
  redirects `APPDATA`/`TEMP` so the app can boot in the sandbox.
- If the app could not boot, `ping` never returns `"ok": true` and the control
  fails with the server log as evidence — never proceed on a half-started app.