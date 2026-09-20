/**
 * Immutable wheel-run API client.
 * The dashboard shortlist is sourced only from /api/run.
 */
import { readJsonSafely, fetchWithTimeout } from './api-core.js';

export async function fetchRunState() {
    const response = await fetchWithTimeout('/api/run', { headers: { 'Cache-Control': 'no-cache' } }, 20000);
    const payload = await readJsonSafely(response);
    if (!response.ok) throw new Error(payload?.error || `HTTP error ${response.status}`);
    return payload || {};
}

export async function refreshRun() {
    const response = await fetchWithTimeout('/api/run/refresh', { method: 'POST' }, 15000);
    const payload = await readJsonSafely(response);
    if (!response.ok && response.status !== 409) {
        throw new Error(payload?.error || `HTTP error ${response.status}`);
    }
    return payload || {};
}

/**
 * Record that the owner acted on a recommendation (signals-only journal write).
 * Explicit owner evidence for outcome attribution — makes a manual trade
 * attributable to the recommendation it came from, including when the traded
 * strike differs. Places no order, copies nothing, and never touches the broker.
 */
export async function markRecommendationTaken({ run_id, ticker, option_type, expiration, strike, traded }) {
    const body = { run_id, ticker, option_type, expiration, strike };
    if (traded) body.traded = traded;
    const response = await fetchWithTimeout('/api/run/taken', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }, 15000);
    const payload = await readJsonSafely(response);
    if (!response.ok) {
        // Surface the backend's reason (missing run, contract not in the
        // shortlist, invalid input) instead of a bare HTTP code.
        throw new Error(payload?.error || `HTTP error ${response.status}`);
    }
    return payload || {};
}

/**
 * Read-only copy revalidation, called immediately before a clipboard write.
 * The backend re-checks the CURRENT snapshot (run id + contract) and, for
 * staged tickets, re-fetches last-session evidence directly from OpenD. Never
 * writes/closes anything.
 */
export async function revalidateCopy({ run_id, ticker, option_type, expiration, strike }) {
    const params = new URLSearchParams();
    if (run_id) params.set('run_id', run_id);
    if (ticker) params.set('ticker', ticker);
    if (option_type) params.set('option_type', option_type);
    if (expiration) params.set('expiration', expiration);
    if (strike != null) params.set('strike', String(strike));
    const response = await fetchWithTimeout('/api/run/copy-check?' + params.toString(), {
        headers: { 'Cache-Control': 'no-cache' },
    }, 25000);
    const payload = await readJsonSafely(response);
    if (!response.ok) {
        throw new Error(payload?.error || `HTTP error ${response.status}`);
    }
    return payload || {};
}
