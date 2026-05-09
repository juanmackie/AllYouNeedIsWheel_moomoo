import { state, getSelectedExpirationPreference, getRenderExpirationValue, loadExcludedTickers } from './options-table-state.js';
import { calculatePremium, calculateEarningsSummary, updateEarningsSummary } from './options-table-calc.js';
import { showAlert } from '../utils/alerts.js';
import { fetchOptionData, fetchTickers, saveOptionOrder, fetchAccountData, fetchOptionExpirations } from './api.js';
import { updateOptionsTable, addTickerRowToTable, displayPremiumSummary, showToast, addPutQtyInputEventListeners } from './options-table-rendering.js';
import { addOptionsTableEventListeners } from './options-table-events.js';

let loadPendingOrdersFunc = null;

export async function getLoadPendingOrdersFunction() {
    if (typeof window.loadPendingOrders === 'function') {
        return window.loadPendingOrders;
    }

    if (!loadPendingOrdersFunc) {
        try {
            const requestEvent = new CustomEvent('requestPendingOrdersRefresh', {
                detail: { source: 'options-table' }
            });
            document.dispatchEvent(requestEvent);
        } catch (error) {
            console.error('Error trying to request pending orders refresh:', error);
        }
    }

    return null;
}

export async function refreshPendingOrders() {
    try {
        if (typeof window.loadPendingOrders === 'function') {
            await window.loadPendingOrders();
            return;
        }

        const event = new CustomEvent('ordersUpdated');
        document.dispatchEvent(event);

        const refreshButton = document.getElementById('refresh-pending-orders');
        if (refreshButton) {
            refreshButton.click();
            return;
        }
    } catch (error) {
        console.error('Error refreshing pending orders:', error);
    }
}

export async function refreshOptionsForTicker(ticker, updateUI = false) {
    try {
        const putTabWasActive = document.querySelector('#put-options-tab.active') !== null ||
                               document.querySelector('#put-options-section.active') !== null;

        if (!state.tickersData[ticker]) {
            state.tickersData[ticker] = {
                callOtmPercentage: 10,
                putOtmPercentage: 10,
                putQuantity: 1,
                errors: {}
            };
        }

        const callOtmPercentage = state.tickersData[ticker]?.callOtmPercentage || 10;
        const putOtmPercentage = state.tickersData[ticker]?.putOtmPercentage || 10;

        let allExpirations = [];
        try {
            const expirationData = await fetchOptionExpirations(ticker);

            if (expirationData && expirationData.expirations && expirationData.expirations.length > 0) {
                allExpirations = expirationData.expirations;
                state.tickersData[ticker].expirations = allExpirations;
            }
        } catch (error) {
            console.error(`Error fetching expiration dates for ${ticker}:`, error);
        }

        const selectedCallExpiration = getSelectedExpirationPreference(ticker, 'CALL');
        const selectedPutExpiration = getSelectedExpirationPreference(ticker, 'PUT');

        const callOptionData = await fetchOptionData(ticker, callOtmPercentage, 'CALL', selectedCallExpiration);
        const putOptionData = await fetchOptionData(ticker, putOtmPercentage, 'PUT', selectedPutExpiration);

        if (!state.tickersData[ticker]) {
            state.tickersData[ticker] = {
                data: {
                    data: {}
                },
                callOtmPercentage: callOtmPercentage,
                putOtmPercentage: putOtmPercentage,
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

        if (callOptionData && callOptionData.data && callOptionData.data[ticker]) {
            state.tickersData[ticker].data = state.tickersData[ticker].data || { data: {} };
            state.tickersData[ticker].data.data = state.tickersData[ticker].data.data || {};
            state.tickersData[ticker].data.data[ticker] = state.tickersData[ticker].data.data[ticker] || {};

            state.tickersData[ticker].data.data[ticker].stock_price = callOptionData.data[ticker].stock_price || 0;
            state.tickersData[ticker].data.data[ticker].position = callOptionData.data[ticker].position || 0;

            state.tickersData[ticker].data.data[ticker].calls = callOptionData.data[ticker].calls || [];

            if (!state.tickersData[ticker].errors) {
                state.tickersData[ticker].errors = {};
            }
            state.tickersData[ticker].errors.CALL = callOptionData.data[ticker].error || '';
        } else {
            if (state.tickersData[ticker]?.errors) {
                state.tickersData[ticker].errors.CALL = callOptionData?.message || 'No valid covered call data returned';
            }
        }

        if (putOptionData && putOptionData.data && putOptionData.data[ticker]) {
            state.tickersData[ticker].data.data[ticker].puts = putOptionData.data[ticker].puts || [];

            if (!state.tickersData[ticker].errors) {
                state.tickersData[ticker].errors = {};
            }
            state.tickersData[ticker].errors.PUT = putOptionData.data[ticker].error || '';
        } else {
            if (state.tickersData[ticker]?.errors) {
                state.tickersData[ticker].errors.PUT = putOptionData?.message || 'No valid cash-secured put data returned';
            }
        }

        if (updateUI) {
            if (putTabWasActive) {
                const putTab = document.getElementById('put-options-tab');
                const putSection = document.getElementById('put-options-section');
                const callTab = document.getElementById('call-options-tab');
                const callSection = document.getElementById('call-options-section');

                if (putTab && putSection && callTab && callSection) {
                    putTab.classList.add('active');
                    putSection.classList.add('show', 'active');
                    callTab.classList.remove('active');
                    callSection.classList.remove('show', 'active');
                }
            }

            updateOptionsTable();
            addOptionsTableEventListeners();
        }
    } catch (error) {
        console.error(`Error refreshing options for ${ticker}:`, error);
        showAlert(`Error refreshing options for ${ticker}: ${error.message}`, 'danger');
    }
}

export async function refreshAllOptions(optionType) {
    const optionsTableContainer = document.getElementById('options-table-container');
    if (!optionsTableContainer) {
        console.error('Options table container not found');
        return;
    }

    try {
        const putTabWasActive = document.querySelector('#put-options-tab.active') !== null ||
                               document.querySelector('#put-options-section.active') !== null;

        let allTickers = Object.keys(state.tickersData);
        if (allTickers.length === 0) {
            const tickersResult = await fetchTickers();
            if (tickersResult && tickersResult.tickers) {
                allTickers.push(...tickersResult.tickers);
            }
        }

        let tableId, buttonId;
        if (optionType === 'CALL') {
            tableId = 'call-options-table';
            buttonId = 'refresh-all-calls';
        } else if (optionType === 'PUT') {
            tableId = 'put-options-table';
            buttonId = 'refresh-all-puts';
        } else {
            tableId = 'call-options-table';
            buttonId = 'refresh-all-options';
        }

        let tickersToRefresh = [];

        const excludedTickers = loadExcludedTickers();

        if (optionType === 'CALL') {
            tickersToRefresh = allTickers.filter(ticker => {
                const sharesOwned = state.tickersData[ticker]?.data?.data?.[ticker]?.position || 0;
                return sharesOwned >= 100;
            });
        } else if (optionType === 'PUT') {
            tickersToRefresh = allTickers.filter(ticker => {
                const sharesOwned = state.tickersData[ticker]?.data?.data?.[ticker]?.position || 0;
                return state.customTickers.has(ticker) || 
                       (sharesOwned >= 100 && !excludedTickers.includes(ticker));
            });
        } else {
            tickersToRefresh = allTickers;
        }

        for (let i = 0; i < tickersToRefresh.length; i++) {
            const ticker = tickersToRefresh[i];

            const button = document.getElementById(buttonId);
            if (button) {
                const progressText = `Refreshing ${ticker} (${i+1}/${tickersToRefresh.length})`;
                button.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${progressText}`;
            }

            if (optionType) {
                await refreshOptionsForTickerByType(ticker, optionType, false);
            } else {
                await refreshOptionsForTickerByType(ticker, 'CALL', false);
                await refreshOptionsForTickerByType(ticker, 'PUT', false);
            }

            await new Promise(resolve => setTimeout(resolve, 50));
        }

        if (optionType === 'PUT') {
            const putTab = document.getElementById('put-options-tab');
            const putSection = document.getElementById('put-options-section');
            const callTab = document.getElementById('call-options-tab');
            const callSection = document.getElementById('call-options-section');

            if (putTab && putSection && callTab && callSection) {
                putTab.classList.add('active');
                putSection.classList.add('show', 'active');
                callTab.classList.remove('active');
                callSection.classList.remove('show', 'active');
            }
        } else if (putTabWasActive) {
            const putTab = document.getElementById('put-options-tab');
            const putSection = document.getElementById('put-options-section');
            const callTab = document.getElementById('call-options-tab');
            const callSection = document.getElementById('call-options-section');

            if (putTab && putSection && callTab && callSection) {
                putTab.classList.add('active');
                putSection.classList.add('show', 'active');
                callTab.classList.remove('active');
                callSection.classList.remove('show', 'active');
            }
        }

        updateOptionsTable();
        addOptionsTableEventListeners();
    } catch (error) {
        console.error(`Error refreshing ${optionType || 'all'} options:`, error);
        showAlert(`Error refreshing options: ${error.message}`, 'danger');

        updateOptionsTable();
    }
}

export async function refreshOptionsForTickerByType(ticker, optionType, updateUI = false) {
    try {
        let otmPercentage;
        if (optionType === 'CALL') {
            otmPercentage = state.tickersData[ticker]?.callOtmPercentage || 10;
        } else {
            otmPercentage = state.tickersData[ticker]?.putOtmPercentage || 10;
        }

        let allExpirations = [];
        try {
            const expirationData = await fetchOptionExpirations(ticker, optionType);

            if (expirationData && expirationData.expirations && expirationData.expirations.length > 0) {
                allExpirations = expirationData.expirations;
                state.tickersData[ticker].expirations = allExpirations;
            }
        } catch (error) {
            console.error(`Error fetching expiration dates for ${ticker}:`, error);
        }

        const selectedExpiration = getSelectedExpirationPreference(ticker, optionType);

        const optionData = await fetchOptionData(ticker, otmPercentage, optionType, selectedExpiration);

        if (!state.tickersData[ticker]) {
            state.tickersData[ticker] = {
                data: {
                    data: {}
                },
                callOtmPercentage: optionType === 'CALL' ? otmPercentage : 10,
                putOtmPercentage: optionType === 'PUT' ? otmPercentage : 10,
                putQuantity: optionType === 'PUT' ? 1 : 0,
                errors: {}
            };

            state.tickersData[ticker].data.data[ticker] = {
                stock_price: 0,
                position: 0,
                calls: [],
                puts: []
            };
        }

        if (optionData && optionData.data && optionData.data[ticker]) {
            if (optionData.data[ticker].stock_price) {
                state.tickersData[ticker].data.data[ticker].stock_price = optionData.data[ticker].stock_price;
            }
            if (optionData.data[ticker].position) {
                state.tickersData[ticker].data.data[ticker].position = optionData.data[ticker].position;
            }

            if (!state.tickersData[ticker].errors) {
                state.tickersData[ticker].errors = {};
            }

            if (optionType === 'CALL') {
                state.tickersData[ticker].data.data[ticker].calls = optionData.data[ticker].calls || [];
                state.tickersData[ticker].errors.CALL = optionData.data[ticker].error || '';
            } else {
                state.tickersData[ticker].data.data[ticker].puts = optionData.data[ticker].puts || [];
                state.tickersData[ticker].errors.PUT = optionData.data[ticker].error || '';
            }
        }

        if (updateUI) {
            if (optionType === 'PUT') {
                const putTab = document.getElementById('put-options-tab');
                const putSection = document.getElementById('put-options-section');
                const callTab = document.getElementById('call-options-tab');
                const callSection = document.getElementById('call-options-section');

                if (putTab && putSection && callTab && callSection) {
                    putTab.classList.add('active');
                    putSection.classList.add('show', 'active');
                    callTab.classList.remove('active');
                    callSection.classList.remove('show', 'active');
                }
            }

            updateOptionsTable();
            addOptionsTableEventListeners();
        }
    } catch (error) {
        console.error(`Error refreshing ${optionType} options for ${ticker}:`, error);
        showAlert(`Error refreshing ${optionType} options for ${ticker}: ${error.message}`, 'danger');
    }
}

export async function sellAllOptions(optionType) {
    const successOrders = [];
    const failedOrders = [];

    const tickers = Object.keys(state.tickersData);

    const buttonId = optionType === 'CALL' ? 'sell-all-calls' : 'sell-all-puts';
    const button = document.getElementById(buttonId);
    if (button) {
        button.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Processing...`;
        button.disabled = true;
    }

    try {
        for (const ticker of tickers) {
            const tickerData = state.tickersData[ticker];

            if (!tickerData || !tickerData.data || !tickerData.data.data || !tickerData.data.data[ticker]) {
                continue;
            }

            const optionData = tickerData.data.data[ticker];

            const sharesOwned = optionData.position || 0;
            if (optionType === 'CALL' && sharesOwned < 100) {
                continue;
            }

            let options = [];
            if (optionType === 'CALL' && optionData.calls && optionData.calls.length > 0) {
                options = optionData.calls;
            } else if (optionType === 'PUT' && optionData.puts && optionData.puts.length > 0) {
                options = optionData.puts;
            } else {
                continue;
            }

            if (options.length === 0) {
                continue;
            }

            const option = options[0];
            if (!option) {
                continue;
            }

            if (button) {
                button.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Processing ${ticker}...`;
            }

            const orderData = {
                ticker: ticker,
                option_type: optionType,
                strike: parseFloat(option.strike),
                expiration: option.expiration,
                action: 'SELL',
                quantity: optionType === 'CALL' ? 
                    Math.floor(sharesOwned / 100) : 
                    (tickerData.putQuantity || 1),
                bid: parseFloat(option.bid || 0),
                ask: parseFloat(option.ask || 0),
                last: parseFloat(option.last || 0),
                premium: calculatePremium(option.bid, option.ask, option.last),
                delta: parseFloat(option.delta || 0),
                gamma: parseFloat(option.gamma || 0),
                theta: parseFloat(option.theta || 0),
                vega: parseFloat(option.vega || 0),
                implied_volatility: parseFloat(option.implied_volatility || 0),
                timestamp: new Date().toISOString(),
                stock_price: state.tickersData[ticker]?.data?.data?.[ticker]?.stock_price || 0
            };

            if (orderData.bid <= 0 && button.closest('tr')) {
                orderData.bid = parseFloat(option.bid || 0);
                orderData.ask = parseFloat(option.ask || 0);
                orderData.last = parseFloat(option.last || 0);
                orderData.premium = calculatePremium(option.bid, option.ask, option.last);
            }

            if (orderData.bid <= 0 && orderData.ask <= 0 && orderData.last <= 0 && orderData.premium <= 0) {
                orderData.premium = Math.max(orderData.strike * 0.01, 0.05);
            }

            try {
                button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
                button.disabled = true;

                const result = await saveOptionOrder(orderData);

                if (result && result.order_id) {
                    successOrders.push(`${ticker} ${optionType} ${option.strike} ${option.expiration}`);
                } else {
                    failedOrders.push(`${ticker} ${optionType} ${option.strike} ${option.expiration}`);
                }
            } catch (error) {
                failedOrders.push(`${ticker} ${optionType} ${option.strike} ${option.expiration}`);
            }

            await new Promise(resolve => setTimeout(resolve, 100));
        }

        if (button) {
            button.innerHTML = `<i class="bi bi-check2-all"></i> Add All`;
            button.disabled = false;
        }

        if (successOrders.length > 0) {
            showAlert(`Successfully created ${successOrders.length} ${optionType.toLowerCase()} option orders`, 'success');

            await refreshPendingOrders();

            setTimeout(async () => {
                await refreshPendingOrders();
            }, 500);

            setTimeout(async () => {
                await refreshPendingOrders();
            }, 1500);
        } else {
            showAlert(`No ${optionType.toLowerCase()} option orders were created`, 'warning');
        }

        return successOrders.length;
    } catch (error) {
        console.error(`Error in sellAllOptions for ${optionType}:`, error);

        if (button) {
            button.innerHTML = `<i class="bi bi-check2-all"></i> Add All`;
            button.disabled = false;
        }

        showAlert(`Error adding ${optionType.toLowerCase()} options: ${error.message}`, 'danger');

        return 0;
    }
}
