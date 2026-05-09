/**
 * Market regime and earnings display panels.
 * Split from dashboard.js (F042)
 */
import { fetchWeeklyOptionIncome } from './api.js';
import { formatCurrency } from '../utils/formatters.js';

export async function loadTechnicalRegime() {
    try {
        const tickersResp = await fetch('/api/options/watchlist-tickers');
        let tickers = ['SPY'];
        if (tickersResp.ok) { const tickersData = await tickersResp.json(); if (tickersData.tickers && tickersData.tickers.length > 0) tickers = tickersData.tickers.slice(0, 20); }
        const tickersParam = tickers.join(',');
        const response = await fetch(`/api/technical/regime/summary?tickers=${tickersParam}`);
        if (!response.ok) { console.error('Failed to fetch technical regime:', response.status); return; }
        const data = await response.json();
        if (!data.success || !data.summary) { console.error('Technical regime error:', data.error); return; }
        const summary = data.summary; const details = data.details || {};
        const regimeIcon = document.getElementById('regime-icon');
        const regimeText = document.getElementById('regime-text');
        const regimeDetails = document.getElementById('regime-details');
        const contextSection = document.getElementById('context-filter-section');
        if (regimeIcon && regimeText) {
            const emoji = summary.dominant_regime === 'bullish' ? '🟢' : summary.dominant_regime === 'bearish' ? '🔴' : '⚪';
            regimeIcon.textContent = emoji; regimeText.textContent = summary.summary_text || summary.dominant_regime;
            const card = contextSection?.querySelector('.card');
            if (card) { card.classList.remove('regime-bullish', 'regime-bearish', 'regime-neutral'); card.classList.add(`regime-${summary.dominant_regime}`); }
        }
        if (regimeDetails) {
            const bullish = summary.regimes?.bullish || 0;
            const bearish = summary.regimes?.bearish || 0;
            const neutral = summary.regimes?.neutral || 0;
            regimeDetails.textContent = `${bullish}B/${bearish}Br/${neutral}N | ${summary.trending}T/${summary.ranging}R`;
        }
        const adxValue = document.getElementById('adx-value');
        if (adxValue && Object.keys(details).length > 0) {
            let totalAdx = 0; let count = 0;
            for (const [, regime] of Object.entries(details)) { if (regime.adx) { totalAdx += regime.adx; count++; } }
            adxValue.textContent = count > 0 ? (totalAdx / count).toFixed(1) : '--';
        }
    } catch (error) { console.error('Error loading technical regime:', error); }
}

export async function loadLockedTickers() {
    try {
        const response = await fetch('/api/earnings/locked-tickers?lock_days=5');
        if (!response.ok) { console.error('Failed to fetch locked tickers:', response.status); return; }
        const data = await response.json();
        if (!data.success) { console.error('Locked tickers error:', data.error); return; }
        const lockedList = document.getElementById('locked-tickers-list');
        const lockInfo = document.getElementById('earnings-lock-info');
        if (lockedList && lockInfo) {
            if (data.locked && data.locked.length > 0) {
                lockedList.innerHTML = data.locked.map(item => `<div>🔒 ${item.ticker} — Earnings ${item.earnings_date} (${item.days_to_earnings}d)</div>`).join('');
                lockInfo.classList.remove('d-none');
            } else { lockedList.innerHTML = '<div class="text-muted">No tickers locked</div>'; }
        }
        const lockToggle = document.getElementById('earnings-lock-toggle');
        if (lockToggle) lockToggle.checked = true;
    } catch (error) { console.error('Error loading locked tickers:', error); }
}

export async function loadVixRegime() {
    try {
        const response = await fetch('/api/options/vix-regime');
        if (!response.ok) { console.error('Failed to fetch VIX regime:', response.status); return; }
        const data = await response.json();
        if (!data.success || !data.vix_regime) { console.error('VIX regime error:', data.error); return; }
        const regime = data.vix_regime;
        const vixLevel = document.getElementById('vix-level');
        if (vixLevel) vixLevel.textContent = regime.vix;
        const vixBadge = document.getElementById('vix-badge');
        if (vixBadge) {
            vixBadge.textContent = regime.regime.toUpperCase();
            vixBadge.className = `badge bg-${regime.regime === 'complacency' ? 'success' : regime.regime === 'fear' ? 'danger' : 'primary'} fs-6`;
        }
        const vixDesc = document.getElementById('vix-description');
        if (vixDesc) vixDesc.textContent = regime.description;
        const deltaAdj = document.getElementById('vix-delta-adj');
        if (deltaAdj) { const adj = regime.delta_adjustment; deltaAdj.textContent = adj > 0 ? `+${adj.toFixed(2)}` : adj.toFixed(2); deltaAdj.className = adj > 0 ? 'fw-bold text-success' : adj < 0 ? 'fw-bold text-danger' : 'fw-bold'; }
        const exposure = document.getElementById('vix-exposure');
        if (exposure) { const exp = regime.exposure_multiplier; exposure.textContent = `${Math.round(exp * 100)}%`; exposure.className = exp < 1 ? 'fw-bold text-warning' : 'fw-bold'; }
        await loadLockedTickers();
        const vixDate = document.getElementById('vix-date');
        if (vixDate) vixDate.textContent = `Updated: ${new Date().toLocaleTimeString()}`;
    } catch (error) { console.error('Error loading VIX regime:', error); }
}

export async function updateWeeklyEarningsSummary() {
    try {
        const data = await fetchWeeklyOptionIncome();
        const weeklyIncomeSummary = document.getElementById('weekly-income-summary');
        if (weeklyIncomeSummary) weeklyIncomeSummary.textContent = formatCurrency(data.total_income || 0);
        const openShortIncomeSummary = document.getElementById('open-short-income-summary');
        if (openShortIncomeSummary) openShortIncomeSummary.textContent = formatCurrency(data.open_short_total_income || 0);
        const weeklyPositionsCount = document.getElementById('weekly-positions-count');
        if (weeklyPositionsCount) weeklyPositionsCount.textContent = data.positions_count || 0;
        const openShortContractsCount = document.getElementById('open-short-contracts-count');
        if (openShortContractsCount) openShortContractsCount.textContent = data.open_short_contracts_count || 0;
        const fridayDate = document.getElementById('friday-date');
        if (fridayDate && data.this_friday) fridayDate.textContent = data.this_friday;
    } catch (error) { console.error('Error updating weekly earnings summary:', error); }
}
