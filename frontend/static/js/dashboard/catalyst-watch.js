import StateModel from '../utils/state-model.js';
import { formatCurrency } from '../utils/formatters.js';
import { fetchCatalystSignals } from './api-options.js';

let contentEl;
let stateEl;
let lastUpdatedEl;
let listenersBound = false;
let isLoading = false;
const DEFAULT_AUTO_TIMEOUT_MS = 60000;
const DEFAULT_MANUAL_TIMEOUT_MS = 45000;

function initElements() {
    contentEl = document.getElementById('catalyst-content');
    stateEl = document.getElementById('catalyst-state');
    lastUpdatedEl = document.getElementById('catalyst-last-updated');
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

function actionBucketClass(bucket) {
    if (bucket === 'CALL_RESEARCH') return 'text-success';
    if (bucket === 'PUT_RESEARCH') return 'text-danger';
    if (bucket === 'CONFLICT_WATCH') return 'text-warning';
    if (bucket === 'SPECULATIVE_ONLY') return 'text-info';
    if (bucket === 'REJECT') return 'text-secondary';
    return 'text-muted';
}

function directionBadgeClass(direction) {
    if (direction === 'BULLISH') return 'bg-success';
    if (direction === 'BEARISH') return 'bg-danger';
    return 'bg-secondary';
}

function getOverlayBadgeInfo(signal) {
    const overlay = signal.signal_overlay || {};
    const fit = (signal.signal_overlay_fit || overlay.verdict || 'unknown').toLowerCase();
    if (fit === 'unknown') return null;

    const classes = {
        supporting: 'bg-success',
        confirming: 'bg-success',
        neutral: 'bg-info',
        caution: 'bg-warning text-dark',
        conflict: 'bg-danger',
    };
    const labels = {
        supporting: 'Overlay supports',
        confirming: 'Overlay confirms',
        neutral: 'Overlay neutral',
        caution: 'Overlay caution',
        conflict: 'Overlay conflict',
    };
    const summaryParts = [];
    if (overlay.summary) summaryParts.push(overlay.summary);
    if (overlay.capital?.summary) summaryParts.push(`capital: ${overlay.capital.summary}`);
    if (overlay.technical?.summary) summaryParts.push(`technical: ${overlay.technical.summary}`);
    if (overlay.derivatives?.summary) summaryParts.push(`derivatives: ${overlay.derivatives.summary}`);
    const warnings = overlay.warnings || signal.signal_overlay_warnings || [];

    return {
        class: classes[fit] || 'bg-secondary',
        label: labels[fit] || 'Overlay',
        summary: summaryParts.slice(0, 2).join(' • '),
        title: warnings.length > 0 ? warnings.join(' • ') : summaryParts.join(' • '),
    };
}

function renderCard(signal) {
    const template = document.getElementById('catalyst-card-template');
    const clone = template.content.cloneNode(true);

    clone.querySelector('.catalyst-card__ticker').textContent = signal.ticker || 'N/A';

    const actionEl = clone.querySelector('.catalyst-card__action');
    if (signal.action_label) {
        actionEl.textContent = signal.action_label;
        actionEl.classList.add(actionBucketClass(signal.action_bucket));
        if (signal.action_reason) {
            actionEl.title = signal.action_reason;
        }
    }

    const conflictEl = clone.querySelector('.catalyst-card__conflict-warning');
    if (signal.action_bucket === 'CONFLICT_WATCH') {
        conflictEl.textContent = signal.action_reason || 'Conflicting directional flow on same ticker';
        conflictEl.classList.remove('d-none');
    }

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
        earningsEl.textContent = 'No earnings found in cached data';
    }

    const rationaleEl = clone.querySelector('.catalyst-card__rationale');
    if (signal.rationale?.length) {
        rationaleEl.textContent = signal.rationale.slice(0, 3).join(' \u2022 ');
    }

    const blockersEl = clone.querySelector('.catalyst-card__blockers');
    if (signal.blockers?.length) {
        blockersEl.textContent = signal.blockers.slice(0, 2).join(' \u2022 ');
    }

    const socialEl = clone.querySelector('.catalyst-card__social');
    if (signal.social) {
        const s = signal.social;
        const rankDelta = (s.rank_24h_ago || 0) - s.rank;
        const trend = rankDelta > 0 ? `\u2191${rankDelta}` : rankDelta < 0 ? `\u2193${Math.abs(rankDelta)}` : '\u2192';
        socialEl.textContent = `Scan source: social momentum \u2014 ${s.mentions} mentions, rank #${s.rank} ${trend} 24h`;
        socialEl.classList.remove('d-none');
    }

    const overlayEl = clone.querySelector('.catalyst-card__overlay');
    const overlayInfo = getOverlayBadgeInfo(signal);
    if (overlayEl && overlayInfo) {
        overlayEl.classList.remove('d-none');
        overlayEl.innerHTML = `<span class="badge ${overlayInfo.class} me-1">${overlayInfo.label}</span><span>${overlayInfo.summary || 'Multi-dimensional signal overlay'}</span>`;
        overlayEl.title = overlayInfo.title || overlayInfo.summary;
    }

    return clone;
}

function renderSignals(payload) {
    contentEl.innerHTML = '';
    const signals = payload.signals || [];

    if (payload.enabled === false) {
        contentEl.classList.add('d-none');
        StateModel.showEmpty?.('catalyst-state', 'Catalyst Flow scanning is disabled in configuration.');
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
        StateModel.showEmpty?.('catalyst-state', msg);

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

    StateModel.showLoading?.('catalyst-state', 'Scanning options flow for anomalies...');

    try {
        const timeoutMs = manualRefresh ? DEFAULT_MANUAL_TIMEOUT_MS : DEFAULT_AUTO_TIMEOUT_MS;
        const payload = await fetchCatalystSignals(manualRefresh, thresholds, timeoutMs);
        renderSignals(payload);
    } catch (error) {
        console.error('Error loading catalyst signals:', error);
        contentEl.classList.add('d-none');
        if (error?.message?.includes('Request timed out')) {
            StateModel.showEmpty?.(
                'catalyst-state',
                'Catalyst scanning is taking longer than expected. The rest of the dashboard is still available; try Refresh when broker flow data catches up.'
            );
        } else {
            StateModel.showError?.('catalyst-state', 'Unable to load catalyst watch.', () => loadCatalystSignals(true));
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
