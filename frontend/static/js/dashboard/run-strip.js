/**
 * Operational strip: env, read-only, market state, run status, coverage,
 * freshness, and the one manual refresh action.
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

export async function loadRunStrip() {
    const envEl = document.getElementById('run-env');
    const marketEl = document.getElementById('run-market');
    const statusEl = document.getElementById('run-status');
    const lastEl = document.getElementById('run-last-success');
    const coverageEl = document.getElementById('run-coverage');
    const freshnessEl = document.getElementById('run-freshness');
    if (!envEl) return;

    try {
        const resp = await fetch('/api/run');
        if (!resp.ok) return;
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
            if (coverageEl) coverageEl.textContent = `stage: ${attempt.stage}`;
        } else if (snapshot?.run) {
            const run = snapshot.run;
            const status = run.status || 'stale';
            setBadge('run-env', run.env || '--', run.env === 'REAL' ? 'bg-danger' : 'bg-secondary');
            setBadge('run-market', `MARKET ${(run.market_state || 'unknown').toUpperCase()}`, 'bg-secondary');
            setBadge('run-status', status.toUpperCase(), STATUS_CLASSES[status] || 'bg-secondary');
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
                const ages = Object.values(run.quote_fetched_at || {}).map((ts) => {
                    const age = (Date.now() - new Date(ts).getTime()) / 1000;
                    return Math.round(age);
                });
                freshnessEl.textContent = ages.length
                    ? `quote age ${Math.max(...ages)}s (max ${run.max_tradeable_age_sec}s)`
                    : '';
            }
        } else {
            setBadge('run-status', 'NO RUN', 'bg-secondary');
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
                await fetch('/api/run/refresh', { method: 'POST' });
            } finally {
                setTimeout(() => { btn.disabled = false; }, 2000);
            }
        });
    }
    loadRunStrip();
    setInterval(loadRunStrip, 5000);
}
