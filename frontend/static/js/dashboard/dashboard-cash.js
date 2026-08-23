/**
 * Cash management dashboard panels.
 * Split from dashboard.js (F042)
 */
import { fetchWeeklyOptionIncome } from './api.js';
import { escapeHtml, formatCurrency } from '../utils/formatters.js';

export async function updateCashReserveStatus() {
    try {
        const response = await fetch('/api/options/cash-status');
        if (!response.ok) { console.error('Failed to fetch cash status:', response.status); return; }
        const data = await response.json();
        if (!data.success) { console.error('Cash status error:', data.error); return; }
        const badge = document.getElementById('cash-reserve-badge');
        if (badge) { badge.className = `badge bg-${data.reserve_enabled ? 'success' : 'secondary'}`; badge.textContent = data.reserve_enabled ? 'Reserve ON' : 'Reserve OFF'; }
        const reservedEl = document.getElementById('cash-reserved');
        if (reservedEl) reservedEl.textContent = formatCurrency(data.cash_reserved);
        const availableEl = document.getElementById('cash-available');
        if (availableEl) {
            availableEl.textContent = formatCurrency(data.cash_available);
            availableEl.className = data.cash_available < 5000 ? 'text-danger' : 'text-success';
        }
        const toggle = document.getElementById('cash-reserve-toggle');
        if (toggle) toggle.checked = data.reserve_enabled;
        const details = document.getElementById('cash-reserve-details');
        const list = document.getElementById('open-puts-list');
        if (details && list && data.open_puts && data.open_puts.length > 0) {
            list.innerHTML = data.open_puts.map(put =>
                `<div>${escapeHtml(put.ticker)} ${escapeHtml(put.strike)}P ${escapeHtml(put.expiration.slice(4, 6))}/${escapeHtml(put.expiration.slice(6))} (${escapeHtml(put.contracts)} contract${put.contracts > 1 ? 's' : ''})</div>`
            ).join('');
            details.style.display = 'block';
        } else if (details) { details.style.display = 'none'; }
    } catch (error) { console.error('Error updating cash reserve status:', error); }
}

export async function updateIdleCashPanel() {
    try {
        const accResp = await fetch('/api/portfolio');
        if (!accResp.ok) return;
        const accData = await accResp.json();
        const cashBalance = accData.cash_balance || 0;
        const accountValue = accData.account_value || cashBalance;
        const idleEl = document.getElementById('idle-cash-amount');
        const deployedEl = document.getElementById('deployed-cash-amount');
        const barEl = document.getElementById('cash-utilization-bar');
        const hintEl = document.getElementById('idle-cash-hint');
        if (!idleEl) return;
        let deployed = 0;
        try {
            const posResp = await fetch('/api/portfolio/positions');
            if (posResp.ok) {
                const positions = await posResp.json();
                for (const pos of positions) {
                    if (pos.security_type === 'OPT' && pos.option_type === 'PUT' && (pos.position || 0) < 0)
                        deployed += Math.abs(pos.position || 0) * (pos.strike || 0) * 100;
                }
            }
        } catch (e) { }
        const idle = cashBalance - deployed;
        const utilPct = accountValue > 0 ? Math.min(100, (deployed / accountValue) * 100) : 0;
        idleEl.textContent = '$' + Math.max(0, idle).toLocaleString();
        if (deployedEl) deployedEl.textContent = '$' + deployed.toLocaleString();
        if (barEl) barEl.style.width = utilPct.toFixed(0) + '%';
        if (barEl) barEl.className = 'progress-bar ' + (utilPct >= 70 ? 'bg-success' : utilPct >= 40 ? 'bg-warning' : 'bg-danger');
        if (hintEl) { if (idle > accountValue * 0.3) hintEl.classList.remove('d-none'); else hintEl.classList.add('d-none'); }
        if (idleEl) idleEl.className = utilPct >= 70 ? 'fw-bold text-success' : utilPct >= 40 ? 'fw-bold text-warning' : 'fw-bold text-danger';
    } catch (error) { console.error('Error updating idle cash panel:', error); }
}
