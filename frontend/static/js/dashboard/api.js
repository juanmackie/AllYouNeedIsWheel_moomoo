/**
 * API interaction module for dashboard
 * Handles all data fetching and API calls
 */
import { showAlert } from '../utils/alerts.js';


async function readJsonSafely(response) {
    try {
        return await response.json();
    } catch (error) {
        return null;
    }
}


function isOpenDUnavailable(payload) {
    return payload && ['opend_unavailable', 'opend_login_required', 'real_account_unavailable'].includes(payload.error_code);
}


function setConnectionStatusFromPayload(payload) {
    if (!payload) {
        return;
    }

    const status = payload.opend_status || {
        status: payload.error_code || 'error',
        message: payload.error || 'Connection unavailable'
    };

    if (typeof window.updateOpenDStatusBanner === 'function') {
        window.updateOpenDStatusBanner(status);
        return;
    }

    window.appConnectionStatus = status;
    document.dispatchEvent(new CustomEvent('opend-status-changed', { detail: status }));
}


function clearUnavailableStatus() {
    if (!window.appConnectionStatus || window.appConnectionStatus.status !== 'real_account_unavailable') {
        return;
    }

    if (typeof window.updateOpenDStatusBanner === 'function') {
        window.updateOpenDStatusBanner({
            status: 'connected',
            message: 'OpenD is running and ready.'
        });
    }
}


function isRealAccountUnavailableError(error) {
    const status = window.appConnectionStatus || null;
    if (status && status.status === 'real_account_unavailable') {
        return true;
    }

    const message = error?.message || '';
    return message.includes('requested REAL account') || message.includes('real_account_unavailable');
}

/**
 * Fetch account and portfolio data
 * @returns {Promise} Promise with account data
 */
async function fetchAccountData() {
    try {
        const response = await fetch('/api/portfolio');
        const payload = await readJsonSafely(response);
        if (!response.ok) {
            if (isOpenDUnavailable(payload)) {
                setConnectionStatusFromPayload(payload);
                return null;
            }
            throw new Error(payload?.error || `HTTP error ${response.status}`);
        }
        clearUnavailableStatus();
        return payload;
    } catch (error) {
        console.error('Error fetching account data:', error);
        if (!isRealAccountUnavailableError(error)) {
            showAlert(`Error fetching account data: ${error.message}`, 'danger');
        }
        return null;
    }
}

/**
 * Fetch positions data
 * @returns {Promise} Promise with positions data
 */
async function fetchPositions() {
    try {
        const response = await fetch('/api/portfolio/positions');
        const payload = await readJsonSafely(response);
        if (!response.ok) {
            if (isOpenDUnavailable(payload)) {
                setConnectionStatusFromPayload(payload);
                return null;
            }
            throw new Error(payload?.error || `HTTP error ${response.status}`);
        }
        clearUnavailableStatus();
        return payload;
    } catch (error) {
        console.error('Error fetching positions:', error);
        if (!isRealAccountUnavailableError(error)) {
            showAlert(`Error fetching positions: ${error.message}`, 'danger');
        }
        return null;
    }
}

/**
 * Fetch weekly option income data
 * @returns {Promise} Promise with weekly income data from short options expiring this coming Friday
 */
async function fetchWeeklyOptionIncome() {
    try {
        const response = await fetch('/api/portfolio/weekly-income');
        const payload = await readJsonSafely(response);
        if (!response.ok) {
            if (isOpenDUnavailable(payload)) {
                setConnectionStatusFromPayload(payload);
                return {
                    positions: [],
                    total_income: 0,
                    positions_count: 0,
                    error: payload?.error || 'OpenD unavailable'
                };
            }
            throw new Error(payload?.error || `HTTP error ${response.status}`);
        }
        clearUnavailableStatus();
        return payload;
    } catch (error) {
        console.error('Error fetching weekly option income:', error);
        if (!isRealAccountUnavailableError(error)) {
            showAlert(`Error fetching weekly income data: ${error.message}`, 'danger');
        }
        return {
            positions: [],
            total_income: 0,
            positions_count: 0,
            error: error.message
        };
    }
}

/**
 * Fetch option data for a ticker
 * @param {string} ticker - The stock symbol
 * @param {number} otmPercentage - The OTM percentage value (default: 10)
 * @param {string} optionType - The option type to filter by ('CALL' or 'PUT')
 * @param {string} expiration - The specific expiration date to filter by
 * @returns {Promise} Promise with option data
 */
async function fetchOptionData(ticker, otmPercentage = 10, optionType = null, expiration = null) {
    try {
        const timestamp = new Date().getTime();
        let url = `/api/options/otm?tickers=${encodeURIComponent(ticker)}&otm=${otmPercentage}&real_time=true&options_only=true&t=${timestamp}`;
        
        // Add option type to URL if provided
        if (optionType) {
            url += `&optionType=${optionType}`;
        }
        
        // Add expiration date to URL if provided
        if (expiration) {
            url += `&expiration=${encodeURIComponent(expiration)}`;
        }
        
        const response = await fetch(url, {
            headers: {
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0'
            }
        });
        
        if (!response.ok) {
            const payload = await readJsonSafely(response);
            if (isOpenDUnavailable(payload)) {
                setConnectionStatusFromPayload(payload);
                return {
                    status: 'error',
                    message: payload.error,
                    data: {
                        [ticker]: {
                            stock_price: 0,
                            position: 0,
                            calls: [],
                            puts: []
                        }
                    }
                };
            }
            throw new Error(payload?.error || `HTTP error ${response.status}`);
        }
        clearUnavailableStatus();
        
        // Get response as text first to fix any NaN values
        const responseText = await response.text();
        
        // Replace any NaN values with null (or 0) for proper JSON parsing
        let sanitizedResponse = responseText
            .replace(/:NaN/g, ':null')
            .replace(/=NaN/g, '=null')
            .replace(/: NaN/g, ': null');
            
        console.log(`Sanitized response for ${ticker} to fix NaN values`);
        
        // Parse the sanitized JSON
        try {
            return JSON.parse(sanitizedResponse);
        } catch (parseError) {
            console.error(`JSON parse error for ${ticker} even after sanitizing:`, parseError);
            console.error('Response text:', sanitizedResponse.substring(0, 200) + '...');
            throw parseError;
        }
    } catch (error) {
        console.error(`Error fetching options for ${ticker}:`, error);
        if (!isRealAccountUnavailableError(error)) {
            showAlert(`Error fetching options for ${ticker}: ${error.message}`, 'danger');
        }
        
        // Return a fallback empty structure to prevent further errors
        return {
            status: "error",
            message: error.message,
            data: {
                [ticker]: {
                    stock_price: 0,
                    position: 0,
                    calls: [],
                    puts: []
                }
            }
        };
    }
}

/**
 * Fetch all tickers for stock positions only
 * @returns {Promise} Promise with tickers data
 */
async function fetchTickers() {
    try {
        // Only fetch stock positions by using the type=STK filter
        const response = await fetch('/api/portfolio/positions?type=STK');
        const payload = await readJsonSafely(response);
        if (!response.ok) {
            if (isOpenDUnavailable(payload)) {
                setConnectionStatusFromPayload(payload);
                return { tickers: [] };
            }
            throw new Error(payload?.error || `HTTP error ${response.status}`);
        }
        clearUnavailableStatus();
        
        const positionsData = payload;
        
        // Extract unique ticker symbols from stock positions
        const tickers = positionsData.map(position => position.symbol);
        
        return { tickers: tickers };
    } catch (error) {
        console.error('Error fetching tickers:', error);
        if (!isRealAccountUnavailableError(error)) {
            showAlert(`Error fetching tickers: ${error.message}`, 'danger');
        }
        return { tickers: [] };
    }
}

/**
 * Fetch pending orders from the API
 * @param {boolean} executed - Whether to fetch executed orders (true) or pending orders (false)
 * @param {boolean} isRollover - Whether to fetch only rollover orders
 * @returns {Promise<Object>} Pending orders data
 */
async function fetchPendingOrders(executed = false, isRollover = false) {
    try {
        // Construct the URL with query parameters
        let url = `/api/options/pending-orders?executed=${executed}`;
        
        // Add isRollover parameter if specified
        if (isRollover) {
            url += `&isRollover=true`;
        }
        
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        console.log('Pending orders API response:', data);
        return data;
    } catch (error) {
        console.error('Error fetching pending orders:', error);
        return null;
    }
}

/**
 * Save an option order
 * @param {Object} orderData - The order data
 * @returns {Promise} Promise with the saved order
 */
async function saveOptionOrder(orderData) {
    try {
        const response = await fetch('/api/options/order', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(orderData)
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('Error saving order:', error);
        showAlert(`Error saving order: ${error.message}`, 'danger');
        throw error;
    }
}

/**
 * Cancel an order
 * @param {string} orderId - The order ID to cancel
 * @returns {Promise} Promise with the cancelled order
 */
async function cancelOrder(orderId) {
    try {
        // Use the new cancellation endpoint for active orders
        const response = await fetch(`/api/options/cancel/${orderId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.error || 'Failed to cancel order');
        }
        
        return await response.json();
    } catch (error) {
        console.error('Error cancelling order:', error);
        showAlert(`Error cancelling order: ${error.message}`, 'danger');
        throw error;
    }
}

/**
 * Check status of pending/processing orders with moomoo
 * @returns {Promise} Promise with updated orders
 */
async function checkOrderStatus() {
    try {
        const response = await fetch('/api/options/check-orders', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.error || 'Failed to check order status');
        }
        
        return await response.json();
    } catch (error) {
        console.error('Error checking order status:', error);
        // Don't show alert for this regular background operation
        throw error;
    }
}

/**
 * Execute an order
 * @param {number} orderId - The order ID to execute
 * @returns {Promise<Object>} Result object with success/error info
 */
async function executeOrder(orderId) {
    try {
        const response = await fetch(`/api/options/execute/${orderId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            }
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('Error executing order:', error);
        return { success: false, error: error.message };
    }
}

/**
 * Fetch stock prices for one or more tickers
 * @param {Array|string} tickers - Array of ticker symbols or comma-separated string
 * @returns {Promise} Promise with stock prices data
 */
async function fetchStockPrices(tickers) {
    try {
        // Format tickers parameter
        let tickersParam = '';
        if (Array.isArray(tickers)) {
            tickersParam = tickers.join(',');
        } else {
            tickersParam = tickers;
        }
        
        if (!tickersParam) {
            throw new Error('No tickers provided');
        }
        
        // Add timestamp to avoid caching
        const timestamp = new Date().getTime();
        const url = `/api/options/stock-price?tickers=${encodeURIComponent(tickersParam)}&t=${timestamp}`;
        
        const response = await fetch(url, {
            headers: {
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0'
            }
        });
        
        if (!response.ok) {
            const payload = await readJsonSafely(response);
            if (isOpenDUnavailable(payload)) {
                return {};
            }
            throw new Error(payload?.error || `HTTP error ${response.status}`);
        }
        
        const result = await response.json();
        
        if (result.status === 'success' && result.data) {
            return result.data;
        } else {
            throw new Error(result.error || 'Failed to fetch stock prices');
        }
    } catch (error) {
        console.error('Error fetching stock prices:', error);
        return {};
    }
}

/**
 * Fetch available option expiration dates for a ticker
 * @param {string} ticker - The ticker symbol
 * @param {string} optionType - Optional 'CALL' or 'PUT' to filter by preferred DTE ranges
 * @returns {Promise<Object>} - Promise resolving to an object with expiration dates
 */
async function fetchOptionExpirations(ticker, optionType = null) {
    try {
        let url = `/api/options/expirations?ticker=${encodeURIComponent(ticker)}`;
        if (optionType) {
            url += `&option_type=${encodeURIComponent(optionType)}`;
        }
        const response = await fetch(url);
        const payload = await readJsonSafely(response);
        
        if (!response.ok) {
            if (isOpenDUnavailable(payload)) {
                setConnectionStatusFromPayload(payload);
                return { expirations: [], error: payload.error };
            }
            throw new Error(payload?.error || 'Failed to fetch option expirations');
        }
        clearUnavailableStatus();
        
        return payload;
    } catch (error) {
        console.error('Error fetching option expirations:', error);
        throw error;
    }
}

/**
 * Fetch top N option recommendations across portfolio
 * @param {number} limit - Number of recommendations to fetch (default: 3)
 * @returns {Promise<Object>} Promise with top recommendations
 */
async function fetchTopRecommendations(limit = 3, manualRefresh = false) {
    try {
        // Build URL with optional manual refresh parameter
        let url = `/api/options/top-recommendations?limit=${limit}`;
        if (manualRefresh) {
            url += '&refresh=true';
        }
        
        // Fetch WITHOUT cache-busting headers (allow browser cache)
        const response = await fetch(url);
        
        if (!response.ok) {
            const payload = await readJsonSafely(response);
            if (isOpenDUnavailable(payload)) {
                setConnectionStatusFromPayload(payload);
                return { recommendations: [], count: 0, error: payload?.error || 'OpenD unavailable' };
            }
            throw new Error(payload?.error || `HTTP error ${response.status}`);
        }
        
        clearUnavailableStatus();
        const result = await response.json();
        
        // Extract cache headers for display
        const cacheStatus = response.headers.get('X-Cache-Status') || 'MISS';
        const cacheAge = parseInt(response.headers.get('X-Cache-Age') || '0', 10);
        
        return {
            ...result,
            _cacheInfo: {
                status: cacheStatus,
                age: cacheAge
            }
        };
    } catch (error) {
        console.error('Error fetching top recommendations:', error);
        if (!isRealAccountUnavailableError(error)) {
            showAlert(`Error fetching top recommendations: ${error.message}`, 'danger');
        }
        return { recommendations: [], count: 0, error: error.message };
    }
}

// Export all API functions
export {
    fetchAccountData,
    fetchPositions,
    fetchWeeklyOptionIncome,
    fetchOptionData,
    fetchTickers,
    fetchPendingOrders,
    saveOptionOrder,
    cancelOrder,
    executeOrder,
    checkOrderStatus,
    fetchStockPrices,
    fetchOptionExpirations,
    fetchTopRecommendations
};
