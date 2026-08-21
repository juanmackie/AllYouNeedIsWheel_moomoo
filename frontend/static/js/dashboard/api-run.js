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
