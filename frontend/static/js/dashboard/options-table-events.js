import { state, setSelectedExpirationPreference, getSelectedExpirationPreference, saveOtmSettings, removeTicker } from './options-table-state.js';
import { calculatePremium } from './options-table-calc.js';
import { showAlert } from '../utils/alerts.js';
import { formatCurrency } from '../utils/formatters.js';
import { fetchOptionData, fetchOptionExpirations, fetchStockPrices } from './api.js';
import { updateOptionsTable, showToast, addOtmInputEventListeners } from './options-table-rendering.js';
import { refreshOptionsForTicker, refreshOptionsForTickerByType, refreshAllOptions, sellAllOptions } from './options-table-actions.js';

export async function handleExpirationChange(selectElement) {
    const ticker = selectElement.getAttribute('data-ticker');
    const optionType = selectElement.getAttribute('data-option-type');
    const selectedExpiration = selectElement.value;

    setSelectedExpirationPreference(ticker, optionType, selectedExpiration);

    try {
        const row = selectElement.closest('tr');
        const cells = row.querySelectorAll('td');
        cells.forEach(cell => {
            if (!cell.querySelector('select') && !cell.querySelector('input')) {
                cell.innerHTML = '<div class="spinner-border spinner-border-sm text-secondary" role="status"></div>';
            }
        });

        const otmPercentage = optionType === 'CALL'
            ? state.tickersData[ticker]?.callOtmPercentage || 10
            : state.tickersData[ticker]?.putOtmPercentage || 10;

        const optionData = await fetchOptionData(ticker, otmPercentage, optionType, selectedExpiration);

        if (optionData && optionData.data && optionData.data[ticker]) {
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

            updateOptionsTable();
            addOptionsTableEventListeners();
        }
    } catch (error) {
        console.error(`Error updating options for new expiration: ${error.message}`);
        showAlert(`Error updating options: ${error.message}`, 'danger');

        updateOptionsTable();
        addOptionsTableEventListeners();
    }
}

export function addOptionsTableEventListeners() {
    const container = document.getElementById('options-table-container');
    if (!container) return;

    if (typeof bootstrap !== 'undefined') {
        const tabEls = document.querySelectorAll('#options-tabs button[data-bs-toggle="tab"]');
        tabEls.forEach(tabEl => {
            const tab = new bootstrap.Tab(tabEl);

            tabEl.addEventListener('click', event => {
                event.preventDefault();
                tab.show();
            });
        });
    } else {
        const callTab = document.getElementById('call-options-tab');
        const putTab = document.getElementById('put-options-tab');
        const callSection = document.getElementById('call-options-section');
        const putSection = document.getElementById('put-options-section');

        if (callTab && putTab && callSection && putSection) {
            callTab.addEventListener('click', (e) => {
                e.preventDefault();
                callTab.classList.add('active');
                putTab.classList.remove('active');
                callSection.classList.add('show', 'active');
                putSection.classList.remove('show', 'active');
            });
        
            putTab.addEventListener('click', (e) => {
                e.preventDefault();
                callTab.classList.remove('active');
                putTab.classList.add('active');
                callSection.classList.remove('show', 'active');
                putSection.classList.add('show', 'active');
            });
        }
    }

    if (!state.containerEventListenersInitialized) {
        container.addEventListener('click', async (event) => {
            if (event.target.classList.contains('refresh-option') ||
                event.target.closest('.refresh-option')) {

                const button = event.target.classList.contains('refresh-option') ?
                           event.target :
                           event.target.closest('.refresh-option');

                const ticker = button.dataset.ticker;
                const optionType = button.dataset.type;

                if (ticker) {
                    button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Loading...';
                    button.disabled = true;

                    try {
                        if (optionType) {
                            await refreshOptionsForTickerByType(ticker, optionType, true);
                        } else {
                            await refreshOptionsForTicker(ticker, true);
                        }
                        button.innerHTML = '<i class="bi bi-arrow-repeat"></i>';
                    } catch (error) {
                        console.error('Error refreshing ticker:', error);
                    } finally {
                        button.disabled = false;
                    }
                }
            }

            if (event.target.classList.contains('refresh-otm') ||
                event.target.closest('.refresh-otm')) {

                const button = event.target.classList.contains('refresh-otm') ?
                               event.target :
                               event.target.closest('.refresh-otm');

                const ticker = button.dataset.ticker;
                if (ticker) {
                    button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
                    button.disabled = true;

                    try {
                        const inputGroup = button.closest('.input-group');
                        const otmInput = inputGroup.querySelector('.otm-input');
                        const otmPercentage = parseInt(otmInput.value, 10);
                        const optionType = otmInput.dataset.optionType || 'CALL';

                        const row = button.closest('tr');
                        let selectedExpiration = null;
                        if (row) {
                            const expirationSelect = row.querySelector('.expiration-select');
                            if (expirationSelect) {
                                selectedExpiration = expirationSelect.value;
                            }
                        }

                        if (state.tickersData[ticker]) {
                            if (optionType === 'CALL') {
                                state.tickersData[ticker].callOtmPercentage = otmPercentage;
                            } else {
                                state.tickersData[ticker].putOtmPercentage = otmPercentage;
                            }

                            saveOtmSettings();

                            setSelectedExpirationPreference(ticker, optionType, selectedExpiration);
                        }

                        if (selectedExpiration) {
                            const optionData = await fetchOptionData(ticker, otmPercentage, optionType, selectedExpiration);

                            if (optionData && optionData.data && optionData.data[ticker]) {
                                if (optionType === 'CALL') {
                                    state.tickersData[ticker].data.data[ticker].calls = optionData.data[ticker].calls || [];
                                } else {
                                    state.tickersData[ticker].data.data[ticker].puts = optionData.data[ticker].puts || [];
                                }

                                updateOptionsTable();
                                addOptionsTableEventListeners();

                                button.classList.remove('btn-primary');
                                button.classList.add('btn-outline-secondary');
                                button.innerHTML = '<i class="bi bi-arrow-repeat"></i>';

                                showToast('success', 'OTM Applied', `${ticker} ${optionType} options refreshed with ${otmPercentage}% OTM`);
                            }
                        } else {
                            await refreshOptionsForTickerByType(ticker, optionType, true);

                            button.classList.remove('btn-primary');
                            button.classList.add('btn-outline-secondary');
                            button.innerHTML = '<i class="bi bi-arrow-repeat"></i>';

                            showToast('success', 'OTM Applied', `${ticker} ${optionType} options refreshed`);
                        }
                    } catch (error) {
                        console.error(`Error refreshing ${ticker} with new OTM%:`, error);
                        showToast('error', 'Refresh Failed', `Failed to refresh ${ticker} options. ${error.message || 'Please try again.'}`);
                    } finally {
                        button.disabled = false;
                    }
                }
            }

            if (event.target.classList.contains('sell-option') ||
                event.target.closest('.sell-option')) {

                const button = event.target.classList.contains('sell-option') ?
                               event.target :
                               event.target.closest('.sell-option');

                if (button.disabled) {
                    return;
                }

                const ticker = button.dataset.ticker;
                const optionType = button.dataset.optionType;
                const strike = button.dataset.strike;
                const expiration = button.dataset.expiration;

                if (ticker && optionType && strike && expiration) {
                    const orderData = {
                        ticker: ticker,
                        option_type: optionType,
                        strike: parseFloat(strike),
                        expiration: expiration,
                        action: 'SELL',
                        quantity: optionType === 'CALL' ?
                            Math.floor(state.tickersData[ticker]?.data?.data?.[ticker]?.position / 100) || 1 :
                            (state.tickersData[ticker]?.putQuantity || 1),
                        bid: parseFloat(button.dataset.bid || 0),
                        ask: parseFloat(button.dataset.ask || 0),
                        last: parseFloat(button.dataset.last || 0),
                        premium: calculatePremium(button.dataset.bid, button.dataset.ask, button.dataset.last),
                        delta: parseFloat(button.dataset.delta || 0),
                        gamma: parseFloat(button.dataset.gamma || 0),
                        theta: parseFloat(button.dataset.theta || 0),
                        vega: parseFloat(button.dataset.vega || 0),
                        implied_volatility: parseFloat(button.dataset.implied_volatility || 0),
                        timestamp: new Date().toISOString(),
                        stock_price: state.tickersData[ticker]?.data?.data?.[ticker]?.stock_price || 0
                    };

                    if (orderData.bid <= 0 && button.closest('tr')) {
                        const row = button.closest('tr');
                        const bidCell = row.querySelector('td[data-field="bid"]');
                        const askCell = row.querySelector('td[data-field="ask"]');
                        const lastCell = row.querySelector('td[data-field="last"]');

                        if (bidCell) orderData.bid = parseFloat(bidCell.textContent) || orderData.bid;
                        if (askCell) orderData.ask = parseFloat(askCell.textContent) || orderData.ask;
                        if (lastCell) orderData.last = parseFloat(lastCell.textContent) || orderData.last;
                    }

                    if (orderData.bid <= 0 && orderData.ask <= 0 && orderData.last <= 0 && orderData.premium <= 0) {
                        orderData.premium = Math.max(orderData.strike * 0.01, 0.05);
                    }

                    try {
                        button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
                        button.disabled = true;

                        const result = await saveOptionOrder(orderData);

                        if (result && result.order_id) {
                            await refreshPendingOrders();
                        }
                    } catch (error) {
                        console.error('Error saving order:', error);
                    } finally {
                        button.innerHTML = 'Add';
                        button.disabled = false;
                    }
                }
            }

            if (event.target.id === 'sell-all-calls' ||
                event.target.closest('#sell-all-calls')) {

                const button = event.target.id === 'sell-all-calls' ?
                               event.target :
                               event.target.closest('#sell-all-calls');

                if (button.disabled) {
                    return;
                }

                try {
                    await sellAllOptions('CALL');
                } catch (error) {
                    console.error('Error in sell all calls handler:', error);
                }
            }

            if (event.target.id === 'sell-all-puts' ||
                event.target.closest('#sell-all-puts')) {

                const button = event.target.id === 'sell-all-puts' ?
                               event.target :
                               event.target.closest('#sell-all-puts');

                if (button.disabled) {
                    return;
                }

                try {
                    await sellAllOptions('PUT');
                } catch (error) {
                    console.error('Error in sell all puts handler:', error);
                }
            }

            if (event.target.id === 'refresh-all-options' ||
                event.target.closest('#refresh-all-options')) {

                const button = event.target.id === 'refresh-all-options' ?
                               event.target :
                               event.target.closest('#refresh-all-options');

                if (button.disabled) {
                    return;
                }

                button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Loading...';
                button.disabled = true;

                try {
                    await refreshAllOptions();
                } catch (error) {
                    console.error('Error refreshing all options:', error);
                } finally {
                    button.innerHTML = '<i class="bi bi-arrow-repeat"></i> Refresh All';
                    button.disabled = false;
                }
            }

            if (event.target.id === 'refresh-all-calls' ||
                event.target.closest('#refresh-all-calls')) {

                const button = event.target.id === 'refresh-all-calls' ?
                               event.target :
                               event.target.closest('#refresh-all-calls');

                if (button.disabled) {
                    return;
                }

                button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Loading...';
                button.disabled = true;

                try {
                    await refreshAllOptions('CALL');
                } catch (error) {
                    console.error('Error refreshing all call options:', error);
                } finally {
                    button.innerHTML = '<i class="bi bi-arrow-repeat"></i> Refresh All Calls';
                    button.disabled = false;
                }
            }

            if (event.target.id === 'refresh-all-puts' ||
                event.target.closest('#refresh-all-puts')) {

                const button = event.target.id === 'refresh-all-puts' ?
                               event.target :
                               event.target.closest('#refresh-all-puts');

                if (button.disabled) {
                    return;
                }

                button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Loading...';
                button.disabled = true;

                try {
                    await refreshAllOptions('PUT');
                } catch (error) {
                    console.error('Error refreshing all put options:', error);
                } finally {
                    button.innerHTML = '<i class="bi bi-arrow-repeat"></i> Refresh All Puts';
                    button.disabled = false;
                }
            }

            if (event.target.classList.contains('delete-ticker') ||
                event.target.closest('.delete-ticker')) {

                const button = event.target.classList.contains('delete-ticker') ?
                               event.target :
                               event.target.closest('.delete-ticker');

                const ticker = button.dataset.ticker;
                if (ticker) {
                    removeTicker(ticker);
                    updateOptionsTable();
                    addOptionsTableEventListeners();
                    showToast('info', 'Ticker Removed', `${ticker} has been removed.`);
                }
            }
        });

        state.containerEventListenersInitialized = true;
    }

    if (state.eventListenersInitialized) {
        return;
    }

    const refreshAllButton = document.getElementById('refresh-all-options');
    if (refreshAllButton) {
        refreshAllButton.addEventListener('click', async () => {
            refreshAllButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Loading...';
            refreshAllButton.disabled = true;

            try {
                await refreshAllOptions();
            } catch (error) {
                console.error('Error refreshing all options:', error);
            } finally {
                refreshAllButton.innerHTML = '<i class="bi bi-arrow-repeat"></i> Refresh All';
                refreshAllButton.disabled = false;
            }
        });
    }

    container.addEventListener('change', async (event) => {
        const expirationSelect = event.target.closest('.expiration-select');
        if (expirationSelect && container.contains(expirationSelect)) {
            await handleExpirationChange(expirationSelect);
        }
    });

    const refreshAllCallsButton = document.getElementById('refresh-all-calls');
    if (refreshAllCallsButton) {
        refreshAllCallsButton.addEventListener('click', async () => {
            refreshAllCallsButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Loading...';
            refreshAllCallsButton.disabled = true;

            try {
                await refreshAllOptions('CALL');
            } catch (error) {
                console.error('Error refreshing all call options:', error);
            } finally {
                refreshAllCallsButton.innerHTML = '<i class="bi bi-arrow-repeat"></i> Refresh All Calls';
                refreshAllCallsButton.disabled = false;
            }
        });
    }

    const refreshAllPutsButton = document.getElementById('refresh-all-puts');
    if (refreshAllPutsButton) {
        refreshAllPutsButton.addEventListener('click', async () => {
            refreshAllPutsButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Loading...';
            refreshAllPutsButton.disabled = true;

            try {
                await refreshAllOptions('PUT');
            } catch (error) {
                console.error('Error refreshing all put options:', error);
            } finally {
                refreshAllPutsButton.innerHTML = '<i class="bi bi-arrow-repeat"></i> Refresh All Puts';
                refreshAllPutsButton.disabled = false;
            }
        });
    }

    state.eventListenersInitialized = true;

    addOtmInputEventListeners();
}

export function setupCustomTickerEventListeners() {
    if (state.customTickerListenersInitialized) return;

    const addCustomTickerBtn = document.getElementById('add-custom-ticker');
    const customTickerInput = document.getElementById('custom-ticker-input');

    if (addCustomTickerBtn && customTickerInput) {
        addCustomTickerBtn.addEventListener('click', async () => {
            const ticker = customTickerInput.value.trim().toUpperCase();
            if (!ticker) {
                return;
            }

            if (state.customTickers.has(ticker)) {
                showToast('warning', 'Ticker already added', `${ticker} is already in your cash-secured puts list.`);
                return;
            }

            addCustomTickerBtn.disabled = true;
            addCustomTickerBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Adding...';

            try {
                let otmPercent = 5;

                const otmPercentSelect = document.getElementById('otm-percent');
                if (otmPercentSelect) {
                    otmPercent = parseInt(otmPercentSelect.value, 10);
                }

                const expirationData = await fetchOptionExpirations(ticker);

                if (!expirationData || !expirationData.expirations || expirationData.expirations.length === 0) {
                    showToast('error', 'Data Error', `Could not find expiration dates for ${ticker}.`);
                    return;
                }

                state.customTickers.add(ticker);

                if (!state.tickersData[ticker]) {
                    state.tickersData[ticker] = {
                        data: {
                            data: {}
                        },
                        callOtmPercentage: parseInt(otmPercent, 10),
                        putOtmPercentage: parseInt(otmPercent, 10),
                        putQuantity: 1,
                        expirations: expirationData.expirations
                    };

                    state.tickersData[ticker].data.data[ticker] = {
                        stock_price: 0,
                        position: 0,
                        calls: [],
                        puts: []
                    };

                    try {
                        const stockPriceData = await fetchStockPrices([ticker]);
                        if (stockPriceData && stockPriceData.data && stockPriceData.data[ticker]) {
                            state.tickersData[ticker].data.data[ticker].stock_price = stockPriceData.data[ticker];
                        }
                    } catch (priceError) {
                        console.error('Error fetching stock price:', priceError);
                    }
                }

                localStorage.setItem('customTickers', JSON.stringify([...state.customTickers]));

                updateOptionsTable();

                addOptionsTableEventListeners();

                customTickerInput.value = '';

                showToast('success', 'Ticker Added', `${ticker} has been added. Select an expiration and click refresh to load options.`);
            } catch (error) {
                console.error('Error adding custom ticker:', error);
                showToast('error', 'Error', `Failed to add ${ticker}: ${error.message}`);
            } finally {
                addCustomTickerBtn.disabled = false;
                addCustomTickerBtn.innerHTML = '<i class="bi bi-plus-circle"></i> Add';
            }
        });

        customTickerInput.addEventListener('keypress', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                addCustomTickerBtn.click();
            }
        });

        state.customTickerListenersInitialized = true;
    }
}
