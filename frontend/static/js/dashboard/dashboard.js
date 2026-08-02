/**
 * Main dashboard module — barrel orchestrator.
 *
 * Composition of sub-modules split from the original monolithic file (F042).
 * Re-exports the primary initialization function so that consumer code
 * (e.g. the HTML template) does not need to change.
 */
import { loadPortfolioData } from './account.js';
import { loadTickers } from './options-table.js';
import { initializeTopRecommendations } from './top-recommendations.js';
import { showAlert } from '../utils/alerts.js';
import { fetchWeeklyOptionIncome } from './api.js';
import { formatCurrency } from '../utils/formatters.js';
import { initializeDashboard } from './dashboard-init.js';

export { initializeDashboard };
export { updateCashReserveStatus, updateIdleCashPanel } from './dashboard-cash.js';

export let weeklyIncomeData = null;

// Bootstrap dashboard on page load
document.addEventListener('DOMContentLoaded', () => {
    initializeDashboard().catch(err => {
        console.error('Dashboard initialization failed:', err);
    });
});
