/**
 * Operational strip: env, read-only, market state, run status, coverage,
 * freshness, and the one manual refresh action.
 *
 * The strip distinguishes three distinct facts:
 *   - attempt state: the latest refresh attempt (refreshing / failed / succeeded)
 *   - the last-good snapshot: retained and shown even after a failed attempt
 *   - freshness of quote data: labeled as data age, never as broker quote age
 */

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

function renderFreshness(freshnessEl, run) {
    const ages = Object.values(run.quote_fetched_at || {})
        .filter((ts) => ts)
        .map((ts) => Math.round((Date.now() - new Date(ts).getTime()) / 1000))
        .filter((a) => Number.isFinite(a));
    const maxAgeSec = run.max_tradeable_age_sec != null ? run.max_tradeable_age_sec : 0;
    if (ages.length) {
        freshnessEl.textContent =
            `UTC fetch ${new Date().toTimeString().slice(0, 8)} · data ${Math.max(...ages)}s old ` +
            `(max ${maxAgeSec}s)`;
    } else {
        freshnessEl.textContent = run.market_state === 'closed'
            ? 'quote stale (market closed)'
            : 'quote stale';
    }
}

function renderSnapshot(attempt, snapshot) {
    const envEl = document.getElementById('run-env');
    const marketEl = document.getElementById('run-market');
    const statusEl = document.getElementById('run-status');
    const lastEl = document.getElementById('run-last-success');
    const coverageEl = document.getElementById('run-coverage');
    const freshnessEl = document.getElementById('run-freshness');

    if (!snapshot?.run) {
        setBadge('run-status', 'NO RUN', 'bg-secondary');
        return;
    }

    const run = snapshot.run;
    const status = snapshot.effective_status || (snapshot.tradeable ? run.status : 'stale');
    setBadge('run-env', run.env || '--', run.env === 'REAL' ? 'bg-danger' : 'bg-secondary');
    setBadge('run-market', `MARKET ${(run.market_state || 'unknown').toUpperCase()}`, 'bg-secondary');

    // A failed refresh attempt keeps the FAILED badge visible while the
    // last-good snapshot is still rendered underneath (retained results).
    if (attempt?.state === 'failed') {
        setBadge('run-status', 'FAILED', STATUS_CLASSES.failed);
    } else {
        setBadge('run-status', status.toUpperCase(), STATUS_CLASSES[status] || 'bg-secondary');
    }

    if (lastEl) {
        const published = run.published_at ? new Date(run.published_at) : null;
        lastEl.textContent = published
            ? `last success: ${published.toLocaleTimeString()}`
            : 'no successful run yet';
    }
    if (coverageEl) {
        coverageEl.textContent =
            run.coverage_total > 0
                ? `coverage ${run.coverage_scanned}/${run.coverage_total}`
                : 'coverage n/a';
    }
    if (freshnessEl) {
        renderFreshness(freshnessEl, run);
    }
}

export async function loadRunStrip() {
    const envEl = document.getElementById('run-env');
    if (!envEl) return;

    try {
        const resp = await fetch('/api/run');
        if (!resp.ok) {
            // Surface a communication failure instead of failing silently.
            setBadge('run-status', 'COMM ERROR', 'bg-danger');
            const coverageEl = document.getElementById('run-coverage');
            if (coverageEl) coverageEl.textContent = 'cannot reach run API';
            return;
        }
        const { attempt, snapshot } = await resp.json();

        const settingsResp = await fetch('/api/settings');
        let activePreset = 'balanced';
        if (settingsResp.ok) {
            const settings = await settingsResp.json();
            activePreset = settings.active || activePreset;
            const presetLabel = (settings.presets && settings.presets[activePreset]?.label) || activePreset;
            setBadge('run-preset', presetLabel, 'bg-secondary');
        }

        if (attempt?.state === 'refreshing') {
            setBadge('run-status', `REFRESHING ${Math.round((attempt.progress || 0) * 100)}%`, STATUS_CLASSES.refreshing);
            const coverageEl = document.getElementById('run-coverage');
            if (coverageEl) coverageEl.textContent = `stage: ${attempt.stage}`;
        } else {
            renderSnapshot(attempt, snapshot);
        }
    } catch (err) {
        console.error('Run strip failed:', err);
    }
}

export function initRunStrip() {
    const btn = document.getElementById('run-refresh-btn');
    if (btn && !btn.dataset.bound) {
        btn.dataset.bound = 'true';
        btn.addEventListener('click', async () => {
            btn.disabled = true;
            try {
                await fetch('/api/run/refresh', {
                    method: 'POST',
                });
            } finally {
                btn.disabled = false;
            }
        });
    }
}
