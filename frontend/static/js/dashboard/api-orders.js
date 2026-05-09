/**
 * Orders API module
 * Split from api.js (F041)
 */
import { showAlert } from '../utils/alerts.js';
import { readJsonSafely } from './api.js';

export async function fetchPendingOrders(executed = false, isRollover = false) {
    try {
        let url = `/api/options/pending-orders?executed=${executed}`;
        if (isRollover) url += `&isRollover=true`;
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP error ${response.status}: ${response.statusText}`);
        return await response.json();
    } catch (error) {
        console.error('Error fetching pending orders:', error);
        return null;
    }
}

export async function saveOptionOrder(orderData) {
    try {
        const response = await fetch('/api/options/order', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(orderData) });
        if (!response.ok) throw new Error(`HTTP error ${response.status}`);
        return await response.json();
    } catch (error) {
        console.error('Error saving order:', error);
        showAlert(`Error saving order: ${error.message}`, 'danger');
        throw error;
    }
}

export async function cancelOrder(orderId) {
    try {
        const response = await fetch(`/api/options/cancel/${orderId}`, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        if (!response.ok) { const data = await response.json(); throw new Error(data.error || 'Failed to cancel order'); }
        return await response.json();
    } catch (error) {
        console.error('Error cancelling order:', error);
        showAlert(`Error cancelling order: ${error.message}`, 'danger');
        throw error;
    }
}

export async function checkOrderStatus() {
    try {
        const response = await fetch('/api/options/check-orders', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        if (!response.ok) { const data = await response.json(); throw new Error(data.error || 'Failed to check order status'); }
        return await response.json();
    } catch (error) {
        console.error('Error checking order status:', error);
        throw error;
    }
}

export async function executeOrder(orderId) {
    try {
        const response = await fetch(`/api/options/execute/${orderId}`, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        if (!response.ok) throw new Error(`HTTP error ${response.status}`);
        return await response.json();
    } catch (error) {
        console.error('Error executing order:', error);
        return { success: false, error: error.message };
    }
}

export async function executeCloseOrder(position) {
    try {
        const response = await fetch('/api/options/close', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker: position.ticker, option_type: position.option_type, strike: position.strike, expiration: position.expiration, quantity: position.quantity || 1 }) });
        if (!response.ok) { const err = await response.json().catch(() => ({})); throw new Error(err.error || `HTTP ${response.status}`); }
        return await response.json();
    } catch (error) {
        console.error('Error closing position:', error);
        throw error;
    }
}
