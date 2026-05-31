import StateModel from '../utils/state-model.js';
import { formatCurrency } from '../utils/formatters.js';
import { showPanelLoading, finishPanelLoading, failPanelLoading } from './options-table-rendering.js';
import { fetchWithTimeout, readJsonSafely } from './api.js';

let contentEl;
let stateEl;
let lastUpdatedEl;
let loadingBannerId = null;
let listenersBound = false;
let isLoading = false;
const DEFAULT_AUTO_TIMEOUT_MS = 60000;
const DEFAULT_MANUAL_TIMEOUT_MS = 45000;

function initElements() {
    contentEl = document.getElementById('catalyst-content');
    stateEl = document.getElementById('catalyst-state');
    lastUpdatedEl = document.getElementById('catalyst-last-updated');
}

async function fetchCatalystSignals(manualRefresh = false, thresholds = null, timeoutMs = DEFAULT_MANUAL_TIMEOUT_MS) {
    let url = `/api/options/catalyst-watch?limit=6`;
    if (manualRefresh) url += '&refresh=true';
    if (thresholds?.minPremiumNotional != null) {
        url += `&min_premium_notional=${thresholds.minPremiumNotional}`;
    }
    if (thresholds?.minVolume != null) {
        url += `&min_volume=${thresholds.minVolume}`;
    }
    if (thresholds?.minFreshVolumeRatio != null) {
        url += `&min_fresh_volume_ratio=${thresholds.minFreshVolumeRatio}`;
    }
    if (thresholds?.maxScanTickers != null) {
        url += `&max_scan_tickers=${thresholds.maxScanTickers}`;
    }
    if (thresholds?.maxExpirations != null) {
        url += `&max_expirations=${thresholds.maxExpirations}`;
    }
    const response = await fetchWithTimeout(url, {
        headers: {
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
        },
    }, timeoutMs);
    const payload = await readJsonSafely(response);
    if (!response.ok || !payload?.success) {
        throw new Error(payload?.error || `HTTP error ${response.status}`);
    }
    return payload;
}

function formatCompactDollar(value) {
    if (value == null || Number.isNaN(Number(value))) return 'N/A';
    const num = Number(value);
    if (num >= 1_000_000) return `$${(num / 1_000_000).toFixed(1)}M`;
    if (num >= 1_000) return `$${(num / 1_000).toFixed(1)}K`;
    return formatCurrency(num);
}

function signalBadgeClass(signal) {
    if (signal === 'GREEN') return 'bg-success';
    if (signal === 'YELLOW') return 'bg-warning text-dark';
    if (signal === 'WATCH') return 'bg-info text-dark';
    return 'bg-secondary';
}

function directionBadgeClass(direction) {
    if (direction === 'BULLISH') return 'bg-success';
    if (direction === 'BEARISH') return 'bg-danger';
    return 'bg-secondary';
}

function renderCard(signal) {
    const template = document.getElementById('catalyst-card-template');
    const clone = template.content.cloneNode(true);

    clone.querySelector('.catalyst-card__ticker').textContent = signal.ticker || 'N/A';

    const dirBadge = clone.querySelector('.catalyst-card__direction');
    dirBadge.textContent = signal.direction || 'NEUTRAL';
    dirBadge.classList.add(...directionBadgeClass(signal.direction).split(' '));

    const badge = clone.querySelector('.catalyst-card__signal');
    badge.textContent = signal.label || signal.signal || 'Watch';
    badge.classList.add(...signalBadgeClass(signal.signal).split(' '));

    clone.querySelector('.catalyst-card__score-value').textContent = signal.score != null ? signal.score.toFixed(1) : 'N/A';

    clone.querySelector('.catalyst-card__premium').textContent = formatCompactDollar(signal.premium_notional);
    clone.querySelector('.catalyst-card__fresh').textContent = signal.fresh_volume_ratio != null ? `${signal.fresh_volume_ratio.toFixed(0)}x` : 'N/A';
    clone.querySelector('.catalyst-card__otm').textContent = signal.otm_pct != null ? `${signal.otm_pct.toFixed(1)}%` : 'N/A';
    clone.querySelector('.catalyst-card__strike').textContent = signal.strike != null ? formatCurrency(signal.strike) : 'N/A';

    const clusterCount = signal.cluster_expirations?.length || 0;
    clone.querySelector('.catalyst-card__cluster').textContent = clusterCount > 1
        ? `${clusterCount} expirations`
        : 'Single expiration';

    const hedgedEl = clone.querySelector('.catalyst-card__hedged');
    if (signal.is_hedged) {
        hedgedEl.textContent = 'Yes (mirrored)';
        hedgedEl.className = 'text-warning';
    } else {
        hedgedEl.textContent = 'No';
        hedgedEl.className = 'text-success';
    }

    const earningsEl = clone.querySelector('.catalyst-card__earnings');
    if (signal.earnings_dte != null && signal.earnings_dte >= 0) {
        earningsEl.textContent = `${signal.earnings_dte}d away`;
    } else {
        earningsEl.textContent = 'No close earnings';
    }

    const rationaleEl = clone.querySelector('.catalyst-card__rationale');
    if (signal.rationale?.length) {
        rationaleEl.textContent = signal.rationale.slice(0, 3).join(' • ');
    }

    const blockersEl = clone.querySelector('.catalyst-card__blockers');
    if (signal.blockers?.length) {
        blockersEl.textContent = signal.blockers.slice(0, 2).join(' • ');
    }

    const socialEl = clone.querySelector('.catalyst-card__social');
    if (signal.social) {
        const s = signal.social;
        const rankDelta = (s.rank_24h_ago || 0) - s.rank;
        const trend = rankDelta > 0 ? `↑${rankDelta}` : rankDelta < 0 ? `↓${Math.abs(rankDelta)}` : '→';
        socialEl.textContent = `Social rising: ${s.mentions} mentions, rank #${s.rank} ${trend} 24h`;
        socialEl.classList.remove('d-none');
    }

    return clone;
}

function renderSignals(payload) {
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

    if (payload.enabled === false) {
        contentEl.classList.add('d-none');
        StateModel.showEmpty('catalyst-state', 'Catalyst Flow scanning is disabled in configuration.');
        return;
    }

    if (!signals.length) {
        contentEl.classList.add('d-none');
        const parts = [];
        if (payload.scanned != null) parts.push(`Scanned ${payload.scanned} tickers`);
        if (payload.cache_hits != null) parts.push(`${payload.cache_hits} cached`);
        if (payload.rejected_by_threshold_count != null && payload.rejected_by_threshold_count > 0) parts.push(`${payload.rejected_by_threshold_count} rejected`);
        if (payload.elapsed_seconds != null) parts.push(`${payload.elapsed_seconds}s`);
        if (payload.errors?.length) parts.push(`${payload.errors.length} errors`);

        let msg;
        if (payload.served_from_cache && payload.cache_age_seconds != null) {
            const mins = Math.round(payload.cache_age_seconds / 60);
            msg = `Showing cached catalyst signals from ${mins} minute${mins !== 1 ? 's' : ''} ago; refreshing in background.`;
        } else if (payload.scan_pending) {
            msg = 'Waiting for broker flow data...';
        } else {
            msg = 'No anomalous flow detected on the watchlist.';
        }

        if (parts.length) msg += ` (${parts.join(', ')})`;
        if (payload.thresholds) {
            const t = payload.thresholds;
            const pn = formatCompactDollar(t.min_premium_notional);
            const fvr = t.min_fresh_volume_ratio ?? 'N/A';
            const mv = t.min_volume ?? 'N/A';
            const me = t.max_expirations ?? 'N/A';
            const mst = t.max_scan_tickers ?? 'N/A';
            const md = t.max_dte ?? 'N/A';
            msg += ` Thresholds: ${pn} premium, ${fvr}x vol/OI, ${mv} vol, ${me} expiration(s), ${mst} ticker(s), ${md}d max. Try Refresh or lower thresholds.`;
        }
        StateModel.showEmpty('catalyst-state', msg);

        if (lastUpdatedEl && payload.generated_at) {
            const date = new Date(payload.generated_at);
            let label = `Updated: ${date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`;
            if (payload.served_from_cache && payload.cache_age_seconds != null) {
                const mins = Math.round(payload.cache_age_seconds / 60);
                label += ` (cached ${mins}m ago)`;
                lastUpdatedEl.className = 'text-warning';
            } else {
                lastUpdatedEl.className = '';
            }
            lastUpdatedEl.textContent = label;
            lastUpdatedEl.classList.remove('d-none');
        }
        return;
    }

    signals.forEach(s => contentEl.appendChild(renderCard(s)));

    stateEl.innerHTML = '';
    const scanParts = [];
    if (payload.scanned != null) scanParts.push(`${payload.scanned} scanned`);
    if (payload.cache_hits != null) scanParts.push(`${payload.cache_hits} cached`);
    if (payload.candidate_count != null) scanParts.push(`${payload.candidate_count} candidates`);
    if (payload.rejected_by_threshold_count != null && payload.rejected_by_threshold_count > 0) scanParts.push(`${payload.rejected_by_threshold_count} rejected`);
    if (payload.errors?.length) scanParts.push(`${payload.errors.length} errors`);
    if (payload.elapsed_seconds != null) scanParts.push(`${payload.elapsed_seconds}s`);
    if (scanParts.length) {
        const info = document.createElement('small');
        info.className = 'text-muted d-block mb-1';
        info.textContent = scanParts.join(', ');
        stateEl.appendChild(info);
    }
    contentEl.classList.remove('d-none');

    if (lastUpdatedEl && payload.generated_at) {
        const date = new Date(payload.generated_at);
        let label = `Updated: ${date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`;
        if (payload.served_from_cache && payload.cache_age_seconds != null) {
            const mins = Math.round(payload.cache_age_seconds / 60);
            label += ` (cached ${mins}m ago)`;
            lastUpdatedEl.className = 'text-warning';
        } else {
            lastUpdatedEl.className = '';
        }
        lastUpdatedEl.textContent = label;
        lastUpdatedEl.classList.remove('d-none');
    }
}

export async function loadCatalystSignals(manualRefresh = false, thresholds = null) {
    if (!contentEl) initElements();
    if (!contentEl) return;
    if (isLoading) return;
    isLoading = true;

    loadingBannerId = showPanelLoading('catalyst-watch-section', 'Scanning options flow for anomalies...');
    StateModel.showEmpty('catalyst-state', 'Scanning options flow...');

    try {
        const timeoutMs = manualRefresh ? DEFAULT_MANUAL_TIMEOUT_MS : DEFAULT_AUTO_TIMEOUT_MS;
        const payload = await fetchCatalystSignals(manualRefresh, thresholds, timeoutMs);
        renderSignals(payload);
    } catch (error) {
        console.error('Error loading catalyst signals:', error);
        if (loadingBannerId) {
            failPanelLoading(
                loadingBannerId,
                error?.message?.includes('Request timed out')
                    ? 'Catalyst watch is slow right now'
                    : 'Unable to load catalyst watch'
            );
            loadingBannerId = null;
        }
        contentEl.classList.add('d-none');
        if (error?.message?.includes('Request timed out')) {
            StateModel.showEmpty(
                'catalyst-state',
                'Catalyst scanning is taking longer than expected. The rest of the dashboard is still available; try Refresh when broker flow data catches up.'
            );
        } else {
            StateModel.showError('catalyst-state', 'Unable to load catalyst watch.', () => loadCatalystSignals(true));
        }
    } finally {
        isLoading = false;
    }
}

export function initializeCatalystWatch() {
    initElements();
    if (!listenersBound) {
        listenersBound = true;
        document.getElementById('refresh-catalyst-signals')?.addEventListener('click', () => {
            loadCatalystSignals(true, {
                maxScanTickers: 2,
                maxExpirations: 1,
            });
        });
        document.getElementById('sensitive-catalyst-scan')?.addEventListener('click', () => {
            loadCatalystSignals(true, {
                minPremiumNotional: 250000,
                minVolume: 100,
                minFreshVolumeRatio: 2,
                maxScanTickers: 4,
                maxExpirations: 1,
            });
        });
    }
    return loadCatalystSignals(false, {
        maxScanTickers: 2,
        maxExpirations: 1,
    });
}
