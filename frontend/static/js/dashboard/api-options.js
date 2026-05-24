/**
 * Options data API module
 * Split from api.js (F041)
 */
import { showAlert } from '../utils/alerts.js';
import { readJsonSafely, isOpenDUnavailable, setConnectionStatusFromPayload, clearUnavailableStatus, isRealAccountUnavailableError, fetchWithTimeout } from './api.js';

export async function fetchOptionData(ticker, otmPercentage = 10, optionType = null, expiration = null) {
    try {
        const timestamp = new Date().getTime();
        let url = `/api/options/otm?tickers=${encodeURIComponent(ticker)}&otm=${otmPercentage}&real_time=true&options_only=true&t=${timestamp}`;
        if (optionType) url += `&optionType=${optionType}`;
        if (expiration) url += `&expiration=${encodeURIComponent(expiration)}`;
        const response = await fetchWithTimeout(url, { headers: { 'Cache-Control': 'no-cache, no-store, must-revalidate', 'Pragma': 'no-cache', 'Expires': '0' } }, 20000);
        if (!response.ok) {
            const payload = await readJsonSafely(response);
            if (isOpenDUnavailable(payload)) {
                setConnectionStatusFromPayload(payload);
                return { status: 'error', message: payload.error, data: { [ticker]: { stock_price: 0, position: 0, calls: [], puts: [] } } };
            }
            throw new Error(payload?.error || `HTTP error ${response.status}`);
        }
        clearUnavailableStatus();
        const responseText = await response.text();
        try { return JSON.parse(responseText); }
        catch (parseError) {
            console.error(`JSON parse error for ${ticker}:`, parseError);
            throw parseError;
        }
    } catch (error) {
        const isTimeout = error?.message?.includes('Request timed out');
        (isTimeout ? console.warn : console.error)(`Error fetching options for ${ticker}:`, error);
        if (!isTimeout && !isRealAccountUnavailableError(error)) showAlert(`Error fetching options for ${ticker}: ${error.message}`, 'danger');
        return { status: "error", message: error.message, data: { [ticker]: { stock_price: 0, position: 0, calls: [], puts: [] } } };
    }
}

export async function fetchStockPrices(tickers) {
    try {
        let tickersParam = '';
        if (Array.isArray(tickers)) tickersParam = tickers.join(',');
        else tickersParam = tickers;
        if (!tickersParam) throw new Error('No tickers provided');
        const timestamp = new Date().getTime();
        const url = `/api/options/stock-price?tickers=${encodeURIComponent(tickersParam)}&t=${timestamp}`;
        const response = await fetchWithTimeout(url, { headers: { 'Cache-Control': 'no-cache, no-store, must-revalidate', 'Pragma': 'no-cache', 'Expires': '0' } }, 15000);
        if (!response.ok) {
            const payload = await readJsonSafely(response);
            if (isOpenDUnavailable(payload)) return {};
            throw new Error(payload?.error || `HTTP error ${response.status}`);
        }
        const result = await response.json();
        if (result.status === 'success' && result.data) return result.data;
        else throw new Error(result.error || 'Failed to fetch stock prices');
    } catch (error) {
        const isTimeout = error?.message?.includes('Request timed out');
        (isTimeout ? console.warn : console.error)('Error fetching stock prices:', error);
        return {};
    }
}

export async function fetchOptionExpirations(ticker, optionType = null) {
    try {
        let url = `/api/options/expirations?ticker=${encodeURIComponent(ticker)}`;
        if (optionType) url += `&option_type=${encodeURIComponent(optionType)}`;
        const response = await fetchWithTimeout(url, {}, 15000);
        const payload = await readJsonSafely(response);
        if (!response.ok) {
            if (isOpenDUnavailable(payload)) { setConnectionStatusFromPayload(payload); return { expirations: [], error: payload.error }; }
            throw new Error(payload?.error || 'Failed to fetch option expirations');
        }
        clearUnavailableStatus();
        return payload;
    } catch (error) {
        const isTimeout = error?.message?.includes('Request timed out');
        (isTimeout ? console.warn : console.error)('Error fetching option expirations:', error);
        throw error;
    }
}

export async function fetchTopRecommendations(limit = 3, manualRefresh = false) {
    try {
        let url = `/api/options/top-recommendations?limit=${limit}`;
        if (manualRefresh) url += '&refresh=true';
        const response = await fetchWithTimeout(url, {}, 30000);
        if (!response.ok) {
            const payload = await readJsonSafely(response);
            if (isOpenDUnavailable(payload)) { setConnectionStatusFromPayload(payload); return { signals: [], count: 0, error: payload?.error || 'OpenD unavailable' }; }
            throw new Error(payload?.error || `HTTP error ${response.status}`);
        }
        clearUnavailableStatus();
        const result = await response.json();
        const cacheStatus = response.headers.get('X-Cache-Status') || 'MISS';
        const cacheAge = parseInt(response.headers.get('X-Cache-Age') || '0', 10);
        return { ...result, _cacheInfo: { status: cacheStatus, age: cacheAge } };
    } catch (error) {
        const isTimeout = error?.message?.includes('Request timed out');
        (isTimeout ? console.warn : console.error)('Error fetching top recommendations:', error);
        if (!isRealAccountUnavailableError(error) && !isTimeout) showAlert(`Error fetching top recommendations: ${error.message}`, 'danger');
        return { signals: [], count: 0, error: error.message, timedOut: isTimeout };
    }
}
