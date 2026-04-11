/**
 * Main dashboard module
 * Coordinates all dashboard components and initializes the dashboard
 */
import { loadPortfolioData } from './account.js';
import { loadTickers } from './options-table.js';
import { loadPendingOrders } from './orders.js';
import { initializeTopRecommendations } from './top-recommendations.js';
import { loadMacroRegime } from './macro.js';
import { showAlert } from '../utils/alerts.js';
import { fetchWeeklyOptionIncome } from './api.js';
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
        console.log('Cash reserve toggle:', enabled);
        
        // Refresh the status
        await updateCashReserveStatus();
    } catch (error) {
        console.error('Error toggling cash reserve:', error);
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
 * Initialize the dashboard
 */
async function initializeDashboard() {
    try {
        console.log('Initializing dashboard...');
        
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
            loadMacroRegime()
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
        
        // Initialize Bootstrap tooltips
        const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
        tooltipTriggerList.map(function (tooltipTriggerEl) {
            return new bootstrap.Tooltip(tooltipTriggerEl);
        });
        
        console.log('Dashboard initialization complete');
    } catch (error) {
        console.error('Error initializing dashboard:', error);
        showAlert(`Error initializing dashboard: ${error.message}`, 'danger');
    }
}

// Initialize the dashboard when the DOM is loaded
document.addEventListener('DOMContentLoaded', initializeDashboard); 