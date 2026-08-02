const state = {
    tickersData: {},
    portfolioSummary: null,
    eventListenersInitialized: false,
    containerEventListenersInitialized: false,
    customTickers: new Set(),
    watchlistTickers: new Set(),
    portfolioTickers: [],
    customTickerListenersInitialized: false,
    screeningConfig: null,
};

function getUnavailableTickerMessage() {
    const status = window.appConnectionStatus || null;
    if (status && status.status === 'real_account_unavailable') {
        return 'No portfolio tickers available because OpenD is not exposing your REAL trading account yet.';
    }
    return 'No stock positions available. Please add stock positions first.';
}

function getSelectedExpirationPreference(ticker, optionType) {
    return state.tickersData[ticker]?.selectedExpirations?.[optionType] || null;
}

function setSelectedExpirationPreference(ticker, optionType, expiration) {
    if (!state.tickersData[ticker]) {
        state.tickersData[ticker] = {};
    }
    if (!state.tickersData[ticker].selectedExpirations) {
        state.tickersData[ticker].selectedExpirations = {};
    }
    if (expiration) {
        state.tickersData[ticker].selectedExpirations[optionType] = expiration;
    } else {
        delete state.tickersData[ticker].selectedExpirations[optionType];
    }
}

function formatExpirationLabel(expiration) {
    if (!expiration || expiration.length !== 8) {
        return expiration || '-';
    }
    return `${expiration.slice(0, 4)}-${expiration.slice(4, 6)}-${expiration.slice(6, 8)}`;
}

function getRenderExpirationValue(ticker, optionType, fallbackValue = '') {
    return getSelectedExpirationPreference(ticker, optionType) || fallbackValue;
}

function getOtmBounds(optionType) {
    if (optionType === 'PUT') {
        return {
            min: state.screeningConfig?.cspMinOtmPct ?? 5,
            max: state.screeningConfig?.cspMaxOtmPct ?? 15,
            defaultValue: state.screeningConfig?.cspDefaultOtmPct ?? 10,
        };
    }

    return {
        min: 1,
        max: 50,
        defaultValue: state.screeningConfig?.callDefaultOtmPct ?? 10,
    };
}

function normalizeOtmValue(optionType, value) {
    const bounds = getOtmBounds(optionType);
    const numericValue = parseInt(value, 10);
    if (Number.isNaN(numericValue)) {
        return bounds.defaultValue;
    }
    return Math.max(bounds.min, Math.min(bounds.max, numericValue));
}

function getDefaultOtm(optionType) {
    return getOtmBounds(optionType).defaultValue;
}

function ensureTickerDataState(ticker, defaults = {}) {
    if (!state.tickersData[ticker]) {
        state.tickersData[ticker] = {};
    }

    const tickerState = state.tickersData[ticker];
    tickerState.data = tickerState.data || { data: {} };
    tickerState.data.data = tickerState.data.data || {};
    tickerState.data.data[ticker] = tickerState.data.data[ticker] || {};

    const optionData = tickerState.data.data[ticker];
    if (typeof optionData.stock_price === 'undefined') optionData.stock_price = 0;
    if (typeof optionData.position === 'undefined') optionData.position = 0;
    optionData.calls = optionData.calls || [];
    optionData.puts = optionData.puts || [];

    if (typeof tickerState.callOtmPercentage === 'undefined') {
        tickerState.callOtmPercentage = normalizeOtmValue('CALL', defaults.callOtmPercentage ?? getDefaultOtm('CALL'));
    }
    if (typeof tickerState.putOtmPercentage === 'undefined') {
        tickerState.putOtmPercentage = normalizeOtmValue('PUT', defaults.putOtmPercentage ?? getDefaultOtm('PUT'));
    }
    if (typeof tickerState.putQuantity === 'undefined') {
        tickerState.putQuantity = defaults.putQuantity || 1;
    }
    tickerState.errors = tickerState.errors || {};

    return tickerState;
}

function saveOtmSettings() {
    try {
        const otmSettings = {};
        Object.keys(state.tickersData).forEach(ticker => {
            otmSettings[ticker] = {
                callOtmPercentage: normalizeOtmValue('CALL', state.tickersData[ticker].callOtmPercentage ?? getDefaultOtm('CALL')),
                putOtmPercentage: normalizeOtmValue('PUT', state.tickersData[ticker].putOtmPercentage ?? getDefaultOtm('PUT')),
                putQuantity: state.tickersData[ticker].putQuantity || 1
            };
        });
        localStorage.setItem('otmSettings', JSON.stringify(otmSettings));
    } catch (error) {
        console.error('Error saving OTM settings:', error);
    }
}

function loadOtmSettings() {
    try {
        const savedSettings = localStorage.getItem('otmSettings');
        if (savedSettings) {
            const settings = JSON.parse(savedSettings);
            Object.keys(settings).forEach(ticker => {
                if (!state.tickersData[ticker]) {
                    state.tickersData[ticker] = {};
                }
                const putOtm = settings[ticker].putOtmPercentage;
                state.tickersData[ticker].callOtmPercentage = normalizeOtmValue('CALL', settings[ticker].callOtmPercentage ?? getDefaultOtm('CALL'));
                state.tickersData[ticker].putOtmPercentage = normalizeOtmValue('PUT', putOtm ?? getDefaultOtm('PUT'));
                state.tickersData[ticker].putQuantity = settings[ticker].putQuantity || 1;
            });

            if (needsMigration) {
                localStorage.setItem('_otmMigratedToGrowth', 'true');
                localStorage.setItem('otmSettings', JSON.stringify(settings));
            }
        }
    } catch (error) {
        console.error('Error loading OTM settings:', error);
    }
}

function getSavedTabPreference() {
    try {
        return localStorage.getItem('optionsTableTab');
    } catch (e) {
        return null;
    }
}

function setSavedTabPreference(tab) {
    try {
        localStorage.setItem('optionsTableTab', tab);
    } catch (e) {}
}

function loadCustomTickers() {
    try {
        const savedTickers = localStorage.getItem('customTickers');
        if (savedTickers) {
            const tickersArray = JSON.parse(savedTickers);
            state.customTickers = new Set(tickersArray);
        }
    } catch (error) {
        console.error('Error loading custom tickers:', error);
    }
}

function loadExcludedTickers() {
    try {
        const savedExcluded = localStorage.getItem('excludedPositionTickers');
        if (savedExcluded) {
            return JSON.parse(savedExcluded);
        }
    } catch (error) {
        console.error('Error loading excluded tickers:', error);
    }
    return [];
}

function removeCustomTicker(ticker) {
    if (state.customTickers.has(ticker)) {
        state.customTickers.delete(ticker);
        localStorage.setItem('customTickers', JSON.stringify([...state.customTickers]));
        if (state.tickersData[ticker]) {
            delete state.tickersData[ticker];
        }
        saveOtmSettings();
    }
}

function excludePositionTicker(ticker) {
    let excludedTickers = loadExcludedTickers();
    if (!excludedTickers.includes(ticker)) {
        excludedTickers.push(ticker);
        localStorage.setItem('excludedPositionTickers', JSON.stringify(excludedTickers));
    }
    if (state.tickersData[ticker]) {
        delete state.tickersData[ticker];
    }
    saveOtmSettings();
}

function removeTicker(ticker) {
    if (state.customTickers.has(ticker)) {
        removeCustomTicker(ticker);
    } else {
        excludePositionTicker(ticker);
    }
}

function initialize() {
    loadCustomTickers();
    loadOtmSettings();
}

export {
    state,
    getUnavailableTickerMessage,
    getSelectedExpirationPreference,
    setSelectedExpirationPreference,
    formatExpirationLabel,
    getRenderExpirationValue,
    ensureTickerDataState,
    saveOtmSettings,
    loadOtmSettings,
    loadCustomTickers,
    loadExcludedTickers,
    removeCustomTicker,
    excludePositionTicker,
    removeTicker,
    initialize,
    getSavedTabPreference,
    setSavedTabPreference,
    getDefaultOtm,
    getOtmBounds,
    normalizeOtmValue,
    getDefaultOtm as getDefaultOtmValue,
};
