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

async function fetchGoalFlows() {
    try {
        const resp = await fetch('/api/portfolio/goal-flows');
        if (!resp.ok) return null;
        return await resp.json();
    } catch (err) {
        console.error('Growth panel: cash-flow fetch failed:', err);
        return null;
    }
}

async function refreshGrowthPanel(message = '') {
    const messageEl = document.getElementById('goal-flow-message');
    if (messageEl) messageEl.textContent = message;
    await renderGrowthPanel();
}

function bindGoalFlowControls() {
    const form = document.getElementById('goal-flow-form');
    if (!form || form.dataset.bound === 'true') return;
    form.dataset.bound = 'true';
    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const body = {
            date: document.getElementById('goal-flow-date')?.value,
            direction: document.getElementById('goal-flow-direction')?.value,
            amount: Number(document.getElementById('goal-flow-amount')?.value),
            currency: document.getElementById('goal-flow-currency')?.value,
        };
        try {
            const response = await fetch('/api/portfolio/goal-flows', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Could not add flow');
            form.reset();
            const currency = document.getElementById('goal-flow-currency');
            if (currency) currency.value = 'USD';
            await refreshGrowthPanel('Flow added. Confirm completeness again after reviewing the list.');
        } catch (error) {
            await refreshGrowthPanel(error.message || 'Could not add flow');
        }
    });

    const list = document.getElementById('goal-owner-flows');
    if (list && list.dataset.bound !== 'true') {
        list.dataset.bound = 'true';
        list.addEventListener('click', async (event) => {
            const button = event.target.closest('[data-delete-flow]');
            if (!button) return;
            try {
                const response = await fetch(`/api/portfolio/goal-flows/${encodeURIComponent(button.dataset.deleteFlow)}`, {
                    method: 'DELETE',
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || 'Could not remove flow');
                await refreshGrowthPanel('Flow removed. Confirm completeness again after reviewing the list.');
            } catch (error) {
                await refreshGrowthPanel(error.message || 'Could not remove flow');
            }
        });
    }

    const confirmButton = document.getElementById('goal-flow-confirm');
    if (confirmButton && confirmButton.dataset.bound !== 'true') {
        confirmButton.dataset.bound = 'true';
        confirmButton.addEventListener('click', async () => {
            const through = confirmButton.dataset.currentMarketDate || confirmButton.dataset.through;
            if (!through) {
                await refreshGrowthPanel('No account history date is available to confirm.');
                return;
            }
            try {
                const response = await fetch('/api/portfolio/goal-flows/completeness', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ complete: true, through }),
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || 'Could not confirm flow history');
                await refreshGrowthPanel('Flow history confirmed.');
            } catch (error) {
                await refreshGrowthPanel(error.message || 'Could not confirm flow history');
            }
        });
    }

    const revokeButton = document.getElementById('goal-flow-revoke');
    if (revokeButton && revokeButton.dataset.bound !== 'true') {
        revokeButton.dataset.bound = 'true';
        revokeButton.addEventListener('click', async () => {
            try {
                const response = await fetch('/api/portfolio/goal-flows/completeness', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ complete: false }),
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || 'Could not revoke confirmation');
                await refreshGrowthPanel('Flow confirmation revoked.');
            } catch (error) {
                await refreshGrowthPanel(error.message || 'Could not revoke confirmation');
            }
        });
    }
}

function renderGoalFlows(payload) {
    const statusEl = document.getElementById('goal-flow-status');
    const list = document.getElementById('goal-owner-flows');
    if (statusEl) {
        const complete = Boolean(payload?.flow_completeness?.complete);
        statusEl.textContent = complete ? `COMPLETE THROUGH ${payload.flow_completeness.attested_through}` : 'FLOWS INCOMPLETE';
        statusEl.className = `badge ${complete ? 'bg-success' : 'bg-warning text-dark'}`;
    }
    if (!list) return;
    list.replaceChildren();
    const rows = payload?.flows || [];
    if (!rows.length) {
        list.textContent = 'No owner-declared flows.';
    } else {
        for (const flow of rows) {
            const row = document.createElement('div');
            const detail = document.createElement('span');
            const amount = Number(flow.amount);
            detail.textContent = `${flow.date} · ${flow.direction === 'OUT' ? 'Withdrawal' : 'Deposit'} · ${Number.isFinite(amount) ? amount.toFixed(2) : '—'} ${flow.currency || ''}`;
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'btn btn-sm btn-link text-danger py-0';
            remove.dataset.deleteFlow = flow.cashflow_id;
            remove.textContent = 'Remove';
            row.append(detail, ' ', remove);
            list.appendChild(row);
        }
    }
    const dateInput = document.getElementById('goal-flow-date');
    if (dateInput && payload?.baseline_date) dateInput.min = payload.baseline_date;
    if (dateInput && payload?.current_market_date) dateInput.max = payload.current_market_date;
    const confirmButton = document.getElementById('goal-flow-confirm');
    if (confirmButton && payload?.flow_completeness) {
        confirmButton.dataset.through = payload.flow_completeness.required_through || '';
        confirmButton.dataset.currentMarketDate = payload.current_market_date || '';
    }
    bindGoalFlowControls();
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
    // S04: render every target label from the server objective, including
    // initial/empty states.
    const headingEl = document.getElementById('growth-heading');
    const etaLabelEl = document.getElementById('growth-eta-label');
    if (headingEl) headingEl.textContent = `Path to ${targetMultiple}x`;
    if (etaLabelEl) etaLabelEl.textContent = `ETA to ${targetMultiple}x`;
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
    // C07/S04: render the server's durable target and status — never recompute a
    // moving target from current NAV on the client.
    const serverPace0 = payload?.pace || {};
    const targetNav = Number(serverPace0.target_nav);
    const reached = Boolean(serverPace0.reached);
    if (navEl) navEl.textContent = formatCurrency(currentNav);
    if (targetEl) targetEl.textContent = Number.isFinite(targetNav) ? formatCurrency(targetNav) : '—';
    const targetLabel = document.getElementById('growth-target-label');
    if (targetLabel) targetLabel.textContent = `${targetMultiple}x`;

    // Server-side pace math (core.goal_pace) is the single
    // source of truth — the client never recomputes pace.
    const serverPace = payload && payload.pace ? payload.pace : null;
    const flowsIncomplete = serverPace?.confidence_state === 'flows_incomplete';
    const progressPct = Number(serverPace?.progress_pct) || 0;
    const pace = serverPace && serverPace.annualized_pace !== null ? Number(serverPace.annualized_pace) : null;
    const statusReached = Boolean(serverPace?.reached);
    const requiredCagr = serverPace?.required_cagr === null ? null : Number(serverPace?.required_cagr);
    const gapPp = serverPace?.gap_pp === null ? null : Number(serverPace?.gap_pp);

    const progressEl = document.getElementById('growth-progress');
    const barEl = document.getElementById('growth-progress-bar');
    if (progressEl) {
        progressEl.textContent = flowsIncomplete
            ? '— · flows incomplete'
            : `${Math.max(0, Math.min(progressPct, 100)).toFixed(1)}% of the way to ${targetMultiple}x`;
    }
    if (barEl) {
        barEl.style.width = flowsIncomplete ? '0%' : `${Math.max(0, Math.min(progressPct, 100)).toFixed(1)}%`;
        barEl.setAttribute('aria-valuenow', progressPct.toFixed(1));
        if (flowsIncomplete) {
            barEl.setAttribute('aria-valuetext', 'Unavailable until cash-flow history is confirmed complete');
            barEl.classList.remove('bg-success');
            barEl.classList.add('bg-secondary');
        } else {
            barEl.removeAttribute('aria-valuetext');
            barEl.classList.remove('bg-secondary');
            barEl.classList.add('bg-success');
        }
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

    const requiredEl = document.getElementById('growth-required-cagr');
    if (requiredEl) requiredEl.textContent = Number.isFinite(requiredCagr) ? `${(requiredCagr * 100).toFixed(1)}%/yr` : '—';
    const gapEl = document.getElementById('growth-gap-pp');
    if (gapEl) {
        gapEl.textContent = Number.isFinite(gapPp) ? `${gapPp > 0 ? '+' : ''}${gapPp.toFixed(1)} pp` : '—';
        gapEl.classList.toggle('text-success', Number.isFinite(gapPp) && gapPp >= 0);
        gapEl.classList.toggle('text-danger', Number.isFinite(gapPp) && gapPp < 0);
    }

    const etaEl = document.getElementById('growth-eta');
    if (statusReached) {
        etaEl.textContent = 'Reached';
    } else if (!serverPace?.observation_ready || !Number.isFinite(pace)) {
        etaEl.textContent = '—';
    } else if (serverPace?.years_at_current_pace === null || serverPace?.years_at_current_pace === undefined) {
        etaEl.textContent = 'Never at current pace';
    } else {
        const years = Number(serverPace?.years_at_current_pace);
        etaEl.textContent = Number.isFinite(years) ? `~${years.toFixed(1)} years` : '—';
    }

    if (badge) {
        if (flowsIncomplete) {
            badge.textContent = 'FLOWS INCOMPLETE';
            badge.className = 'badge bg-warning text-dark';
        } else if (statusReached) {
            badge.textContent = 'TARGET REACHED';
            badge.className = 'badge bg-success';
        } else if (!serverPace?.observation_ready || pace === null) {
            badge.textContent = 'COLLECTING 60 DAYS';
            badge.className = 'badge bg-secondary';
        } else if (serverPace?.on_track === true) {
            badge.textContent = 'ON REQUIRED PACE';
            badge.className = 'badge bg-success';
        } else {
            badge.textContent = 'BELOW REQUIRED PACE';
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
    const [historyPayload, journalPayload, flowPayload] = await Promise.all([
        fetchHistory(),
        fetchJournal(),
        fetchGoalFlows(),
    ]);
    const series = historyPayload ? historyPayload.series : [];

    const curveContainer = document.getElementById('growth-equity-curve');
    if (curveContainer) renderEquityCurve(curveContainer, series);
    renderPace(historyPayload, series);
    renderJournal(journalPayload);
    renderGoalFlows(flowPayload);

    const snapshotCount = document.getElementById('journal-snapshots');
    if (snapshotCount && historyPayload) snapshotCount.textContent = String(historyPayload.count || 0);
}
