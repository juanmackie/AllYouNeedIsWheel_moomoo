/**
 * Top Recommendations Module
 * Displays the highest-scoring option opportunities with auto-refresh
 */
import { fetchRunState, refreshRun } from './api-run.js';
import { escapeHtml, formatCurrency, formatPercent } from '../utils/formatters.js';
import { showPanelLoading, finishPanelLoading, failPanelLoading } from './options-table-rendering.js';
import StateModel from '../utils/state-model.js';

// Module state
let signalsData = null;
let autoRefreshInterval = null;
let isVisible = true;
let listenersBound = false;
let isInitialized = false;
let activeSignalType = 'all';
let _isLoading = false; // in-flight guard — prevents overlapping requests
let pendingRecommendationRequest = null;
const REFRESH_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes
const GENERATING_RETRY_DELAYS_MS = [8000, 15000, 30000, 60000, 120000];
let loadingBannerId = null;
let generatingRetryTimer = null;
let generatingRetryCount = 0;
let _isBackendGenerating = false;
let _toggleRefreshInProgress = false;

// DOM Elements (initialized lazily)
let container, contentEl, cardsContainer, lastUpdatedEl;
let blockedListEl, blockedCountEl, bpIndicator;
let tabContainer, signalCountDisplay;

/**
 * Initialize DOM element references
 */
function initElements() {
    container = document.getElementById('top-recommendations-container');
    contentEl = document.getElementById('top-recommendations-content');
    cardsContainer = document.getElementById('top-recommendations-cards');
    lastUpdatedEl = document.getElementById('top-recs-last-updated');
    blockedListEl = document.getElementById('blocked-candidates-list');
    blockedCountEl = document.getElementById('blocked-candidates-count');
    bpIndicator = document.getElementById('buying-power-indicator');
    tabContainer = document.getElementById('signal-tabs');
    signalCountDisplay = document.getElementById('signal-count-display');
}

/**
 * Get heatmap color class based on score
 * @param {number} score - Option score (0-100)
 * @returns {string} CSS class
 */
function getScoreColorClass(score) {
    if (score == null) return 'bg-secondary';
    if (score >= 70) return 'bg-success';
    if (score >= 50) return 'bg-warning text-dark';
    return 'bg-secondary';
}

/**
 * Get rank badge class and label
 * @param {number} rank - Rank (1-based)
 * @returns {Object} badge class and label
 */
function getRankBadge(rank) {
    const badges = {
        1: { class: 'rank-gold', icon: '🥇', label: '#1 Pick' },
        2: { class: 'rank-silver', icon: '🥈', label: '#2 Pick' },
        3: { class: 'rank-bronze', icon: '🥉', label: '#3 Pick' }
    };
    return badges[rank] || { class: 'rank-standard', icon: `#${rank}`, label: `#${rank} Pick` };
}

/**
 * Get confidence badge class and label based on confidence score
 * @param {number} confidence - Confidence score (0-100)
 * @returns {Object} badge class and label
 */
function getConfidenceBadge(confidence) {
    if (confidence == null) return { class: 'bg-secondary', label: 'Conf: ?' };
    if (confidence >= 80) return { class: 'bg-success', label: 'High conf' };
    if (confidence >= 60) return { class: 'bg-info', label: 'Med conf' };
    if (confidence >= 40) return { class: 'bg-warning text-dark', label: 'Low conf' };
    return { class: 'bg-danger', label: 'Poor conf' };
}

function addClassTokens(el, className) {
    if (!el || !className) return;
    className.split(/\s+/).filter(Boolean).forEach(cls => el.classList.add(cls));
}

function getOverlayBadgeInfo(rec) {
    const overlay = rec.signal_overlay || {};
    const fit = (rec.signal_overlay_fit || overlay.verdict || 'unknown').toLowerCase();
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
    const warningParts = overlay.warnings || rec.signal_overlay_warnings || [];

    return {
        class: classes[fit] || 'bg-secondary',
        label: labels[fit] || 'Overlay',
        summary: summaryParts.slice(0, 2).join(' • '),
        title: warningParts.length > 0 ? warningParts.join(' • ') : summaryParts.join(' • '),
    };
}

/**
 * Get data source display string and class
 * @param {Object} rec - Recommendation data
 * @returns {Object} text and icon class
 */
function normalizeSourceLabel(value, fallback = 'Unknown') {
    const source = String(value || '').trim().toLowerCase();
    if (!source) return fallback;
    if (source === 'broker' || source === 'moomoo' || source === 'opend') return 'Moomoo';
    if (source === 'portfolio fallback' || source === 'portfolio_fallback') return 'Portfolio fallback';
    if (source === 'yahoo') return 'yfinance';
    if (source === 'yfinance') return 'yfinance';
    return String(value);
}

function sourceBadgeClass(value) {
    const source = String(value || '').trim().toLowerCase();
    if (!source || source === 'unknown') return 'bg-secondary';
    if (source === 'broker' || source === 'moomoo' || source === 'opend') return 'bg-success';
    if (source === 'yfinance') return 'bg-warning text-dark';
    return 'bg-info text-dark';
}

function getDataSourceInfo(rec) {
    const wd = rec.wheel_decision || {};
    const priceSource = rec.price_source || wd.price_source || rec.data_source || 'unknown';
    const chainSource = rec.chain_source || wd.chain_source || 'unknown';
    const ivSource = rec.iv_source || wd.iv_source || 'unknown';
    const quoteTs = rec.quote_timestamp || wd.quote_timestamp || wd.generated_at || rec.generated_at;
    let freshness = '';
    if (quoteTs) {
        const ageSec = (Date.now() - new Date(quoteTs).getTime()) / 1000;
        if (ageSec < 60) freshness = 'just now';
        else if (ageSec < 300) freshness = `${Math.floor(ageSec / 60)}m ago`;
        else if (ageSec < 3600) freshness = `${Math.floor(ageSec / 60)}m ago`;
        else freshness = '>1h ago';
    }
    const icon = String(priceSource || '').toLowerCase() === 'yfinance'
        || String(chainSource || '').toLowerCase() === 'yfinance'
        || String(ivSource || '').toLowerCase() === 'yfinance'
        ? 'bi-database-exclamation'
        : 'bi-database-check';
    const sources = [
        { label: 'Price', value: priceSource },
        { label: 'Chain', value: chainSource },
        { label: 'IV', value: ivSource },
    ];
    const freshnessClass = freshness && freshness.includes('>') ? 'text-warning' : '';
    return { icon, sources, freshness, freshnessClass };
}

/**
 * Extract top score drivers from score_details
 * @param {Object} scoreDetails - Score breakdown dict
 * @returns {Object} { positive: string[], negative: string[] }
 */
function extractScoreDrivers(scoreDetails) {
    if (!scoreDetails) return { positive: [], negative: [] };
    const thresholds = { iv_adjusted: 70, liquidity: 70, theta_delta: 65, expected_value: 60, iv_environment: 65, upside: 70, buffer: 70, capital_efficiency: 65, delta_fit: 60, otm_fit: 65, annualized: 60 };
    const labels = { iv_adjusted: 'IV-adj return', liquidity: 'Liquidity', theta_delta: 'Theta/Delta', expected_value: 'Expected value', iv_environment: 'IV environment', upside: 'Upside', buffer: 'Buffer', capital_efficiency: 'Capital eff.', delta_fit: 'Delta fit', otm_fit: 'OTM fit', annualized: 'Yield' };
    const positive = [];
    const negative = [];
    for (const [key, val] of Object.entries(scoreDetails)) {
        if (typeof val !== 'number') continue;
        const label = labels[key] || key;
        const threshold = thresholds[key] || 60;
        if (val >= threshold) positive.push(`${label}: ${val.toFixed(0)}`);
        else if (val < 40) negative.push(`${label}: ${val.toFixed(0)}`);
    }
    // Sort by value descending for positive, ascending for negative
    positive.sort((a, b) => {
        const va = parseFloat(a.split(': ')[1]);
        const vb = parseFloat(b.split(': ')[1]);
        return vb - va;
    });
    negative.sort((a, b) => {
        const va = parseFloat(a.split(': ')[1]);
        const vb = parseFloat(b.split(': ')[1]);
        return va - vb;
    });
    return { positive: positive.slice(0, 3), negative: negative.slice(0, 3) };
}

/**
 * Format expiration date
 * @param {string} expiration - YYYYMMDD format
 * @returns {string} Formatted date
 */
function formatExpiration(expiration) {
    if (!expiration || expiration.length !== 8) return expiration || '-';
    const year = expiration.slice(0, 4);
    const month = expiration.slice(4, 6);
    const day = expiration.slice(6, 8);
    const date = new Date(`${year}-${month}-${day}`);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

/**
 * Create a recommendation card
 * @param {Object} rec - Recommendation data
 * @returns {HTMLElement} Card element
 */
/**
 * Build an explicit copy-to-ticket payload for a signal. No order is placed;
 * this is manual ticket text for the broker UI.
 */
function buildTicketText(rec, { staged = false } = {}) {
    const wd = rec.wheel_decision || {};
    const action = getSignalType(rec) === 'covered_call' ? 'SELL TO OPEN COVERED CALL' : 'SELL TO OPEN CSP';
    const optionType = rec.option_type || wd.option_type || '';
    const ticker = rec.ticker || '?';
    const expiry = (rec.expiration || '').replace(/-/g, '');
    const strike = rec.strike != null ? Number(rec.strike).toFixed(2) : '?';
    const qty = Number(rec.recommended_contracts || 0);
    const limit = rec.limit_target_per_contract != null
        ? (Number(rec.limit_target_per_contract) / 100).toFixed(2)
        : (rec.mid_price != null ? Number(rec.mid_price).toFixed(2) : '?');
    const premium = rec.bid_premium_per_contract != null
        ? Number(rec.bid_premium_per_contract).toFixed(2)
        : (rec.premium_per_contract != null ? Number(rec.premium_per_contract).toFixed(2) : '?');
    const dte = rec.dte != null ? rec.dte : '?';
    const cashRequired = rec.cash_required != null
        ? 'Cash required: $' + Number(rec.cash_required).toFixed(2)
        : (rec.strike != null ? 'Cash required: $' + (Number(rec.strike) * 100).toFixed(2) : '');
    const sourceInfo = getDataSourceInfo(rec);
    const chainSource = rec.chain_source || wd.chain_source || 'moomoo';
    const freshness = sourceInfo.freshness || 'unknown age';
    const lines = [
        action + ' — ' + ticker,
        optionType + ' ' + ticker + ' ' + expiry + ' ' + strike + ' x' + qty,
        'Limit $' + limit + ' (midpoint, not guaranteed) — executable bid $' + premium + '/contract, DTE ' + dte,
        cashRequired,
        'Source: ' + chainSource + ' (Moomoo) — quote ' + freshness,
    ];
    if (staged) {
        lines.push('STAGED FOR US MARKET OPEN — premium is the last broker quote, NOT live; verify the live quote before placing.');
    }
    const eventTier = rec.event_tier || wd.event_tier || '';
    if (eventTier === 'event_unknown') {
        lines.push('EVENT RISK: earnings status unknown — verify the earnings date before placing.');
    } else if (eventTier === 'earnings_before_expiry') {
        lines.push('EVENT RISK: earnings before expiry — high risk, confirm before placing.');
    }
    return lines.filter(Boolean).join('\n');
}

async function copyTicket(rec, btn) {
    const staged = !Boolean(signalsData?.tradeable);
    const text = buildTicketText(rec, { staged });
    const original = btn.innerHTML;
    try {
        await navigator.clipboard.writeText(text);
        btn.innerHTML = '<i class="bi bi-check-circle"></i> Copied';
        btn.classList.add('btn-success');
    } catch (err) {
        console.error('Clipboard failed:', err);
        btn.innerHTML = '<i class="bi bi-x-circle"></i> Copy failed';
        btn.classList.add('btn-danger');
    }
    setTimeout(() => {
        btn.innerHTML = original;
        btn.classList.remove('btn-success', 'btn-danger');
    }, 2000);
}

function createRecommendationCard(rec, rankedNeighbor = null) {
    const template = document.getElementById('recommendation-card-template');
    if (!template) {
        throw new Error('Recommendation card template is missing');
    }

    const clone = template.content.cloneNode(true);
    const card = clone.querySelector('.recommendation-card');
    if (!card) {
        throw new Error('Recommendation card root is missing');
    }
    
    // Rank badge (icon + text label)
    const rankInfo = getRankBadge(rec.rank);
    const rankBadge = clone.querySelector('.rank-badge');
    rankBadge.textContent = rankInfo.icon + ' ' + rankInfo.label;
    rankBadge.classList.add(rankInfo.class);
    
    // Ticker
    clone.querySelector('.ticker-badge').textContent = rec.ticker;
    
    // Signal type badge
    const signalType = rec.signal_type || (rec.option_type === 'CALL' ? 'covered_call' : 'csp');
    const signalTypeBadge = clone.querySelector('.signal-type-badge');
    const signalLabels = { csp: 'CSP', covered_call: 'Covered Call', call: 'Call', put: 'Put' };
    const signalColors = { csp: 'bg-danger', covered_call: 'bg-success', call: 'bg-info', put: 'bg-warning text-dark' };
    signalTypeBadge.textContent = signalLabels[signalType] || signalType;
    addClassTokens(signalTypeBadge, signalColors[signalType] || 'bg-secondary');
    
    // Option type badge
    const optionTypeBadge = clone.querySelector('.option-type-badge');
    optionTypeBadge.textContent = rec.option_type;
    optionTypeBadge.classList.add(rec.option_type === 'CALL' ? 'bg-success' : 'bg-danger');
    
    // Strike price
    clone.querySelector('.strike-price').textContent = rec.strike != null ? `$${rec.strike.toFixed(2)}` : 'N/A';
    
    // Expiration
    clone.querySelector('.expiration-date').textContent = rec.expiration ? formatExpiration(rec.expiration) : 'N/A';
    
    // DTE badge
    const dteBadge = clone.querySelector('.dte-badge');
    dteBadge.textContent = rec.dte != null ? `${rec.dte} DTE` : 'N/A DTE';
    
    // Premium velocity — headlining metric
    const velocityEl = clone.querySelector('.premium-velocity');
    const dte = rec.dte;
    const dailyVelocity = rec.premium_velocity_per_day != null
        ? Number(rec.premium_velocity_per_day)
        : null;
    let velocityDisplay = dailyVelocity != null && dailyVelocity > 0
        ? '$' + dailyVelocity.toFixed(2) + ' / day'
        : 'N/A';
    let velocityPositive = dailyVelocity != null && dailyVelocity > 0;
    velocityEl.textContent = velocityDisplay;
    velocityEl.classList.add(velocityPositive ? 'text-success' : 'text-muted');

    // Premium evidence: bid is executable; midpoint is a separate target.
    clone.querySelector('.premium-amount').textContent = rec.bid_premium_per_contract != null
        ? formatCurrency(Number(rec.bid_premium_per_contract))
        : (rec.premium_per_contract != null ? formatCurrency(rec.premium_per_contract) : 'N/A');
    const limitTargetEl = clone.querySelector('.limit-target');
    if (limitTargetEl) {
        limitTargetEl.textContent = rec.limit_target_per_contract != null
            ? formatCurrency(Number(rec.limit_target_per_contract) / 100)
            : 'N/A';
    }
    const liquidityEl = clone.querySelector('.liquidity-evidence');
    if (liquidityEl) {
        const spread = rec.wheel_decision?.spread_pct ?? rec.spread_pct;
        const oi = rec.open_interest;
        const volume = rec.volume;
        liquidityEl.textContent = `${spread != null ? `${Number(spread).toFixed(1)}%` : 'N/A'} / ${oi ?? 'N/A'} / ${volume ?? 'N/A'}`;
    }
    const quantityEl = clone.querySelector('.quantity-evidence');
    if (quantityEl) {
        const recommended = Number(rec.recommended_contracts || 0);
        const maximum = Number(rec.max_contracts || 0);
        quantityEl.textContent = `${recommended} recommended / ${maximum} max`;
    }
    
    // Annualized return
    const annualizedEl = clone.querySelector('.annualized-return');
    annualizedEl.textContent = rec.annualized_return != null ? `${rec.annualized_return.toFixed(1)}%` : 'N/A';
    annualizedEl.classList.add(rec.annualized_return != null && rec.annualized_return > 0 ? 'text-success' : 'text-danger');
    
    // Score badge (secondary — premium velocity is the primary ranking metric)
    const scoreBadge = clone.querySelector('.score-badge');
    scoreBadge.textContent = rec.score != null ? `Score: ${rec.score.toFixed(1)}` : 'Score: N/A';
    addClassTokens(scoreBadge, getScoreColorClass(rec.score));

    // Explicit quality/event tiers are the actionability explanation.
    const tierBadge = clone.querySelector('.underlying-quality-badge');
    const qualityTier = rec.quality_tier || rec.wheel_decision?.quality_tier || 'marginal';
    const eventTier = rec.event_tier || rec.wheel_decision?.event_tier || 'event_unknown';
    if (tierBadge) {
        tierBadge.textContent = `${qualityTier} · ${eventTier.replaceAll('_', ' ')}`;
        addClassTokens(tierBadge, qualityTier === 'qualified' ? 'bg-success' : 'bg-warning text-dark');
        tierBadge.classList.remove('d-none');
    }

    // Confidence badge
    const confidenceBadge = clone.querySelector('.confidence-badge');
    const confidence = rec.confidence ?? rec.confidence_score ?? rec.wheel_decision?.confidence_score ?? 100;
    const ci = getConfidenceBadge(confidence);
    confidenceBadge.textContent = ci.label;
    addClassTokens(confidenceBadge, ci.class);

    // Research-only badge
    const researchOnlyBadge = clone.querySelector('.research-only-badge');
    if (rec.research_only) {
        researchOnlyBadge.textContent = 'Research only';
        addClassTokens(researchOnlyBadge, 'bg-secondary');
        researchOnlyBadge.classList.remove('d-none');
    }

    // Data source + freshness
    const sourceEl = clone.querySelector('.signal-data-source');
    const sourceInfo = getDataSourceInfo(rec);
    const sourceBadges = sourceInfo.sources.map((item) => {
        const label = normalizeSourceLabel(item.value);
        const badgeClass = sourceBadgeClass(item.value);
        return `<span class="badge ${badgeClass} me-1" title="${escapeHtml(item.label)} source">${escapeHtml(item.label)}: ${escapeHtml(label)}</span>`;
    }).join('');
    const freshnessClass = sourceInfo.freshnessClass || 'text-muted';
    const freshnessHtml = sourceInfo.freshness ? `<span class="badge bg-light text-dark border ${freshnessClass ? 'ms-1' : ''}" title="Data freshness">${escapeHtml(sourceInfo.freshness)}</span>` : '';
    sourceEl.innerHTML = `<i class="bi ${sourceInfo.icon}"></i> ${sourceBadges}${freshnessHtml}`;
    
    const overlayEl = clone.querySelector('.signal-overlay');
    const overlayBadge = overlayEl?.querySelector('.signal-overlay__badge');
    const overlaySummary = overlayEl?.querySelector('.signal-overlay__summary');
    const overlayInfo = getOverlayBadgeInfo(rec);
    if (overlayEl && overlayBadge && overlaySummary && overlayInfo) {
        overlayEl.classList.remove('d-none');
        overlayBadge.textContent = overlayInfo.label;
        addClassTokens(overlayBadge, overlayInfo.class);
        overlaySummary.textContent = overlayInfo.summary || 'Multi-dimensional signal overlay';
        overlayEl.title = overlayInfo.title || overlaySummary.textContent;
    }

    // Warnings
    const warningsEl = clone.querySelector('.recommendation-warnings');
    if (rec.warnings && rec.warnings.length > 0) {
        const criticalWarnings = rec.warnings.filter(w => 
            w.includes('EARNINGS TODAY') || w.includes('extreme risk')
        );
        const otherWarnings = rec.warnings.filter(w => 
            !w.includes('EARNINGS TODAY') && !w.includes('extreme risk')
        );
        
        let warningHtml = '';
        if (criticalWarnings.length > 0) {
            warningHtml += `<div class="text-danger fw-bold"><i class="bi bi-exclamation-triangle-fill"></i> ${escapeHtml(criticalWarnings[0])}</div>`;
        }
        if (otherWarnings.length > 0) {
            warningHtml += `<div><i class="bi bi-exclamation-circle"></i> ${escapeHtml(otherWarnings.slice(0, 2).join(' • '))}</div>`;
        }
        warningsEl.innerHTML = warningHtml;
    } else {
        warningsEl.innerHTML = '<span class="text-success"><i class="bi bi-check-circle"></i> No warnings</span>';
    }

    // Missing-risk penalty: unknown earnings/risk metadata ranks below known-risk.
    const riskBadge = clone.querySelector('.missing-risk-badge');
    if (riskBadge && rec.days_to_earnings == null && !rec.earnings_date) {
        riskBadge.textContent = 'unknown earnings/risk metadata — ranked below known-risk candidates';
        riskBadge.classList.remove('d-none');
    }

    // Why this winner: backend-provided bid velocity; browser does not rank.
    const whyEl = clone.querySelector('.why-winner');
    if (whyEl) {
        const velocity = dailyVelocity != null ? dailyVelocity.toFixed(2) : null;
        const next = rankedNeighbor;
        if (velocity != null) {
            if (next && next.premium_velocity_per_day != null) {
                const nextVelocity = Number(next.premium_velocity_per_day).toFixed(2);
                whyEl.textContent = `Why this pick: $${velocity}/day premium velocity` +
                    (parseFloat(velocity) > parseFloat(nextVelocity)
                        ? ` beats next candidate ($${nextVelocity}/day)`
                        : ` (next candidate $${nextVelocity}/day)`);
            } else {
                whyEl.textContent = `Why this pick: $${velocity}/day premium velocity`;
            }
        } else {
            whyEl.textContent = 'Why this pick: passed all hard gates';
        }
    }
    
    // Copy-to-ticket (explicit; clipboard success/failure feedback)
    const copyBtn = clone.querySelector('.copy-ticket-btn');
    if (copyBtn) {
        const runTradeable = Boolean(signalsData?.tradeable);
        const candidateEligible = Boolean(rec.copy_eligible);
        const quantityReady = Number(rec.recommended_contracts || 0) > 0;
        // Copy is allowed for any copy-eligible candidate regardless of market state.
        // When the run is not live-tradeable (US market closed / stale quote) the ticket is
        // staged for placement at US open, with the premium labelled as the last broker quote.
        const canCopy = candidateEligible && quantityReady;
        copyBtn.disabled = !canCopy;
        if (canCopy) {
            if (runTradeable) {
                copyBtn.title = 'Copy a manual ticket draft (live broker quote)';
                copyBtn.innerHTML = '<i class="bi bi-clipboard"></i> Copy ticket';
            } else {
                copyBtn.title = 'Copy a staged limit ticket — US market closed; verify live quote at open';
                copyBtn.innerHTML = '<i class="bi bi-clock"></i> Stage ticket';
            }
        } else {
            copyBtn.title = 'Review only: candidate is not copy eligible';
            copyBtn.innerHTML = '<i class="bi bi-eye"></i> Review only';
        }
        copyBtn.addEventListener('click', () => {
            if (canCopy) copyTicket(rec, copyBtn);
        });
    }

    // Details
    clone.querySelector('.otm-pct').textContent = rec.otm_pct != null ? `${rec.otm_pct.toFixed(1)}%` : 'N/A';
    clone.querySelector('.delta-value').textContent = rec.delta != null ? rec.delta.toFixed(3) : 'N/A';
    const ivRankEl = clone.querySelector('.iv-rank');
    const ivStatus = rec.iv_status || rec.wheel_decision?.iv_status;
    if (ivStatus === 'unknown' || (rec.iv_rank == null && rec.implied_volatility == null)) {
        ivRankEl.textContent = 'IV unavailable';
        ivRankEl.classList.add('text-muted');
    } else {
        ivRankEl.textContent = rec.iv_rank != null ? `${rec.iv_rank.toFixed(0)}%` : 'N/A';
    }

    // Macro impact line (macro enrichment is out of scope; always neutral)
    const macroImpactEl = clone.querySelector('.macro-impact');
    const macroData = null;
    if (macroImpactEl && rec.macro_multiplier != null && macroData) {
        const multiplier = rec.macro_multiplier;
        const impactPct = ((multiplier - 1.0) * 100).toFixed(0);
        const impactSign = impactPct > 0 ? '+' : '';
        const impactText = multiplier > 1.0 ? 'boost' : multiplier < 1.0 ? 'penalty' : 'neutral';
        const impactColor = multiplier > 1.0 ? 'text-success' : multiplier < 1.0 ? 'text-warning' : 'text-muted';

        macroImpactEl.innerHTML = `
            <span class="${impactColor}">
                Macro: ${impactSign}${impactPct}% ${escapeHtml(impactText)} (${escapeHtml(macroData.rate_regime)}/${escapeHtml(macroData.credit_stress)})
            </span>
        `;
    } else if (macroImpactEl) {
        macroImpactEl.innerHTML = '<span class="text-muted">Macro: N/A</span>';
    }

    // CSP-specific details
    const cspSection = clone.querySelector('.csp-details');
    if (signalType === 'csp' || signalType === 'put') {
        cspSection.classList.remove('d-none');
        const cashReq = rec.cash_required ?? rec.capital_required ?? rec.wheel_decision?.cash_required;
        const cspCash = signalsData?.cash_available_for_csp || 0;
        const cashPct = cashReq != null && cspCash > 0 ? (cashReq / cspCash) * 100 : 0;
        const breakevenBuffer = rec.breakeven_buffer_pct ?? rec.wheel_decision?.breakeven_buffer_pct;
        const expectedMove = rec.expected_move_buffer ?? rec.wheel_decision?.expected_move_buffer;

        clone.querySelector('.csp-cash-required').textContent = cashReq ? formatCurrency(cashReq) : 'N/A';
        clone.querySelector('.csp-cash-pct').textContent = cashPct > 0 ? `${cashPct.toFixed(1)}%` : 'N/A';
        clone.querySelector('.csp-breakeven-buffer').textContent = breakevenBuffer != null ? `${breakevenBuffer.toFixed(1)}%` : 'N/A';
        clone.querySelector('.csp-expected-move').textContent = expectedMove != null ? `${expectedMove.toFixed(1)}%` : 'N/A';
        // Show active preset profile on CSP card (read-only)
        const screenerSummary = clone.querySelector('.csp-screener-summary');
        if (screenerSummary) {
            const cspProfile = signalsData?.preset?.csp_profile_summary;
            if (cspProfile) {
                screenerSummary.textContent = cspProfile;
            } else {
                const sp = signalsData?.preset?.screener_profile;
                if (sp && Object.keys(sp).length > 0) {
                    const parts = [];
                    if (sp.csp_min_otm_pct != null && sp.csp_max_otm_pct != null) parts.push(`OTM ${sp.csp_min_otm_pct}-${sp.csp_max_otm_pct}%`);
                    if (sp.csp_min_dte != null && sp.csp_max_dte != null) parts.push(`DTE ${sp.csp_min_dte}-${sp.csp_max_dte}`);
                    screenerSummary.textContent = parts.length > 0 ? parts.join(', ') : '';
                }
            }
        }
    }

    // Covered call-specific details
    const ccSection = clone.querySelector('.cc-details');
    if (signalType === 'covered_call' || signalType === 'call') {
        ccSection.classList.remove('d-none');
        const ifCalledReturn = rec.wheel_decision?.if_called_return;
        const ifCalledProceeds = rec.strike != null && rec.premium_per_contract != null
            ? (rec.strike * 100) + rec.premium_per_contract : null;
        const avgCost = rec.wheel_decision?.avg_cost || 0;
        const costBasisDist = rec.strike != null && avgCost > 0
            ? ((rec.strike - avgCost) / avgCost) * 100 : null;
        const intent = rec.covered_call_intent || rec.wheel_decision?.covered_call_intent || '';

        clone.querySelector('.cc-if-called-return').textContent = ifCalledReturn != null ? `${ifCalledReturn.toFixed(1)}%` : 'N/A';
        clone.querySelector('.cc-if-called-proceeds').textContent = ifCalledProceeds != null ? formatCurrency(ifCalledProceeds) : 'N/A';
        clone.querySelector('.cc-cost-basis-dist').textContent = costBasisDist != null ? `${costBasisDist.toFixed(1)}%` : 'N/A';
        const intentLabels = { 'income': 'Income', 'profit-taking': 'Profit-taking', 'upside-capping risk': 'Capping upside' };
        clone.querySelector('.cc-intent').textContent = intentLabels[intent] || intent || 'N/A';
    }

    // Score drivers
    const driversSection = clone.querySelector('.score-drivers');
    const scoreDetails = rec.score_details || rec.wheel_decision?.score_details;
    const drivers = extractScoreDrivers(scoreDetails);
    if (drivers.positive.length > 0 || drivers.negative.length > 0) {
        driversSection.classList.remove('d-none');
        if (drivers.positive.length > 0) {
            driversSection.querySelector('.score-drivers__positive').textContent = 'Drivers: ' + drivers.positive.join(' | ');
        }
        if (drivers.negative.length > 0) {
            driversSection.querySelector('.score-drivers__negative').textContent = 'Drags: ' + drivers.negative.join(' | ');
        }
    }

    // Hard blockers (if any leak through — should be empty for surfaced signals)
    const blockersEl = clone.querySelector('.hard-blockers');
    const hardBlockers = rec.hard_blockers || rec.wheel_decision?.hard_blockers || rec.blocked_reason_codes;
    if (hardBlockers && hardBlockers.length > 0) {
        blockersEl.classList.remove('d-none');
        blockersEl.querySelector('.hard-blockers-list').textContent = hardBlockers.join(' | ');
    }

    // Show existing positions if any
    if (rec.existing_position > 0) {
        const detailsEl = clone.querySelector('.recommendation-details');
        const existingDiv = document.createElement('div');
        existingDiv.className = 'd-flex justify-content-between text-info fw-bold mt-1';
        existingDiv.innerHTML = `
            <span><i class="bi bi-check-circle-fill"></i> Existing ${escapeHtml(rec.option_type)}s:</span>
            <span>${rec.existing_position} short</span>
        `;
        detailsEl.appendChild(existingDiv);
    }
    
    // Add card border based on rank
    if (rec.rank === 1) {
        card.classList.add('border-warning');
        card.style.borderWidth = '3px';
    } else if (rec.rank === 2) {
        card.classList.add('border-secondary');
        card.style.borderWidth = '2px';
    } else if (rec.rank === 3) {
        card.classList.add('border-info');
        card.style.borderWidth = '2px';
    }
    
    return clone;
}

/**
 * Show loading state — uses an inline banner at the top of the panel
 * so the existing content (if any) stays visible during refresh.
 */
function showLoading() {
    loadingBannerId = showPanelLoading('top-recommendations-container', 'Analyzing live opportunities...');
}

function getGrowthModeLabel() {
    const growthBanner = document.getElementById('growth-mode-banner');
    return growthBanner && !growthBanner.classList.contains('d-none') ? 'Growth signals' : 'Signals';
}

/**
 * Show generating state — signals are being computed but took longer than expected.
 * Keeps any existing stale content visible beneath the banner.
 */
function showGenerating() {
    _isBackendGenerating = true;
    const label = getGrowthModeLabel();
    if (loadingBannerId) {
        finishPanelLoading(loadingBannerId, `${label} still generating...`);
        loadingBannerId = null;
    }
    const stateEl = document.getElementById('top-recommendations-state');
    // When toggle-triggered, clear stale cards and show explicit recomputing state
    if (_toggleRefreshInProgress) {
        if (cardsContainer) cardsContainer.innerHTML = '';
        if (contentEl) contentEl.classList.add('d-none');
        if (tabContainer) tabContainer.classList.add('d-none');
        if (stateEl) {
            stateEl.innerHTML = '';
            const notice = document.createElement('div');
            notice.className = 'text-warning small text-center py-2';
            notice.innerHTML = `<i class="bi bi-hourglass-split"></i> Recomputing watchlist signals... This may take a moment.`;
            stateEl.appendChild(notice);
        }
        // Schedule retry polling even for toggle-triggered regenerations
        if (!generatingRetryTimer) {
            const nextRetry = scheduleGeneratingRetry();
        }
    } else {
        // Don't hide existing content — stale signals remain visible during auto-refresh
        if (contentEl) contentEl.classList.remove('d-none');
        if (stateEl) {
            const nextRetry = scheduleGeneratingRetry();
            let notice = stateEl.querySelector('[data-generating-notice="true"]');
            if (!notice) {
                notice = document.createElement('div');
                notice.dataset.generatingNotice = 'true';
                notice.className = 'text-warning small text-center py-2';
                stateEl.appendChild(notice);
            }
            if (nextRetry.scheduled) {
                notice.textContent = `Fresh ${label.toLowerCase()} are being computed. Showing prior data while retry ${nextRetry.attempt}/${GENERATING_RETRY_DELAYS_MS.length} runs in ${Math.round(nextRetry.delay / 1000)}s.`;
            } else {
                notice.textContent = `${label} generation is still taking longer than expected. Automatic retries are paused for now; use Refresh after broker data settles.`;
            }
        }
    }
    if (lastUpdatedEl) {
        lastUpdatedEl.textContent = lastUpdatedEl.textContent || 'Generating...';
        lastUpdatedEl.classList.remove('d-none');
    }
}

function clearGeneratingRetry() {
    if (generatingRetryTimer) {
        clearTimeout(generatingRetryTimer);
        generatingRetryTimer = null;
    }
}

function resetGeneratingRetryState() {
    clearGeneratingRetry();
    generatingRetryCount = 0;
}

function scheduleGeneratingRetry() {
    if (generatingRetryTimer) {
        return {
            scheduled: true,
            delay: GENERATING_RETRY_DELAYS_MS[Math.max(0, generatingRetryCount - 1)] || GENERATING_RETRY_DELAYS_MS[0],
            attempt: Math.max(generatingRetryCount, 1),
        };
    }
    if (generatingRetryCount >= GENERATING_RETRY_DELAYS_MS.length) {
        return { scheduled: false, delay: null, attempt: generatingRetryCount };
    }
    const delay = GENERATING_RETRY_DELAYS_MS[generatingRetryCount];
    const attempt = generatingRetryCount + 1;
    generatingRetryCount += 1;
    generatingRetryTimer = setTimeout(() => {
        generatingRetryTimer = null;
        loadTopRecommendations(false);
    }, delay);
    return { scheduled: true, delay, attempt };
}

/**
 * Show content with signals — finish the loading banner
 */
function showContent() {
    _isBackendGenerating = false;
    _toggleRefreshInProgress = false;
    resetGeneratingRetryState();
    if (loadingBannerId) {
        finishPanelLoading(loadingBannerId, 'Recommendations loaded');
        loadingBannerId = null;
    }
    document.getElementById('top-recommendations-state').innerHTML = '';
    document.getElementById('top-recommendations-content').classList.remove('d-none');
}

/**
 * Show empty state
 */
function getDominantBlockedReason(result) {
    const counts = result?.blocked_reason_counts || result?._diagnostics?.blocked_reason_counts || {};
    const countEntries = Object.entries(counts)
        .map(([reason, count]) => ({ reason, count: Number(count) || 0 }))
        .filter(item => item.reason && item.count > 0)
        .sort((a, b) => b.count - a.count);
    if (countEntries.length > 0) {
        return `${countEntries[0].reason.replace(/_/g, ' ')} (${countEntries[0].count})`;
    }

    const blocked = result?.blocked_signals || [];
    if (blocked.length === 0) return '';
    const grouped = new Map();
    blocked.forEach(item => {
        const reason = item.reason_text || item.reason_code || 'Unknown blocker';
        grouped.set(reason, (grouped.get(reason) || 0) + (Number(item.ticker_count || 1) || 1));
    });
    const [reason, count] = Array.from(grouped.entries()).sort((a, b) => b[1] - a[1])[0] || [];
    return reason ? `${reason}${count > 1 ? ` (${count})` : ''}` : '';
}

function getCashDiagnosticSummary(result) {
    const diagnostics = result?.cash_diagnostics || {};
    const raw = diagnostics.raw_summary_fields || {};
    const parts = [];
    if (result?.cash_available_for_csp != null) {
        parts.push(`CSP cash ${formatCurrency(Number(result.cash_available_for_csp) || 0)}`);
    }
    if (diagnostics.cash_available_for_csp_source) {
        parts.push(`source ${diagnostics.cash_available_for_csp_source}`);
    } else if (diagnostics.available_cash_source) {
        parts.push(`available cash source ${diagnostics.available_cash_source}`);
    }
    const rawDetails = ['available_cash', 'cash_available', 'us_cash', 'usd_net_cash_power', 'buying_power', 'excess_liquidity']
        .filter(field => raw[field] != null)
        .slice(0, 3)
        .map(field => `${field}=${formatCurrency(Number(raw[field]) || 0)}`);
    if (rawDetails.length > 0) {
        parts.push(`raw ${rawDetails.join(', ')}`);
    }
    return parts.join('; ');
}

function getScanCoverageSummary(result) {
    const diag = result?._diagnostics || {};
    const parts = [];
    const scanned = diag.scan_tickers_count;
    const cap = diag.scan_tickers_cap;
    if (scanned != null && cap != null) {
        parts.push(`Scanned ${scanned}/${cap} watchlist tickers`);
    } else if (scanned != null) {
        parts.push(`Scanned ${scanned} watchlist tickers`);
    }
    if (diag.watchlist_errors) {
        parts.push(`${diag.watchlist_errors} watchlist errors`);
    }
    if (diag.skipped_csp_count) {
        parts.push(`${diag.skipped_csp_count} CSP skip diagnostics`);
    }
    return parts.join('; ');
}

function showEmpty(result = null) {
    _isBackendGenerating = false;
    _toggleRefreshInProgress = false;
    const label = getGrowthModeLabel();
    resetGeneratingRetryState();
    if (loadingBannerId) {
        finishPanelLoading(loadingBannerId, `No ${label.toLowerCase()}`);
        loadingBannerId = null;
    }
    const details = [];
    const blockedReason = getDominantBlockedReason(result);
    const cashDiagnostic = getCashDiagnosticSummary(result);
    const scanDiagnostic = getScanCoverageSummary(result);
    if (blockedReason) details.push(`Dominant blocker: ${blockedReason}.`);
    if (cashDiagnostic) details.push(cashDiagnostic);
    if (scanDiagnostic) details.push(scanDiagnostic);
    // Planning/partial runs carry an explicit directive (e.g. infeasible scan).
    if (result?.message) details.push(result.message);
    const detailText = details.length > 0 ? ` ${details.join(' ')}` : ' Try refresh or adjust criteria.';
    StateModel.showEmpty('top-recommendations-state', `No ${label.toLowerCase()} available right now.${detailText}`);
    document.getElementById('top-recommendations-content').classList.add('d-none');
}

/**
 * Show error state
 * @param {string} message - Optional custom error message
 */
function showError(message) {
    _isBackendGenerating = false;
    _toggleRefreshInProgress = false;
    resetGeneratingRetryState();
    if (loadingBannerId) {
        failPanelLoading(loadingBannerId, 'Unable to load signals');
        loadingBannerId = null;
    }
    StateModel.showError('top-recommendations-state', message || 'Unable to load signals.', () => loadTopRecommendations());
    document.getElementById('top-recommendations-content').classList.add('d-none');
}

/**
 * Update last updated timestamp
 * @param {string} timestamp - ISO timestamp
 */
function updateTimestamp(timestamp, cacheInfo = null) {
    if (!lastUpdatedEl) return;
    
    if (timestamp) {
        const date = new Date(timestamp);
        const timeStr = date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
        
        let cacheIndicator = '';
        if (cacheInfo) {
            const ageMinutes = Math.floor(cacheInfo.age / 60);
            if (cacheInfo.status === 'HIT' && ageMinutes > 0) {
                cacheIndicator = ` (cached ${ageMinutes}m ago)`;
            } else if (cacheInfo.status === 'STALE') {
                cacheIndicator = ` (refreshing...)`;
            } else if (cacheInfo.status === 'STALE_FALLBACK') {
                cacheIndicator = ` (stale - refresh failed)`;
            }
        }
        
        lastUpdatedEl.textContent = `Updated: ${timeStr}${cacheIndicator}`;
        lastUpdatedEl.classList.remove('d-none');
        
        if (cacheInfo && (cacheInfo.status === 'STALE' || cacheInfo.status === 'STALE_FALLBACK')) {
            lastUpdatedEl.classList.add('text-warning');
        } else {
            lastUpdatedEl.classList.remove('text-warning');
        }
    } else {
        lastUpdatedEl.classList.add('d-none');
    }
}

/**
 * Render a lane's cards into its container
 * @param {string} containerId - DOM id of the lane's card row
 * @param {Array} recs - Array of recommendation objects for this lane
 */
/**
 * Apply the active preset banner and labels (read-only effective values)
 * @param {Object} result - Full API response
 */
function applyPreset(result) {
    const preset = result?.preset;
    document.getElementById('growth-mode-banner')?.classList.remove('d-none');
    document.getElementById('growth-mode-objective').textContent = preset?.label ? `${preset.label} preset` : 'Balanced preset';
    document.getElementById('growth-mode-drawdown').textContent = preset?.version ? `v${preset.version}` : '';
    document.getElementById('top-recs-title').textContent = 'Wheel signals';
    document.getElementById('top-recs-eyebrow').textContent = preset?.label?.toUpperCase() || 'BALANCED';

    // Show CSP screener profile (read-only)
    const cspProfileText = document.getElementById('growth-csp-profile-text');
    if (cspProfileText) {
        if (preset?.csp_profile_summary) {
            cspProfileText.textContent = preset.csp_profile_summary;
            document.getElementById('growth-csp-profile-label').classList.remove('d-none');
        } else {
            const sp = preset?.screener_profile;
            if (sp && Object.keys(sp).length > 0) {
                const parts = [];
                parts.push(`Δ ${sp.csp_target_delta ?? '?'} ±${sp.csp_delta_tolerance ?? '?'}`);
                parts.push(`DTE ${sp.csp_min_dte ?? '?'}-${sp.csp_max_dte ?? '?'} (pref ${sp.csp_preferred_dte ?? '?'})`);
                parts.push(`OTM ${sp.csp_min_otm_pct ?? '?'}-${sp.csp_max_otm_pct ?? '?'}%`);
                if (sp.require_cash_fit) parts.push('cash-fit req.');
                cspProfileText.textContent = parts.join(' | ');
                document.getElementById('growth-csp-profile-label').classList.remove('d-none');
            } else {
                cspProfileText.textContent = '';
            }
        }
    }
}

/**
 * Apply growth mode fields to a recommendation card
 * @param {HTMLElement} card - Card document fragment
 * @param {Object} rec - Recommendation data
 */
function applyGrowthFieldsToCard(card, rec) {
    const growthDetails = card.querySelector('.growth-mode-details');
    if (!growthDetails) return;

    // Growth mode is always-on — growth details are always visible
    growthDetails.classList.remove('d-none');

    // Show the unified contract_score (always growth-weighted)
    const score = rec.score ?? rec.contract_score ?? 0;
    const scoreBadge = card.querySelector('.score-badge');
    if (scoreBadge) {
        scoreBadge.textContent = `Score: ${score.toFixed(1)}`;
        scoreBadge.className = `score-badge badge fs-6 ${getScoreColorClass(score)}`;
        scoreBadge.title = 'Composite score capped at 100';
    }

    const goalImpact = card.querySelector('.growth-impact');
    if (goalImpact) {
        const label = score >= 70 ? 'High' : score >= 50 ? 'Medium' : 'Low';
        goalImpact.textContent = `${label} (${score.toFixed(1)})`;
        goalImpact.className = `fw-semibold ${score >= 70 ? 'text-success' : score >= 50 ? 'text-warning' : 'text-muted'}`;
    }

    const riskBudget = card.querySelector('.risk-budget');
    if (riskBudget && rec.risk_budget_used_pct != null) {
        const pct = rec.risk_budget_used_pct;
        riskBudget.textContent = `${pct.toFixed(1)}%`;
        riskBudget.className = `fw-semibold ${pct > 50 ? 'text-danger' : pct > 25 ? 'text-warning' : 'text-success'}`;
    }

    const stressLoss = card.querySelector('.stress-loss');
    if (stressLoss && rec.stress_loss != null) {
        stressLoss.textContent = `$${rec.stress_loss.toFixed(0)}`;
        stressLoss.className = `fw-semibold ${rec.stress_loss > 5000 ? 'text-danger' : rec.stress_loss > 2000 ? 'text-warning' : 'text-muted'}`;
    }

    const growthRationale = card.querySelector('.growth-rationale');
    if (growthRationale && rec.score_rationale) {
        growthRationale.textContent = rec.score_rationale;
    }

}

/**
 * Render blocked signal diagnostics
 * @param {Array} blocked - Array of blocked signal objects
 */
function renderBlockedSignals(blocked) {
    const section = document.getElementById('blocked-candidates-section');
    if (!section) return;
    if (!blocked || blocked.length === 0) {
        section.classList.add('d-none');
        return;
    }
    section.classList.remove('d-none');
    const grouped = new Map();
    blocked.forEach(item => {
        const key = `${item.reason_code || ''}::${item.reason_text || ''}`;
        const current = grouped.get(key) || { ...item, count: 0, tickers: [] };
        const count = Number(item.ticker_count || 1) || 1;
        current.count += count;
        const ticker = item.ticker || '';
        if (ticker && !current.tickers.includes(ticker)) {
            current.tickers.push(ticker);
        }
        grouped.set(key, current);
    });

    const rows = Array.from(grouped.values());
    if (blockedCountEl) blockedCountEl.textContent = rows.reduce((sum, item) => sum + item.count, 0);
    if (!blockedListEl) return;
    blockedListEl.innerHTML = rows.map(b => `
        <div class="d-flex justify-content-between align-items-center py-1 border-bottom border-light">
            <span class="fw-semibold">${escapeHtml((b.tickers && b.tickers.length > 0 ? b.tickers.join(', ') : b.ticker || '?') + (b.count > 1 ? ` (${b.count} tickers)` : ''))}</span>
            <span class="text-muted small">${escapeHtml(b.reason_text || b.reason_code || 'Unknown')}</span>
        </div>
    `).join('');
}

/**
 * Update buying power indicator
 * @param {Object} result - API response
 */
function updateBuyingPowerIndicator(result) {
    if (!bpIndicator) return;
    const cspCash = result?.cash_available_for_csp;
    const bp = result?.broker_buying_power;
    const reserved = result?.cash_reserved_for_csp;
    if (cspCash != null && cspCash >= 0) {
        bpIndicator.classList.remove('d-none');
        const bpEl = document.getElementById('bp-amount');
        const reservedEl = document.getElementById('bp-reserved');
        const brokerEl = document.getElementById('bp-broker');
        const diagnosticsEl = document.getElementById('bp-diagnostics');
        if (bpEl) bpEl.textContent = formatCurrency(cspCash);
        if (reservedEl) reservedEl.textContent = formatCurrency(reserved || 0);
        if (brokerEl) brokerEl.textContent = formatCurrency(bp || 0);
        if (diagnosticsEl) {
            const diagnostics = result?.cash_diagnostics || {};
            const raw = diagnostics.raw_summary_fields || {};
            const details = [];
            if (diagnostics.available_cash_source) {
                details.push(`available cash source: ${diagnostics.available_cash_source}`);
            }
            if (diagnostics.cash_available_for_csp_source) {
                details.push(`CSP cash source: ${diagnostics.cash_available_for_csp_source}`);
            }
            const rawFields = [
                'us_avl_withdrawal_cash',
                'us_cash',
                'usd_net_cash_power',
                'cash',
                'available_cash',
                'cash_available',
                'buying_power',
                'excess_liquidity',
            ];
            const nonZeroRaw = rawFields
                .filter(field => raw[field] != null && Number(raw[field]) !== 0)
                .map(field => `${field}=${formatCurrency(Number(raw[field]))}`);
            if (nonZeroRaw.length > 0) {
                details.push(`raw: ${nonZeroRaw.join(', ')}`);
            }
            diagnosticsEl.textContent = details.join(' | ');
            diagnosticsEl.classList.toggle('d-none', details.length === 0);
        }
    } else {
        bpIndicator.classList.add('d-none');
    }
}

/**
 * Get the display label for a signal type
 */
function getSignalType(rec) {
    return rec.signal_type || (rec.option_type === 'CALL' ? 'covered_call' : 'csp');
}

/**
 * Render signals filtered by active tab
 */
function renderFilteredSignals() {
    if (!signalsData || !cardsContainer) return;
    const allSignals = signalsData.signals || [];
    const filtered = activeSignalType === 'all'
        ? allSignals
        : allSignals.filter(s => getSignalType(s) === activeSignalType);

    cardsContainer.innerHTML = '';
    filtered.forEach((rec, index) => {
        const card = createRecommendationCard(rec, filtered[index + 1] || null);
        applyGrowthFieldsToCard(card, rec);
        cardsContainer.appendChild(card);
    });

    // Update count display
    if (signalCountDisplay) {
        const typeLabel = activeSignalType === 'all' ? 'Total' : activeSignalType.replace(/_/g, ' ');
        signalCountDisplay.textContent = `${filtered.length} ${typeLabel} signals of ${allSignals.length} total`;
    }

    if (filtered.length > 0) {
        showContent();
    }
}

/**
 * Switch active signal type tab
 * @param {string} signalType - 'all', 'csp', 'covered_call', 'call', 'put'
 */
function switchSignalTab(signalType) {
    activeSignalType = signalType;
    // Update tab button states
    const tabBtns = document.querySelectorAll('#signal-tabs [data-signal-type]');
    tabBtns.forEach(btn => {
        btn.classList.toggle('active', btn.dataset.signalType === signalType);
    });
    renderFilteredSignals();
}

/**
 * Initialize signal-type tab buttons
 */
function initSignalTabs() {
    const tabBtns = document.querySelectorAll('#signal-tabs [data-signal-type]');
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            switchSignalTab(btn.dataset.signalType);
        });
    });
}

/**
 * Show market state badge based on _freshness metadata.
 * @param {Object} freshness - _freshness object from API
 */
function showMarketStateBadge(freshness) {
    const badge = document.getElementById('market-state-badge');
    if (!badge) return;
    if (!freshness || freshness.market_state === 'open') {
        badge.classList.add('d-none');
        return;
    }
    badge.classList.remove('d-none');
    badge.textContent = 'Market closed — data from last session';
    badge.className = 'badge bg-secondary text-white me-1';
    badge.title = `Data generated at: ${freshness.generated_at || 'unknown'}`;
}

/**
 * Render the unified signal payload.
 * @param {Object} result - Full API response with signals
 * @param {string} timestamp - Generation timestamp
 * @param {Object|null} cacheInfo - Cache metadata
 */
function renderRecommendations(result, timestamp, cacheInfo = null) {
    if (!cardsContainer) return;
    signalsData = result;
    
    // Show market state badge (after-hours indicator)
    showMarketStateBadge(result?._freshness);
    
    // Apply growth mode banner
    applyPreset(result);
    
    // Update buying power indicator
    updateBuyingPowerIndicator(result);
    
    // Show tabs and render filtered
    if (result?.signals?.length > 0) {
        if (tabContainer) tabContainer.classList.remove('d-none');
        renderFilteredSignals();
    } else {
        if (tabContainer) tabContainer.classList.add('d-none');
        cardsContainer.innerHTML = '';
    }
    
    // Blocked signal diagnostics
    renderBlockedSignals(result?.blocked_signals || []);
    
    if (result?.signals?.length > 0) {
        updateTimestamp(timestamp, cacheInfo);
    } else {
        showEmpty(result);
        updateTimestamp(null);
    }
}

/**
 * Load top recommendations from API
 */
export async function loadTopRecommendations(manualRefresh = false) {
    if (_isLoading) {
        pendingRecommendationRequest = { manualRefresh };
        console.debug('Top recommendations: already loading, queued latest request');
        return;
    }
    if (manualRefresh) {
        resetGeneratingRetryState();
    }
    _isLoading = true;
    
    if (!container) initElements();
    
    if (!generatingRetryTimer) {
        showLoading();
    }
    
    try {
        if (manualRefresh) {
            await refreshRun();
        }
        const envelope = await fetchRunState();
        const snapshot = envelope.snapshot;
        const attempt = envelope.attempt || {};
        const result = (envelope.error || envelope.generating || envelope.signals)
            ? envelope
            : snapshot
            ? {
                ...snapshot,
                generated_at: snapshot.run?.generated_at,
                preset: snapshot.preset || {},
                market_state: snapshot.run?.market_state,
                status: snapshot.effective_status || snapshot.run?.status,
                tradeable: Boolean(snapshot.tradeable),
                attempt,
            }
            : (['queued', 'refreshing'].includes(attempt.state)
                ? { generating: true, signals: [], attempt }
                : { error: attempt.latest_error || 'No completed wheel run is available.' });

        if (result.error) {
            if (result.generating) showGenerating();
            else if (['opend_unavailable', 'opend_login_required', 'real_account_unavailable'].includes(result.error_code)) {
                showError('OpenD unavailable — login required to view wheel signals.');
            } else showError(result.error);
            return;
        }
        
        applyPreset(result);

        // Backend is still generating — don't replace what's on screen
        if (result.generating) {
            console.debug('Top recommendations: backend is generating fresh data');
            showGenerating();
            return;
        }

        if (result.generation_timed_out) {
            _isBackendGenerating = false;
            _toggleRefreshInProgress = false;
            resetGeneratingRetryState();
            if (loadingBannerId) {
                failPanelLoading(loadingBannerId, 'Signal generation timed out');
                loadingBannerId = null;
            }
            const details = [];
            const blockedReason = getDominantBlockedReason(result);
            const cashDiagnostic = getCashDiagnosticSummary(result);
            const scanDiagnostic = getScanCoverageSummary(result);
            if (blockedReason) details.push(`Dominant blocker: ${blockedReason}.`);
            if (cashDiagnostic) details.push(cashDiagnostic);
            if (scanDiagnostic) details.push(scanDiagnostic);
            const diagText = details.length > 0 ? ` ${details.join(' ')}` : '';
            StateModel.showError(
                'top-recommendations-state',
                result.message || `Signal generation timed out — scan did not finish within the retry budget.${diagText}`,
                () => loadTopRecommendations()
            );
            if (contentEl) contentEl.classList.add('d-none');
            if (tabContainer) tabContainer.classList.add('d-none');
            updateTimestamp(result.generated_at, null);
            return;
        }
        
        signalsData = result;
        
        renderRecommendations(result, result.generated_at, null);
        
    } catch (error) {
        console.error('Error loading top recommendations:', error);
        showError();
    } finally {
        _isLoading = false;
        if (pendingRecommendationRequest) {
            const nextRequest = pendingRecommendationRequest;
            pendingRecommendationRequest = null;
            setTimeout(() => {
                loadTopRecommendations(nextRequest.manualRefresh);
            }, 0);
        }
    }
}

/**
 * Start auto-refresh
 */
function startAutoRefresh() {
    // Clear any existing interval
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
    }
    
    // Set up new interval
    autoRefreshInterval = setInterval(() => {
        if (isVisible) {
            loadTopRecommendations(false);
        }
    }, REFRESH_INTERVAL_MS);
}

/**
 * Stop auto-refresh
 */
function stopAutoRefresh() {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
    }
}

/**
 * Handle visibility change
 */
function handleVisibilityChange() {
    isVisible = !document.hidden;
    
    if (isVisible) {
        // Refresh when tab becomes visible again (in case data is stale)
        loadTopRecommendations(false);
    }
}

/**
 * Preset selector: three versioned presets; effective values are read-only.
 */
let _presetState = null;

export async function initPresetSelector() {
    const buttonsEl = document.getElementById('preset-buttons');
    if (!buttonsEl || buttonsEl.dataset.bound) return;
    buttonsEl.dataset.bound = 'true';
    try {
        const resp = await fetch('/api/settings');
        if (!resp.ok) return;
        _presetState = await resp.json();
        renderPresetSelector();
    } catch (err) {
        console.error('Preset selector failed to load:', err);
    }
}

function renderPresetSelector() {
    const buttonsEl = document.getElementById('preset-buttons');
    const effectiveEl = document.getElementById('preset-effective');
    if (!buttonsEl || !_presetState) return;
    buttonsEl.innerHTML = '';
    const labels = { conservative: 'Conservative', balanced: 'Balanced', aggressive: 'Aggressive' };
    for (const [key, preset] of Object.entries(_presetState.presets || {})) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm ' + (key === _presetState.active ? 'btn-primary' : 'btn-outline-secondary');
        btn.textContent = labels[key] || preset.label || key;
        btn.dataset.preset = key;
        btn.addEventListener('click', () => selectPreset(key));
        buttonsEl.appendChild(btn);
    }
    if (effectiveEl) {
        const eff = _presetState.effective || {};
        effectiveEl.textContent =
            `Effective: DTE ${eff.csp_min_dte}-${eff.csp_max_dte} (pref ${eff.csp_preferred_dte}), ` +
            `delta ${eff.csp_target_delta}±${eff.csp_delta_tolerance}, OTM ${eff.csp_min_otm_pct}-${eff.csp_max_otm_pct}%, ` +
            `min BP $${Math.round(eff.min_csp_buying_power || 0)}, max ${eff.max_buying_power_pct_per_csp || 0}% BP per CSP — read-only`;
    }
}

async function selectPreset(key) {
    try {
        const resp = await fetch('/api/settings/preset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ preset: key }),
        });
        if (!resp.ok) return;
        _presetState = await resp.json();
        renderPresetSelector();
        loadTopRecommendations(true);
    } catch (err) {
        console.error('Preset selection failed:', err);
    }
}

/**
 * Set up event listeners
 */
function setupEventListeners() {
    if (listenersBound) return;
    listenersBound = true;

    // Refresh button
    const refreshBtn = document.getElementById('refresh-top-recommendations');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            loadTopRecommendations(true);
        });
    }
    
    // Retry button
    const retryBtn = document.getElementById('retry-top-recommendations');
    if (retryBtn) {
        retryBtn.addEventListener('click', () => {
            loadTopRecommendations(false);
        });
    }

    // Preset selector
    initPresetSelector();

    // Signal-type tabs
    initSignalTabs();
    
    // Visibility change
    document.addEventListener('visibilitychange', handleVisibilityChange);
}

/**
 * Initialize the top recommendations module
 */
export function initializeTopRecommendations() {
    initElements();
    if (!container) return;
    setupEventListeners();
    
    // Initial load
    if (!isInitialized) {
        isInitialized = true;
        loadTopRecommendations();
    }
    
    // Start auto-refresh
    startAutoRefresh();
}

/**
 * Cleanup function (call when leaving page)
 */
export function cleanupTopRecommendations() {
    stopAutoRefresh();
    clearGeneratingRetry();
    document.removeEventListener('visibilitychange', handleVisibilityChange);
    signalsData = null;
    activeSignalType = 'all';
    pendingRecommendationRequest = null;
    isVisible = true;
    listenersBound = false;
    isInitialized = false;
    _isLoading = false;
    loadingBannerId = null;
    generatingRetryCount = 0;
    _isBackendGenerating = false;
    _toggleRefreshInProgress = false;
}

export function isBackendGenerating() { return _isBackendGenerating; }
