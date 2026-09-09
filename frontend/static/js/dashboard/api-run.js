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
