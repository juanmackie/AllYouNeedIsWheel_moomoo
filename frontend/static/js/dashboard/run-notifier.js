/**
 * Single shared run-state poll (P1b).
 *
 * One 5-second poll of /api/run drives the whole screen:
 *   - starts at page load with an IMMEDIATE first fetch (never waits a tick to
 *     see a run; a run that completed before the first tick is picked up)
 *   - exactly one in-flight request at a time — the next tick is scheduled
 *     only after the previous completes, so slow responses never overlap
 *   - unbounded while a run exists or a refresh attempt is active, so a scan
 *     that takes longer than 60s is still adopted (no 30-tick budget)
 *   - stops only when confirmed idle (a fresh install with no run and no active
 *     attempt, observed twice); any manual refresh click restarts it
 *   - on run_id change (including the first-ever run and a run already complete
 *     at page load) it notifies the registered publish handler, which fans out
 *     explicit reloads to every panel via read-only fetches — never POST
 *     /api/run/refresh, so a refresh never triggers another broker scan.
 *
 * This replaces the old bounded watcher (2000ms x 30 = 60s) that started after
 * the refresh POST, missed first-ever runs, missed completions before its first
 * poll, and silently gave up on scans longer than 60s.
 */

import { fetchWithTimeout, readJsonSafely } from './api-core.js';
import { renderRunStrip } from './run-strip.js';

const POLL_INTERVAL_MS = 5000;

let running = false;      // the single shared poll instance
let inFlight = false;     // one in-flight request max
let timer = null;
let haveBaseline = false; // first observation has been made
let lastRunId = null;     // last observed run_id
let idlePolls = 0;        // consecutive confirmed-idle observations
let publishHandler = null;

export function getPollIntervalMs() {
    return POLL_INTERVAL_MS;
}

function extractRunId(payload) {
    const run = payload && payload.snapshot && payload.snapshot.run;
    return run ? run.run_id : null;
}

function attemptIsActive(attempt) {
    const state = attempt && attempt.state;
    return state === 'queued' || state === 'refreshing';
}

function clearTimer() {
    if (timer) {
        clearTimeout(timer);
        timer = null;
    }
}

function scheduleNext() {
    clearTimer();
    timer = setTimeout(() => { timer = null; void tick(); }, POLL_INTERVAL_MS);
}

/**
 * Poll once. Mirrors /api/run into the operational strip (reusing the payload
 * it just fetched — no second request) and watches for run_id changes.
 */
async function tick() {
    if (!running || inFlight) return;
    inFlight = true;

    let payload = null;
    try {
        const resp = await fetchWithTimeout(
            '/api/run',
            { headers: { 'Cache-Control': 'no-cache' } },
            10000
        );
        if (resp.ok) {
            payload = await readJsonSafely(resp);
        }
    } catch (err) {
        // Network exception: keep the last-good strip visible and keep polling;
        // never blank the screen on a transient failure.
        console.error('Run-state poll request failed:', err);
    }

    inFlight = false;
    if (!running) return;

    if (!payload) {
        // Transient failure — retry on the next tick without changing state.
        scheduleNext();
        return;
    }

    const attempt = payload.attempt;
    const snapshot = payload.snapshot;
    const runId = extractRunId(payload);

    // Feed the operational strip from this same payload (progress/failure/last-
    // good all render here); failures keep the last-good snapshot underneath.
    try {
        renderRunStrip(attempt, snapshot);
    } catch (err) {
        console.error('Run strip render failed:', err);
    }

    const wasBaseline = haveBaseline;
    if (!haveBaseline) {
        haveBaseline = true;
    }

    // Publish fan-out: first observation adopts whatever run already exists
    // (completion before the first tick, or first-ever run on a fresh page);
    // every later observation fans out only on a run_id CHANGE.
    if (!wasBaseline) {
        lastRunId = runId;
        if (runId !== null) {
            notify(runId);
        }
    } else if (runId !== lastRunId) {
        lastRunId = runId;
        notify(runId);
    }

    // Continue while there is anything to watch. Stop only when confirmed idle
    // (no run AND no active attempt), observed on a tick after the baseline was
    // already established — two idle observations guarantee a refresh click that
    // races the first poll is not missed.
    if (runId === null && !attemptIsActive(attempt)) {
        idlePolls += 1;
        if (wasBaseline && idlePolls >= 2) {
            stopTimers();
        } else {
            scheduleNext();
        }
    } else {
        idlePolls = 0;
        scheduleNext();
    }
}

function notify(runId) {
    if (typeof publishHandler === 'function') {
        publishHandler(runId);
    }
}

/**
 * Register the handler invoked on a newly published run (run_id adopted).
 * The handler must re-render panels via read-only fetches and must NOT
 * POST /api/run/refresh.
 */
export function onRunPublished(handler) {
    publishHandler = typeof handler === 'function' ? handler : null;
}

/**
 * Start the single shared run-state poll. Idempotent — if it is already
 * running it is left in place (exactly one poll at a time) and the immediate
 * first fetch is not repeated.
 */
export function startRunStatePoll() {
    if (running) return;
    running = true;
    haveBaseline = false;
    lastRunId = null;
    idlePolls = 0;
    void tick();
}

/**
 * Restart the shared poll after it stopped in the idle (fresh-install) state.
 * Safe to call on every refresh click; no-op while it is already running.
 */
export function ensureRunStatePoll() {
    if (running) return;
    running = true;
    haveBaseline = false;
    lastRunId = null;
    idlePolls = 0;
    void tick();
}

/** Stop the poll (cleanup/testing). */
export function stopRunStatePoll() {
    stopTimers();
    publishHandler = null;
}

function stopTimers() {
    running = false;
    inFlight = false; // a tick that never settles must not strand the poll
    clearTimer();
}
