/**
 * Dashboard initialization and position command panel.
 * Split from dashboard.js (F042)
 */
import { loadPortfolioData } from './account.js';
import { initializeTopRecommendations, loadTopRecommendations, isBackendGenerating } from './top-recommendations.js';
import { formatCurrency } from '../utils/formatters.js';
import { fetchWeeklyOptionIncome } from './api-portfolio.js';
import { updateCashReserveStatus } from './dashboard-cash.js';
import { updateIdleCashPanel } from './dashboard-cash.js';
import { initWatchlistPanel, loadWatchlist } from './watchlist-panel.js';
import { initRunStrip, loadRunStrip } from './run-strip.js';
import { startRunStatePoll, ensureRunStatePoll, onRunPublished } from './run-notifier.js';
import { renderGrowthPanel } from './growth-panel.js';
import { renderOutcomePanel } from './outcome-panel.js';
import { renderWeeklyIncome } from './weekly-income.js';
import { state as optionsTableState } from './options-table-state.js';
import { initializeSectionNavigation } from './section-navigation.js';

let signalPanelsInitialized = false;
let dashboardStartupInProgress = false;
let runPublishedDuringStartup = false;

/**
 * Initialize primary signals immediately, then load account and position data.
 */
export async function initializeDashboard() {
    try {
        initializeSectionNavigation();
        if (!document.querySelector('.content-container')) {
            const mainContainer = document.querySelector('main .container') || document.querySelector('main');
            if (mainContainer) {
                const contentContainer = document.createElement('div');
                contentContainer.className = 'content-container';
                mainContainer.prepend(contentContainer);
            }
        }
        dashboardStartupInProgress = true;

        // Start the wheel shortlist and operational state as soon as the DOM
        // is ready. Account and position diagnostics can load independently.
        initWatchlistPanel();
        initRunStrip();
        initializeTopRecommendations();
        void Promise.resolve(loadRunStrip()).catch((error) => {
            console.error('Initial run strip load failed:', error);
        });
        initializeSignalPanels();

        // Register consumers and controls before the immediate shared poll.
        // The poll starts before any broker-backed panel is awaited.
        onRunPublished(handleRunPublished);
        startRunStatePoll();
        const runRefreshBtn = document.getElementById('run-refresh-btn');
        if (runRefreshBtn && !runRefreshBtn.dataset.viewerBound) {
            runRefreshBtn.dataset.viewerBound = 'true';
            runRefreshBtn.addEventListener('click', () => ensureRunStatePoll());
        }

        showWaveLoading('wave1', 'Loading account data...');
        try {
            await loadPortfolioData();
            await updateCashReserveStatus();
        } catch (error) { console.error('Wave 1 error:', error); }
        hideWaveLoading('wave1');

        showWaveLoading('wave2', 'Loading positions...');
        try {
            await loadPositionsCommandPanel();
        } catch (error) { console.error('Wave 2 error:', error); }
        hideWaveLoading('wave2');

        showWaveLoading('wave3', 'Loading market data...');
        try {
            await updateIdleCashPanel();
        } catch (error) { console.error('Wave 3 error:', error); }
        hideWaveLoading('wave3');

        const cashReserveToggle = document.getElementById('cash-reserve-toggle');
        if (cashReserveToggle && !cashReserveToggle.dataset.bound) {
            cashReserveToggle.dataset.bound = 'true';
            cashReserveToggle.addEventListener('change', (e) => toggleCashReserve(e.target.checked));
        }

    } catch (error) {
        console.error('Dashboard initialization error:', error);
    } finally {
        dashboardStartupInProgress = false;
        if (runPublishedDuringStartup) {
            runPublishedDuringStartup = false;
            void reloadSecondaryPanelsAfterPublish();
        }
    }
}

function handleRunPublished() {
    if (dashboardStartupInProgress) {
        // Keep the primary shortlist current even if a secondary broker read
        // stalls. Defer the remaining fan-out to avoid duplicate account and
        // position reads while startup requests are still in flight.
        void reloadPanel(() => loadTopRecommendations(false));
        runPublishedDuringStartup = true;
        return;
    }
    void reloadAllPanelsAfterPublish();
}

/**
 * P1b publish fan-out: a newly published immutable run causes an explicit
 * reload of every rendered panel via read-only fetches. Each reload runs in its
 * own promise so one panel's failure cannot blank the rest of the screen, and
 * nothing here POSTs /api/run/refresh (a publish never re-triggers a scan).
 */
async function reloadAllPanelsAfterPublish() {
    await Promise.all([
        reloadPanel(() => loadTopRecommendations(false)),
        ...secondaryPanelReloads(),
    ]);
}

async function reloadSecondaryPanelsAfterPublish() {
    await Promise.all(secondaryPanelReloads());
}

function secondaryPanelReloads() {
    const reloads = [
        () => loadRunStrip(),
        () => loadPortfolioData(),
        () => loadPositionsCommandPanel(),
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
    return reloads.map(reloadPanel);
}

function reloadPanel(reload) {
    return Promise.resolve()
        .then(reload)
        .catch((err) => {
            console.error('Panel reload failed after publish:', err);
        });
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

function setPositionMessage(tbody, message, className = 'text-muted') {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 13;
    cell.className = `text-center ${className}`;
    cell.textContent = message;
    row.appendChild(cell);
    tbody.replaceChildren(row);
}

function appendPositionCell(row, content, className = '') {
    const cell = document.createElement('td');
    if (className) cell.className = className;
    if (content instanceof Node) cell.appendChild(content);
    else cell.textContent = String(content);
    row.appendChild(cell);
    return cell;
}

function renderPositionRow(pos) {
    const row = document.createElement('tr');
    const expiry = String(pos.expiration || '');
    const ticker = String(pos.ticker || '');
    const optionType = String(pos.option_type || '').toUpperCase();
    const isPut = optionType === 'PUT' || optionType === 'P';
    const type = isPut ? 'PUT' : 'CALL';
    const strike = Number(pos.strike) || 0;
    const qty = Math.abs(Number(pos.position || 0)) || 0;
    const pressure = Number(pos.roll_pressure || 0) || 0;
    const profit = Number(pos.profit_target_progress || 0) || 0;
    const otmPct = Number(pos.otm_pct || 0) || 0;
    const isItm = otmPct < 0;
    const deepItm = isItm && Math.abs(otmPct) > 15;
    const entryCredit = Number(pos.avg_cost ?? pos.wheel_decision?.avg_cost ?? 0) || 0;
    const mark = Number(pos.mid_price || 0) || 0;
    const delta = Math.abs(Number(pos.delta || 0)) || null;
    const daysToEarnings = pos.wheel_decision?.days_to_earnings ?? null;
    row.setAttribute('data-contract-key', `${ticker} ${expiry} ${isPut ? 'P' : 'C'}${strike.toFixed(2)}`);

    appendPositionCell(row, (() => {
        const strong = document.createElement('strong');
        strong.textContent = ticker;
        return strong;
    })());
    const typeCell = document.createElement('td');
    const typeBadge = document.createElement('span');
    typeBadge.className = `badge ${isPut ? 'bg-danger' : 'bg-success'}`;
    typeBadge.setAttribute('aria-label', isPut ? 'Put' : 'Call');
    typeBadge.textContent = type;
    typeCell.appendChild(typeBadge);
    row.appendChild(typeCell);

    const strikeCell = document.createElement('td');
    strikeCell.appendChild(document.createTextNode(`$${strike.toFixed(2)}`));
    if (isItm) {
        const itmBadge = document.createElement('span');
        itmBadge.className = 'badge bg-danger badge-pill';
        itmBadge.textContent = 'ITM';
        strikeCell.appendChild(document.createTextNode(' '));
        strikeCell.appendChild(itmBadge);
    }
    row.appendChild(strikeCell);
    appendPositionCell(row, expiry.length === 8 ? `${expiry.slice(0, 4)}-${expiry.slice(4, 6)}-${expiry.slice(6, 8)}` : expiry, 'small');
    appendPositionCell(row, pos.dte ?? '—', 'small');
    appendPositionCell(row, `-${qty}`, 'small');
    appendPositionCell(row, entryCredit > 0 ? `$${entryCredit.toFixed(2)}` : '—', 'small');
    appendPositionCell(row, mark > 0 ? `$${mark.toFixed(2)}` : '—', 'small');

    const pnlCell = document.createElement('td');
    if (entryCredit > 0 && mark > 0) {
        const pnlPct = ((entryCredit - mark) / entryCredit) * 100;
        const pnlDollars = (entryCredit - mark) * 100 * qty;
        const pnl = document.createElement('span');
        pnl.className = `${pnlPct >= 0 ? 'text-success' : 'text-danger'} fw-bold`;
        pnl.textContent = `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(0)}%`;
        const detail = document.createElement('small');
        detail.className = 'text-muted';
        detail.textContent = `$${pnlDollars >= 0 ? '' : '-'}${Math.abs(pnlDollars).toFixed(0)}`;
        pnlCell.append(pnl, document.createElement('br'), detail);
    } else {
        const unknown = document.createElement('span');
        unknown.className = 'text-muted';
        unknown.textContent = '—';
        pnlCell.appendChild(unknown);
    }
    row.appendChild(pnlCell);
    appendPositionCell(row, delta === null ? '—' : delta.toFixed(2), 'small');
    appendPositionCell(row, renderEarningsBadge(daysToEarnings), 'small');

    const pressureCell = document.createElement('td');
    pressureCell.style.minWidth = '80px';
    const progress = document.createElement('div');
    progress.className = 'progress';
    progress.style.height = '6px';
    progress.setAttribute('role', 'progressbar');
    progress.setAttribute('aria-valuenow', pressure.toFixed(0));
    progress.setAttribute('aria-valuemin', '0');
    progress.setAttribute('aria-valuemax', '100');
    progress.setAttribute('aria-label', `Roll pressure for ${pos.ticker || pos.symbol || 'position'}: ${pressure.toFixed(0)} out of 100`);
    const bar = document.createElement('div');
    bar.className = `progress-bar ${pressure >= 70 ? 'bg-warning' : 'bg-secondary'}`;
    bar.style.width = `${Math.min(pressure, 100)}%`;
    progress.appendChild(bar);
    const pressureText = document.createElement('small');
    pressureText.className = 'text-muted';
    pressureText.textContent = pressure.toFixed(0);
    pressureCell.append(progress, pressureText);
    row.appendChild(pressureCell);

    const reasons = Array.isArray(pos.exit_reasons) ? pos.exit_reasons.join(' • ') : '';
    const verdict = String(pos.exit_verdict || '').toUpperCase();
    const target = pos.roll_target;
    let targetLabel = '';
    if (target && target.ticker && target.option_type && /^\d{8}$/.test(String(target.expiration || ''))) {
        const targetStrike = Number(target.strike);
        if (Number.isFinite(targetStrike)) {
            const targetExpiry = String(target.expiration);
            targetLabel = `${String(target.ticker).toUpperCase()} ${String(target.option_type).toUpperCase()} ${targetStrike.toFixed(2)} ${targetExpiry.slice(0, 4)}-${targetExpiry.slice(4, 6)}-${targetExpiry.slice(6, 8)}`;
        }
    }
    let statusBadge, statusClass;
    if (verdict === 'CLOSE') { statusBadge = 'CLOSE'; statusClass = 'bg-danger'; }
    else if (verdict === 'TAKE_PROFIT') { statusBadge = 'TAKE PROFIT'; statusClass = 'bg-success'; }
    else if (verdict === 'ROTATE') { statusBadge = targetLabel ? `ROTATE → ${targetLabel}` : 'ROTATE'; statusClass = 'bg-primary'; }
    else if (verdict === 'ROLL') { statusBadge = targetLabel ? `ROLL → ${targetLabel}` : 'ROLL'; statusClass = 'bg-warning text-dark'; }
    else if (verdict === 'HOLD') {
        if (deepItm) { statusBadge = 'HOLD ⚠'; statusClass = 'bg-danger'; }
        else { statusBadge = 'HOLD'; statusClass = isItm ? 'bg-secondary' : 'bg-info'; }
    } else if (profit >= 50) { statusBadge = 'TAKE PROFIT'; statusClass = 'bg-success'; }
    else if (deepItm) { statusBadge = 'WATCH ⚠'; statusClass = 'bg-danger'; }
    else if (isItm) { statusBadge = 'HOLD'; statusClass = 'bg-secondary'; }
    else if (pressure >= 70) { statusBadge = 'ROLL SOON'; statusClass = 'bg-warning text-dark'; }
    else { statusBadge = 'HOLD'; statusClass = 'bg-info'; }

    const actionCell = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = `badge ${statusClass} position-verdict`;
    badge.textContent = statusBadge;
    if (reasons) badge.title = reasons;
    actionCell.appendChild(badge);
    actionCell.appendChild(document.createTextNode(' '));
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-outline-secondary btn-sm copy-roll-btn';
    button.disabled = true;
    button.textContent = 'Review only';
    const copyBlocker = targetLabel
        ? 'Roll tickets have no current-run copy revalidation.'
        : 'No fresh eligible roll target is available.';
    button.title = copyBlocker;
    button.setAttribute('aria-label', `Roll ticket unavailable. ${copyBlocker}`);
    actionCell.appendChild(button);
    const copyReason = document.createElement('small');
    copyReason.className = 'd-block text-muted mt-1 roll-copy-reason';
    copyReason.textContent = copyBlocker;
    actionCell.appendChild(copyReason);
    row.appendChild(actionCell);
    return row;
}

export async function loadPositionsCommandPanel() {
    const tbody = document.getElementById('position-monitor-body');
    if (!tbody) return;
    try {
        const resp = await fetch('/api/portfolio/roll-pressure');
        if (!resp.ok) {
            setPositionMessage(tbody, 'Could not load positions');
            return;
        }
        const data = await resp.json();
        const positions = data.positions || [];
        if (positions.length === 0) {
            setPositionMessage(tbody, 'No open short option positions');
            return;
        }
        tbody.replaceChildren(...positions.map(renderPositionRow));
        const refreshBtn = document.getElementById('refresh-position-monitor');
        if (refreshBtn && !refreshBtn.dataset.bound) {
            refreshBtn.dataset.bound = 'true';
            refreshBtn.onclick = () => loadPositionsCommandPanel();
        }
    } catch (err) {
        console.error('Error loading positions:', err);
        setPositionMessage(tbody, 'Error loading positions', 'text-danger');
    }
}

function renderEarningsBadge(days) {
    const badge = document.createElement('span');
    if (days === null || days === undefined) {
        badge.className = 'text-muted';
        badge.textContent = '—';
    } else if (days <= 0) {
        badge.className = 'badge bg-danger';
        badge.textContent = 'ER TODAY';
    } else if (days <= 3) {
        badge.className = 'badge bg-warning text-dark';
        badge.textContent = `ER ${days}d`;
    } else if (days <= 7) {
        badge.className = 'badge bg-info text-dark';
        badge.textContent = `ER ${days}d`;
    } else {
        badge.className = 'text-muted small';
        badge.textContent = `ER ${days}d`;
    }
    return badge;
}

function toggleCashReserve(_enabled) {
    updateCashReserveStatus();
}

// Bootstrap dashboard on page load. This module is loaded as a deferred module
// script, which runs after parsing but BEFORE DOMContentLoaded — so the listener
// is registered in time for the boot. (In jsdom/vitest the event has already
// fired, so importing this module in tests never auto-boots the dashboard, and
// no `readyState` guard is needed or wanted: guarding on 'loading' would skip
// the boot in a real browser.)
document.addEventListener('DOMContentLoaded', () => {
    initializeDashboard().catch((err) => {
        console.error('Dashboard initialization failed:', err);
    });
});
