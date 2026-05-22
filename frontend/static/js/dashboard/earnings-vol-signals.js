import StateModel from '../utils/state-model.js';
import { formatCurrency } from '../utils/formatters.js';
import { showPanelLoading, finishPanelLoading, failPanelLoading } from './options-table-rendering.js';

let contentEl;
let stateEl;
let lastUpdatedEl;
let loadingBannerId = null;

function initElements() {
    contentEl = document.getElementById('earnings-vol-content');
    stateEl = document.getElementById('earnings-vol-state');
    lastUpdatedEl = document.getElementById('earnings-vol-last-updated');
}

async function fetchEarningsVolSignals(manualRefresh = false) {
    const url = `/api/earnings/vol-signals?limit=6${manualRefresh ? '&refresh=true' : ''}`;
    const response = await fetch(url, {
        headers: {
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
        },
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload?.success) {
        throw new Error(payload?.error || `HTTP error ${response.status}`);
    }
    return payload;
}

function signalClass(signal) {
    if (signal === 'GREEN') return 'bg-success';
    if (signal === 'YELLOW') return 'bg-warning text-dark';
    if (signal === 'WATCH') return 'bg-info text-dark';
    return 'bg-secondary';
}

function formatPct(value, digits = 0) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return 'N/A';
    return `${(Number(value) * 100).toFixed(digits)}%`;
}

function formatNumber(value, digits = 2) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return 'N/A';
    return Number(value).toFixed(digits);
}

function renderCard(signal) {
    const template = document.getElementById('earnings-vol-card-template');
    const clone = template.content.cloneNode(true);

    clone.querySelector('.earnings-vol-card__ticker').textContent = signal.ticker || 'N/A';
    clone.querySelector('.earnings-vol-card__date').textContent = signal.earnings_date
        ? `${signal.earnings_date} | ${signal.days_to_earnings ?? '?'}d`
        : 'No earnings date';

    const sourceEl = clone.querySelector('.earnings-vol-card__source');
    if (signal.earnings_source || signal.time_of_day) {
        const parts = [];
        if (signal.time_of_day) parts.push(signal.time_of_day);
        if (signal.earnings_source) parts.push(signal.earnings_source);
        sourceEl.textContent = parts.join(' | ');
    }

    const badge = clone.querySelector('.earnings-vol-card__signal');
    badge.textContent = signal.label || signal.signal || 'Avoid';
    badge.classList.add(...signalClass(signal.signal).split(' '));

    clone.querySelector('.earnings-vol-card__score-value').textContent = formatNumber(signal.score, 1);
    clone.querySelector('.earnings-vol-card__iv').textContent = `${formatPct(signal.front_iv)} / ${formatPct(signal.back_iv)}`;
    clone.querySelector('.earnings-vol-card__ivrv').textContent = formatNumber(signal.iv_rv_ratio, 2);
    clone.querySelector('.earnings-vol-card__spread').textContent = signal.spread_pct == null ? 'N/A' : `${Number(signal.spread_pct).toFixed(1)}%`;
    clone.querySelector('.earnings-vol-card__risk').textContent = signal.max_risk_per_contract == null
        ? 'N/A'
        : formatCurrency(signal.max_risk_per_contract);

    const setupEls = {
        structure: clone.querySelector('.earnings-vol-card__structure'),
        strike: clone.querySelector('.earnings-vol-card__strike'),
        sell: clone.querySelector('.earnings-vol-card__sell'),
        buy: clone.querySelector('.earnings-vol-card__buy'),
        debit: clone.querySelector('.earnings-vol-card__debit'),
        entry: clone.querySelector('.earnings-vol-card__entry'),
        exit: clone.querySelector('.earnings-vol-card__exit'),
        target: clone.querySelector('.earnings-vol-card__target'),
        cut: clone.querySelector('.earnings-vol-card__cut'),
    };

    setupEls.structure.textContent = signal.structure || 'ATM calendar';
    setupEls.strike.textContent = signal.atm_strike != null ? formatCurrency(signal.atm_strike) : 'N/A';
    setupEls.sell.textContent = signal.front_expiration || 'N/A';
    setupEls.buy.textContent = signal.back_expiration || 'N/A';
    setupEls.debit.textContent = signal.estimated_calendar_debit != null
        ? formatCurrency(signal.estimated_calendar_debit)
        : 'N/A';
    setupEls.entry.textContent = signal.entry_plan || 'N/A';
    setupEls.exit.textContent = signal.exit_plan || 'N/A';
    setupEls.target.textContent = signal.profit_target || 'N/A';
    setupEls.cut.textContent = signal.invalidation || 'N/A';

    const notesEl = clone.querySelector('.earnings-vol-card__notes');
    const lines = signal.blockers?.length ? signal.blockers : signal.notes;
    notesEl.textContent = lines?.length ? lines.slice(0, 2).join(' | ') : 'Clean enough to research.';

    return clone;
}

function renderSignals(payload) {
    // Finish the loading banner if active
    if (loadingBannerId) {
        if (payload.signals && payload.signals.length > 0) {
            finishPanelLoading(loadingBannerId, 'Signals loaded');
        } else {
            finishPanelLoading(loadingBannerId, 'No signals found');
        }
        loadingBannerId = null;
    }

    contentEl.innerHTML = '';
    const signals = payload.signals || [];

    if (!signals.length) {
        contentEl.classList.add('d-none');
        StateModel.showEmpty('earnings-vol-state', 'No earnings-vol signals found for the current watchlist.');
        return;
    }

    signals.forEach(signal => {
        contentEl.appendChild(renderCard(signal));
    });

    stateEl.innerHTML = '';
    contentEl.classList.remove('d-none');

    if (lastUpdatedEl && payload.generated_at) {
        const date = new Date(payload.generated_at);
        lastUpdatedEl.textContent = `Updated: ${date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`;
        lastUpdatedEl.classList.remove('d-none');
    }
}

export async function loadEarningsVolSignals(manualRefresh = false) {
    if (!contentEl) initElements();
    if (!contentEl) return;

    // Show an inline loading banner instead of hiding content.
    // If this is a refresh, existing content stays visible underneath.
    loadingBannerId = showPanelLoading('earnings-vol-signals-section', 'Scanning earnings volatility signals...');

    try {
        const payload = await fetchEarningsVolSignals(manualRefresh);
        renderSignals(payload);
    } catch (error) {
        console.error('Error loading earnings vol signals:', error);
        if (loadingBannerId) {
            failPanelLoading(loadingBannerId, 'Unable to load earnings vol signals');
            loadingBannerId = null;
        }
        contentEl.classList.add('d-none');
        StateModel.showError('earnings-vol-state', 'Unable to load earnings vol signals.', () => loadEarningsVolSignals(true));
    }
}

export function initializeEarningsVolSignals() {
    initElements();
    document.getElementById('refresh-earnings-vol-signals')?.addEventListener('click', () => {
        loadEarningsVolSignals(true);
    });
    loadEarningsVolSignals();
}
