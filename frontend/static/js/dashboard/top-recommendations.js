/**
 * Top Recommendations Module
 * Displays the highest-scoring option opportunities with auto-refresh
 */
import { fetchTopRecommendations } from './api.js';
import { formatCurrency, formatPercent } from '../utils/formatters.js';
import { getMacroData } from './macro.js';
import { showPanelLoading, finishPanelLoading, failPanelLoading } from './options-table-rendering.js';
import StateModel from '../utils/state-model.js';

// Module state
let signalsData = null;
let autoRefreshInterval = null;
let isVisible = true;
const REFRESH_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes
let loadingBannerId = null;

// DOM Elements (initialized lazily)
let container, contentEl, cardsContainer, lastUpdatedEl;
let blockedListEl, blockedCountEl, bpIndicator;

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
}

/**
 * Get heatmap color class based on score
 * @param {number} score - Option score (0-100)
 * @returns {string} CSS class
 */
function getScoreColorClass(score) {
    if (score == null) return 'bg-secondary';
    if (score >= 90) return 'bg-success';
    if (score >= 80) return 'bg-success';
    if (score >= 70) return 'bg-info';
    if (score >= 60) return 'bg-warning';
    if (score >= 50) return 'bg-warning';
    return 'bg-danger';
}

/**
 * Get rank badge class and label
 * @param {number} rank - Rank (1-based)
 * @returns {Object} badge class and label
 */
function getRankBadge(rank) {
    const badges = {
        1: { class: 'rank-gold', icon: '🥇', label: '#1' },
        2: { class: 'rank-silver', icon: '🥈', label: '#2' },
        3: { class: 'rank-bronze', icon: '🥉', label: '#3' }
    };
    return badges[rank] || { class: 'rank-standard', icon: `#${rank}`, label: `#${rank}` };
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
function createRecommendationCard(rec) {
    const template = document.getElementById('recommendation-card-template');
    if (!template) {
        throw new Error('Recommendation card template is missing');
    }

    const clone = template.content.cloneNode(true);
    const card = clone.querySelector('.recommendation-card');
    if (!card) {
        throw new Error('Recommendation card root is missing');
    }
    
    // Rank badge
    const rankInfo = getRankBadge(rec.rank);
    const rankBadge = clone.querySelector('.rank-badge');
    rankBadge.textContent = rankInfo.icon;
    rankBadge.classList.add(rankInfo.class);
    
    // Ticker
    clone.querySelector('.ticker-badge').textContent = rec.ticker;
    
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
    
    // Premium
    clone.querySelector('.premium-amount').textContent = rec.premium_per_contract != null ? formatCurrency(rec.premium_per_contract) : 'N/A';
    
    // Annualized return
    const annualizedEl = clone.querySelector('.annualized-return');
    annualizedEl.textContent = rec.annualized_return != null ? `${rec.annualized_return.toFixed(1)}%` : 'N/A';
    annualizedEl.classList.add(rec.annualized_return != null && rec.annualized_return > 0 ? 'text-success' : 'text-danger');
    
    // Score badge
    const scoreBadge = clone.querySelector('.score-badge');
    scoreBadge.textContent = rec.score != null ? `Score: ${rec.score.toFixed(1)}` : 'Score: N/A';
    scoreBadge.classList.add(getScoreColorClass(rec.score));
    
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
            warningHtml += `<div class="text-danger fw-bold"><i class="bi bi-exclamation-triangle-fill"></i> ${criticalWarnings[0]}</div>`;
        }
        if (otherWarnings.length > 0) {
            warningHtml += `<div><i class="bi bi-exclamation-circle"></i> ${otherWarnings.slice(0, 2).join(' • ')}</div>`;
        }
        warningsEl.innerHTML = warningHtml;
    } else {
        warningsEl.innerHTML = '<span class="text-success"><i class="bi bi-check-circle"></i> No warnings</span>';
    }
    
    // Details
    clone.querySelector('.otm-pct').textContent = rec.otm_pct != null ? `${rec.otm_pct.toFixed(1)}%` : 'N/A';
    clone.querySelector('.delta-value').textContent = rec.delta != null ? rec.delta.toFixed(3) : 'N/A';
    clone.querySelector('.iv-rank').textContent = rec.iv_rank != null ? `${rec.iv_rank.toFixed(0)}%` : 'N/A';

    // Macro impact line
    const macroImpactEl = clone.querySelector('.macro-impact');
    const macroData = getMacroData();
    if (macroImpactEl && rec.macro_multiplier != null && macroData) {
        const multiplier = rec.macro_multiplier;
        const impactPct = ((multiplier - 1.0) * 100).toFixed(0);
        const impactSign = impactPct > 0 ? '+' : '';
        const impactText = multiplier > 1.0 ? 'boost' : multiplier < 1.0 ? 'penalty' : 'neutral';
        const impactColor = multiplier > 1.0 ? 'text-success' : multiplier < 1.0 ? 'text-warning' : 'text-muted';

        macroImpactEl.innerHTML = `
            <span class="${impactColor}">
                Macro: ${impactSign}${impactPct}% ${impactText} (${macroData.rate_regime}/${macroData.credit_stress})
            </span>
        `;
    } else if (macroImpactEl) {
        macroImpactEl.innerHTML = '<span class="text-muted">Macro: N/A</span>';
    }

    // Show existing positions if any
    if (rec.existing_position > 0) {
        const detailsEl = clone.querySelector('.recommendation-details');
        const existingDiv = document.createElement('div');
        existingDiv.className = 'd-flex justify-content-between text-info fw-bold mt-1';
        existingDiv.innerHTML = `
            <span><i class="bi bi-check-circle-fill"></i> Existing ${rec.option_type}s:</span>
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

/**
 * Show content with signals — finish the loading banner
 */
function showContent() {
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
function showEmpty() {
    if (loadingBannerId) {
        finishPanelLoading(loadingBannerId, 'No signals');
        loadingBannerId = null;
    }
    StateModel.showEmpty('top-recommendations-state', 'No signals available right now. Check back after market open or add positions to your portfolio.');
    document.getElementById('top-recommendations-content').classList.add('d-none');
}

/**
 * Show error state
 */
function showError() {
    if (loadingBannerId) {
        failPanelLoading(loadingBannerId, 'Unable to load signals');
        loadingBannerId = null;
    }
    StateModel.showError('top-recommendations-state', 'Unable to load signals.', () => loadTopRecommendations());
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
 * Apply growth mode banner and labels
 * @param {Object} result - Full API response
 */
function applyGrowthMode(result) {
    const growthMode = result?.growth_mode;
    // Growth mode is always-on — always show the growth banner
    document.getElementById('growth-mode-banner')?.classList.remove('d-none');
    document.getElementById('growth-mode-objective').textContent = growthMode?.objective?.replace(/_/g, ' ') || 'time to 2x';
    document.getElementById('growth-mode-drawdown').textContent = `${((growthMode?.max_drawdown_pct ?? 0.40) * 100).toFixed(0)}%`;
    document.getElementById('top-recs-title').textContent = 'Growth signals';
    document.getElementById('top-recs-eyebrow').textContent = 'Growth mode';
    document.getElementById('top-recs-desc').textContent = 'Signals ranked by estimated impact on reaching your 2x account target.';

    // Show CSP screener profile
    const cspProfileText = document.getElementById('growth-csp-profile-text');
    if (cspProfileText) {
        if (growthMode.csp_profile_summary) {
            cspProfileText.textContent = growthMode.csp_profile_summary;
            document.getElementById('growth-csp-profile-label').classList.remove('d-none');
        } else if (growthMode.screener_profile && Object.keys(growthMode.screener_profile).length > 0) {
            const sp = growthMode.screener_profile;
            const parts = [];
            parts.push(`Δ ${sp.csp_target_delta ?? '?'} ±${sp.csp_delta_tolerance ?? '?'}`);
            const dtePref = sp.csp_preferred_dte ?? '?';
            const dteMin = sp.csp_min_dte ?? '?';
            const dteMax = sp.csp_max_dte ?? '?';
            parts.push(`DTE ${dteMin}-${dteMax} (pref ${dtePref})`);
            parts.push(`OTM ${sp.csp_default_otm_pct ?? '?'}%`);
            parts.push(`IV ≥${sp.min_iv_rank ?? '?'}%`);
            if (sp.require_cash_fit) parts.push('cash-fit req.');
            cspProfileText.textContent = parts.join(' | ');
            document.getElementById('growth-csp-profile-label').classList.remove('d-none');
        } else {
            cspProfileText.textContent = '';
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
        scoreBadge.className = `score-badge badge fs-6 ${score >= 70 ? 'bg-success' : score >= 50 ? 'bg-warning text-dark' : 'bg-secondary'}`;
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

    // Covered call intent
    if (rec.covered_call_intent) {
        const ccBadge = card.querySelector('.covered-call-intent');
        if (ccBadge) {
            ccBadge.textContent = rec.covered_call_intent;
            ccBadge.classList.remove('d-none');
            if (rec.covered_call_intent === 'upside-capping risk') {
                ccBadge.className = 'badge bg-warning text-dark covered-call-intent';
            } else if (rec.covered_call_intent === 'profit-taking') {
                ccBadge.className = 'badge bg-info covered-call-intent';
            } else {
                ccBadge.className = 'badge bg-secondary covered-call-intent';
            }
        }
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
    if (blockedCountEl) blockedCountEl.textContent = blocked.length;
    if (!blockedListEl) return;
    blockedListEl.innerHTML = blocked.map(b => `
        <div class="d-flex justify-content-between align-items-center py-1 border-bottom border-light">
            <span class="fw-semibold">${b.ticker || '?'}</span>
            <span class="text-muted small">${b.reason_text || b.reason_code || 'Unknown'}</span>
        </div>
    `).join('');
}

/**
 * Update buying power indicator
 * @param {Object} result - API response
 */
function updateBuyingPowerIndicator(result) {
    if (!bpIndicator) return;
    const bp = result?.broker_buying_power;
    const reserved = result?.cash_reserved_for_csp;
    if (bp != null && bp > 0) {
        bpIndicator.classList.remove('d-none');
        const bpEl = document.getElementById('bp-amount');
        const reservedEl = document.getElementById('bp-reserved');
        if (bpEl) bpEl.textContent = formatCurrency(bp);
        if (reservedEl) reservedEl.textContent = formatCurrency(reserved || 0);
    } else {
        bpIndicator.classList.add('d-none');
    }
}

/**
 * Render the unified signal payload.
 * @param {Object} result - Full API response with signals
 * @param {string} timestamp - Generation timestamp
 * @param {Object|null} cacheInfo - Cache metadata
 */
function renderRecommendations(result, timestamp, cacheInfo = null) {
    if (!cardsContainer) return;
    
    // Apply growth mode banner
    applyGrowthMode(result);
    
    // Update buying power indicator
    updateBuyingPowerIndicator(result);
    
    // Clear the grid and render the canonical signal list.
    cardsContainer.innerHTML = '';
    const signals = result?.signals || [];
    signals.forEach(rec => {
        const card = createRecommendationCard(rec);
        applyGrowthFieldsToCard(card, rec);
        cardsContainer.appendChild(card);
    });
    
    // Blocked signal diagnostics
    renderBlockedSignals(result?.blocked_signals || []);
    
    if (signals.length > 0) {
        showContent();
        updateTimestamp(timestamp, cacheInfo);
    } else {
        showEmpty();
        updateTimestamp(null);
    }
}

/**
 * Load top recommendations from API
 */
export async function loadTopRecommendations(manualRefresh = false) {
    if (!container) initElements();
    
    showLoading();
    
    try {
        const result = await fetchTopRecommendations(3, manualRefresh);
        
        if (result.error) {
            console.error('Error loading top recommendations:', result.error);
            showError();
            return;
        }
        
        signalsData = result;
        
        const cacheInfo = result._cache || null;
        
        if (cacheInfo && cacheInfo.cache_status === 'STALE_FALLBACK') {
            renderRecommendations(result, result.generated_at, cacheInfo);
            if (lastUpdatedEl) {
                const errorMsg = cacheInfo.error ? ` (refresh failed: ${cacheInfo.error})` : '';
                lastUpdatedEl.textContent = `Showing cached data${errorMsg}`;
                lastUpdatedEl.classList.add('text-warning');
                lastUpdatedEl.classList.remove('d-none');
            }
            return;
        }
        
        renderRecommendations(result, result.generated_at, cacheInfo);
        
    } catch (error) {
        console.error('Error loading top recommendations:', error);
        showError();
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
            loadTopRecommendations();
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
        loadTopRecommendations();
    }
}

/**
 * Set up event listeners
 */
function setupEventListeners() {
    // Refresh button
    const refreshBtn = document.getElementById('refresh-top-recommendations');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            loadTopRecommendations(true); // manual refresh - bypass cache
        });
    }
    
    // Retry button
    const retryBtn = document.getElementById('retry-top-recommendations');
    if (retryBtn) {
        retryBtn.addEventListener('click', () => {
            loadTopRecommendations();
        });
    }
    
    // Visibility change
    document.addEventListener('visibilitychange', handleVisibilityChange);
}

/**
 * Initialize the top recommendations module
 */
export function initializeTopRecommendations() {
    initElements();
    setupEventListeners();
    
    // Initial load
    loadTopRecommendations();
    
    // Start auto-refresh
    startAutoRefresh();
}

/**
 * Cleanup function (call when leaving page)
 */
export function cleanupTopRecommendations() {
    stopAutoRefresh();
    document.removeEventListener('visibilitychange', handleVisibilityChange);
}
