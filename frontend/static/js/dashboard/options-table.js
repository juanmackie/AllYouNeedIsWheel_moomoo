/**
 * Options Table module for handling options display and interaction
 * Orchestrator that composes functionality from focused sub-modules.
 */
import { fetchTickers, fetchAccountData, fetchOptionExpirations, fetchStockPrices } from './api.js';
import { state, loadOtmSettings, loadCustomTickers } from './options-table-state.js';
import { calculateEarningsSummary } from './options-table-calc.js';
import { updateOptionsTable, addTickerRowToTable, displayPremiumSummary, addPutQtyInputEventListeners, initializeOptionsTableTooltips } from './options-table-rendering.js';
import { addOptionsTableEventListeners, setupCustomTickerEventListeners } from './options-table-events.js';
import { refreshOptionsForTicker, refreshAllOptions, refreshOptionsForTickerByType, sellAllOptions } from './options-table-actions.js';

async function loadTickers() {
    try {
        const savedTickers = localStorage.getItem('customTickers');
        if (savedTickers) {
            const tickersArray = JSON.parse(savedTickers);
            state.customTickers = new Set(tickersArray);
        }
    } catch (error) {
        console.error('Error loading custom tickers:', error);
    }

    loadOtmSettings();

    const optionsTableContainer = document.getElementById('options-table-container');
    if (!optionsTableContainer) {
        console.error("Options table container not found");
        return;
    }

    const putTabWasActive = document.querySelector('#put-options-tab.active') !== null ||
                           document.querySelector('#put-options-section.active') !== null;

    const tabsHTML = `
        <ul class="nav nav-tabs mb-3" id="options-tabs" role="tablist">
            <li class="nav-item" role="presentation">
                <button class="nav-link ${putTabWasActive ? '' : 'active'}" id="call-options-tab" data-bs-toggle="tab" data-bs-target="#call-options-section" type="button" role="tab" aria-controls="call-options-section" aria-selected="${putTabWasActive ? 'false' : 'true'}">
                    Covered Calls
                </button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link ${putTabWasActive ? 'active' : ''}" id="put-options-tab" data-bs-toggle="tab" data-bs-target="#put-options-section" type="button" role="tab" aria-controls="put-options-section" aria-selected="${putTabWasActive ? 'true' : 'false'}">
                    Cash-Secured Puts
                </button>
            </li>
        </ul>

        <div class="tab-content" id="options-tabs-content">
            <div class="tab-pane fade ${putTabWasActive ? '' : 'show active'}" id="call-options-section" role="tabpanel" aria-labelledby="call-options-tab">
                <div class="d-flex justify-content-end mb-2">
                    <button class="btn btn-sm btn-outline-success me-2" id="sell-all-calls">
                        <i class="bi bi-check2-all"></i> Add All
                    </button>
                    <button class="btn btn-sm btn-outline-primary" id="refresh-all-calls">
                        <i class="bi bi-arrow-repeat"></i> Refresh All Calls
                    </button>
                </div>
                <div class="table-responsive">
                    <table class="table table-striped table-hover table-sm" id="call-options-table">
                        <thead>
                            <tr>
                                <th data-bs-toggle="tooltip" title="Stock symbol and quality metrics">Ticker</th>
                                <th data-bs-toggle="tooltip" title="Number of shares you own (need 100+ to sell calls)">Shares</th>
                                <th data-bs-toggle="tooltip" title="Current stock market price">Stock Price</th>
                                <th data-bs-toggle="tooltip" title="Out-of-the-Money %: Distance from current price. Higher = safer but less premium.">OTM %</th>
                                <th data-bs-toggle="tooltip" title="Price where your shares would be called away">Strike</th>
                                <th data-bs-toggle="tooltip" title="When the option expires">Expiration</th>
                                <th data-bs-toggle="tooltip" title="Midpoint between bid and ask - target this price">Mid Price</th>
                                <th data-bs-toggle="tooltip" title="Probability option finishes in-the-money (0-1 scale). Lower = safer.">Delta</th>
                                <th data-bs-toggle="tooltip" title="Implied Volatility: Market's price movement expectation. Higher IV = more premium.">IV%</th>
                                <th data-bs-toggle="tooltip" title="Number of contracts you can sell (1 = 100 shares)">Qty</th>
                                <th data-bs-toggle="tooltip" title="Total income you'll receive from selling">Total Premium</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td colspan="12" class="text-center p-3">
                                    <div class="spinner-border text-primary" role="status"></div>
                                    <p class="mt-2">Loading options data...</p>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="tab-pane fade ${putTabWasActive ? 'show active' : ''}" id="put-options-section" role="tabpanel" aria-labelledby="put-options-tab">
                <div class="d-flex justify-content-between mb-2">
                    <div class="d-flex align-items-center">
                        <div class="input-group input-group-sm" style="width: 250px;">
                            <input type="text" class="form-control" id="custom-ticker-input" 
                                placeholder="Add ticker (e.g., AAPL)" maxlength="5">
                            <button class="btn btn-outline-primary" id="add-custom-ticker">
                                <i class="bi bi-plus-circle"></i> Add
                            </button>
                        </div>
                    </div>
                    <div>
                        <button class="btn btn-sm btn-outline-success me-2" id="sell-all-puts">
                            <i class="bi bi-check2-all"></i> Add All
                        </button>
                        <button class="btn btn-sm btn-outline-primary" id="refresh-all-puts">
                            <i class="bi bi-arrow-repeat"></i> Refresh All Puts
                        </button>
                    </div>
                </div>
                <div class="table-responsive">
                    <table class="table table-striped table-hover table-sm" id="put-options-table">
                        <thead>
                            <tr>
                                <th data-bs-toggle="tooltip" title="Stock symbol and quality metrics">Ticker</th>
                                <th data-bs-toggle="tooltip" title="Current stock market price">Stock Price</th>
                                <th data-bs-toggle="tooltip" title="Out-of-the-Money %: Distance from current price. Higher = safer but less premium.">OTM %</th>
                                <th data-bs-toggle="tooltip" title="Price where you'd be obligated to buy shares">Strike</th>
                                <th data-bs-toggle="tooltip" title="When the option expires">Expiration</th>
                                <th data-bs-toggle="tooltip" title="Midpoint between bid and ask - target this price">Mid Price</th>
                                <th data-bs-toggle="tooltip" title="Probability option finishes in-the-money (0-1 scale). Lower = safer.">Delta</th>
                                <th data-bs-toggle="tooltip" title="Implied Volatility: Market's price movement expectation. Higher IV = more premium.">IV%</th>
                                <th data-bs-toggle="tooltip" title="Number of contracts to sell (1 = obligation to buy 100 shares)">Qty</th>
                                <th data-bs-toggle="tooltip" title="Total income you'll receive from selling">Total Premium</th>
                                <th data-bs-toggle="tooltip" title="Cash needed in your account as collateral">Cash Required</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td colspan="12" class="text-center p-3">
                                    <div class="spinner-border text-primary" role="status"></div>
                                    <p class="mt-2">Loading options data...</p>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    `;

    optionsTableContainer.innerHTML = tabsHTML;

    addOptionsTableEventListeners();
    setupCustomTickerEventListeners();

    try {
        state.portfolioSummary = await fetchAccountData();
    } catch (error) {
        console.error('Error fetching portfolio data:', error);
    }

    const data = await fetchTickers();
    let portfolioTickers = [];

    if (data && data.tickers) {
        portfolioTickers = data.tickers;
    }

    const allTickers = [...new Set([...portfolioTickers, ...state.customTickers])];

    document.querySelector('#call-options-table tbody').innerHTML = '';
    document.querySelector('#put-options-table tbody').innerHTML = '';

    const totalTickers = allTickers.length;
    for (let i = 0; i < totalTickers; i++) {
        const ticker = allTickers[i];

        if (!state.tickersData[ticker]) {
            state.tickersData[ticker] = {
                data: {
                    data: {}
                },
                callOtmPercentage: 10,
                putOtmPercentage: 10,
                putQuantity: 1,
                errors: {}
            };

            state.tickersData[ticker].data.data[ticker] = {
                stock_price: 0,
                position: 0,
                calls: [],
                puts: []
            };
        }

        const progressMessage = `Loading data for ${ticker} (${i+1}/${totalTickers})...`;
        const callStatusRow = document.createElement('tr');
        callStatusRow.id = `call-status-${ticker}`;
        callStatusRow.innerHTML = `
            <td colspan="13" class="text-center">
                <div class="d-flex align-items-center justify-content-center">
                    <div class="spinner-border spinner-border-sm text-primary me-2" role="status"></div>
                    <span>${progressMessage}</span>
                </div>
            </td>
        `;
        document.querySelector('#call-options-table tbody').appendChild(callStatusRow);

        const putStatusRow = document.createElement('tr');
        putStatusRow.id = `put-status-${ticker}`;
        putStatusRow.innerHTML = `
            <td colspan="13" class="text-center">
                <div class="d-flex align-items-center justify-content-center">
                    <div class="spinner-border spinner-border-sm text-primary me-2" role="status"></div>
                    <span>${progressMessage}</span>
                </div>
            </td>
        `;
        document.querySelector('#put-options-table tbody').appendChild(putStatusRow);

        try {
            await refreshOptionsForTicker(ticker, false);

            document.getElementById(`call-status-${ticker}`)?.remove();
            document.getElementById(`put-status-${ticker}`)?.remove();

            addTickerRowToTable('call-options-table', 'CALL', ticker);
            addTickerRowToTable('put-options-table', 'PUT', ticker);

            addPutQtyInputEventListeners();
        } catch (error) {
            console.error(`Error loading data for ticker ${ticker}:`, error);

            const errorMessage = `Error loading data for ${ticker}: ${error.message}`;
            if (document.getElementById(`call-status-${ticker}`)) {
                document.getElementById(`call-status-${ticker}`).innerHTML = `
                    <td colspan="13" class="text-center text-danger">
                        <i class="bi bi-exclamation-triangle"></i> ${errorMessage}
                    </td>
                `;
            }
            if (document.getElementById(`put-status-${ticker}`)) {
                document.getElementById(`put-status-${ticker}`).innerHTML = `
                    <td colspan="13" class="text-center text-danger">
                        <i class="bi bi-exclamation-triangle"></i> ${errorMessage}
                    </td>
                `;
            }

            setTimeout(() => {
                document.getElementById(`call-status-${ticker}`)?.remove();
                document.getElementById(`put-status-${ticker}`)?.remove();
            }, 3000);
        }

        const earningsSummary = calculateEarningsSummary();
        displayPremiumSummary(earningsSummary);
    }

    document.querySelectorAll('tr[id^="call-status-"], tr[id^="put-status-"]').forEach(row => row.remove());

    if (document.querySelector('#call-options-table tbody').children.length === 0) {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td colspan="13" class="text-center p-3">
                <div class="alert alert-info m-0">
                    No covered call opportunities found.
                </div>
            </td>
        `;
        document.querySelector('#call-options-table tbody').appendChild(row);
    }

    if (document.querySelector('#put-options-table tbody').children.length === 0) {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td colspan="13" class="text-center p-3">
                <div class="alert alert-info m-0">
                    No cash secured put opportunities found.
                    Add a ticker to see put option opportunities.
                </div>
            </td>
        `;
        document.querySelector('#put-options-table tbody').appendChild(row);
    }

    const earningsSummary = calculateEarningsSummary();
    displayPremiumSummary(earningsSummary);
}

export {
    loadTickers,
    refreshOptionsForTicker,
    refreshOptionsForTickerByType,
    refreshAllOptions,
    sellAllOptions
};
