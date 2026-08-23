/**
 * Account module for handling portfolio data
 * Manages account summary and positions display
 */
import { fetchAccountData, fetchPositions, fetchEarningsStatus, refreshAllEarnings, updateSingleEarnings } from './api.js';
import { showAlert } from '../utils/alerts.js';
import { escapeHtml, formatCurrency, formatPercent } from '../utils/formatters.js';

// Store account data
let accountData = null;
let positionsData = null;


function getOpenDStatus() {
    return window.appConnectionStatus || null;
}


function getUnavailablePositionsMessage() {
    const status = getOpenDStatus();
    if (!status) {
        return 'OpenD is unavailable. Position data cannot be loaded.';
    }
    if (status.status === 'login_required') {
        return 'OpenD login is required to load portfolio and position data.';
    }
    if (status.status === 'unavailable') {
        return status.message || 'OpenD is unavailable. Position data cannot be loaded.';
    }
    return 'Position data is unavailable right now.';
}


function updateUnavailableAccountState() {
    accountData = null;
    positionsData = [];

    updateDataStatusIndicator(true);

    const zeroValueIds = [
        'account-value',
        'cash-balance',
        'excess-liquidity',
        'initial-margin'
    ];

    zeroValueIds.forEach((id) => {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = formatCurrency(0);
        }
    });

    const leveragePercentageElement = document.getElementById('leverage-percentage');
    if (leveragePercentageElement) {
        leveragePercentageElement.textContent = formatPercent(0);
    }

    const leverageBar = document.getElementById('leverage-bar');
    if (leverageBar) {
        leverageBar.style.width = '0%';
        leverageBar.setAttribute('aria-valuenow', '0');
        leverageBar.className = 'progress-bar bg-secondary';
    }

    const positionsCountElement = document.getElementById('positions-count');
    if (positionsCountElement) {
        positionsCountElement.textContent = '0';
    }

    const unavailableMessage = getUnavailablePositionsMessage();
    populateStockPositionsTable([], unavailableMessage);
    populateOptionPositionsTable([], unavailableMessage);
}

/**
 * Update account summary display
 */
function updateAccountSummary() {
    if (!accountData) return;

    // Update the data status indicator
    updateDataStatusIndicator(accountData.is_frozen);

    // Update account value
    const accountValueElement = document.getElementById('account-value');
    if (accountValueElement) {
        accountValueElement.textContent = formatCurrency(accountData.account_value || 0);
    }

    // Update cash balance
    const cashBalanceElement = document.getElementById('cash-balance');
    if (cashBalanceElement) {
        cashBalanceElement.textContent = formatCurrency(accountData.available_cash || accountData.cash_balance || 0);
    }

    // Update positions count
    const positionsCountElement = document.getElementById('positions-count');
    if (positionsCountElement) {
        const posDict = accountData.positions || {};
        const posCount = typeof posDict === 'object' ? Object.keys(posDict).length : (posDict || 0);
        positionsCountElement.textContent = posCount;
    }

    // Update new margin metrics

    // Excess Liquidity
    const excessLiquidityElement = document.getElementById('excess-liquidity');
    if (excessLiquidityElement) {
        excessLiquidityElement.textContent = formatCurrency(accountData.excess_liquidity || 0);
    }

    // Initial Margin
    const initialMarginElement = document.getElementById('initial-margin');
    if (initialMarginElement) {
        initialMarginElement.textContent = formatCurrency(accountData.initial_margin || 0);
    }

    // Leverage Percentage
    const leveragePercentageElement = document.getElementById('leverage-percentage');
    if (leveragePercentageElement) {
        leveragePercentageElement.textContent = formatPercent(accountData.leverage_percentage || 0);
    }

    // Update the leverage progress bar
    const leverageBar = document.getElementById('leverage-bar');
    if (leverageBar) {
        const leveragePercentage = accountData.leverage_percentage || 0;

        // Set the width of the progress bar
        leverageBar.style.width = `${Math.min(100, leveragePercentage)}%`;
        leverageBar.setAttribute('aria-valuenow', Math.min(100, leveragePercentage));

        // Update the color based on leverage level
        if (leveragePercentage < 30) {
            leverageBar.className = 'progress-bar bg-success'; // Low leverage - green
        } else if (leveragePercentage < 60) {
            leverageBar.className = 'progress-bar bg-warning'; // Medium leverage - yellow
        } else {
            leverageBar.className = 'progress-bar bg-danger';  // High leverage - red
        }
    }
}

/**
 * Load positions that have reached 50%+ of max profit (ready to recycle capital).
 * Fetches from the alerts endpoint which flags profit_target_progress >= 50%.
 */
async function loadRecycleReadyPositions() {
    try {
        const response = await fetch('/api/portfolio/alerts');
        if (!response.ok) return;
        const data = await response.json();
        if (!data || !data.alerts) return;

        const recycleAlerts = data.alerts.filter(a =>
            a.alert_type === 'profit_target_50'
        );

        const panel = document.getElementById('recycle-panel');
        const list = document.getElementById('recycle-positions-list');
        if (!panel || !list) return;

        if (recycleAlerts.length === 0) {
            panel.style.display = 'none';
            return;
        }

        panel.style.display = 'block';
        list.innerHTML = recycleAlerts.map(a =>
            `<div class="d-flex justify-content-between align-items-center py-1 border-bottom">
                <span><strong>${escapeHtml(a.ticker)}</strong> ${escapeHtml(a.option_type)} $${escapeHtml(a.strike)}</span>
                <span class="badge bg-success">${escapeHtml(a.message)}</span>
            </div>`
        ).join('');
    } catch (e) {
    }
}

/**
 * Populate positions tables
 */
function populatePositionsTable() {
    if (!positionsData) return;

    // Debug log to see what data we're working with
    // Filter positions by security_type
    const stockPositions = positionsData.filter(position =>
        position.security_type === 'STK' || position.securityType === 'STK' || position.sec_type === 'STK');

    const optionPositions = positionsData.filter(position =>
        position.security_type === 'OPT' || position.securityType === 'OPT' || position.sec_type === 'OPT');
    // Populate stock positions table
    populateStockPositionsTable(stockPositions);

    // Populate option positions table
    populateOptionPositionsTable(optionPositions);
}

/**
 * Populate stock positions table
 * @param {Array} stockPositions - Array of stock positions
 */
function populateStockPositionsTable(stockPositions, emptyMessage = 'No stock positions found') {
    const stockTableBody = document.getElementById('stock-positions-table-body');
    if (!stockTableBody) return;

    // Clear table
    stockTableBody.innerHTML = '';

    if (stockPositions.length === 0) {
        const noDataRow = document.createElement('tr');
        noDataRow.innerHTML = `<td colspan="6" class="text-center">${emptyMessage}</td>`;
        stockTableBody.appendChild(noDataRow);
        return;
    }

    // Sort positions by market value (descending)
    stockPositions.sort((a, b) => {
        const marketValueA = a.market_value || 0;
        const marketValueB = b.market_value || 0;
        return marketValueB - marketValueA;
    });

    // Add stock positions
    stockPositions.forEach(position => {
        const row = document.createElement('tr');

        const avgCost = position.avg_cost || position.average_cost || 0;
        const marketValue = position.market_value || 0;
        const unrealizedPnL = position.unrealized_pnl || 0;

        // Calculate the P&L percentage based on the position's cost basis
        let unrealizedPnLPercent = 0;
        const totalCostBasis = Math.abs(position.position) * avgCost;
        if (totalCostBasis > 0) {
            unrealizedPnLPercent = (unrealizedPnL / totalCostBasis) * 100;
        }

        const pnlClass = unrealizedPnL >= 0 ? 'text-success' : 'text-danger';

        		// Earnings badge - data included in positions API response
		const earningsBadge = createEarningsBadgeHTML(position.earnings, position.symbol);

        row.innerHTML = `
            <td>${escapeHtml(position.symbol)}${earningsBadge}</td>
            <td>${escapeHtml(position.position)}</td>
            <td>${formatCurrency(avgCost)}</td>
            <td>${formatCurrency(position.market_price || 0)}</td>
            <td>${formatCurrency(marketValue)}</td>
            <td class="${pnlClass}">${formatCurrency(unrealizedPnL)} (${formatPercent(unrealizedPnLPercent)})</td>
        `;

        stockTableBody.appendChild(row);
    });
}

/**
 * Populate option positions table
 * @param {Array} optionPositions - Array of option positions
 */
function populateOptionPositionsTable(optionPositions, emptyMessage = 'No option positions found') {
    const optionTableBody = document.getElementById('option-positions-table-body');
    if (!optionTableBody) return;

    // Clear table
    optionTableBody.innerHTML = '';

    if (optionPositions.length === 0) {
        const noDataRow = document.createElement('tr');
        noDataRow.innerHTML = `<td colspan="9" class="text-center">${emptyMessage}</td>`;
        optionTableBody.appendChild(noDataRow);
        return;
    }

    // Group options by type (CALL/PUT)
    const callOptions = optionPositions.filter(position => {
        if (position.contract && position.contract.right) {
            return position.contract.right === 'C';
        } else {
            const optType = position.option_type || '';
            return optType === 'CALL' || optType === 'C' || optType === 'Call';
        }
    });

    const putOptions = optionPositions.filter(position => {
        if (position.contract && position.contract.right) {
            return position.contract.right === 'P';
        } else {
            const optType = position.option_type || '';
            return optType === 'PUT' || optType === 'P' || optType === 'Put';
        }
    });

    // Sort each group by market value (descending)
    const sortOptions = (a, b) => {
        const marketValueA = a.market_value || 0;
        const marketValueB = b.market_value || 0;
        return marketValueB - marketValueA;
    };

    callOptions.sort(sortOptions);
    putOptions.sort(sortOptions);

    // Add CALL options with header if there are any
    if (callOptions.length > 0) {
        const callHeader = document.createElement('tr');
        callHeader.className = 'table-primary';
        callHeader.innerHTML = `<td colspan="9" class="fw-bold">CALL OPTIONS (${callOptions.length})</td>`;
        optionTableBody.appendChild(callHeader);

        addOptionsToTable(callOptions, optionTableBody);
    }

    // Add PUT options with header if there are any
    if (putOptions.length > 0) {
        const putHeader = document.createElement('tr');
        putHeader.className = 'table-warning';
        putHeader.innerHTML = `<td colspan="9" class="fw-bold">PUT OPTIONS (${putOptions.length})</td>`;
        optionTableBody.appendChild(putHeader);

        addOptionsToTable(putOptions, optionTableBody);
    }
}

/**
 * Add options to the table
 * @param {Array} options - Array of option positions
 * @param {HTMLElement} tableBody - Table body element
 */
function addOptionsToTable(options, tableBody) {
    options.forEach(position => {
        const row = document.createElement('tr');

        const avgCost = position.avg_cost || position.average_cost || 0;
        const marketValue = position.market_value || 0;
        const unrealizedPnL = position.unrealized_pnl || 0;

        // Calculate the P&L percentage based on the position's cost basis
        let unrealizedPnLPercent = 0;
        const totalCostBasis = Math.abs(position.position) * avgCost * 100;
        if (totalCostBasis > 0) {
            unrealizedPnLPercent = (unrealizedPnL / totalCostBasis) * 100;
        }

        // Extract option details
        let optionType = '-';
        let strike = '-';
        let expiry = '-';

        // Get option details from either contract object or direct properties
        if (position.contract && position.contract.right) {
            optionType = position.contract.right === 'P' ? 'PUT' : 'CALL';
            strike = position.contract.strike ? formatCurrency(position.contract.strike) : '-';
            expiry = position.contract.lastTradeDateOrContractMonth || '-';
        } else {
            // Try to get from direct properties
            optionType = position.option_type || '-';
            strike = position.strike ? formatCurrency(position.strike) : '-';
            expiry = position.expiration || '-';
        }

        const pnlClass = unrealizedPnL >= 0 ? 'text-success' : 'text-danger';

        		// Earnings badge - data included in positions API response
		const earningsBadge = createEarningsBadgeHTML(position.earnings, position.symbol);

        row.innerHTML = `
            <td>${escapeHtml(position.symbol)}${earningsBadge}</td>
            <td>${escapeHtml(position.position)}</td>
            <td>${escapeHtml(optionType)}</td>
            <td>${escapeHtml(strike)}</td>
            <td>${escapeHtml(expiry)}</td>
            <td>${formatCurrency(avgCost)}</td>
            <td>${formatCurrency(position.market_price || 0)}</td>
            <td>${formatCurrency(marketValue)}</td>
            <td class="${pnlClass}">${formatCurrency(unrealizedPnL)} (${formatPercent(unrealizedPnLPercent)})</td>
        `;

        tableBody.appendChild(row);
    });
}

/**
 * Load portfolio data from API
 */
async function loadPortfolioData() {
    try {
        // Fetch account data
        accountData = await fetchAccountData();
        if (accountData) {
            updateAccountSummary();
            await loadPositionsTable();
            loadRecycleReadyPositions();
        } else {
            updateUnavailableAccountState();
        }
    } catch (error) {
        console.error('Error loading portfolio data:', error);
        showAlert('Error loading portfolio data. Please check your connection to moomoo OpenD.', 'danger');
        updateUnavailableAccountState();
    }
}

/**
 * Load positions data from API
 */
async function loadPositionsTable() {
    const data = await fetchPositions();
    if (data) {
        positionsData = data;
        if (!accountData && document.getElementById('positions-count')) {
            document.getElementById('positions-count').textContent = positionsData.length || 0;
        }
        populatePositionsTable();
    } else {
        positionsData = [];
        const unavailableMessage = getUnavailablePositionsMessage();
        populateStockPositionsTable([], unavailableMessage);
        populateOptionPositionsTable([], unavailableMessage);
    }
}

/**
 * Update the data status indicator
 * @param {boolean} isFrozen - Whether the data is frozen (true) or real-time (false)
 */
function updateDataStatusIndicator(isFrozen) {
    const dataStatusIndicator = document.getElementById('data-status-indicator');
    const dataStatusIconContainer = document.getElementById('data-status-icon');
    const dataUpdateTime = document.getElementById('data-update-time');

    if (!dataStatusIndicator || !dataStatusIconContainer || !dataUpdateTime) return;

    const dataStatusIcon = dataStatusIconContainer.querySelector('i');
    if (!dataStatusIcon) return;

    // Get current time for the update timestamp
    const now = new Date();
    const timeString = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    dataUpdateTime.textContent = `Updated ${timeString}`;

    const opendStatus = getOpenDStatus();
    if (opendStatus && opendStatus.status && opendStatus.status !== 'connected') {
        dataStatusIndicator.setAttribute('title', opendStatus.message || 'OpenD is unavailable');

        if (opendStatus.status === 'login_required') {
            dataStatusIndicator.className = 'badge bg-warning text-dark';
            dataStatusIndicator.textContent = 'LOGIN REQUIRED';
            dataStatusIcon.className = 'bi bi-person-lock';
        } else if (opendStatus.status === 'unavailable') {
            dataStatusIndicator.className = 'badge bg-danger';
            dataStatusIndicator.textContent = 'OPEN OPEND';
            dataStatusIcon.className = 'bi bi-plug';
        } else {
            dataStatusIndicator.className = 'badge bg-secondary';
            dataStatusIndicator.textContent = 'CONNECTING';
            dataStatusIcon.className = 'bi bi-arrow-repeat';
        }
        return;
    }

    if (isFrozen) {
        // Frozen data state
        dataStatusIndicator.className = 'badge bg-warning text-dark';
        dataStatusIndicator.textContent = 'FROZEN DATA';
        dataStatusIndicator.setAttribute('title', 'Using frozen data because market is closed');

        // Change icon to snowflake
        dataStatusIcon.className = 'bi bi-snow';
    } else {
        // Real-time data state
        dataStatusIndicator.className = 'badge bg-success';
        dataStatusIndicator.textContent = 'REAL-TIME';
        dataStatusIndicator.setAttribute('title', 'Using real-time market data');

        // Change icon to lightning
        dataStatusIcon.className = 'bi bi-lightning-fill';
    }
}


document.addEventListener('opend-status-changed', () => {
    if (!accountData) {
        const indicator = document.getElementById('data-status-indicator');
        const icon = document.querySelector('#data-status-icon i');
        if (indicator) {
            indicator.className = 'badge bg-info';
            indicator.textContent = 'LOADING DATA';
        }
        if (icon) icon.className = 'bi bi-arrow-repeat';
    }
});

// Export functions

/**
 * Initialize Earnings UI monitoring and refresh
 */
function initEarningsUI() {
    const refreshBtn = document.getElementById('refresh-earnings-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', async () => {
            try {
                // Show loading state
                refreshBtn.disabled = true;
                const icon = refreshBtn.querySelector('i');
                if (icon) icon.className = 'bi bi-arrow-clockwise btn-spin';
                updateEarningsStatusIndicator('REFRESHING...');

                const result = await refreshAllEarnings();

                if (result && result.success) {
                    showAlert(`Successfully refreshed earnings for ${result.updated_count} symbols.`, 'success');
                    // Reload positions to show new badges
                    if (typeof loadPositionsTable === 'function') {
                        loadPositionsTable();
                    }
                } else {
                    showAlert('Failed to refresh earnings.', 'danger');
                }
            } catch (error) {
                showAlert('Error refreshing earnings: ' + error.message, 'danger');
            } finally {
                refreshBtn.disabled = false;
                const icon = refreshBtn.querySelector('i');
                if (icon) icon.className = 'bi bi-arrow-clockwise';
                updateEarningsStatus();
            }
        });
    }

    // Initial status check
    updateEarningsStatus();

    // Periodically update status (every 30 seconds)
    setInterval(updateEarningsStatus, 30000);
}

/**
 * Update the global earnings status indicator
 */
async function updateEarningsStatus() {
    const indicator = document.getElementById('earnings-status-indicator');
    if (!indicator) return;

    const data = await fetchEarningsStatus();
    if (data) {
        updateEarningsStatusIndicator(data.status.toUpperCase());
    } else {
        updateEarningsStatusIndicator('UNKNOWN');
    }
}

/**
 * Update indicator UI
 * @param {string} statusText
 */
function updateEarningsStatusIndicator(statusText) {
    const indicator = document.getElementById('earnings-status-indicator');
    if (!indicator) return;

    indicator.textContent = `EARNINGS: ${statusText}`;
    if (statusText === 'RUNNING') {
        indicator.className = 'badge bg-success';
    } else if (statusText === 'REFRESHING...') {
        indicator.className = 'badge bg-info text-dark';
    } else {
        indicator.className = 'badge bg-secondary';
    }
}

export {
    formatCurrency,
    formatPercent,
    updateAccountSummary,
    populatePositionsTable,
    loadPortfolioData,
    loadPositionsTable,
    updateDataStatusIndicator
};

/**
 * Creates HTML for the earnings badge
 * @param {Object} earnings - Earnings data from API
 * @returns {string} HTML string
 */

/**
 * Creates HTML for the earnings badge
 * @param {Object} earnings - Earnings data from API
 * @param {string} ticker - The stock symbol
 * @returns {string} HTML string
 */
function createEarningsBadgeHTML(earnings, ticker) {
	if (!earnings) {
        // Even if no earnings, return a tiny refresh icon if it's a known symbol
        return ` <i class="bi bi-arrow-clockwise earnings-refresh-btn text-muted"
            data-ticker="${escapeHtml(ticker)}"
            title="Update earnings data for ${escapeHtml(ticker)}"
            style="cursor: pointer; font-size: 0.75rem;"></i>`;
    }

	const dateStr = earnings.date
		? new Date(earnings.date + 'T00:00:00').toLocaleDateString()
		: '';
	const daysText = earnings.days === 0
		? 'today'
		: (earnings.days === 1 ? 'tomorrow' : `in ${earnings.days} days`);
	const level = earnings.level || 'soon';

	const sourceParts = [];
	if (earnings.time_of_day) sourceParts.push(earnings.time_of_day);
	if (earnings.earnings_source) sourceParts.push(earnings.earnings_source);
	const sourceInfo = sourceParts.length ? ` · ${sourceParts.join(' · ')}` : '';

	// Three-tier badge: red (today), yellow (1-7 days), blue (8-30 days)
	let badgeClass;
	if (level === 'today') {
		badgeClass = 'badge bg-danger';
	} else if (level === 'upcoming') {
		badgeClass = 'badge bg-info text-dark';
	} else {
		badgeClass = 'badge bg-warning text-dark';
	}

	return ` <span class="${badgeClass}"
		title="${escapeHtml('Earnings ' + daysText + ' (' + dateStr + ')' + sourceInfo)}"
		style="font-size: 0.65rem; font-weight: 600; cursor: help;">(e)</span><i class="bi bi-arrow-clockwise earnings-refresh-btn ms-1 text-muted"
            data-ticker="${escapeHtml(ticker)}"
            title="Refresh earnings data for ${escapeHtml(ticker)}"
            style="cursor: pointer; font-size: 0.6rem;"></i>`;
}


// Initialize Earnings UI
document.addEventListener('DOMContentLoaded', () => {
    initEarningsUI();
});
