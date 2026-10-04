/**
 * Operational strip: env, read-only, market state, run status, coverage,
 * freshness, and the one manual refresh action.
 *
 * The strip distinguishes three distinct facts:
 *   - attempt state: the latest refresh attempt (refreshing / failed / succeeded)
 *   - the last-good snapshot: retained and shown even after a failed attempt
 *   - freshness of quote data: labeled as data age with the actual broker/market
 *     fetch timestamp in UTC — never as broker quote age, and never local "now"
 *
 * Rendering is split from fetching so the single shared run-state poll
 * (run-notifier.js) can render the strip from the payload it already fetched,
 * keeping exactly one /api/run request per 5s tick.
 */

import { fetchWithTimeout, readJsonSafely } from './api-core.js';
import { renderActiveWatchlist } from './active-watchlist.js';
import { refreshRun } from './api-run.js';

// A healthy status read of the same run does not resolve a rejected refresh.
let refreshRequestError = null;
let lastRenderedRunId = null;

const STATUS_CLASSES = {
    ready: 'bg-success',
    partial: 'bg-warning text-dark',
    planning: 'bg-info text-dark',
    stale: 'bg-danger',
    refreshing: 'bg-primary',
    failed: 'bg-danger',
};

function setBadge(id, text, cls) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    el.className = `badge ${cls || 'bg-secondary'}`;
}

function utcHms(tsMs) {
    return new Date(tsMs).toISOString().slice(11, 19);
}

function setRunWarning(warning) {
    const banner = document.getElementById('run-warning-banner');
    if (!banner) return;
    for (const field of ['title', 'reason', 'action']) {
        const el = document.getElementById(`run-warning-${field}`);
        const text = warning?.[field] || '';
        // API errors are plain text, and unchanged polls must not re-announce them.
        if (el && el.textContent !== text) el.textContent = text;
    }
    banner.className = warning
        ? `alert alert-${warning.tone || 'danger'} show mb-3`
        : 'alert d-none mb-3';
}

function renderRunWarning(attempt, snapshot) {
    if (refreshRequestError) {
        setRunWarning({
            title: 'Refresh could not start',
            reason: refreshRequestError,
            action: 'Any previous results remain displayed. Check the app and OpenD connection, then select Refresh run.',
        });
        return;
    }
    if (attempt?.state === 'failed') {
        setRunWarning({
            title: 'Refresh failed',
            reason: attempt.latest_error || 'The latest refresh did not complete.',
            action: (snapshot?.run
                ? 'Previous successful results are still shown. '
                : 'No completed results are available. ') +
                'Check the OpenD connection and login, resolve the error above, then select Refresh run.',
        });
        return;
    }
    const quota = snapshot?.rejected?.find((item) => item.reason_code === 'scan_infeasible');
    if (quota) {
        setRunWarning({
            title: 'CSP scan blocked by OpenD quota',
            reason: quota.reason_text || 'The full watchlist cannot be scanned within the current OpenD quota and scan budget.',
            action: 'Contract discovery needs more time than the configured scan budget. Review the discovery estimate, then select Refresh run. Copy actions stay blocked until coverage is complete; covered-call results are review only.',
            tone: 'warning',
        });
        return;
    }
    setRunWarning(null);
}

function renderFreshness(freshnessEl, run) {
    const fetches = Object.values(run.quote_fetched_at || {})
        .filter((ts) => ts)
        .map((ts) => new Date(ts).getTime())
        .filter((tsMs) => Number.isFinite(tsMs));
    const maxAgeSec = run.max_tradeable_age_sec != null ? run.max_tradeable_age_sec : 0;
    if (fetches.length) {
        // The latest actual broker/market fetch timestamp, rendered in UTC.
        const latestUtc = utcHms(Math.max(...fetches));
        const dataAgeSec = Math.max(...fetches.map((tsMs) => Math.round((Date.now() - tsMs) / 1000)));
        freshnessEl.textContent =
            `fetch ${latestUtc}Z · data ${dataAgeSec}s old ` +
            `(max ${maxAgeSec}s)`;
    } else {
        freshnessEl.textContent = run.market_state === 'closed'
            ? 'quote stale (market closed)'
            : 'quote stale';
    }
}

/**
 * Pure render of the strip from an already-fetched attempt + snapshot.
 * Used by loadRunStrip() (fetch + render) and by the shared run-state poll,
 * which passes in the payload it just fetched so no second request is made.
 *
 * @param {object|null} attempt - latest refresh attempt state
 * @param {object|null} snapshot - latest completed snapshot (may be null)
 * @param {object} [opts] - { presetLabel } applied when provided
 */
export function renderRunStrip(attempt, snapshot, opts = {}) {
    const envEl = document.getElementById('run-env');
    if (!envEl) return;

    if (opts.presetLabel) {
        setBadge('run-preset', opts.presetLabel, 'bg-secondary');
    }

    const active = attempt?.state === 'refreshing' || attempt?.state === 'queued';
    const runId = snapshot?.run?.run_id ?? null;
    if (active || runId !== lastRenderedRunId) refreshRequestError = null;
    lastRenderedRunId = runId;
    renderRunWarning(attempt, snapshot);
    renderActiveWatchlist(snapshot);

    if (!snapshot?.run) {
        if (attempt?.state === 'failed' || refreshRequestError) {
            setBadge('run-status', 'FAILED', STATUS_CLASSES.failed);
        } else if (active) {
            setBadge('run-status', 'REFRESHING', STATUS_CLASSES.refreshing);
        } else {
            setBadge('run-status', 'NO RUN', 'bg-secondary');
        }
        return;
    }

    const run = snapshot.run;
    const status = snapshot.effective_status || (snapshot.tradeable ? run.status : 'stale');
    setBadge('run-env', run.env || '--', run.env === 'REAL' ? 'bg-danger' : 'bg-secondary');
    setBadge('run-market', `MARKET ${(run.market_state || 'unknown').toUpperCase()}`, 'bg-secondary');

    // Retain last-good metadata even while a refresh is active or failed.
    if (active) {
        setBadge('run-status', `REFRESHING ${Math.round((attempt.progress || 0) * 100)}%`, STATUS_CLASSES.refreshing);
    } else if (attempt?.state === 'failed' || refreshRequestError) {
        setBadge('run-status', 'FAILED', STATUS_CLASSES.failed);
    } else {
        setBadge('run-status', status.toUpperCase(), STATUS_CLASSES[status] || 'bg-secondary');
    }

    const lastEl = document.getElementById('run-last-success');
    if (lastEl) {
        const published = run.published_at ? new Date(run.published_at) : null;
        lastEl.textContent = published
            ? `last success: ${utcHms(published.getTime())}Z`
            : 'no successful run yet';
    }
    const coverageEl = document.getElementById('run-coverage');
    if (coverageEl) {
        coverageEl.textContent = active
            ? `stage: ${attempt.stage || attempt.state}`
            : run.coverage_total > 0
                ? `coverage ${run.coverage_scanned}/${run.coverage_total}`
                : 'coverage n/a';
    }
    const freshnessEl = document.getElementById('run-freshness');
    if (freshnessEl) {
        renderFreshness(freshnessEl, run);
    }
}

export function renderRunCommunicationError() {
    // Surface a communication failure instead of failing silently; the last-
    // good snapshot stays visible underneath the COMM ERROR badge.
    setBadge('run-status', 'COMM ERROR', 'bg-danger');
    const coverageEl = document.getElementById('run-coverage');
    if (coverageEl) coverageEl.textContent = 'cannot reach run API';
    setRunWarning({
        title: 'Run status unavailable',
        reason: 'Cannot reach or read the run API. Displayed results may be outdated.',
        action: 'Status checks retry automatically. Check that the app is running and the connection is available before using retained results.',
    });
}

export async function loadRunStrip() {
    const envEl = document.getElementById('run-env');
    if (!envEl) return;

    let attempt = null;
    let snapshot = null;
    try {
        const resp = await fetchWithTimeout(
            '/api/run',
            { headers: { 'Cache-Control': 'no-cache' } },
            10000
        );
        if (!resp.ok) {
            renderRunCommunicationError();
            return;
        }
        const payload = await readJsonSafely(resp);
        if (!payload) {
            renderRunCommunicationError();
            return;
        }
        attempt = payload?.attempt ?? null;
        snapshot = payload?.snapshot ?? null;
    } catch (err) {
        console.error('Run strip failed:', err);
        renderRunCommunicationError();
        return;
    }

    // The active preset label is static per session; fetch it once here rather
    // than on every 5s poll tick.
    let presetLabel = null;
    try {
        const settingsResp = await fetch('/api/settings');
        if (settingsResp.ok) {
            const settings = await settingsResp.json();
            const activePreset = settings.active || 'balanced';
            presetLabel = (settings.presets && settings.presets[activePreset]?.label) || activePreset;
        }
    } catch (err) {
        console.debug('Preset label unavailable:', err);
    }

    renderRunStrip(attempt, snapshot, { presetLabel });
}

export function initRunStrip() {
    const btn = document.getElementById('run-refresh-btn');
    if (btn && !btn.dataset.bound) {
        btn.dataset.bound = 'true';
        btn.addEventListener('click', async () => {
            btn.disabled = true;
            try {
                await refreshRun();
                refreshRequestError = null;
            } catch (err) {
                refreshRequestError = err.message || 'The refresh request failed.';
                renderRunWarning(null, null);
                setBadge('run-status', 'FAILED', STATUS_CLASSES.failed);
            } finally {
                btn.disabled = false;
            }
        });
    }
}
