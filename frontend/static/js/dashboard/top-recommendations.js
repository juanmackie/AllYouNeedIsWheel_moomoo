/**
 * Top Recommendations Module
 * Displays the highest-scoring option opportunities with auto-refresh
 */
import { fetchTopRecommendations, saveOptionOrder, executeOrder } from './api.js';
import { showAlert } from '../utils/alerts.js';
import { formatCurrency, formatPercent } from '../utils/formatters.js';

// Module state
let recommendationsData = null;
let autoRefreshInterval = null;
let isVisible = true;
const REFRESH_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes

// DOM Elements (initialized lazily)
let container, loadingEl, contentEl, emptyEl, errorEl, cardsContainer, lastUpdatedEl;

/**
 * Initialize DOM element references
 */
function initElements() {
    container = document.getElementById('top-recommendations-container');
    loadingEl = document.getElementById('top-recommendations-loading');
    contentEl = document.getElementById('top-recommendations-content');
    emptyEl = document.getElementById('top-recommendations-empty');
    errorEl = document.getElementById('top-recommendations-error');
    cardsContainer = document.getElementById('top-recommendations-cards');
    lastUpdatedEl = document.getElementById('top-recs-last-updated');
}

/**
 * Get heatmap color class based on score
 * @param {number} score - Option score (0-100)
 * @returns {string} CSS class
 */
function getScoreColorClass(score) {
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
    const clone = template.content.cloneNode(true);
    const card = clone.querySelector('.card');
    
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
    clone.querySelector('.strike-price').textContent = `$${rec.strike.toFixed(2)}`;
    
    // Expiration
    clone.querySelector('.expiration-date').textContent = formatExpiration(rec.expiration);
    
    // DTE badge
    const dteBadge = clone.querySelector('.dte-badge');
    dteBadge.textContent = `${rec.dte} DTE`;
    
    // Premium
    clone.querySelector('.premium-amount').textContent = formatCurrency(rec.premium_per_contract);
    
    // Annualized return
    const annualizedEl = clone.querySelector('.annualized-return');
    annualizedEl.textContent = `${rec.annualized_return.toFixed(1)}%`;
    annualizedEl.classList.add(rec.annualized_return > 0 ? 'text-success' : 'text-danger');
    
    // Score badge
    const scoreBadge = clone.querySelector('.score-badge');
    scoreBadge.textContent = `Score: ${rec.score.toFixed(1)}`;
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
    clone.querySelector('.otm-pct').textContent = `${rec.otm_pct.toFixed(1)}%`;
    clone.querySelector('.delta-value').textContent = rec.delta.toFixed(3);
    clone.querySelector('.iv-rank').textContent = `${rec.iv_rank.toFixed(0)}%`;
    
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
            showAlert(`Order added for ${rec.ticker} ${rec.option_type} $${rec.strike.toFixed(2)}. Check Pending Orders to execute.`, 'success');
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
            showAlert(`Order executed successfully for ${rec.ticker} ${rec.option_type} $${rec.strike.toFixed(2)}!`, 'success');
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
    loadingEl.classList.remove('d-none');
    contentEl.classList.add('d-none');
    emptyEl.classList.add('d-none');
    errorEl.classList.add('d-none');
}

/**
 * Show content with recommendations
 */
function showContent() {
    loadingEl.classList.add('d-none');
    contentEl.classList.remove('d-none');
    emptyEl.classList.add('d-none');
    errorEl.classList.add('d-none');
}

/**
 * Show empty state
 */
function showEmpty() {
    loadingEl.classList.add('d-none');
    contentEl.classList.add('d-none');
    emptyEl.classList.remove('d-none');
    errorEl.classList.add('d-none');
}

/**
 * Show error state
 */
function showError() {
    loadingEl.classList.add('d-none');
    contentEl.classList.add('d-none');
    emptyEl.classList.add('d-none');
    errorEl.classList.remove('d-none');
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
        
        // Add cache status indicator
        let cacheIndicator = '';
        if (cacheInfo) {
            const ageMinutes = Math.floor(cacheInfo.age / 60);
            if (cacheInfo.status === 'HIT' && ageMinutes > 0) {
                cacheIndicator = ` (cached ${ageMinutes}m ago)`;
            } else if (cacheInfo.status === 'STALE') {
                cacheIndicator = ` (refreshing...)`;
            }
        }
        
        lastUpdatedEl.textContent = `Updated: ${timeStr}${cacheIndicator}`;
        lastUpdatedEl.classList.remove('d-none');
        
        // Add visual indicator for stale data
        if (cacheInfo && cacheInfo.status === 'STALE') {
            lastUpdatedEl.classList.add('text-warning');
        } else {
            lastUpdatedEl.classList.remove('text-warning');
        }
    } else {
        lastUpdatedEl.classList.add('d-none');
    }
}

/**
 * Render recommendations
 * @param {Array} recommendations - Array of recommendation objects
 * @param {string} timestamp - Generation timestamp
 */
function renderRecommendations(recommendations, timestamp, cacheInfo = null) {
    if (!cardsContainer) return;
    
    // Clear existing cards
    cardsContainer.innerHTML = '';
    
    if (!recommendations || recommendations.length === 0) {
        showEmpty();
        updateTimestamp(null);
        return;
    }
    
    // Create and append cards
    recommendations.forEach(rec => {
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
        
        // Extract cache info from response
        const cacheInfo = result._cacheInfo || null;
        
        renderRecommendations(result.recommendations, result.generated_at, cacheInfo);
        
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
    
    console.log('Top recommendations module initialized');
}

/**
 * Cleanup function (call when leaving page)
 */
export function cleanupTopRecommendations() {
    stopAutoRefresh();
    document.removeEventListener('visibilitychange', handleVisibilityChange);
}
