/**
 * Portfolio and earnings API module
 * Split from api.js (F041)
 */
import { showAlert } from '../utils/alerts.js';
import { readJsonSafely, isOpenDUnavailable, setConnectionStatusFromPayload, clearUnavailableStatus, isRealAccountUnavailableError } from './api.js';

export async function fetchAccountData() {
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

export async function fetchPositions() {
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

export async function fetchWeeklyOptionIncome() {
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
                    open_short_positions_count: 0,
                    open_short_contracts_count: 0,
                    open_short_total_income: 0,
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
            open_short_positions_count: 0,
            open_short_contracts_count: 0,
            open_short_total_income: 0,
            error: error.message
        };
    }
}

export async function fetchTickers() {
    try {
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
        const tickers = positionsData.map(position => position.symbol);
        return { tickers };
    } catch (error) {
        console.error('Error fetching tickers:', error);
        if (!isRealAccountUnavailableError(error)) {
            showAlert(`Error fetching tickers: ${error.message}`, 'danger');
        }
        return { tickers: [] };
    }
}

export async function fetchWatchlistTickers() {
    try {
        const response = await fetch('/api/options/watchlist-tickers');
        const payload = await readJsonSafely(response);
        if (!response.ok) {
            if (isOpenDUnavailable(payload)) {
                setConnectionStatusFromPayload(payload);
                return { tickers: [], mode: 'static_fallback' };
            }
            throw new Error(payload?.error || `HTTP error ${response.status}`);
        }
        clearUnavailableStatus();
        return payload;
    } catch (error) {
        console.error('Error fetching watchlist tickers:', error);
        return { tickers: [], mode: 'static_fallback' };
    }
}

export async function fetchRollPressure() {
    try {
        const timestamp = new Date().getTime();
        const response = await fetch(`/api/portfolio/roll-pressure?t=${timestamp}`, {
            headers: { 'Cache-Control': 'no-cache, no-store, must-revalidate', 'Pragma': 'no-cache', 'Expires': '0' }
        });
        if (!response.ok) {
            const payload = await readJsonSafely(response);
            if (isOpenDUnavailable(payload)) {
                return { positions: [], count: 0, error: 'OpenD unavailable' };
            }
            throw new Error(payload?.error || `HTTP ${response.status}`);
        }
        const data = await response.json();
        return { positions: data.positions || [], count: data.count || 0, generated_at: data.generated_at };
    } catch (error) {
        console.error('Error fetching roll pressure:', error);
        if (!isRealAccountUnavailableError(error)) {
            showAlert(`Error fetching roll pressure: ${error.message}`, 'danger');
        }
        return { positions: [], count: 0, error: error.message };
    }
}

export async function fetchEarningsStatus() {
    try {
        const response = await fetch('/api/earnings/status');
        return await readJsonSafely(response);
    } catch (error) {
        console.error('Error fetching earnings status:', error);
        return null;
    }
}

export async function refreshAllEarnings() {
    try {
        const response = await fetch('/api/earnings/refresh', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        return await readJsonSafely(response);
    } catch (error) {
        console.error('Error refreshing all earnings:', error);
        throw error;
    }
}

export async function updateSingleEarnings(ticker) {
    try {
        const response = await fetch(`/api/earnings/update/${ticker}`);
        return await readJsonSafely(response);
    } catch (error) {
        console.error(`Error updating earnings for ${ticker}:`, error);
        throw error;
    }
}
