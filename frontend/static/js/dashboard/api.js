/**
 * API interaction module — barrel re-export.
 *
 * Contains shared helpers and re-exports domain-specific API
 * functions from sub-modules.  All existing importers continue
 * to work unchanged.
 */
import { showAlert } from '../utils/alerts.js';

// ── Shared helpers ──────────────────────────────────────────────

export async function readJsonSafely(response) {
    try { return await response.json(); }
    catch { return null; }
}

export function isOpenDUnavailable(payload) {
    return payload && ['opend_unavailable', 'opend_login_required', 'real_account_unavailable'].includes(payload.error_code);
}

export function setConnectionStatusFromPayload(payload) {
    if (!payload) return;
    const status = payload.opend_status || { status: payload.error_code || 'error', message: payload.error || 'Connection unavailable' };
    if (typeof window.updateOpenDStatusBanner === 'function') {
        window.updateOpenDStatusBanner(status);
        return;
    }
    window.appConnectionStatus = status;
    document.dispatchEvent(new CustomEvent('opend-status-changed', { detail: status }));
}

export function clearUnavailableStatus() {
    if (!window.appConnectionStatus || window.appConnectionStatus.status !== 'real_account_unavailable') return;
    if (typeof window.updateOpenDStatusBanner === 'function') {
        window.updateOpenDStatusBanner({ status: 'connected', message: 'OpenD is running and ready.' });
    }
}

export function isRealAccountUnavailableError(error) {
    const status = window.appConnectionStatus || null;
    if (status && status.status === 'real_account_unavailable') return true;
    const message = error?.message || '';
    return message.includes('requested REAL account') || message.includes('real_account_unavailable');
}

// ── Re-exports from sub-modules ─────────────────────────────────

export {
    fetchAccountData, fetchPositions, fetchWeeklyOptionIncome,
    fetchTickers, fetchRollPressure, fetchEarningsStatus,
    refreshAllEarnings, updateSingleEarnings
} from './api-portfolio.js';

export {
    fetchPendingOrders, saveOptionOrder, cancelOrder,
    checkOrderStatus, executeOrder, executeCloseOrder
} from './api-orders.js';

export {
    fetchOptionData, fetchStockPrices, fetchOptionExpirations,
    fetchTopRecommendations
} from './api-options.js';
