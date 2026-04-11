/**
 * Macro Regime Module
 * Fetches and displays macro economic regime data from FRED.
 * Updates dashboard with rate environment, credit stress, growth, and inflation context.
 */
import { showAlert } from '../utils/alerts.js';

// Store macro data
let macroData = null;

/**
 * Format regime value for display
 */
function formatRegimeValue(value) {
    if (!value || value === 'unknown') return '--';
    return value.charAt(0).toUpperCase() + value.slice(1);
}

/**
 * Get badge class for regime type
 */
function getRegimeBadgeClass(regime, type) {
    if (!regime || regime === 'unknown') return 'bg-secondary';

    // Rate regimes
    if (type === 'rate') {
        if (regime === 'rising') return 'badge-macro-rising';
        if (regime === 'falling') return 'badge-macro-falling';
        return 'badge-macro-stable';
    }

    // Credit stress
    if (type === 'credit') {
        if (regime === 'low') return 'badge-macro-low';
        if (regime === 'high') return 'badge-macro-high';
        return 'badge-macro-moderate';
    }

    // Growth regimes
    if (type === 'growth') {
        if (regime === 'expansion') return 'badge-macro-expansion';
        if (regime === 'contraction') return 'badge-macro-contraction';
        return 'badge-macro-slowdown';
    }

    // Inflation trends
    if (type === 'inflation') {
        if (regime === 'rising') return 'badge-macro-rising-inflation';
        if (regime === 'falling') return 'badge-macro-falling-inflation';
        return 'badge-macro-stable-inflation';
    }

    return 'bg-secondary';
}

/**
 * Get banner class based on macro multiplier
 */
function getAdviceBannerClass(multiplier) {
    if (!multiplier || multiplier >= 1.0) return 'bg-success-subtle';
    if (multiplier >= 0.90) return 'bg-warning-subtle';
    return 'bg-danger-subtle';
}

/**
 * Update macro regime display
 */
function updateMacroDisplay(data) {
    macroData = data;

    // Update rate regime
    const ratesEl = document.getElementById('macro-rates');
    const ratesBadge = document.getElementById('macro-rates-badge');
    if (ratesEl) {
        ratesEl.textContent = formatRegimeValue(data.rate_regime);
    }
    if (ratesBadge) {
        ratesBadge.textContent = data.rate_description ? data.rate_description.split(' - ')[0] : '--';
        ratesBadge.className = `badge ${getRegimeBadgeClass(data.rate_regime, 'rate')}`;
    }

    // Update credit stress
    const creditEl = document.getElementById('macro-credit');
    const creditBadge = document.getElementById('macro-credit-badge');
    if (creditEl) {
        creditEl.textContent = formatRegimeValue(data.credit_stress);
    }
    if (creditBadge) {
        creditBadge.textContent = data.credit_description ? data.credit_description.split(' - ')[0] : '--';
        creditBadge.className = `badge ${getRegimeBadgeClass(data.credit_stress, 'credit')}`;
    }

    // Update growth regime
    const growthEl = document.getElementById('macro-growth');
    const growthBadge = document.getElementById('macro-growth-badge');
    if (growthEl) {
        growthEl.textContent = formatRegimeValue(data.growth_regime);
    }
    if (growthBadge) {
        growthBadge.textContent = data.growth_description ? data.growth_description.split(' - ')[0] : '--';
        growthBadge.className = `badge ${getRegimeBadgeClass(data.growth_regime, 'growth')}`;
    }

    // Update inflation trend
    const inflationEl = document.getElementById('macro-inflation');
    const inflationBadge = document.getElementById('macro-inflation-badge');
    if (inflationEl) {
        inflationEl.textContent = formatRegimeValue(data.inflation_trend);
    }
    if (inflationBadge) {
        inflationBadge.textContent = data.inflation_description ? data.inflation_description.split(' - ')[0] : '--';
        inflationBadge.className = `badge ${getRegimeBadgeClass(data.inflation_trend, 'inflation')}`;
    }

    // Update yield curve
    const yieldCurveEl = document.getElementById('macro-yield-curve');
    if (yieldCurveEl) {
        const slope = data.yield_curve_slope;
        const status = data.yield_curve_status;
        const icon = status === 'inverted' ? '⚠️' : status === 'flat' ? '⚡' : '✓';
        yieldCurveEl.textContent = `${icon} ${slope > 0 ? '+' : ''}${slope.toFixed(2)}% (${status})`;
    }

    // Update advice banner
    const adviceEl = document.getElementById('macro-advice-text');
    const adviceBanner = document.getElementById('macro-advice-banner');
    if (adviceEl) {
        adviceEl.textContent = data.advice || 'No macro advice available';
    }
    if (adviceBanner) {
        adviceBanner.className = `mt-3 p-2 rounded ${getAdviceBannerClass(data.macro_multiplier)}`;
    }

    // Log if macro is disabled
    if (!data.enabled) {
        console.warn('Macro regime detection is disabled. Add FRED_API_KEY to .env to enable.');
    }
}

/**
 * Fetch macro regime data from API
 */
async function fetchMacroRegime() {
    try {
        const response = await fetch('/api/macro/regime');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error('Error fetching macro regime:', error);
        return {
            enabled: false,
            macro_multiplier: 1.0,
            summary: 'Error fetching macro data',
            advice: 'Check server logs for details',
            rate_regime: 'unknown',
            credit_stress: 'unknown',
            growth_regime: 'unknown',
            inflation_trend: 'unknown',
            yield_curve_slope: 0,
            yield_curve_status: 'unknown'
        };
    }
}

/**
 * Load macro regime data
 */
async function loadMacroRegime() {
    try {
        const data = await fetchMacroRegime();
        updateMacroDisplay(data);
    } catch (error) {
        console.error('Error loading macro regime:', error);
        showAlert('Error loading macro environment data.', 'warning');
    }
}

/**
 * Update macro data from portfolio response (if available)
 */
function updateFromPortfolioContext(macroContext) {
    if (!macroContext) return;
    updateMacroDisplay(macroContext);
}

/**
 * Get cached macro data
 */
function getMacroData() {
    return macroData;
}

export {
    loadMacroRegime,
    updateFromPortfolioContext,
    getMacroData
};
