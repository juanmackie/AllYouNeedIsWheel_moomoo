/**
 * Watchlist panel: merged canonical union (Moomoo group + app + config)
 * with origin labels, add/remove, and scan-feasibility notice.
 */
import { escapeHtml } from '../utils/formatters.js';

let _watchlistData = null;

function originBadge(origin) {
    const labels = { moomoo: 'Moomoo', app: 'App', config: 'Config' };
    const classes = { moomoo: 'bg-primary', app: 'bg-success', config: 'bg-secondary' };
    return `<span class="badge ${classes[origin] || 'bg-secondary'}">${labels[origin] || origin}</span>`;
}

export async function loadWatchlist() {
    const tagsEl = document.getElementById('watchlist-tags');
    const summaryEl = document.getElementById('watchlist-summary');
    const infeasibleEl = document.getElementById('watchlist-infeasible');
    if (!tagsEl) return;
    try {
        const resp = await fetch('/api/watchlist');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        _watchlistData = await resp.json();
        const union = _watchlistData.union || [];
        const counts = _watchlistData.sources || {};
        const total = Object.values(counts).reduce((acc, list) => acc + (list || []).length, 0);
        summaryEl.textContent =
            `${union.length} canonical symbols (${total} raw entries: ` +
            `Moomoo ${(counts.moomoo || []).length}, App ${(counts.app || []).length}, ` +
            `Config ${(counts.config || []).length})`;

        tagsEl.innerHTML = '';
        union.forEach((item) => {
            const tag = document.createElement('span');
            tag.className = 'badge bg-light text-dark border d-inline-flex align-items-center gap-1';
            tag.innerHTML =
                `<span>${escapeHtml(item.ticker)}</span>` +
                item.origins.map(originBadge).join('') +
                (item.origins.includes('app')
                    ? `<button type="button" class="btn-close btn-close-sm ms-1" style="font-size:0.55rem" ` +
                      `aria-label="Remove ${escapeHtml(item.ticker)}" data-remove="${escapeHtml(item.ticker)}"></button>`
                    : '');
            tagsEl.appendChild(tag);
        });
        tagsEl.querySelectorAll('[data-remove]').forEach((btn) => {
            btn.addEventListener('click', async () => {
                await fetch(`/api/watchlist/${encodeURIComponent(btn.dataset.remove)}`, { method: 'DELETE' });
                loadWatchlist();
            });
        });
        infeasibleEl.classList.add('d-none');
    } catch (err) {
        console.error('Watchlist panel failed to load:', err);
        summaryEl.textContent = 'Watchlist unavailable';
    }
}

export function initWatchlistPanel() {
    const addBtn = document.getElementById('watchlist-add-btn');
    const addInput = document.getElementById('watchlist-add-input');
    const refreshBtn = document.getElementById('watchlist-refresh-btn');
    if (addBtn && !addBtn.dataset.bound) {
        addBtn.dataset.bound = 'true';
        addBtn.addEventListener('click', async () => {
            const symbol = (addInput.value || '').trim().toUpperCase();
            if (!symbol) return;
            const resp = await fetch('/api/watchlist', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol }),
            });
            if (resp.ok) {
                addInput.value = '';
                loadWatchlist();
            }
        });
    }
    if (refreshBtn && !refreshBtn.dataset.bound) {
        refreshBtn.dataset.bound = 'true';
        refreshBtn.addEventListener('click', loadWatchlist);
    }
    loadWatchlist();
}
