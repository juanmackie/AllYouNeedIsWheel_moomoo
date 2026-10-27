#!/usr/bin/env bash
# Verification control for AllYouNeedIsWheel_moomoo.
#
# Drives the REAL Flask app through tests/e2e/fixture_server.py: the run
# model, persistence, routes and frontend are real; only broker/market-data
# I/O is stubbed at the service boundary (that is the point at which the app
# would dial out to OpenD).
#
# Proves the fail-closed guarantee documented in CHANGELOG.md (2026-08-30
# incident): a refresh whose broker portfolio read fails must keep the last
# published run. The old behavior published a $0-cash run with an empty book.
#
# Exit 0 only when every assertion holds. Evidence goes to
# verification/evidence/ayniw-verify-<stamp>/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERIFY_PORT="${VERIFY_PORT:-8181}"
BASE="http://127.0.0.1:${VERIFY_PORT}"
SCRATCH="$ROOT/_tmp/verification-scratch"
EVIDENCE_DIR="$ROOT/verification/evidence"
STAMP="$(date +%Y%m%d-%H%M%S)"
EVIDENCE="$EVIDENCE_DIR/ayniw-verify-$STAMP"
SERVER_PID=""

log() { printf '%s\n' "$*"; }
fail() { log "FAIL: $*"; exit 1; }

cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$SCRATCH"
}
trap cleanup EXIT

mkdir -p "$SCRATCH/appdata" "$SCRATCH/tmp" "$SCRATCH/logs" "$EVIDENCE"
EVIDENCE_WIN="$(cygpath -m "$EVIDENCE" 2>/dev/null || echo "$EVIDENCE")"

# The moomoo SDK writes its log under %APPDATA% at import time; the sandbox
# denies writes there. Redirect app data and temp to the ignored scratch dir.
export APPDATA="$SCRATCH/appdata" appdata="$SCRATCH/appdata"
export TEMP="$SCRATCH/tmp" TMP="$SCRATCH/tmp" TMPDIR="$SCRATCH/tmp"
export WHEEL_LOG_DIR="$SCRATCH/logs"

log "starting the real app under the e2e fixture (port $VERIFY_PORT)..."
(
  cd "$ROOT"
  uv run python tests/e2e/fixture_server.py --port "$VERIFY_PORT"
) > "$SCRATCH/server.log" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 45); do
  if curl -sf "$BASE/__e2e/ping" > "$EVIDENCE/ping.json" 2>/dev/null; then
    break
  fi
  sleep 2
done
grep -q '"ok": *true' "$EVIDENCE/ping.json" 2>/dev/null || {
  cp "$SCRATCH/server.log" "$EVIDENCE/server.log"
  fail "fixture server did not become ready; see $EVIDENCE/server.log"
}

# Fixed closed-market session, so the journey is deterministic.
curl -sf -X POST "$BASE/__e2e/clock" \
  -H "Content-Type: application/json" -d '{"session":"closed"}' \
  -o "$EVIDENCE/clock.json" || fail "clock control failed"

# Seed the last published run.
curl -sf -X POST "$BASE/__e2e/publish-run" \
  -H "Content-Type: application/json" \
  -d '{"scene":"complete_closed","preset":"balanced"}' \
  -o "$EVIDENCE/publish.json" || fail "publish-run control failed"
RUN_ID="$(python -c "import json; print(json.load(open('$EVIDENCE_WIN/publish.json'))['run_id'])")"
[[ -n "$RUN_ID" ]] || fail "publish-run returned no run_id"
log "seeded published run: $RUN_ID"

# Record the published snapshot BEFORE the failing refresh.
curl -sf "$BASE/api/run" -o "$EVIDENCE/run-before.json" || fail "GET /api/run failed before refresh"
python - "$EVIDENCE_WIN/run-before.json" "$EVIDENCE_WIN/snapshot-before.json" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
snapshot = payload.get("snapshot")
json.dump(snapshot, open(sys.argv[2], "w", encoding="utf-8"), indent=2, sort_keys=True)
PY

# Make the broker portfolio read fail (the CHANGELOG 2026-08-30 scenario).
curl -sf -X POST "$BASE/__e2e/refresh" \
  -H "Content-Type: application/json" \
  -d '{"mode":"fail","fail_message":"simulated broker portfolio read failure (verification control)"}' \
  -o "$EVIDENCE/refresh-fail.json" || fail "refresh-fail control failed"

# Trigger the real refresh attempt through the app's own route.
CODE="$(curl -s -o "$EVIDENCE/refresh-trigger.json" -w '%{http_code}' -X POST "$BASE/api/run/refresh")"
[[ "$CODE" == "202" ]] || fail "POST /api/run/refresh returned $CODE, expected 202"

# Wait for the attempt to settle, then assert the fail-closed contract.
curl -sf -X POST "$BASE/__e2e/wait-refresh-idle?timeout=90" \
  -o "$EVIDENCE/idle.json" || fail "refresh never reached idle"
python - "$EVIDENCE_WIN/idle.json" "$RUN_ID" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d.get("attempt_state") == "failed", f"expected attempt_state=failed, got {d.get('attempt_state')}"
assert d.get("attempt_error"), "failed attempt carried no error message"
seeded = sys.argv[2]
assert d.get("run_id") == seeded, f"published run was replaced: {d.get('run_id')} != {seeded}"
PY

# The failing refresh must leave the published snapshot byte-for-byte intact.
curl -sf "$BASE/api/run" -o "$EVIDENCE/run-after.json" || fail "GET /api/run failed after refresh"
python - "$EVIDENCE_WIN/run-after.json" "$EVIDENCE_WIN/snapshot-after.json" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
snapshot = payload.get("snapshot")
json.dump(snapshot, open(sys.argv[2], "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
python - "$EVIDENCE_WIN/snapshot-before.json" "$EVIDENCE_WIN/snapshot-after.json" <<'PY'
import json, sys

def strip_ticks(d):
    """Drop the live quote-age tick; it advances with wall clock between reads."""
    if isinstance(d, dict):
        return {k: strip_ticks(v) for k, v in d.items() if k != "quote_age_sec"}
    if isinstance(d, list):
        return [strip_ticks(v) for v in d]
    return d

before = json.load(open(sys.argv[1], encoding="utf-8"))
after = json.load(open(sys.argv[2], encoding="utf-8"))
assert before.get("run") == after.get("run"), "the published run record changed"
assert strip_ticks(before) == strip_ticks(after), (
    "the failing refresh replaced the last published snapshot (phantom $0 run)"
)
PY

cat > "$EVIDENCE/summary.txt" <<EOF
PASS: failed refresh kept the last published run ($RUN_ID).
attempt_state=failed with the simulated broker read error; snapshot unchanged.
Scenario: CHANGELOG.md 2026-08-30 incident ("published a \$0-cash run with an
empty book"); the control now asserts the opposite of that behavior.
EOF
log "PASS: failed refresh kept the last published run ($RUN_ID); evidence in $EVIDENCE/"