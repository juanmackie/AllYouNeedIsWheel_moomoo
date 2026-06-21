/**
 * Shared API helpers for dashboard modules.
 * Kept separate from the barrel module to avoid circular imports.
 */

export function withTimeout(promise, ms = 15000) {
    return Promise.race([
        promise,
        new Promise((_, reject) =>
            setTimeout(() => reject(new Error(`Request timed out (${ms}ms)`)), ms)
        ),
    ]);
}

export async function fetchWithTimeout(url, options = {}, ms = 15000) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), ms);

    try {
        const response = await fetch(url, { ...options, signal: controller.signal });
        return response;
    } catch (error) {
        if (error?.name === 'AbortError') {
            throw new Error(`Request timed out (${ms}ms)`);
        }
        throw error;
    } finally {
        clearTimeout(timeoutId);
    }
}

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
