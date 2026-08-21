/**
 * API interaction module — barrel re-export.
 *
 * Contains shared helpers and re-exports domain-specific API
 * functions from sub-modules.  All existing importers continue
 * to work unchanged.
 */

export {
    withTimeout,
    fetchWithTimeout,
    readJsonSafely,
    isOpenDUnavailable,
    setConnectionStatusFromPayload,
    clearUnavailableStatus,
    isRealAccountUnavailableError,
} from './api-core.js';

// ── Re-exports from sub-modules ─────────────────────────────────

export {
    fetchAccountData, fetchPositions, fetchWeeklyOptionIncome,
    fetchTickers, fetchRollPressure, fetchEarningsStatus,
    refreshAllEarnings, updateSingleEarnings
} from './api-portfolio.js';

export {
    fetchOptionData, fetchStockPrices, fetchOptionExpirations
} from './api-options.js';
