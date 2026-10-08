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

function renderNextOpen(el, snapshot, run) {
    if (!el) return;
    if (!run) {
        el.textContent = '';
        return;
    }
    if (run.market_state === 'open') {
        el.textContent = 'US market open';
        return;
    }
    const session = snapshot?.active_watchlist?.market_session;
    const nextOpenMs = Date.parse(session?.next_open_at || '');
    if (!Number.isFinite(nextOpenMs)) {
        el.textContent = '';
        return;
    }
    const remainingMinutes = Math.ceil((nextOpenMs - Date.now()) / 60_000);
    if (remainingMinutes <= 0) {
        el.textContent = 'US open estimate passed · refresh run';
        return;
    }
    const hours = Math.floor(remainingMinutes / 60);
    const minutes = remainingMinutes % 60;
    const countdown = hours ? `${hours}h ${String(minutes).padStart(2, '0')}m` : `${minutes}m`;
    const marketTime = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit',
    }).format(nextOpenMs);
    const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'local';
    const localTime = new Intl.DateTimeFormat(undefined, {
        hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
    }).format(nextOpenMs);
    const fallback = session?.calendar_source === 'weekday_fallback' ? ' · weekday estimate' : '';
    el.textContent = `US opens in ${countdown} · ${marketTime} ET · ${localTime} ${localZone}${fallback}`;
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

    if (!snapshot?.run) {
        setBadge('run-status', 'NO RUN', 'bg-secondary');
        renderActiveWatchlist(snapshot);
        renderNextOpen(document.getElementById('run-next-open'), snapshot, null);
        return;
    }

    const run = snapshot.run;
    renderActiveWatchlist(snapshot);
    renderNextOpen(document.getElementById('run-next-open'), snapshot, run);
    const status = snapshot.effective_status || (snapshot.tradeable ? run.status : 'stale');
    setBadge('run-env', run.env || '--', run.env === 'REAL' ? 'bg-danger' : 'bg-secondary');
    setBadge('run-market', `MARKET ${(run.market_state || 'unknown').toUpperCase()}`, 'bg-secondary');

    if (attempt?.state === 'refreshing') {
        setBadge('run-status', `REFRESHING ${Math.round((attempt.progress || 0) * 100)}%`, STATUS_CLASSES.refreshing);
        const coverageEl = document.getElementById('run-coverage');
        if (coverageEl) coverageEl.textContent = `stage: ${attempt.stage}`;
        return;
    }

    // A failed refresh attempt keeps the FAILED badge visible while the
    // last-good snapshot is still rendered underneath (retained results).
    if (attempt?.state === 'failed') {
        setBadge('run-status', 'FAILED', STATUS_CLASSES.failed);
    } else {
        setBadge('run-status', status.toUpperCase(), STATUS_CLASSES[status] || 'bg-secondary');
    }

    const lastEl = document.getElementById('run-last-success');
    if (lastEl) {
        const published = run.published_at ? new Date(run.published_at) : null;
        const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'local';
        const localTime = published
            ? new Intl.DateTimeFormat(undefined, {
                hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
            }).format(published)
            : '';
        lastEl.textContent = published
            ? `last success: ${utcHms(published.getTime())}Z · ${localTime} ${localZone}`
            : 'no successful run yet';
    }
    const coverageEl = document.getElementById('run-coverage');
    if (coverageEl) {
        coverageEl.textContent =
            run.coverage_total > 0
                ? `coverage ${run.coverage_scanned}/${run.coverage_total}`
                : 'coverage n/a';
    }
    const freshnessEl = document.getElementById('run-freshness');
    if (freshnessEl) {
        renderFreshness(freshnessEl, run);
    }
}

function setCommError() {
    // Surface a communication failure instead of failing silently; the last-
    // good snapshot stays visible underneath the COMM ERROR badge.
    setBadge('run-status', 'COMM ERROR', 'bg-danger');
    const coverageEl = document.getElementById('run-coverage');
    if (coverageEl) coverageEl.textContent = 'cannot reach run API';
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
            setCommError();
            return;
        }
        const payload = await readJsonSafely(resp);
        attempt = payload?.attempt ?? null;
        snapshot = payload?.snapshot ?? null;
    } catch (err) {
        console.error('Run strip failed:', err);
        setCommError();
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
                const resp = await fetch('/api/run/refresh', {
                    method: 'POST',
                });
                return resp;
            } finally {
                btn.disabled = false;
            }
        });
    }
}
