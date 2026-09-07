/**
 * One bounded run-state poll (C10).
 *
 * After a manual refresh starts a background run, panels currently show an old
 * snapshot until their own slow poll (top recommendations: 5 min) picks it up.
 *
 * This module runs a single bounded poll of /api/run that watches for a
 * `run_id` CHANGE. When a new run is published, it notifies every affected
 * panel so they adopt the completed run immediately — via read-only fetches
 * only (never POST /api/run/refresh), so a refresh never triggers another
 * broker scan. The poll is bounded so it stops on its own (or on first adopt),
 * leaving eventual consistency to the existing slow polls.
 */

import { fetchWithTimeout, readJsonSafely } from './api-core.js';

const POLL_INTERVAL_MS = 2000;
const MAX_POLLS = 30; // ~60s bounded window, then give up quietly

let watcher = null; // { timer, pollsLeft, baselineRunId, adopted }
let adoptHandler = null;

function extractRunId(payload) {
    const run = payload && payload.snapshot && payload.snapshot.run;
    return run ? run.run_id : null;
}

function isStillRefreshing(payload) {
    const state = payload && payload.attempt && payload.attempt.state;
    return state === 'queued' || state === 'refreshing';
}

function clearTimer() {
    if (watcher && watcher.timer) {
        clearTimeout(watcher.timer);
        watcher.timer = null;
    }
}

function stop() {
    clearTimer();
    watcher = null;
}

/**
 * Poll once. The first observation establishes the baseline run id; a later
 * observation with a DIFFERENT run id means the refresh completed and a new
 * immutable snapshot was published — adopt it once and stop.
 */
async function poll() {
    if (!watcher) return;
    if (watcher.pollsLeft <= 0) {
        stop();
        return;
    }
    watcher.pollsLeft -= 1;

    let payload = null;
    try {
        const resp = await fetchWithTimeout(
            '/api/run',
            { headers: { 'Cache-Control': 'no-cache' } },
            10000
        );
        payload = await readJsonSafely(resp);
    } catch {
        // transient failure — keep polling until the bounded budget is spent
    }

    const runId = extractRunId(payload);

    // Adopt on the first observed run_id change from the baseline.
    if (runId && watcher.baselineRunId !== null && runId !== watcher.baselineRunId && !watcher.adopted) {
        watcher.adopted = true;
        stop();
        if (typeof adoptHandler === 'function') {
            adoptHandler(runId);
        }
        return;
    }
    if (runId) watcher.baselineRunId = runId;

    // Stop early once the refresh attempt has resolved without a new run_id
    // (nothing left to adopt) unless we never observed a baseline yet.
    if (watcher.baselineRunId !== null && payload && !isStillRefreshing(payload)) {
        stop();
        return;
    }

    watcher.timer = setTimeout(poll, POLL_INTERVAL_MS);
}

/**
 * Register the handler invoked (once) when a new run is adopted after a change
 * in run_id. The handler must re-render panels via read-only fetches and must
 * NOT POST /api/run/refresh.
 */
export function onRunAdopted(handler) {
    adoptHandler = typeof handler === 'function' ? handler : null;
}

/**
 * Start the single bounded run-state poll. If a watcher is already running it
 * is left in place (exactly one poll at a time).
 */
export function startRunWatcher() {
    if (watcher) return;
    watcher = { timer: null, pollsLeft: MAX_POLLS, baselineRunId: null, adopted: false };
    watcher.timer = setTimeout(poll, POLL_INTERVAL_MS);
}

/** Stop the poll (used for cleanup/testing). */
export function stopRunWatcher() {
    stop();
    adoptHandler = null;
}
