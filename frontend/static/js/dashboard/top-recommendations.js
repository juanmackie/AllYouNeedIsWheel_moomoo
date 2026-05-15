/**
 * Top Recommendations Module
 * Displays the highest-scoring option opportunities with auto-refresh
 */
import { fetchTopRecommendations, saveOptionOrder, executeOrder } from './api.js';
import { showAlert } from '../utils/alerts.js';
import { formatCurrency, formatPercent } from '../utils/formatters.js';
import { getMacroData } from './macro.js';
import StateModel from '../utils/state-model.js';

// Module state
let recommendationsData = null;
let autoRefreshInterval = null;
let isVisible = true;
const REFRESH_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes

// DOM Elements (initialized lazily)
let container, contentEl, cardsContainer, lastUpdatedEl;

/**
 * Initialize DOM element references
 */
function initElements() {
    container = document.getElementById('top-recommendations-container');
    contentEl = document.getElementById('top-recommendations-content');
    cardsContainer = document.getElementById('top-recommendations-cards');
    lastUpdatedEl = document.getElementById('top-recs-last-updated');
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
    
    // Action buttons
    const addOrderBtn = clone.querySelector('.add-order-btn');
    const executeNowBtn = clone.querySelector('.execute-now-btn');
    
    addOrderBtn.addEventListener('click', () => handleAddOrder(rec));
    executeNowBtn.addEventListener('click', () => handleExecuteNow(rec));
    
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
 * Handle "Add Order" button click
 * @param {Object} rec - Recommendation data
 */
async function handleAddOrder(rec) {
    try {
        const orderData = {
            ticker: rec.ticker,
            option_type: rec.option_type,
            strike: rec.strike,
            expiration: rec.expiration,
            action: 'SELL',
            quantity: rec.option_type === 'CALL' ? rec.max_contracts : 1,
            order_type: 'LIMIT',
            limit_price: rec.mid_price,
            bid: rec.bid,
            ask: rec.ask,
            last: rec.mid_price
        };
        
        const result = await saveOptionOrder(orderData);
        
        if (result && result.order_id) {
            const strikeStr = rec.strike != null ? `$${rec.strike.toFixed(2)}` : 'N/A';
            showAlert(`Order added for ${rec.ticker} ${rec.option_type} ${strikeStr}. Check Pending Orders to execute.`, 'success');
        } else {
            showAlert('Failed to add order. Please try again.', 'danger');
        }
    } catch (error) {
        console.error('Error adding order:', error);
        showAlert(`Error adding order: ${error.message}`, 'danger');
    }
}

/**
 * Handle "Execute Now" button click
 * @param {Object} rec - Recommendation data
 */
async function handleExecuteNow(rec) {
    try {
        // First, create the order
        const orderData = {
            ticker: rec.ticker,
            option_type: rec.option_type,
            strike: rec.strike,
            expiration: rec.expiration,
            action: 'SELL',
            quantity: rec.option_type === 'CALL' ? rec.max_contracts : 1,
            order_type: 'LIMIT',
            limit_price: rec.mid_price,
            bid: rec.bid,
            ask: rec.ask,
            last: rec.mid_price
        };
        
        const saveResult = await saveOptionOrder(orderData);
        
        if (!saveResult || !saveResult.order_id) {
            showAlert('Failed to create order. Please try again.', 'danger');
            return;
        }
        
        // Then execute it immediately
        const executeResult = await executeOrder(saveResult.order_id);
        
        if (executeResult && executeResult.success) {
            const strikeStr = rec.strike != null ? `$${rec.strike.toFixed(2)}` : 'N/A';
            showAlert(`Order executed successfully for ${rec.ticker} ${rec.option_type} ${strikeStr}!`, 'success');
        } else {
            showAlert(`Order created but execution failed: ${executeResult?.error || 'Unknown error'}. Check Pending Orders.`, 'warning');
        }
    } catch (error) {
        console.error('Error executing order:', error);
        showAlert(`Error executing order: ${error.message}`, 'danger');
    }
}

/**
 * Show loading state
 */
function showLoading() {
    StateModel.showLoading('top-recommendations-state', 'Analyzing live opportunities...');
    document.getElementById('top-recommendations-content').classList.add('d-none');
}

/**
 * Show content with recommendations
 */
function showContent() {
    document.getElementById('top-recommendations-state').innerHTML = '';
    document.getElementById('top-recommendations-content').classList.remove('d-none');
}

/**
 * Show empty state
 */
function showEmpty() {
    StateModel.showEmpty('top-recommendations-state', 'No recommendations available right now. Check back after market open or add positions to your portfolio.');
    document.getElementById('top-recommendations-content').classList.add('d-none');
}

/**
 * Show error state
 */
function showError() {
    StateModel.showError('top-recommendations-state', 'Unable to load recommendations.', () => loadTopRecommendations());
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
function renderLaneCards(containerId, recs) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '';
    if (!recs || recs.length === 0) return;

    recs.forEach(rec => {
        const card = createRecommendationCard(rec);
        container.appendChild(card);
    });
}

/**
 * Show or hide a lane section
 * @param {string} sectionId - DOM id of the lane section wrapper
 * @param {boolean} visible - whether to show
 */
function setLaneVisible(sectionId, visible) {
    const el = document.getElementById(sectionId);
    if (el) {
        if (visible) el.classList.remove('d-none');
        else el.classList.add('d-none');
    }
}

/**
 * Render recommendations from lanes structure (v2) or legacy list
 * @param {Object} result - Full API response with lanes + recommendations
 * @param {string} timestamp - Generation timestamp
 * @param {Object|null} cacheInfo - Cache metadata
 */
function renderRecommendations(result, timestamp, cacheInfo = null) {
    if (!cardsContainer) return;
    
    // Clear all lane containers and the legacy fallback
    cardsContainer.innerHTML = '';

    const ccRecs = result?.lanes?.covered_calls?.recommendations || [];
    const wcRecs = result?.lanes?.watchlist_csp?.recommendations || [];
    const hasLanes = ccRecs.length > 0 || wcRecs.length > 0;
    
    if (hasLanes) {
        renderLaneCards('lanes-covered-calls-cards', ccRecs);
        renderLaneCards('lanes-watchlist-csp-cards', wcRecs);
        setLaneVisible('lanes-covered-calls-section', ccRecs.length > 0);
        setLaneVisible('lanes-watchlist-csp-section', wcRecs.length > 0);

        showContent();
        updateTimestamp(timestamp, cacheInfo);
        return;
    }
    
    // Legacy fallback: render recommendations list directly
    setLaneVisible('lanes-covered-calls-section', false);
    setLaneVisible('lanes-watchlist-csp-section', false);
    
    const recs = (result && result.recommendations) || [];
    
    if (!recs || recs.length === 0) {
        showEmpty();
        updateTimestamp(null);
        return;
    }
    
    recs.forEach(rec => {
        const card = createRecommendationCard(rec);
        cardsContainer.appendChild(card);
    });
    
    showContent();
    updateTimestamp(timestamp, cacheInfo);
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
        
        recommendationsData = result;
        
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
