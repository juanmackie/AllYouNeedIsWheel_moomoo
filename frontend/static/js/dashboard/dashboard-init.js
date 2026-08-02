/**
 * Dashboard initialization and position command panel.
 * Split from dashboard.js (F042)
 */
import { loadPortfolioData } from './account.js';
import { initializeTopRecommendations, isBackendGenerating } from './top-recommendations.js';
import { formatCurrency } from '../utils/formatters.js';
import { fetchWeeklyOptionIncome } from './api.js';
import { updateCashReserveStatus } from './dashboard-cash.js';
import { updateIdleCashPanel } from './dashboard-cash.js';
import { initWatchlistPanel } from './watchlist-panel.js';

let signalPanelsInitialized = false;

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


        const refreshAllBtn = document.getElementById('refresh-all-btn');
        if (refreshAllBtn && !refreshAllBtn.dataset.bound) {
            refreshAllBtn.dataset.bound = 'true';
            refreshAllBtn.addEventListener('click', (e) => {
                e.preventDefault();
                initializeDashboard();
            });
        }
    } catch (error) {
        console.error('Dashboard initialization error:', error);
    }
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

export async function loadPositionsCommandPanel() {
    const tbody = document.getElementById('positions-command-body');
    if (!tbody) return;
    try {
        const resp = await fetch('/api/portfolio/roll-pressure');
        if (!resp.ok) { tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">Could not load positions</td></tr>'; return; }
        const data = await resp.json();
        const positions = data.positions || [];
        if (positions.length === 0) { tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">No open short option positions</td></tr>'; return; }
        tbody.innerHTML = positions.map(pos => {
            const profit = pos.profit_target_progress || 0; const pressure = pos.roll_pressure || 0;
            const isItm = (pos.otm_pct || 0) < 0; const deepItm = isItm && Math.abs(pos.otm_pct) > 15;
            const daysToEarnings = (pos.wheel_decision && pos.wheel_decision.days_to_earnings) || null;
            let statusBadge, statusClass, btnHtml;
            if (profit >= 50) { statusBadge = 'Recycle'; statusClass = 'bg-success'; btnHtml = '<span class="text-muted small">Close externally</span>'; }
            else if (deepItm) { statusBadge = 'Watch'; statusClass = 'bg-danger'; btnHtml = '<span class="text-danger small">⚠ Deep ITM — monitor</span>'; }
            else if (isItm) { statusBadge = 'Holding'; statusClass = 'bg-secondary'; btnHtml = '<span class="text-muted small">✓ Assign OK</span>'; }
            else if (pressure >= 70) { statusBadge = 'Urgent'; statusClass = 'bg-warning text-dark'; btnHtml = '<span class="text-warning small">Roll pressure high</span>'; }
            else { statusBadge = 'Active'; statusClass = 'bg-info'; btnHtml = '<span class="text-muted small">✓ On track</span>'; }
            const barWidth = Math.min(profit, 100);
            const barColor = profit >= 50 ? 'bg-success' : profit >= 30 ? 'bg-warning' : 'bg-secondary';
            const expiry = pos.expiration || '';
            const expiryDisplay = expiry.length === 8 ? expiry.slice(0,4) + '-' + expiry.slice(4,6) + '-' + expiry.slice(6,8) : expiry;
            const typeLabel = pos.option_type === 'PUT' ? 'Put' : 'Call';
            return `<tr><td><strong>${pos.ticker}</strong></td><td><span class="badge ${pos.option_type === 'PUT' ? 'bg-danger' : 'bg-success'}" aria-label="${typeLabel}">${pos.option_type}</span></td><td>$${(pos.strike || 0).toFixed(2)}</td><td class="small">${expiryDisplay}</td><td class="small">${renderEarningsBadge(daysToEarnings)}</td><td style="min-width:100px"><div class="progress" style="height:6px" role="progressbar" aria-valuenow="${profit.toFixed(0)}" aria-valuemin="0" aria-valuemax="100"><div class="progress-bar ${barColor}" style="width:${barWidth}%"></div></div><small class="${profit >= 50 ? 'text-success fw-bold' : 'text-muted'}">${profit.toFixed(0)}% profit</small></td><td><span class="badge ${statusClass}">${statusBadge}</span></td><td>${btnHtml}</td></tr>`;
        }).join('');
        const refreshBtn = document.getElementById('refresh-positions-command');
        if (refreshBtn) refreshBtn.onclick = () => loadPositionsCommandPanel();
    } catch (err) {
        console.error('Error loading positions command panel:', err);
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-danger">Error loading positions</td></tr>';
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
