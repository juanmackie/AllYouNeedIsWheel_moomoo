import { state, setSelectedExpirationPreference, getSelectedExpirationPreference, ensureTickerDataState, saveOtmSettings, removeTicker, setSavedTabPreference, getDefaultOtm, getOtmBounds, normalizeOtmValue } from './options-table-state.js';
import { calculatePremium } from './options-table-calc.js';
import { showAlert } from '../utils/alerts.js';
import { formatCurrency } from '../utils/formatters.js';
import { fetchOptionData, fetchOptionExpirations, fetchStockPrices } from './api.js';
import { updateOptionsTable, showToast, addOtmInputEventListeners } from './options-table-rendering.js';

async function getOptionsTableActions() {
    return import('./options-table-actions.js');
}

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
            ? state.tickersData[ticker]?.callOtmPercentage || getDefaultOtm('CALL')
            : state.tickersData[ticker]?.putOtmPercentage || getDefaultOtm('PUT');

        const optionData = await fetchOptionData(ticker, otmPercentage, optionType, selectedExpiration);

        if (optionData && optionData.data && optionData.data[ticker]) {
            ensureTickerDataState(ticker);

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
        const isTimeout = error?.message?.includes('Request timed out');
        (isTimeout ? console.warn : console.error)(`Error updating options for new expiration: ${error.message}`);
        if (!isTimeout) showAlert(`Error updating options: ${error.message}`, 'danger');

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

            tabEl.addEventListener('shown.bs.tab', () => {
                const tabId = tabEl.getAttribute('id');
                setSavedTabPreference(tabId === 'put-options-tab' ? 'PUT' : 'CALL');
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
                setSavedTabPreference('CALL');
            });
        
            putTab.addEventListener('click', (e) => {
                e.preventDefault();
                callTab.classList.remove('active');
                putTab.classList.add('active');
                callSection.classList.remove('show', 'active');
                putSection.classList.add('show', 'active');
                setSavedTabPreference('PUT');
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
                            const { refreshOptionsForTickerByType } = await getOptionsTableActions();
                            await refreshOptionsForTickerByType(ticker, optionType, true);
                        } else {
                            const { refreshOptionsForTicker } = await getOptionsTableActions();
                            await refreshOptionsForTicker(ticker, true);
                        }
                        button.innerHTML = '<i class="bi bi-arrow-repeat"></i>';
                    } catch (error) {
                        const isTimeout = error?.message?.includes('Request timed out');
                        (isTimeout ? console.warn : console.error)('Error refreshing ticker:', error);
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
                        const bounds = getOtmBounds(optionType);
                        const normalizedOtm = normalizeOtmValue(optionType, otmPercentage);

                        if (isNaN(otmPercentage) || otmPercentage < bounds.min || otmPercentage > bounds.max) {
                            otmInput.value = normalizedOtm;
                            showToast('warning', 'Invalid Value', `${optionType === 'PUT' ? 'CSP' : 'Call'} OTM% must be between ${bounds.min} and ${bounds.max}`);
                        }

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
                                state.tickersData[ticker].callOtmPercentage = normalizedOtm;
                            } else {
                                state.tickersData[ticker].putOtmPercentage = normalizedOtm;
                            }

                            saveOtmSettings();

                            setSelectedExpirationPreference(ticker, optionType, selectedExpiration);
                        }

                        if (selectedExpiration) {
                            const optionData = await fetchOptionData(ticker, normalizedOtm, optionType, selectedExpiration);

                            if (optionData && optionData.data && optionData.data[ticker]) {
                                ensureTickerDataState(ticker);

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

                                showToast('success', 'OTM Applied', `${ticker} ${optionType} options refreshed with ${normalizedOtm}% OTM`);
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

            if (event.target.id === 'sell-all-calls' ||
                event.target.closest('#sell-all-calls')) {

                const button = event.target.id === 'sell-all-calls' ?
                               event.target :
                               event.target.closest('#sell-all-calls');

                if (button.disabled) {
                    return;
                }

                try {
                    const { sellAllOptions } = await getOptionsTableActions();
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
                    const { sellAllOptions } = await getOptionsTableActions();
                    await sellAllOptions('PUT');
                } catch (error) {
                    console.error('Error in sell all puts handler:', error);
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
                    const { refreshAllOptions } = await getOptionsTableActions();
                    await refreshAllOptions('CALL');
                    } catch (error) {
                        const isTimeout = error?.message?.includes('Request timed out');
                        (isTimeout ? console.warn : console.error)('Error refreshing all call options:', error);
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
                    const { refreshAllOptions } = await getOptionsTableActions();
                    await refreshAllOptions('PUT');
                    } catch (error) {
                        const isTimeout = error?.message?.includes('Request timed out');
                        (isTimeout ? console.warn : console.error)('Error refreshing all put options:', error);
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

    container.addEventListener('change', async (event) => {
        const expirationSelect = event.target.closest('.expiration-select');
        if (expirationSelect && container.contains(expirationSelect)) {
            await handleExpirationChange(expirationSelect);
        }
    });

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
                const otmPercent = getDefaultOtm('CALL');

                const expirationData = await fetchOptionExpirations(ticker);

                if (!expirationData || !expirationData.expirations || expirationData.expirations.length === 0) {
                    showToast('error', 'Data Error', `Could not find expiration dates for ${ticker}.`);
                    return;
                }

                state.customTickers.add(ticker);

                const tickerState = ensureTickerDataState(ticker, {
                    callOtmPercentage: otmPercent,
                    putOtmPercentage: getDefaultOtm('PUT'),
                    putQuantity: 1
                });
                tickerState.expirations = expirationData.expirations;

                try {
                    const stockPriceData = await fetchStockPrices([ticker]);
                    if (stockPriceData && stockPriceData.data && stockPriceData.data[ticker]) {
                        state.tickersData[ticker].data.data[ticker].stock_price = stockPriceData.data[ticker];
                    }
                } catch (priceError) {
                    console.error('Error fetching stock price:', priceError);
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
