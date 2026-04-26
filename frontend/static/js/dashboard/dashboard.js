/**
 * Main dashboard module
 * Coordinates all dashboard components and initializes the dashboard
 */
import { loadPortfolioData } from './account.js';
import { loadTickers } from './options-table.js';
import { loadPendingOrders } from './orders.js';
import { initializeTopRecommendations } from './top-recommendations.js';
import { loadMacroRegime } from './macro.js';
import { initializeLLMAdvisor } from './llm-advisor.js';
import { showAlert } from '../utils/alerts.js';
import { fetchWeeklyOptionIncome, executeCloseOrder } from './api.js';
import { formatCurrency } from '../utils/formatters.js';

// Store weekly income data
let weeklyIncomeData = null;

/**
 * Fetch and update cash reserve status
 */
async function updateCashReserveStatus() {
    try {
        const response = await fetch('/api/options/cash-status');
        if (!response.ok) {
            console.error('Failed to fetch cash status:', response.status);
            return;
        }
        
        const data = await response.json();
        if (!data.success) {
            console.error('Cash status error:', data.error);
            return;
        }
        
        // Update cash reserve badge
        const badge = document.getElementById('cash-reserve-badge');
        if (badge) {
            if (data.reserve_enabled) {
                badge.className = 'badge bg-success';
                badge.textContent = 'Reserve ON';
            } else {
                badge.className = 'badge bg-secondary';
                badge.textContent = 'Reserve OFF';
            }
        }
        
        // Update cash amounts
        const reservedEl = document.getElementById('cash-reserved');
        if (reservedEl) {
            reservedEl.textContent = formatCurrency(data.cash_reserved);
        }
        
        const availableEl = document.getElementById('cash-available');
        if (availableEl) {
            availableEl.textContent = formatCurrency(data.cash_available);
            // Highlight in red if low
            if (data.cash_available < 5000) {
                availableEl.className = 'text-danger';
            } else {
                availableEl.className = 'text-success';
            }
        }
        
        // Update toggle
        const toggle = document.getElementById('cash-reserve-toggle');
        if (toggle) {
            toggle.checked = data.reserve_enabled;
        }
        
        // Update open puts list
        const details = document.getElementById('cash-reserve-details');
        const list = document.getElementById('open-puts-list');
        if (details && list && data.open_puts && data.open_puts.length > 0) {
            list.innerHTML = data.open_puts.map(put => 
                `<div>${put.ticker} ${put.strike}P ${put.expiration.slice(4,6)}/${put.expiration.slice(6)} (${put.contracts} contract${put.contracts > 1 ? 's' : ''})</div>`
            ).join('');
            details.style.display = 'block';
        } else if (details) {
            details.style.display = 'none';
        }
        
    } catch (error) {
        console.error('Error updating cash reserve status:', error);
    }
}

/**
 * Toggle cash reserve setting
 */
async function toggleCashReserve(enabled) {
    try {
        // This would need a backend endpoint to toggle the setting
        // For now, just update the UI
        // console.log('Cash reserve toggle:', enabled);
        
        // Refresh the status
        await updateCashReserveStatus();
    } catch (error) {
        console.error('Error toggling cash reserve:', error);
    }
}

/**
 * Render an earnings proximity badge for the position command panel.
 * @param {number|null} days - Days to next earnings, or null if unknown
 * @returns {string} HTML span with badge
 */
function renderEarningsBadge(days) {
    if (days === null || days === undefined) {
        return '<span class="text-muted">—</span>';
    }
    if (days <= 0) {
        return '<span class="badge bg-danger">ER TODAY</span>';
    }
    if (days <= 3) {
        return `<span class="badge bg-warning text-dark">ER ${days}d</span>`;
    }
    if (days <= 7) {
        return `<span class="badge bg-info text-dark">ER ${days}d</span>`;
    }
    return `<span class="text-muted small">ER ${days}d</span>`;
}

/**
 * Load scored positions and render the command panel with CLOSE/HOLD actions.
 * Fetches from /api/portfolio/roll-pressure which returns profit_target_progress,
 * roll_pressure, and warnings for every open short option.
 */
async function loadPositionsCommandPanel() {
    const tbody = document.getElementById('positions-command-body');
    if (!tbody) return;

    try {
        const resp = await fetch('/api/portfolio/roll-pressure');
        if (!resp.ok) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">Could not load positions</td></tr>';
            return;
        }
        const data = await resp.json();
        const positions = data.positions || [];

        if (positions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">No open short option positions</td></tr>';
            return;
        }

        tbody.innerHTML = positions.map(pos => {
            const profit = pos.profit_target_progress || 0;
            const pressure = pos.roll_pressure || 0;
            const isItm = (pos.otm_pct || 0) < 0;
            const deepItm = isItm && Math.abs(pos.otm_pct) > 15;
            const daysToEarnings = (pos.wheel_decision && pos.wheel_decision.days_to_earnings) || null;

            // Determine status badge
            let statusBadge, statusClass, btnHtml;
            if (profit >= 50) {
                statusBadge = 'Recycle';
                statusClass = 'bg-success';
                btnHtml = `<button class="btn btn-sm btn-success close-position-btn"
                    data-ticker="${pos.ticker}"
                    data-type="${pos.option_type}"
                    data-strike="${pos.strike}"
                    data-expiration="${pos.expiration}">
                    <i class="bi bi-arrow-return-left"></i> CLOSE
                </button>`;
            } else if (deepItm) {
                statusBadge = 'Watch';
                statusClass = 'bg-danger';
                btnHtml = '<span class="text-danger small">⚠ Deep ITM — monitor</span>';
            } else if (isItm) {
                statusBadge = 'Holding';
                statusClass = 'bg-secondary';
                btnHtml = '<span class="text-muted small">✓ Assign OK</span>';
            } else if (pressure >= 70) {
                statusBadge = 'Urgent';
                statusClass = 'bg-warning text-dark';
                btnHtml = '<span class="text-warning small">Roll pressure high</span>';
            } else {
                statusBadge = 'Active';
                statusClass = 'bg-info';
                btnHtml = '<span class="text-muted small">✓ On track</span>';
            }

            // Profit bar
            const barWidth = Math.min(profit, 100);
            const barColor = profit >= 50 ? 'bg-success' : profit >= 30 ? 'bg-warning' : 'bg-secondary';

            const expiry = pos.expiration || '';
            const expiryDisplay = expiry.length === 8
                ? expiry.slice(0,4) + '-' + expiry.slice(4,6) + '-' + expiry.slice(6,8)
                : expiry;

            return `<tr>
                <td><strong>${pos.ticker}</strong></td>
                <td><span class="badge ${pos.option_type === 'PUT' ? 'bg-danger' : 'bg-success'}">${pos.option_type}</span></td>
                <td>$${(pos.strike || 0).toFixed(2)}</td>
                <td class="small">${expiryDisplay}</td>
                <td class="small">${renderEarningsBadge(daysToEarnings)}</td>
                <td style="min-width:100px">
                    <div class="progress" style="height:6px">
                        <div class="progress-bar ${barColor}" style="width:${barWidth}%"></div>
                    </div>
                    <small class="${profit >= 50 ? 'text-success fw-bold' : 'text-muted'}">${profit.toFixed(0)}%</small>
                </td>
                <td><span class="badge ${statusClass}">${statusBadge}</span></td>
                <td>${btnHtml}</td>
            </tr>`;
        }).join('');

        // Attach close button handlers
        tbody.querySelectorAll('.close-position-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                btn.disabled = true;
                btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
                try {
                    const result = await executeCloseOrder({
                        ticker: btn.dataset.ticker,
                        option_type: btn.dataset.type,
                        strike: parseFloat(btn.dataset.strike),
                        expiration: btn.dataset.expiration,
                        quantity: 1,
                    });
                    if (result.success) {
                        showAlert(result.message || 'Position closed successfully!', 'success');
                        loadPositionsCommandPanel(); // Refresh the panel
                    } else {
                        showAlert('Failed to close position: ' + (result.error || 'Unknown'), 'danger');
                        btn.disabled = false;
                        btn.innerHTML = '<i class="bi bi-arrow-return-left"></i> CLOSE';
                    }
                } catch (err) {
                    showAlert('Error closing position: ' + err.message, 'danger');
                    btn.disabled = false;
                    btn.innerHTML = '<i class="bi bi-arrow-return-left"></i> CLOSE';
                }
            });
        });

        // Wire refresh button
        const refreshBtn = document.getElementById('refresh-positions-command');
        if (refreshBtn) {
            refreshBtn.onclick = () => loadPositionsCommandPanel();
        }

    } catch (err) {
        console.error('Error loading positions command panel:', err);
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-danger">Error loading positions</td></tr>';
    }
}

/**
 * Fetch and display technical market regime (200 EMA + ADX)
 */
async function loadTechnicalRegime() {
    try {
        // Get tickers from the portfolio/watchlist
        const tickersResp = await fetch('/api/options/watchlist-tickers');
        let tickers = ['SPY']; // Default fallback
        
        if (tickersResp.ok) {
            const tickersData = await tickersResp.json();
            if (tickersData.tickers && tickersData.tickers.length > 0) {
                tickers = tickersData.tickers.slice(0, 20); // Limit to 20 tickers
            }
        }
        
        const tickersParam = tickers.join(',');
        const response = await fetch(`/api/technical/regime/summary?tickers=${tickersParam}`);
        
        if (!response.ok) {
            console.error('Failed to fetch technical regime:', response.status);
            return;
        }
        
        const data = await response.json();
        if (!data.success || !data.summary) {
            console.error('Technical regime error:', data.error);
            return;
        }
        
        const summary = data.summary;
        const details = data.details || {};
        
        // Update regime icon and text
        const regimeIcon = document.getElementById('regime-icon');
        const regimeText = document.getElementById('regime-text');
        const regimeDetails = document.getElementById('regime-details');
        const contextSection = document.getElementById('context-filter-section');
        
        if (regimeIcon && regimeText) {
            // Set icon and text based on dominant regime
            const emoji = summary.dominant_regime === 'bullish' ? '🟢' : 
                           summary.dominant_regime === 'bearish' ? '🔴' : '⚪';
            regimeIcon.textContent = emoji;
            regimeText.textContent = summary.summary_text || summary.dominant_regime;
            
            // Update card class for color-coded background
            const card = contextSection?.querySelector('.card');
            if (card) {
                card.classList.remove('regime-bullish', 'regime-bearish', 'regime-neutral');
                card.classList.add(`regime-${summary.dominant_regime}`);
            }
        }
        
        if (regimeDetails) {
            const bullish = summary.regimes?.bullish || 0;
            const bearish = summary.regimes?.bearish || 0;
            const neutral = summary.regimes?.neutral || 0;
            regimeDetails.textContent = `${bullish}B/${bearish}Br/${neutral}N | ${summary.trending}T/${summary.ranging}R`;
        }
        
        // Update ADX value
        const adxValue = document.getElementById('adx-value');
        if (adxValue && Object.keys(details).length > 0) {
            // Calculate average ADX
            let totalAdx = 0;
            let count = 0;
            for (const [ticker, regime] of Object.entries(details)) {
                if (regime.adx) {
                    totalAdx += regime.adx;
                    count++;
                }
            }
            adxValue.textContent = count > 0 ? (totalAdx / count).toFixed(1) : '--';
        }
        
    } catch (error) {
        console.error('Error loading technical regime:', error);
    }
}

/**
 * Load and display locked tickers (earnings lock)
 */
async function loadLockedTickers() {
    try {
        const response = await fetch('/api/earnings/locked-tickers?lock_days=5');
        
        if (!response.ok) {
            console.error('Failed to fetch locked tickers:', response.status);
            return;
        }
        
        const data = await response.json();
        if (!data.success) {
            console.error('Locked tickers error:', data.error);
            return;
        }
        
        const lockedList = document.getElementById('locked-tickers-list');
        const lockInfo = document.getElementById('earnings-lock-info');
        
        if (lockedList && lockInfo) {
            if (data.locked && data.locked.length > 0) {
                lockedList.innerHTML = data.locked.map(item => 
                    `<div>🔒 ${item.ticker} — Earnings ${item.earnings_date} (${item.days_to_earnings}d)</div>`
                ).join('');
                lockInfo.classList.remove('d-none');
            } else {
                lockedList.innerHTML = '<div class="text-muted">No tickers locked</div>';
            }
        }
        
        // Update toggle state
        const lockToggle = document.getElementById('earnings-lock-toggle');
        if (lockToggle) {
            lockToggle.checked = true; // Default ON
        }
        
    } catch (error) {
        console.error('Error loading locked tickers:', error);
    }
}

/**
 * Fetch and display VIX market regime
 */
async function loadVixRegime() {
    try {
        const response = await fetch('/api/options/vix-regime');
        if (!response.ok) {
            console.error('Failed to fetch VIX regime:', response.status);
            return;
        }
        
        const data = await response.json();
        if (!data.success || !data.vix_regime) {
            console.error('VIX regime error:', data.error);
            return;
        }
        
        const regime = data.vix_regime;
        
        // Update VIX level
        const vixLevel = document.getElementById('vix-level');
        if (vixLevel) {
            vixLevel.textContent = regime.vix;
        }
        
        // Update badge with regime name
        const vixBadge = document.getElementById('vix-badge');
        if (vixBadge) {
            vixBadge.textContent = regime.regime.toUpperCase();
            
            // Set badge color based on regime
            if (regime.regime === 'complacency') {
                vixBadge.className = 'badge bg-success fs-6';
            } else if (regime.regime === 'fear') {
                vixBadge.className = 'badge bg-danger fs-6';
            } else {
                vixBadge.className = 'badge bg-primary fs-6';
            }
        }
        
        // Update description
        const vixDesc = document.getElementById('vix-description');
        if (vixDesc) {
            vixDesc.textContent = regime.description;
        }
        
        // Update delta adjustment
        const deltaAdj = document.getElementById('vix-delta-adj');
        if (deltaAdj) {
            const adj = regime.delta_adjustment;
            deltaAdj.textContent = adj > 0 ? `+${adj.toFixed(2)}` : adj.toFixed(2);
            deltaAdj.className = adj > 0 ? 'fw-bold text-success' : (adj < 0 ? 'fw-bold text-danger' : 'fw-bold');
        }
        
        // Update exposure multiplier
        const exposure = document.getElementById('vix-exposure');
        if (exposure) {
            const exp = regime.exposure_multiplier;
            exposure.textContent = `${Math.round(exp * 100)}%`;
            exposure.className = exp < 1 ? 'fw-bold text-warning' : 'fw-bold';
        }
        
        // Load locked tickers for earnings lock
        await loadLockedTickers();
        
        // Update date
        const vixDate = document.getElementById('vix-date');
        if (vixDate) {
            const now = new Date();
            vixDate.textContent = `Updated: ${now.toLocaleTimeString()}`;
        }
        
    } catch (error) {
        console.error('Error loading VIX regime:', error);
    }
}

/**
 * Update the weekly earnings summary card
 */
async function updateWeeklyEarningsSummary() {
    try {
        const data = await fetchWeeklyOptionIncome();
        weeklyIncomeData = data;
        
        // Update the weekly income summary card
        const weeklyIncomeSummary = document.getElementById('weekly-income-summary');
        if (weeklyIncomeSummary) {
            weeklyIncomeSummary.textContent = formatCurrency(data.total_income || 0);
        }
        
        // Update the count of positions expiring this Friday
        const weeklyPositionsCount = document.getElementById('weekly-positions-count');
        if (weeklyPositionsCount) {
            weeklyPositionsCount.textContent = data.positions_count || 0;
        }
        
        // Update the Friday date if available
        const fridayDate = document.getElementById('friday-date');
        if (fridayDate && data.this_friday) {
            fridayDate.textContent = data.this_friday;
        }
    } catch (error) {
        console.error('Error updating weekly earnings summary:', error);
    }
}

/**
 * Update idle cash utilization panel.
 * Calculates deployed vs idle cash and warns if >30% idle.
 */
async function updateIdleCashPanel() {
    try {
        // Get account data
        const accResp = await fetch('/api/portfolio');
        if (!accResp.ok) return;
        const accData = await accResp.json();
        
        const cashBalance = accData.cash_balance || 0;
        const accountValue = accData.account_value || cashBalance;
        
        const idleEl = document.getElementById('idle-cash-amount');
        const deployedEl = document.getElementById('deployed-cash-amount');
        const barEl = document.getElementById('cash-utilization-bar');
        const hintEl = document.getElementById('idle-cash-hint');
        const modeEl = document.getElementById('idle-cash-mode');
        
        if (!idleEl) return;
        
        // Calculate deployed cash from positions
        let deployed = 0;
        try {
            const posResp = await fetch('/api/portfolio/positions');
            if (posResp.ok) {
                const positions = await posResp.json();
                for (const pos of positions) {
                    if (pos.security_type === 'OPT' && pos.option_type === 'PUT' && (pos.position || 0) < 0) {
                        deployed += Math.abs(pos.position || 0) * (pos.strike || 0) * 100;
                    }
                }
            }
        } catch (e) {
            console.debug('Could not fetch positions for idle cash calc:', e);
        }
        
        const idle = cashBalance - deployed;
        const utilPct = accountValue > 0 ? Math.min(100, (deployed / accountValue) * 100) : 0;
        
        idleEl.textContent = '$' + Math.max(0, idle).toLocaleString();
        deployedEl.textContent = '$' + deployed.toLocaleString();
        if (barEl) barEl.style.width = utilPct.toFixed(0) + '%';
        if (modeEl) modeEl.textContent = localStorage.getItem('sizingMode') || 'Conservative';
        
        // Color code: green >=70%, yellow 40-70%, red <40%
        if (barEl) {
            barEl.className = 'progress-bar ' + (
                utilPct >= 70 ? 'bg-success' :
                utilPct >= 40 ? 'bg-warning' :
                'bg-danger'
            );
        }
        
        // Show hint if idle > 30%
        if (hintEl) {
            if (idle > accountValue * 0.3) {
                hintEl.classList.remove('d-none');
            } else {
                hintEl.classList.add('d-none');
            }
        }
        
        idleEl.className = utilPct >= 70 ? 'fw-bold text-success' : utilPct >= 40 ? 'fw-bold text-warning' : 'fw-bold text-danger';
        
    } catch (error) {
        console.error('Error updating idle cash panel:', error);
    }
}

/**
 * Initialize global sizing mode selector.
 * Persists choice to localStorage, used by recommendation cards.
 */
function initSizingModeSelector() {
    const radios = document.querySelectorAll('input[name="sizing-mode"]');
    if (!radios.length) return;
    
    // Restore saved preference
    const saved = localStorage.getItem('sizingMode');
    if (saved === 'aggressive') {
        const aggressive = document.getElementById('sizing-aggressive');
        if (aggressive) aggressive.checked = true;
    } else {
        const conservative = document.getElementById('sizing-conservative');
        if (conservative) conservative.checked = true;
    }
    
    // Listen for changes
    radios.forEach(radio => {
        radio.addEventListener('change', () => {
            if (radio.checked) {
                localStorage.setItem('sizingMode', radio.value);
                // console.log('Sizing mode changed to:', radio.value);
                updateIdleCashPanel();
            }
        });
    });
}

/**
 * Initialize the dashboard
 */
async function initializeDashboard() {
    try {
        // console.log('Initializing dashboard...');
        
        // Create a container for alerts if it doesn't exist
        if (!document.querySelector('.content-container')) {
            const mainContainer = document.querySelector('main .container') || document.querySelector('main');
            if (mainContainer) {
                const contentContainer = document.createElement('div');
                contentContainer.className = 'content-container';
                mainContainer.prepend(contentContainer);
            }
        }
        
        // Load all dashboard components in parallel
        await Promise.all([
            loadPortfolioData(),
            loadTickers(),
            loadPendingOrders(),
            updateWeeklyEarningsSummary(),
            updateCashReserveStatus(),
            loadVixRegime(),
            loadMacroRegime(),
            loadTechnicalRegime(),
            loadPositionsCommandPanel(),
            updateIdleCashPanel()
        ]);
        
        // Set up cash reserve toggle listener
        const cashReserveToggle = document.getElementById('cash-reserve-toggle');
        if (cashReserveToggle) {
            cashReserveToggle.addEventListener('change', (e) => {
                toggleCashReserve(e.target.checked);
            });
        }
        
        // Initialize top recommendations (separate to avoid blocking other components)
        initializeTopRecommendations();

        // Initialize LLM advisor (non-blocking, disabled by default)
        initializeLLMAdvisor();
        
        // Load locked tickers for earnings lock display
        await loadLockedTickers();
        
        // Initialize global sizing mode selector
        initSizingModeSelector();
        
        // Initialize Bootstrap tooltips
        const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
        tooltipTriggerList.map(function (tooltipTriggerEl) {
            return new bootstrap.Tooltip(tooltipTriggerEl);
        });
        
        // console.log('Dashboard initialization complete');
    } catch (error) {
        console.error('Error initializing dashboard:', error);
        showAlert(`Error initializing dashboard: ${error.message}`, 'danger');
    }
}

// Initialize the dashboard when the DOM is loaded
document.addEventListener('DOMContentLoaded', initializeDashboard); 