/**
 * Dashboard initialization and position command panel.
 * Split from dashboard.js (F042)
 */
import { loadPortfolioData } from './account.js';
import { initializeTopRecommendations } from './top-recommendations.js';
import { initializeLLMAdvisor } from './llm-advisor.js';
import { formatCurrency } from '../utils/formatters.js';
import { fetchWeeklyOptionIncome } from './api.js';
import { updateCashReserveStatus } from './dashboard-cash.js';
import { updateWeeklyEarningsSummary } from './dashboard-regime.js';
import { updateIdleCashPanel } from './dashboard-cash.js';
import { loadMacroRegime } from './macro.js';
import { loadVixRegime, loadTechnicalRegime } from './dashboard-regime.js';

let diagnosticsLoaded = false;

/**
 * Initialize the dashboard with progressive loading
 * Wave 1: Account data (critical path)
 * Wave 2: Positions & orders
 * Wave 3: Signals (fast path)
 * Wave Lazy: Heavy diagnostics (only when user opens #research-diagnostics)
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
                loadMacroRegime(),
                loadVixRegime(),
                loadTechnicalRegime(),
                updateWeeklyEarningsSummary(),
                updateIdleCashPanel()
            ]);
        } catch (error) { console.error('Wave 3 error:', error); }
        hideWaveLoading('wave3');

        initSizingModeSelector();

        const cashReserveToggle = document.getElementById('cash-reserve-toggle');
        if (cashReserveToggle && !cashReserveToggle.dataset.bound) {
            cashReserveToggle.dataset.bound = 'true';
            cashReserveToggle.addEventListener('change', (e) => toggleCashReserve(e.target.checked));
        }

        initializeLLMAdvisor();

        // Diagnostics are lazily loaded when the user opens the section
        initLazyDiagnostics();

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
 * Lazy-load diagnostics when user opens #research-diagnostics
 */
async function loadDiagnosticsOnce() {
    if (diagnosticsLoaded) return;
    diagnosticsLoaded = true;

    import('./earnings-vol-signals.js').then(mod => {
        mod.initializeEarningsVolSignals();
    }).catch(err => {
        console.error('Failed to load earnings-vol-signals:', err);
    });

    import('./weekly-income.js').then(mod => {
        mod.renderWeeklyIncome();
        const refreshBtn = document.getElementById('refresh-filled-orders');
        if (refreshBtn) {
            refreshBtn.removeEventListener('click', mod.renderWeeklyIncome);
            refreshBtn.addEventListener('click', () => mod.renderWeeklyIncome());
        }
    }).catch(err => {
        console.error('Failed to load weekly-income:', err);
    });

    import('./options-table.js').then(async mod => {
        await mod.loadTickers();
    }).catch(err => {
        console.error('Options table error:', err);
    });

    window.setTimeout(() => {
        import('./catalyst-watch.js').then(mod => {
            mod.initializeCatalystWatch().catch(err => {
                console.error('Catalyst watch error:', err);
            });
        }).catch(err => {
            console.error('Catalyst watch error:', err);
        });
    }, 1500);
}

function initLazyDiagnostics() {
    const details = document.getElementById('research-diagnostics');
    if (!details) return;

    if (!details.dataset.bound) {
        details.dataset.bound = 'true';
        details.addEventListener('toggle', () => {
            if (!details.open || diagnosticsLoaded) return;
            loadDiagnosticsOnce();
        });
    }

    if (details.open) {
        loadDiagnosticsOnce();
    }
}

function showWaveLoading(waveId, message) {
    const el = document.getElementById(`${waveId}-loading`);
    if (el) { el.textContent = message; el.classList.remove('d-none'); }
}

function hideWaveLoading(waveId) {
    const el = document.getElementById(`${waveId}-loading`);
    if (el) el.classList.add('d-none');
}

export function initSizingModeSelector() {
    const radios = document.querySelectorAll('input[name="sizing-mode"]');
    if (!radios.length) return;
    const saved = localStorage.getItem('sizingMode');
    if (saved === 'aggressive') {
        const aggressive = document.getElementById('sizing-aggressive');
        if (aggressive) aggressive.checked = true;
    } else {
        const conservative = document.getElementById('sizing-conservative');
        if (conservative) conservative.checked = true;
    }
    radios.forEach(radio => {
        radio.addEventListener('change', () => {
            if (radio.checked) { localStorage.setItem('sizingMode', radio.value); updateIdleCashPanel(); }
        });
    });
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
