const state = {
    tickersData: {},
    portfolioSummary: null,
    eventListenersInitialized: false,
    containerEventListenersInitialized: false,
    customTickers: new Set(),
    customTickerListenersInitialized: false,
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

function saveOtmSettings() {
    try {
        const otmSettings = {};
        Object.keys(state.tickersData).forEach(ticker => {
            otmSettings[ticker] = {
                callOtmPercentage: state.tickersData[ticker].callOtmPercentage || 10,
                putOtmPercentage: state.tickersData[ticker].putOtmPercentage || 10,
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
                state.tickersData[ticker].callOtmPercentage = settings[ticker].callOtmPercentage || 10;
                state.tickersData[ticker].putOtmPercentage = settings[ticker].putOtmPercentage || 10;
                state.tickersData[ticker].putQuantity = settings[ticker].putQuantity || 1;
            });
        }
    } catch (error) {
        console.error('Error loading OTM settings:', error);
    }
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
    saveOtmSettings,
    loadOtmSettings,
    loadCustomTickers,
    loadExcludedTickers,
    removeCustomTicker,
    excludePositionTicker,
    removeTicker,
    initialize,
};
