/**
 * Dashboard initialization and position command panel.
 * Split from dashboard.js (F042)
 */
import { loadPortfolioData } from './account.js';
import { initializeTopRecommendations, loadTopRecommendations, isBackendGenerating } from './top-recommendations.js';
import { formatCurrency, escapeHtml } from '../utils/formatters.js';
import { fetchWeeklyOptionIncome } from './api.js';
import { updateCashReserveStatus } from './dashboard-cash.js';
import { updateIdleCashPanel } from './dashboard-cash.js';
import { initWatchlistPanel, loadWatchlist } from './watchlist-panel.js';
import { initRunStrip, loadRunStrip } from './run-strip.js';
import { startRunStatePoll, ensureRunStatePoll, onRunPublished } from './run-notifier.js';
import { renderGrowthPanel } from './growth-panel.js';
import { renderOutcomePanel } from './outcome-panel.js';
import { renderWeeklyIncome } from './weekly-income.js';
import { state as optionsTableState } from './options-table-state.js';

let signalPanelsInitialized = false;

/** Escape a string for safe use inside a double-quoted HTML attribute. */
function escapeAttr(value) {
    return escapeHtml(String(value)).replace(/"/g, '&quot;');
}

/**
 * Initialize the dashboard with progressive loading
 * Wave 1: Account data (critical path)
 * Wave 2: Positions & orders
 * Wave 3: Signals (fast path)
 * Wave 4: Visible signal panels (directly rendered on dashboard)
 */
export async function initializeDashboard() {
    try {
        if (!document.querySelector('.content-container')) {
            const mainContainer = document.querySelector('main .container') || document.querySelector('main');
            if (mainContainer) {
                const contentContainer = document.createElement('div');
                contentContainer.className = 'content-container';
                mainContainer.prepend(contentContainer);
            }
        }

        showWaveLoading('wave1', 'Loading account data...');
        try {
            await loadPortfolioData();
        initWatchlistPanel();
        initRunStrip();
            // Initial operational-strip render (also fetches the active preset
            // label once per session). The shared poll keeps it fresh after.
            await loadRunStrip();
            await updateCashReserveStatus();
        } catch (error) { console.error('Wave 1 error:', error); }
        hideWaveLoading('wave1');

        showWaveLoading('wave2', 'Loading positions...');
        try {
            await loadPositionsCommandPanel();
        } catch (error) { console.error('Wave 2 error:', error); }
        hideWaveLoading('wave2');

        // Signals start loading NOW — before heavier diagnostics
        initializeTopRecommendations();

        showWaveLoading('wave3', 'Loading market data...');
        try {
            await Promise.all([
                updateIdleCashPanel()
            ]);
        } catch (error) { console.error('Wave 3 error:', error); }
        hideWaveLoading('wave3');

        initializeSignalPanels();


        const cashReserveToggle = document.getElementById('cash-reserve-toggle');
        if (cashReserveToggle && !cashReserveToggle.dataset.bound) {
            cashReserveToggle.dataset.bound = 'true';
            cashReserveToggle.addEventListener('change', (e) => toggleCashReserve(e.target.checked));
        }

        // P1b: one shared 5s run-state poll drives the whole screen. Started at
        // page load with an immediate first fetch, it detects every newly
        // published run (including first-ever runs and completions before its
        // first tick) and fans out explicit reloads to every panel. The poll
        // only reads /api/run; the viewer never POSTs /api/run/refresh, so a
        // publish never rolls into another broker scan.
        onRunPublished(reloadAllPanelsAfterPublish);
        startRunStatePoll();
        // A manual refresh click restarts the poll if it stopped in the
        // fresh-install idle state (no run yet, no active attempt).
        const runRefreshBtn = document.getElementById('run-refresh-btn');
        if (runRefreshBtn && !runRefreshBtn.dataset.viewerBound) {
            runRefreshBtn.dataset.viewerBound = 'true';
            runRefreshBtn.addEventListener('click', () => ensureRunStatePoll());
        }
    } catch (error) {
        console.error('Dashboard initialization error:', error);
    }
}

/**
 * P1b publish fan-out: a newly published immutable run causes an explicit
 * reload of every rendered panel via read-only fetches. Each reload runs in its
 * own promise so one panel's failure cannot blank the rest of the screen, and
 * nothing here POSTs /api/run/refresh (a publish never re-triggers a scan).
 */
async function reloadAllPanelsAfterPublish() {
    const reloads = [
        () => loadRunStrip(),
        () => loadPortfolioData(),
        () => loadPositionsCommandPanel(),
        () => loadTopRecommendations(false),
        () => loadWatchlist(),
        () => updateCashReserveStatus(),
        () => updateIdleCashPanel(),
        () => renderGrowthPanel(),
        () => renderOutcomePanel(),
        () => renderWeeklyIncome(),
    ];
    // The options table performs a heavy read-only chain scan; reload it on a
    // publish only when it has actually been loaded, never for an unopened panel.
    if (optionsScannerLoaded()) {
        reloads.push(() => import('./options-table.js').then((mod) => mod.loadTickers()));
    }
    await Promise.all(
        reloads.map((reload) =>
            Promise.resolve().then(reload).catch((err) => {
                console.error('Panel reload failed after publish:', err);
            })
        )
    );
}

function optionsScannerLoaded() {
    return Object.keys(optionsTableState.tickersData || {}).length > 0;
}

/**
 * Load the signal panels rendered directly on the dashboard.
 * These were previously hidden behind the removed research diagnostics lazy gate.
 */
async function initializeSignalPanels() {
    if (signalPanelsInitialized) return;
    signalPanelsInitialized = true;

    import('./weekly-income.js').then(mod => {
        mod.renderWeeklyIncome();
        const refreshBtn = document.getElementById('refresh-filled-orders');
        if (refreshBtn && !refreshBtn.dataset.bound) {
            refreshBtn.dataset.bound = 'true';
            refreshBtn.addEventListener('click', () => mod.renderWeeklyIncome());
        }
    }).catch(err => {
        console.error('Failed to load weekly income:', err);
    });

    import('./growth-panel.js').then(mod => {
        mod.renderGrowthPanel();
        // P1b: the growth panel adopts newly completed runs through the single
        // shared run-state poll (see onRunPublished/reloadAllPanelsAfterPublish
        // above), not a `#refresh-all-btn` template emission (no template emits
        // one). No extra click binding here.
    }).catch(err => {
        console.error('Failed to load growth panel:', err);
    });

    import('./outcome-panel.js').then(mod => {
        mod.renderOutcomePanel();
        // Same C10 adoption path as the growth panel — the outcome view
        // re-renders on a newly adopted run; no template emits a refresh button.
    }).catch(err => {
        console.error('Failed to load outcome panel:', err);
    });

    import('./options-table.js').then(mod => {
        const loadBtn = document.getElementById('load-options-scanner');
        if (loadBtn && !loadBtn.dataset.bound) {
            loadBtn.dataset.bound = 'true';
            loadBtn.addEventListener('click', () => {
                if (isBackendGenerating()) {
                    const prev = loadBtn.textContent;
                    loadBtn.disabled = true;
                    loadBtn.textContent = 'Growth signals loading…';
                    setTimeout(() => {
                        loadBtn.disabled = false;
                        loadBtn.textContent = prev;
                    }, 3000);
                    return;
                }
                loadBtn.disabled = true;
                loadBtn.textContent = 'Loading scanner...';
                mod.loadTickers().catch(err => {
                    loadBtn.disabled = false;
                    loadBtn.textContent = 'Load scanner';
                    console.error('Options table error:', err);
                });
            });
        }
    }).catch(err => {
        console.error('Options table error:', err);
    });
}

function showWaveLoading(waveId, message) {
    const el = document.getElementById(`${waveId}-loading`);
    if (el) { el.textContent = message; el.classList.remove('d-none'); }
}

function hideWaveLoading(waveId) {
    const el = document.getElementById(`${waveId}-loading`);
    if (el) el.classList.add('d-none');
}

function copyRollTicket(pos, btn) {
    const expiry = (pos.expiration || '').replace(/-/g, '');
    const strike = (pos.strike || 0).toFixed(2);
    const ticker = pos.ticker || '?';
    const type = pos.option_type === 'PUT' ? 'PUT' : 'CALL';
    const qty = Math.max(1, Math.abs(Number(pos.position || 0)) || 1);
    const pressure = pos.roll_pressure != null ? pos.roll_pressure.toFixed(0) : '?';
    const text = [
        'ROLL — ' + ticker,
        'BTC: ' + type + ' ' + ticker + ' ' + expiry + ' ' + strike + ' x' + qty,
        'STO: ' + type + ' ' + ticker + ' <next expiry> ' + strike + ' x' + qty + ' — select next monthly expiry in broker',
        'Reason: roll pressure ' + pressure + '/100',
        'Source: Moomoo positions (read-only)',
    ].join('\n');
    const original = btn.innerHTML;
    navigator.clipboard.writeText(text).then(() => {
        btn.innerHTML = '<i class="bi bi-check-circle"></i> Copied';
        btn.classList.add('btn-success');
    }).catch(() => {
        btn.innerHTML = '<i class="bi bi-x-circle"></i> Failed';
        btn.classList.add('btn-danger');
    }).finally(() => {
        setTimeout(() => { btn.innerHTML = original; btn.classList.remove('btn-success', 'btn-danger'); }, 2000);
    });
}

export async function loadPositionsCommandPanel() {
    const tbody = document.getElementById('position-monitor-body');
    if (!tbody) return;
    const loadingEl = document.getElementById('position-monitor-loading');
    try {
        const resp = await fetch('/api/portfolio/roll-pressure');
        if (!resp.ok) { tbody.innerHTML = '<tr><td colspan="13" class="text-center text-muted">Could not load positions</td></tr>'; return; }
        const data = await resp.json();
        const positions = data.positions || [];
        if (positions.length === 0) { tbody.innerHTML = '<tr><td colspan="13" class="text-center text-muted">No open short option positions</td></tr>'; return; }
        tbody.innerHTML = positions.map(pos => {
            const profit = pos.profit_target_progress || 0;
            const pressure = pos.roll_pressure || 0;
            const isItm = (pos.otm_pct || 0) < 0;
            const deepItm = isItm && Math.abs(pos.otm_pct) > 15;
            const daysToEarnings = (pos.wheel_decision && pos.wheel_decision.days_to_earnings) ?? null;

            // Live P&L for a short leg: entry credit (avg_cost) vs current mark.
            const qty = Math.abs(Number(pos.position || 0)) || 0;
            const entryCredit = Number(pos.avg_cost ?? pos.wheel_decision?.avg_cost ?? 0) || 0;
            const mark = Number(pos.mid_price || 0) || 0;
            let pnlPct = null;
            let pnlDollar = null;
            // A valid current mark is required to report live P&L: an unknown/zero
            // mark must not be presented as +100% max profit. (C09)
            if (entryCredit > 0 && mark > 0) {
                pnlPct = ((entryCredit - mark) / entryCredit) * 100;
                pnlDollar = (entryCredit - mark) * 100 * qty;
            }
            const pnlHtml = pnlPct === null
                ? '<span class="text-muted">—</span>'
                : `<span class="${pnlPct >= 0 ? 'text-success' : 'text-danger'} fw-bold">${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(0)}%</span>`
                    + `<br><small class="text-muted">$${pnlDollar >= 0 ? '' : '-'}${Math.abs(pnlDollar).toFixed(0)}</small>`;

            const deltaVal = Math.abs(Number(pos.delta || 0)) || null;
            const deltaHtml = deltaVal === null ? '<span class="text-muted">—</span>' : deltaVal.toFixed(2);

            // Exit-playbook verdict from the server (deterministic rules);
            // fall back to a pressure-based heuristic for stale payloads.
            let statusBadge, statusClass;
            const reasons = Array.isArray(pos.exit_reasons) ? pos.exit_reasons.join(' • ') : '';
            const verdict = String(pos.exit_verdict || '').toUpperCase();
            if (verdict === 'CLOSE') { statusBadge = 'CLOSE'; statusClass = 'bg-danger'; }
            else if (verdict === 'TAKE_PROFIT') { statusBadge = 'TAKE PROFIT'; statusClass = 'bg-success'; }
            else if (verdict === 'ROLL') { statusBadge = 'ROLL'; statusClass = 'bg-warning text-dark'; }
            else if (verdict === 'HOLD') {
                if (deepItm) { statusBadge = 'HOLD ⚠'; statusClass = 'bg-danger'; }
                else { statusBadge = 'HOLD'; statusClass = isItm ? 'bg-secondary' : 'bg-info'; }
            } else if (profit >= 50) { statusBadge = 'TAKE PROFIT'; statusClass = 'bg-success'; }
            else if (deepItm) { statusBadge = 'WATCH ⚠'; statusClass = 'bg-danger'; }
            else if (isItm) { statusBadge = 'HOLD'; statusClass = 'bg-secondary'; }
            else if (pressure >= 70) { statusBadge = 'ROLL SOON'; statusClass = 'bg-warning text-dark'; }
            else { statusBadge = 'HOLD'; statusClass = 'bg-info'; }

            const expiry = pos.expiration || '';
            const expiryDisplay = expiry.length === 8 ? expiry.slice(0,4) + '-' + expiry.slice(4,6) + '-' + expiry.slice(6,8) : expiry;
            const typeLabel = pos.option_type === 'PUT' ? 'Put' : 'Call';
            const contractKey = `${pos.ticker} ${expiry} ${typeLabel.charAt(0)}${Number(pos.strike || 0).toFixed(2)}`;
            // API-fed strings are escaped before HTML interpolation (frontend AGENTS.md).
            const escTicker = escapeHtml(pos.ticker || '');
            const escType = escapeHtml(pos.option_type || '');
            const escExpiry = escapeHtml(expiryDisplay);
            const escContractKey = escapeAttr(contractKey);
            return `<tr data-contract-key="${escContractKey}">`
                + `<td><strong>${escTicker}</strong></td>`
                + `<td><span class="badge ${pos.option_type === 'PUT' ? 'bg-danger' : 'bg-success'}" aria-label="${typeLabel}">${escType}</span></td>`
                + `<td>$${(pos.strike || 0).toFixed(2)}${isItm ? ' <span class="badge bg-danger badge-pill">ITM</span>' : ''}</td>`
                + `<td class="small">${escExpiry}</td>`
                + `<td class="small">${pos.dte ?? '—'}</td>`
                + `<td class="small">-${qty}</td>`
                + `<td class="small">${entryCredit > 0 ? '$' + entryCredit.toFixed(2) : '—'}</td>`
                + `<td class="small">${mark > 0 ? '$' + mark.toFixed(2) : '—'}</td>`
                + `<td>${pnlHtml}</td>`
                + `<td class="small">${deltaHtml}</td>`
                + `<td class="small">${renderEarningsBadge(daysToEarnings)}</td>`
                + `<td style="min-width:80px"><div class="progress" style="height:6px" role="progressbar" aria-valuenow="${pressure.toFixed(0)}" aria-valuemin="0" aria-valuemax="100"><div class="progress-bar ${pressure >= 70 ? 'bg-warning' : 'bg-secondary'}" style="width:${Math.min(pressure, 100)}%"></div></div><small class="text-muted">${pressure.toFixed(0)}</small></td>`
                + `<td><span class="badge ${statusClass} position-verdict"${reasons ? ` title="${escapeAttr(reasons)}"` : ''}>${statusBadge}</span> `
                + `<button type="button" class="btn btn-outline-secondary btn-sm copy-roll-btn" title="Copy roll ticket (manual, read-only)">Copy roll</button></td>`
                + '</tr>';
        }).join('');
        tbody.querySelectorAll('.copy-roll-btn').forEach((btn, index) => {
            btn.addEventListener('click', () => copyRollTicket(positions[index], btn));
        });
        const refreshBtn = document.getElementById('refresh-position-monitor');
        if (refreshBtn && !refreshBtn.dataset.bound) {
            refreshBtn.dataset.bound = 'true';
            refreshBtn.onclick = () => loadPositionsCommandPanel();
        }
    } catch (err) {
        console.error('Error loading position monitor:', err);
        tbody.innerHTML = '<tr><td colspan="13" class="text-center text-danger">Error loading positions</td></tr>';
    }
}

function renderEarningsBadge(days) {
    if (days === null || days === undefined) return '<span class="text-muted">—</span>';
    if (days <= 0) return '<span class="badge bg-danger">ER TODAY</span>';
    if (days <= 3) return `<span class="badge bg-warning text-dark">ER ${days}d</span>`;
    if (days <= 7) return `<span class="badge bg-info text-dark">ER ${days}d</span>`;
    return `<span class="text-muted small">ER ${days}d</span>`;
}

function toggleCashReserve(enabled) {
    updateCashReserveStatus();
}
