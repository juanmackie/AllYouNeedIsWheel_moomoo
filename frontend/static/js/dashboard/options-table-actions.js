import { state, getSelectedExpirationPreference, getRenderExpirationValue, loadExcludedTickers, ensureTickerDataState } from './options-table-state.js';
import { calculatePremium, calculateEarningsSummary, updateEarningsSummary } from './options-table-calc.js';
import { showAlert } from '../utils/alerts.js';
import { fetchOptionData, fetchTickers, fetchAccountData, fetchOptionExpirations } from './api.js';
import { updateOptionsTable, addTickerRowToTable, displayPremiumSummary, showToast, addPutQtyInputEventListeners } from './options-table-rendering.js';

const expirationPrefetches = new Map();

async function prefetchExpirations(ticker, optionType = null) {
    const key = `${ticker}:${optionType || 'ALL'}`;
    if (expirationPrefetches.has(key)) {
        return expirationPrefetches.get(key);
    }

    const promise = fetchOptionExpirations(ticker, optionType, { quietTimeout: true })
        .then(expirationData => {
            if (expirationData && expirationData.expirations && expirationData.expirations.length > 0 && state.tickersData[ticker]) {
                state.tickersData[ticker].expirations = expirationData.expirations;
            }
            return expirationData;
        })
        .catch(() => null)
        .finally(() => {
            expirationPrefetches.delete(key);
        });

    expirationPrefetches.set(key, promise);
    return promise;
}

async function rebindOptionsTableEventListeners() {
    const { addOptionsTableEventListeners } = await import('./options-table-events.js');
    addOptionsTableEventListeners();
}



export async function refreshOptionsForTicker(ticker, updateUI = false, onProgress = null) {
    try {
        const putTabWasActive = document.querySelector('#put-options-tab.active') !== null ||
                               document.querySelector('#put-options-section.active') !== null;

        ensureTickerDataState(ticker);

        const callOtmPercentage = state.tickersData[ticker]?.callOtmPercentage || getDefaultOtm('CALL');
        const putOtmPercentage = state.tickersData[ticker]?.putOtmPercentage || getDefaultOtm('PUT');

        if (onProgress) onProgress('loading expirations');

        let allExpirations = [];
        const cachedExpirations = state.tickersData[ticker]?.expirations || [];
        if (cachedExpirations.length > 0) {
            allExpirations = cachedExpirations;
        } else if (updateUI) {
            try {
                const expirationData = await fetchOptionExpirations(ticker);

                if (expirationData && expirationData.expirations && expirationData.expirations.length > 0) {
                    allExpirations = expirationData.expirations;
                    state.tickersData[ticker].expirations = allExpirations;
                }
            } catch (error) {
                const isTimeout = error?.message?.includes('Request timed out');
                (isTimeout ? console.warn : console.error)(`Error fetching expiration dates for ${ticker}:`, error);
            }
        } else {
            prefetchExpirations(ticker).catch(() => null);
        }

        const selectedCallExpiration = getSelectedExpirationPreference(ticker, 'CALL');
        const selectedPutExpiration = getSelectedExpirationPreference(ticker, 'PUT');

        if (onProgress) onProgress('loading calls');
        const callOptionData = await fetchOptionData(ticker, callOtmPercentage, 'CALL', selectedCallExpiration);
        if (onProgress) onProgress('loading puts');
        const putOptionData = await fetchOptionData(ticker, putOtmPercentage, 'PUT', selectedPutExpiration);

        ensureTickerDataState(ticker, {
            callOtmPercentage,
            putOtmPercentage,
            putQuantity: 1
        });

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
            await rebindOptionsTableEventListeners();
        }
    } catch (error) {
        const isTimeout = error?.message?.includes('Request timed out');
        (isTimeout ? console.warn : console.error)(`Error refreshing options for ${ticker}:`, error);
        if (!isTimeout) showAlert(`Error refreshing options for ${ticker}: ${error.message}`, 'danger');
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
                const isCustom = state.customTickers.has(ticker);
                const isWatchlist = state.watchlistTickers?.has(ticker);

                if (isCustom || isWatchlist) return true;

                // Portfolio ticker — include unless explicitly excluded
                return !excludedTickers.includes(ticker);
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
        await rebindOptionsTableEventListeners();
    } catch (error) {
        console.error(`Error refreshing ${optionType || 'all'} options:`, error);
        showAlert(`Error refreshing options: ${error.message}`, 'danger');

        updateOptionsTable();
    }
}

export async function refreshOptionsForTickerByType(ticker, optionType, updateUI = false, onProgress = null) {
    try {
        let otmPercentage;
        if (optionType === 'CALL') {
            otmPercentage = state.tickersData[ticker]?.callOtmPercentage || getDefaultOtm('CALL');
        } else {
            otmPercentage = state.tickersData[ticker]?.putOtmPercentage || getDefaultOtm('PUT');
        }

        ensureTickerDataState(ticker, {
            callOtmPercentage: optionType === 'CALL' ? otmPercentage : 10,
            putOtmPercentage: optionType === 'PUT' ? otmPercentage : 10,
            putQuantity: 1
        });

        if (onProgress) onProgress('loading expirations');

        let allExpirations = [];
        const cachedExpirations = state.tickersData[ticker]?.expirations || [];
        if (cachedExpirations.length > 0) {
            allExpirations = cachedExpirations;
        } else if (updateUI) {
            try {
                const expirationData = await fetchOptionExpirations(ticker, optionType);

                if (expirationData && expirationData.expirations && expirationData.expirations.length > 0) {
                    allExpirations = expirationData.expirations;
                    state.tickersData[ticker].expirations = allExpirations;
                }
            } catch (error) {
                console.error(`Error fetching expiration dates for ${ticker}:`, error);
            }
        } else {
            prefetchExpirations(ticker, optionType).catch(() => null);
        }

        const selectedExpiration = getSelectedExpirationPreference(ticker, optionType);

        const stepText = optionType === 'CALL' ? 'loading calls' : 'loading puts';
        if (onProgress) onProgress(stepText);
        const optionData = await fetchOptionData(ticker, otmPercentage, optionType, selectedExpiration);

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
            await rebindOptionsTableEventListeners();
        }
    } catch (error) {
        const isTimeout = error?.message?.includes('Request timed out');
        (isTimeout ? console.warn : console.error)(`Error refreshing ${optionType} options for ${ticker}:`, error);
        if (!isTimeout) showAlert(`Error refreshing ${optionType} options for ${ticker}: ${error.message}`, 'danger');
    }
}

export async function sellAllOptions(optionType) {
    showAlert('Signal only mode — review trades in your broker app', 'info');
}
