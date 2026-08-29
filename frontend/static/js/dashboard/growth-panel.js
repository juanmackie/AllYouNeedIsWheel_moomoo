/**
 * Growth panel — equity curve + path-to-5x pace + trade journal stats.
 *
 * Data sources (both read-only):
 * - /api/portfolio/history          → persisted per-run portfolio snapshots
 * - /api/options/analytics/lifecycle → inferred trade events + analytics
 */
import { formatCurrency } from '../utils/formatters.js';
import { createSparkline } from '../utils/sparklines.js';

function _fmtDate(iso) {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso.slice(0, 10);
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

async function fetchHistory() {
    try {
        const resp = await fetch('/api/portfolio/history?limit=365');
        if (!resp.ok) return null;
        return await resp.json();
    } catch (err) {
        console.error('Growth panel: history fetch failed:', err);
        return null;
    }
}

async function fetchJournal() {
    try {
        const resp = await fetch('/api/options/analytics/lifecycle?limit=200');
        if (!resp.ok) return null;
        return await resp.json();
    } catch (err) {
        console.error('Growth panel: journal fetch failed:', err);
        return null;
    }
}

function renderEquityCurve(container, series) {
    container.innerHTML = '';
    if (!series || series.length < 2) {
        container.textContent =
            'Not enough history yet — the curve builds one snapshot per completed run.';
        return;
    }
    const navs = series.map((point) => point.net_liquidation).filter((v) => Number.isFinite(v));
    const width = Math.min(720, Math.max(280, container.clientWidth || 480));
    const height = 120;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', height);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `Net liquidation across ${navs.length} runs`);

    const min = Math.min(...navs);
    const max = Math.max(...navs);
    const range = max - min || 1;
    const points = navs
        .map((value, index) => {
            const x = (index / (navs.length - 1)) * width;
            const y = height - 8 - ((value - min) / range) * (height - 16);
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        })
        .join(' ');

    const rising = navs[navs.length - 1] >= navs[0];
    const color = rising ? '#198754' : '#dc3545';

    const area = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
    area.setAttribute('points', `0,${height} ${points} ${width},${height}`);
    area.setAttribute('fill', color);
    area.setAttribute('opacity', '0.12');

    const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    line.setAttribute('points', points);
    line.setAttribute('fill', 'none');
    line.setAttribute('stroke', color);
    line.setAttribute('stroke-width', '2');

    svg.appendChild(area);
    svg.appendChild(line);
    container.appendChild(svg);

    const caption = document.createElement('p');
    caption.className = 'mb-0 mt-1 text-muted small';
    caption.textContent = `${_fmtDate(series[0].captured_at)} → ${_fmtDate(
        series[series.length - 1].captured_at,
    )} · ${series.length} snapshots`;
    container.appendChild(caption);
}

function renderPace(payload, series) {
    const badge = document.getElementById('growth-track-badge');
    const navEl = document.getElementById('growth-current-nav');
    const targetEl = document.getElementById('growth-target-nav');
    if (!badge && !navEl && !targetEl) return; // panel not present in this DOM
    // Server-declared target multiple (active preset); falls back to the
    // project-wide 5x goal.
    const targetMultiple = Number(payload?.target_multiple) || 5;
    const latest = series && series.length ? series[series.length - 1] : null;

    if (!latest) {
        if (badge) {
            badge.textContent = 'NO HISTORY';
            badge.className = 'badge bg-secondary';
        }
        if (navEl) navEl.textContent = '—';
        if (targetEl) targetEl.textContent = '—';
        return;
    }

    const currentNav = Number(latest.net_liquidation) || 0;
    const targetNav = currentNav * targetMultiple;
    if (navEl) navEl.textContent = formatCurrency(currentNav);
    if (targetEl) targetEl.textContent = formatCurrency(targetNav);
    const targetLabel = document.getElementById('growth-target-label');
    if (targetLabel) targetLabel.textContent = `${targetMultiple}x`;

    // Server-side pace math (core.growth_mode.growth_pace) is the single
    // source of truth — the client never recomputes pace.
    const serverPace = payload && payload.pace ? payload.pace : null;
    const progressPct = Number(serverPace?.progress_pct) || 0;
    const pace = serverPace && serverPace.annualized_pace !== null ? Number(serverPace.annualized_pace) : null;
    const etaDays = serverPace && serverPace.eta_days !== null ? Number(serverPace.eta_days) : null;

    const progressEl = document.getElementById('growth-progress');
    const barEl = document.getElementById('growth-progress-bar');
    if (progressEl) {
        progressEl.textContent = `${Math.max(0, Math.min(progressPct, 100)).toFixed(1)}% of the way to ${targetMultiple}x`;
    }
    if (barEl) {
        barEl.style.width = `${Math.max(0, Math.min(progressPct, 100)).toFixed(1)}%`;
        barEl.setAttribute('aria-valuenow', progressPct.toFixed(1));
    }

    const paceEl = document.getElementById('growth-annualized');
    if (!paceEl) return;
    if (pace === null) {
        paceEl.textContent = '—';
    } else {
        paceEl.textContent = `${(pace * 100).toFixed(1)}%/yr`;
        paceEl.classList.toggle('text-success', pace > 0);
        paceEl.classList.toggle('text-danger', pace < 0);
    }

    const etaEl = document.getElementById('growth-eta');
    if (etaDays === null) {
        etaEl.textContent = '—';
    } else if (etaDays > 365.25 * 40) {
        etaEl.textContent = '>40y';
    } else {
        const years = Math.floor(etaDays / 365.25);
        const months = Math.round((etaDays % 365.25) / 30.44);
        etaEl.textContent = years > 0 ? `~${years}y ${months}m` : `~${months}m`;
    }

    if (badge) {
        if (pace === null) {
            badge.textContent = 'COLLECTING DATA';
            badge.className = 'badge bg-secondary';
        } else if (pace > 0) {
            badge.textContent = 'COMPOUNDING';
            badge.className = 'badge bg-success';
        } else {
            badge.textContent = 'FLAT/DOWN';
            badge.className = 'badge bg-warning text-dark';
        }
    }
}

function renderJournal(payload) {
    const winRateEl = document.getElementById('journal-win-rate');
    if (!winRateEl) return; // panel not present in this DOM
    const analytics = payload && payload.analytics ? payload.analytics : null;
    if (!analytics) {
        winRateEl.textContent = '—';
        return;
    }
    const winRate = analytics.win_rate;
    winRateEl.textContent = typeof winRate === 'number' ? `${winRate.toFixed(0)}%` : '—';
    const exitsEl = document.getElementById('journal-exits');
    if (exitsEl) exitsEl.textContent = String(analytics.total_exits ?? 0);
    const rollsEl = document.getElementById('journal-rolls');
    if (rollsEl) rollsEl.textContent = String(analytics.roll_count ?? 0);

    let entries = 0;
    for (const symbolStats of analytics.per_symbol || []) {
        entries += Number(symbolStats.entries) || 0;
    }
    const entriesEl = document.getElementById('journal-entries');
    if (entriesEl) entriesEl.textContent = String(entries);
}

export async function renderGrowthPanel() {
    const [historyPayload, journalPayload] = await Promise.all([fetchHistory(), fetchJournal()]);
    const series = historyPayload ? historyPayload.series : [];

    const curveContainer = document.getElementById('growth-equity-curve');
    if (curveContainer) renderEquityCurve(curveContainer, series);
    renderPace(historyPayload, series);
    renderJournal(journalPayload);

    const snapshotCount = document.getElementById('journal-snapshots');
    if (snapshotCount && historyPayload) snapshotCount.textContent = String(historyPayload.count || 0);
}
