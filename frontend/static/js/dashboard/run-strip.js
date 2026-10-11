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
import { setAlertState, setTextIfChanged } from '../utils/status-alert.js';

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

const REFRESH_STAGE_LABELS = {
    account: 'Checking OpenD account',
    portfolio: 'Loading portfolio context',
    scan: 'Preparing watchlist scan',
    discover: 'Discovering option contracts',
    csp: 'Scoring cash-secured puts',
    cc: 'Scoring covered calls',
    roll: 'Checking roll pressure',
    publish: 'Publishing completed run',
};

function formatElapsed(startedAt, nowMs = Date.now()) {
    const startedMs = Date.parse(startedAt || '');
    if (!Number.isFinite(startedMs)) return 'time unavailable';
    const totalSeconds = Math.max(0, Math.floor((nowMs - startedMs) / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return minutes ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

function renderRefreshProgress(attempt) {
    const details = document.getElementById('run-progress-details');
    if (!details) return;

    const active = attempt?.state === 'refreshing' || attempt?.state === 'queued';
    details.hidden = !active;
    if (!active) return;

    const stage = String(attempt.stage || attempt.state || 'refreshing');
    const stageEl = document.getElementById('run-progress-stage');
    if (stageEl) stageEl.textContent = REFRESH_STAGE_LABELS[stage] || stage;

    const elapsedEl = document.getElementById('run-progress-elapsed');
    if (elapsedEl) elapsedEl.textContent = formatElapsed(attempt.started_at);

    const updatedEl = document.getElementById('run-progress-updated');
    if (updatedEl) {
        const updatedMs = Date.parse(attempt.updated_at || '');
        updatedEl.textContent = Number.isFinite(updatedMs) ? `${utcHms(updatedMs)}Z` : '';
    }

    const messageEl = document.getElementById('run-progress-message');
    if (messageEl) {
        messageEl.textContent = 'Progress advances as each broker step completes.';
    }
}

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
        // API errors are plain text, and unchanged polls must not re-announce them.
        setTextIfChanged(document.getElementById(`run-warning-${field}`), warning?.[field] || '');
    }
    setAlertState(banner, { tone: warning?.tone || 'danger', visible: Boolean(warning) });
}

const MAX_NAMED_FAILURE_GROUPS = 5;

/**
 * Broker-data failures recorded in the snapshot. Coverage truth comes from
 * the server (`eligibility.coverage`), never from browser arithmetic.
 * Symbols that share a reason are grouped so the banner stays short.
 */
function describeBrokerDataFailure(snapshot) {
    const run = snapshot?.run;
    if (!run) return null;
    const failures = (snapshot.rejected || []).filter((item) => item?.reason_code === 'broker_data_unavailable');
    const coverage = snapshot.eligibility?.coverage;
    const partial = coverage?.truth === 'partial';
    if (!partial && !failures.length) return null;

    const groups = new Map();
    for (const item of failures) {
        const reason = item.reason_text || 'Broker data unavailable';
        groups.set(reason, [...(groups.get(reason) || []), item.ticker || 'unknown']);
    }
    const named = Array.from(groups.entries())
        .slice(0, MAX_NAMED_FAILURE_GROUPS)
        .map(([reason, tickers]) => `${tickers.join(', ')}: ${reason}`);
    if (groups.size > MAX_NAMED_FAILURE_GROUPS) named.push('more in Ticker diagnostics');
    const reason = named.join('; ') || coverage?.reasons?.[0] || 'Some watchlist symbols have no broker data.';

    if (partial) {
        const total = run.coverage_total || 0;
        const missing = Math.max(total - (run.coverage_scanned || 0), 0);
        return {
            title: `Copy blocked: broker data missing for ${missing} of ${total} watchlist symbols`,
            reason,
            action: 'No pick can be copied until every watchlist symbol is covered. Select Refresh run to try again. ' +
                'Contract discovery from today is reused, so the retry is faster.',
        };
    }
    return {
        title: `Broker data missing for ${failures.length} symbol${failures.length === 1 ? '' : 's'}`,
        reason,
        action: 'These symbols were not assessed in this run. Other picks are not affected. Select Refresh run to try again.',
        tone: 'warning',
    };
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
    const dataFailure = describeBrokerDataFailure(snapshot);
    if (dataFailure) {
        setRunWarning(dataFailure);
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
        setBadge('run-preset', `CURRENT PRESET ${opts.presetLabel}`, 'bg-secondary');
    }
    const snapshotPreset = snapshot?.preset;
    const snapshotPresetLabel = snapshotPreset?.label
        ? `${snapshotPreset.label}${snapshotPreset.version ? ` v${snapshotPreset.version}` : ''}`
        : '--';
    setBadge('run-snapshot-preset', `PUBLISHED SNAPSHOT ${snapshotPresetLabel}`, 'bg-secondary');

    const active = attempt?.state === 'refreshing' || attempt?.state === 'queued';
    renderRefreshProgress(attempt);
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
        setBadge('run-status', 'REFRESHING', STATUS_CLASSES.refreshing);
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
        const incomplete = !active && run.coverage_total > 0 && run.coverage_scanned < run.coverage_total;
        coverageEl.textContent = active
            ? `stage: ${attempt.stage || attempt.state}`
            : run.coverage_total > 0
                ? `coverage ${run.coverage_scanned}/${run.coverage_total}${incomplete ? ' INCOMPLETE' : ''}`
                : 'coverage n/a';
        // Incomplete coverage blocks copy, so it must not look like routine muted text.
        coverageEl.classList.toggle('text-danger', incomplete);
        coverageEl.classList.toggle('fw-semibold', incomplete);
        coverageEl.classList.toggle('text-muted', !incomplete);
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
    const progressDetails = document.getElementById('run-progress-details');
    if (progressDetails) progressDetails.hidden = true;
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

    // The preset selector owns the current-settings label. Keeping this run
    // poll independent prevents a delayed settings response from overwriting a
    // newer preset selection.
    renderRunStrip(attempt, snapshot);
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
