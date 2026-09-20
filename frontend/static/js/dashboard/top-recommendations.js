/**
 * Top Recommendations Module
 * Displays the highest-scoring option opportunities with auto-refresh
 */
import { fetchRunState, refreshRun, revalidateCopy, markRecommendationTaken } from './api-run.js';
import { initPresetSelector } from './preset-selector.js';
import { escapeHtml, formatCurrency, formatPercent } from '../utils/formatters.js';
import { showPanelLoading, finishPanelLoading, failPanelLoading } from './options-table-rendering.js';
import StateModel from '../utils/state-model.js';

// Module state
let signalsData = null;

// Recommendations the owner has recorded as acted on for the run on screen
// (from /api/run `taken_links`). Card state only — the backend owns the link.
let takenRecommendedKeys = new Set();

// Daily premium needed to stay on the active preset's 5x pace (from portfolio history).
// Fetched lazily once; null when history is insufficient.
let requiredPremiumPerDay = null;
let requiredPaceFetched = false;
let autoRefreshInterval = null;
let isVisible = true;
let listenersBound = false;
let isInitialized = false;
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
let cspCardsEl, ccCardsEl;
let cspRemainingEl, ccRemainingEl, cspRemainingListEl, ccRemainingListEl;
let cspRemainingCountEl, ccRemainingCountEl;
let strategyRulesEl, strategyRulesTextEl;

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
    cspCardsEl = document.getElementById('top-csp-cards');
    ccCardsEl = document.getElementById('top-cc-cards');
    cspRemainingEl = document.getElementById('csp-remaining');
    ccRemainingEl = document.getElementById('cc-remaining');
    cspRemainingListEl = document.getElementById('csp-remaining-list');
    ccRemainingListEl = document.getElementById('cc-remaining-list');
    cspRemainingCountEl = document.getElementById('csp-remaining-count');
    ccRemainingCountEl = document.getElementById('cc-remaining-count');
    strategyRulesEl = document.getElementById('strategy-rules');
    strategyRulesTextEl = document.getElementById('strategy-rules-text');
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
 * Resolve a candidate's broker-quote age in seconds. Prefers the read-time
 * ``quote_age_sec`` attached by the backend; falls back to parsing a timestamp.
 * @param {Object} rec - Recommendation data
 * @returns {number|null} seconds, or null when unavailable
 */
function getQuoteAgeSec(rec) {
    if (rec && rec.quote_age_sec != null && Number.isFinite(Number(rec.quote_age_sec))) {
        return Number(rec.quote_age_sec);
    }
    const wd = (rec && rec.wheel_decision) || {};
    const ts = rec?.quote_timestamp || wd.quote_timestamp || wd.quote_fetched_at_utc || rec?.quote_fetched_at_utc || '';
    if (!ts) return null;
    const t = new Date(ts).getTime();
    return Number.isFinite(t) ? Math.max((Date.now() - t) / 1000, 0) : null;
}

/**
 * Format a quote age (seconds) as human text; returns the em-dash placeholder
 * when the value is unavailable so missing data never renders as 0/"now".
 * @param {number|null} sec - Quote age in seconds
 * @returns {string}
 */
function formatQuoteAge(sec) {
    if (sec == null || !Number.isFinite(Number(sec)) || Number(sec) < 0) return '—';
    const s = Number(sec);
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m ago`;
    return `${Math.floor(s / 86400)}d ago`;
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

/**
 * Copy eligibility for a candidate, derived from the backend-computed
 * eligibility object attached to every signal at read time.
 *   live  -> copy now (market session open, complete coverage, fresh broker quotes)
 *   staged-> stage for next market open (market closed; re-validated against
 *            OpenD immediately before the clipboard write)
 *   review_only -> never copyable; reasons are surfaced on the disabled button
 */
function copyEligibility(rec) {
    const el = (rec && rec.eligibility) || {};
    const mode = (el.mode === 'live' || el.mode === 'staged') ? el.mode : 'review_only';
    const reasons = Array.isArray(el.reasons) && el.reasons.length
        ? el.reasons.slice()
        : ['candidate is not copy eligible'];
    const canCopy = mode === 'live' || mode === 'staged';
    return { canCopy, mode, reasons, staged: mode === 'staged' };
}

function contractFingerprint(rec) {
    const exp = String((rec && rec.expiration) || '').replace(/-/g, '');
    const strike = Number(rec && rec.strike);
    if (!rec || !rec.ticker || !rec.option_type || !exp || !Number.isFinite(strike)) return '';
    return [String(rec.ticker).toUpperCase(), String(rec.option_type).toUpperCase(), exp, strike.toFixed(2)].join('|');
}

/**
 * Build the set of recommended-contract keys the owner has marked taken.
 * Only the recommendation identity is keyed: the card is the recommendation,
 * whatever strike was actually traded.
 */
function buildTakenKeySet(links) {
    const keys = new Set();
    (Array.isArray(links) ? links : []).forEach((link) => {
        const key = contractFingerprint(link && link.recommendation);
        if (key) keys.add(key);
    });
    return keys;
}

function isTaken(rec) {
    const key = contractFingerprint(rec);
    return Boolean(key) && takenRecommendedKeys.has(key);
}

function setTakenStatus(statusEl, message, isError = false) {
    if (!statusEl) return;
    statusEl.className = `taken-status small mt-1${isError ? ' text-danger' : ' text-success'}`;
    statusEl.textContent = message || '';
}

/**
 * Render the taken control for one card from module state (never a guess).
 */
function renderTakenState(rec, btn, input, statusEl) {
    const taken = isTaken(rec);
    btn.disabled = taken;
    if (input) input.disabled = taken;
    if (taken) {
        btn.classList.remove('btn-outline-secondary');
        btn.classList.add('btn-success');
        btn.innerHTML = '<i class="bi bi-bookmark-check"></i> Taken';
        btn.title = 'Recorded as acted on — outcome attribution links to this recommendation';
        setTakenStatus(statusEl, 'Linked to this recommendation');
    } else {
        btn.classList.add('btn-outline-secondary');
        btn.classList.remove('btn-success');
        btn.innerHTML = '<i class="bi bi-bookmark"></i> Mark taken';
        btn.title = 'Record that you acted on this recommendation (no order is placed)';
        setTakenStatus(statusEl, '');
    }
}

/**
 * Record the owner's taken link for this card. Explicit owner evidence, never
 * an order: the backend validates the contract against the published run and
 * stores a link the outcome engine attributes against. Failures stay visible.
 */
async function markTaken(rec, btn, input, statusEl) {
    const runId = (signalsData && signalsData.run && signalsData.run.run_id) || '';
    if (!runId) {
        setTakenStatus(statusEl, 'Not recorded: no published run id — refresh and retry.', true);
        return;
    }
    const expiration = String(rec.expiration || '').replace(/-/g, '');
    const tradedStrike = input && input.value !== '' ? Number(input.value) : null;
    const traded = Number.isFinite(tradedStrike) && tradedStrike > 0
        ? { ticker: rec.ticker, option_type: rec.option_type, expiration, strike: tradedStrike }
        : undefined;

    const original = btn.innerHTML;
    setButtonBusy(btn, 'Saving…');
    const payload = {
        run_id: runId,
        ticker: rec.ticker,
        option_type: rec.option_type,
        expiration,
        strike: Number(rec.strike),
    };
    // Only send the traded contract when the owner actually stated it: an
    // absent key means "not stated", never "same as recommended".
    if (traded) payload.traded = traded;
    try {
        await markRecommendationTaken(payload);
        takenRecommendedKeys.add(contractFingerprint(rec));
        renderTakenState(rec, btn, input, statusEl);
    } catch (err) {
        btn.disabled = false;
        btn.classList.remove('btn-success');
        btn.innerHTML = original;
        setTakenStatus(statusEl, `Not recorded: ${err && err.message ? err.message : 'request failed'}`, true);
    }
}

function setButtonBusy(btn, label) {
    if (!btn) return;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> ${label}`;
}

function setButtonBlocked(btn, reasons, kind = 'blocked') {
    if (!btn) return;
    btn.disabled = true;
    btn.classList.add(kind === 'error' ? 'btn-danger' : 'btn-warning');
    btn.classList.remove('btn-success', kind === 'error' ? 'btn-warning' : 'btn-danger');
    btn.innerHTML = kind === 'error'
        ? '<i class="bi bi-exclamation-triangle"></i> Not copied'
        : '<i class="bi bi-eye"></i> Review only';
    btn.title = ((reasons || []).filter(Boolean).join(' · ') || 'not copy eligible');
}

async function copyTicket(rec, btn) {
    // Revalidate against the CURRENT run/candidate before writing the clipboard
    // (P1a C03): a fresh read-only GET awaited right before the write. A run
    // change, re-ranked/replaced contract, review_only outcome, fetch failure,
    // or a market open/close transition all abort the copy (never silently
    // copying a different value) and require a second click after a refresh.
    const { canCopy, mode: intent, staged } = copyEligibility(rec);
    if (!canCopy) return;

    const displayedRunId = (signalsData && signalsData.run && signalsData.run.run_id) || '';
    const original = btn.innerHTML;

    setButtonBusy(btn, 'Checking…');

    let reval;
    try {
        reval = await revalidateCopy({
            run_id: displayedRunId,
            ticker: rec.ticker,
            option_type: rec.option_type,
            expiration: rec.expiration,
            strike: rec.strike,
        });
    } catch (err) {
        console.error('Copy revalidation failed:', err);
        setButtonBlocked(btn, ['revalidation request failed — verify and retry'], 'error');
        return; // never write to clipboard
    }

    const currentRunId = (reval && reval.run_id) || '';
    if (reval && reval.matched_run === false) {
        // The run changed under the click: never copy, refresh the card, second click.
        setButtonBlocked(btn, ['run changed — review and click again'], 'error');
        loadTopRecommendations(false);
        return;
    }
    if (displayedRunId && currentRunId && displayedRunId !== currentRunId) {
        // Redundant safety net for the same transition above.
        setButtonBlocked(btn, ['run changed — review and click again'], 'error');
        loadTopRecommendations(false);
        return;
    }

    if (!reval || reval.ok === false) {
        setButtonBlocked(btn, (reval && reval.reasons) || ['copy check failed'], 'error');
        return;
    }
    if (reval.mode === 'review_only') {
        setButtonBlocked(btn, reval.reasons || ['not copy eligible'], 'blocked');
        return; // write nothing
    }

    if (reval.matched_contract === false) {
        // The contract left the shortlist (re-ranked or replaced).
        setButtonBlocked(btn, ['contract changed — review and click again'], 'error');
        loadTopRecommendations(false);
        return;
    }
    if (reval.mode !== intent) {
        // Market open/close transition between page load and the click.
        setButtonBlocked(
            btn,
            [`market ${reval.mode === 'staged' ? 'closed' : 'opened'} since page load — review and click again`],
            'error'
        );
        loadTopRecommendations(false);
        return;
    }

    // Copy the CURRENT contract + mode from the revalidated response.
    const current = (reval.contract && Object.keys(reval.contract).length) ? reval.contract : rec;
    const text = buildTicketText({ ...rec, ...current }, { staged: reval.mode === 'staged' });
    try {
        await navigator.clipboard.writeText(text);
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-check-circle"></i> Copied';
        btn.classList.add('btn-success');
    } catch (err) {
        console.error('Clipboard failed:', err);
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-x-circle"></i> Copy failed';
        btn.classList.add('btn-danger');
    }
    setTimeout(() => {
        btn.innerHTML = original;
        btn.disabled = !canCopy;
        btn.classList.remove('btn-success', 'btn-danger', 'btn-warning');
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
    
    // Premium velocity — secondary supporting metric; ranking is based on
    // backend-provided annualized return on deployed capital.
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
    
    // Annualized return on the secured/covered capital base (primary metric).
    const annualizedEl = clone.querySelector('.annualized-return');
    annualizedEl.textContent = rec.annualized_return != null ? `${rec.annualized_return.toFixed(1)}%` : 'N/A';
    annualizedEl.classList.add(rec.annualized_return != null && rec.annualized_return > 0 ? 'text-success' : 'text-danger');
    
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

    // Display-only risk note; tiers are informational and no longer affect ranking.
    const riskBadge = clone.querySelector('.missing-risk-badge');
    if (riskBadge && eventTier === 'event_unknown') {
        riskBadge.textContent = 'Earnings unknown — verify before placing';
        riskBadge.classList.remove('d-none');
    }

    // Why this winner: backend-provided rank evidence; browser does not rank
    // or compare candidates.
    const whyEl = clone.querySelector('.why-winner');
    if (whyEl) {
        const annualized = rec.annualized_return != null ? Number(rec.annualized_return).toFixed(1) : null;
        whyEl.textContent = annualized != null
            ? `Why this pick: ${annualized}% annualized return on deployed capital`
            : 'Why this pick: passed all hard gates';
    }
    
    // Copy-to-ticket (explicit; clipboard success/failure feedback)
    const copyBtn = clone.querySelector('.copy-ticket-btn');
    if (copyBtn) {
        // Copy eligibility is backend-computed per response and reflects mode
        // + reasons. Buttons are disabled for review_only with reasons visible.
        const { canCopy, staged, reasons } = copyEligibility(rec);
        copyBtn.disabled = !canCopy;
        if (canCopy) {
            if (staged) {
                copyBtn.title = 'Stage ticket — US market closed; re-validated against OpenD before copy';
                copyBtn.innerHTML = '<i class="bi bi-clock"></i> Stage ticket';
            } else {
                copyBtn.title = 'Copy a manual ticket draft (live broker quote)';
                copyBtn.innerHTML = '<i class="bi bi-clipboard"></i> Copy ticket';
            }
        } else {
            copyBtn.title = 'Review only: ' + reasons.join(' · ');
            copyBtn.innerHTML = '<i class="bi bi-eye"></i> Review only';
        }
        copyBtn.addEventListener('click', () => {
            if (canCopy) copyTicket(rec, copyBtn);
        });
    }

    // Owner-recorded taken link: explicit journal evidence that the owner acted
    // on this recommendation (no order, no clipboard). State comes from the run
    // payload, so it survives a reload; failures are shown on the card.
    const takenBtn = clone.querySelector('.mark-taken-btn');
    if (takenBtn) {
        const tradedStrikeInput = clone.querySelector('.traded-strike-input');
        const takenStatusEl = clone.querySelector('.taken-status');
        renderTakenState(rec, takenBtn, tradedStrikeInput, takenStatusEl);
        takenBtn.addEventListener('click', () => markTaken(rec, takenBtn, tradedStrikeInput, takenStatusEl));
    }

    // Details
    clone.querySelector('.otm-pct').textContent = rec.otm_pct != null ? `${rec.otm_pct.toFixed(1)}%` : 'N/A';
    clone.querySelector('.delta-value').textContent = rec.delta != null ? rec.delta.toFixed(3) : 'N/A';
    const ivRankEl = clone.querySelector('.iv-rank');
    const ivStatus = rec.iv_status || rec.wheel_decision?.iv_status;
    if (ivStatus === 'unknown' || ivStatus === 'insufficient_history' || (rec.iv_rank == null && rec.implied_volatility == null)) {
        ivRankEl.textContent = ivStatus === 'insufficient_history' ? 'IV n/a (insufficient history)' : 'IV unavailable';
        ivRankEl.classList.add('text-muted');
    } else {
        const percentile = rec.iv_percentile != null && Number.isFinite(Number(rec.iv_percentile))
            ? ` (%ile ${(Number(rec.iv_percentile) * 100).toFixed(0)})`
            : '';
        ivRankEl.textContent = rec.iv_rank != null ? `${rec.iv_rank.toFixed(0)}%${percentile}` : 'N/A';
    }

    // Return on deployed capital per day (decimal fraction -> % / day). This is
    // the ranking key, shown read-only. Missing stays 'N/A', never a zero.
    const capitalVelocityEl = clone.querySelector('.capital-velocity');
    if (capitalVelocityEl) {
        const capVel = rec.capital_velocity_per_day;
        if (capVel != null && Number.isFinite(Number(capVel)) && Number(capVel) > 0) {
            capitalVelocityEl.textContent = `${(Number(capVel) * 100).toFixed(3)}% / day`;
            capitalVelocityEl.classList.add('text-success');
        } else {
            capitalVelocityEl.textContent = 'N/A';
        }
    }

    // Quote age (broker quote freshness, read-time backend value)
    const quoteAgeEl = clone.querySelector('.quote-age');
    if (quoteAgeEl) {
        quoteAgeEl.textContent = getQuoteAgeSec(rec) != null ? formatQuoteAge(getQuoteAgeSec(rec)) : '—';
    }

    // CSP-specific details
    const cspSection = clone.querySelector('.csp-details');
    if (signalType === 'csp' || signalType === 'put') {
        cspSection.classList.remove('d-none');
        const cashReq = rec.cash_required ?? rec.collateral ?? rec.capital_required ?? rec.wheel_decision?.cash_required;
        const cspCash = signalsData?.cash_available_for_csp || 0;
        const cashPct = cashReq != null && cspCash > 0 ? (cashReq / cspCash) * 100 : 0;
        const breakevenBuffer = rec.breakeven_buffer_pct ?? rec.wheel_decision?.breakeven_buffer_pct;
        const expectedMove = rec.expected_move_buffer ?? rec.wheel_decision?.expected_move_buffer;

        clone.querySelector('.csp-cash-required').textContent = cashReq ? formatCurrency(cashReq) : 'N/A';
        clone.querySelector('.csp-cash-pct').textContent = cashPct > 0 ? `${cashPct.toFixed(1)}%` : 'N/A';

        // Capital-aware sizing: income at the recommended size (executable bid)
        // and cash left after collateral is committed. Pure arithmetic on
        // Moomoo-sourced fields — no new data source.
        const qtyRec = Number(rec.recommended_contracts || 0);
        const bidPremiumPerContract = rec.bid_premium_per_contract ?? rec.wheel_decision?.bid_premium_per_contract;
        const incomeAtSize = qtyRec > 0 && bidPremiumPerContract != null ? qtyRec * Number(bidPremiumPerContract) : null;
        const cashAfter = cashReq != null && cspCash > 0 ? cspCash - qtyRec * Number(cashReq) : null;
        const incomeEl = clone.querySelector('.csp-income-at-size');
        if (incomeEl) incomeEl.textContent = incomeAtSize != null ? formatCurrency(incomeAtSize) : 'N/A';
        const cashAfterEl = clone.querySelector('.csp-cash-after');
        if (cashAfterEl) {
            cashAfterEl.textContent = cashAfter != null ? formatCurrency(Math.max(cashAfter, 0)) : 'N/A';
            cashAfterEl.classList.toggle('text-warning', cashAfter != null && cashAfter < 0);
        }

        // Contribution to the active preset goal: this trade's credit vs the
        // required daily pace derived from persisted portfolio history.
        const contributionEl = clone.querySelector('.csp-pace-contribution');
        if (contributionEl) {
            if (incomeAtSize != null && requiredPremiumPerDay != null && requiredPremiumPerDay > 0) {
                const coverage = (incomeAtSize / requiredPremiumPerDay) * 100;
                const targetMultiple = Number(signalsData?.preset?.screener_profile?.target_account_multiple || 5);
                contributionEl.textContent = `Covers ${coverage.toFixed(1)}% of the daily pace to ${targetMultiple}x`;
                contributionEl.classList.remove('d-none');
            } else {
                contributionEl.textContent = '';
                contributionEl.classList.add('d-none');
            }
        }
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

        const availableSharesEl = clone.querySelector('.cc-available-shares');
        if (availableSharesEl) {
            availableSharesEl.textContent = rec.available_shares != null && Number.isFinite(Number(rec.available_shares))
                ? `${Number(rec.available_shares).toLocaleString()} sh`
                : 'N/A';
        }


        clone.querySelector('.cc-if-called-return').textContent = ifCalledReturn != null ? `${ifCalledReturn.toFixed(1)}%` : 'N/A';
        clone.querySelector('.cc-if-called-proceeds').textContent = ifCalledProceeds != null ? formatCurrency(ifCalledProceeds) : 'N/A';
        clone.querySelector('.cc-cost-basis-dist').textContent = costBasisDist != null ? `${costBasisDist.toFixed(1)}%` : 'N/A';
        const intentLabels = { 'income': 'Income', 'profit-taking': 'Profit-taking', 'upside-capping risk': 'Capping upside' };
        clone.querySelector('.cc-intent').textContent = intentLabels[intent] || intent || 'N/A';
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

    // Entry timing guidance (intraday window advice, server-computed)
    const entryContext = result?.entry_context;
    const timingWrap = document.getElementById('entry-timing-advice');
    const timingText = document.getElementById('entry-timing-text');
    if (timingWrap && timingText && entryContext?.message) {
        timingText.textContent = entryContext.message;
        const qualityClass = {
            good: 'text-success',
            fair: 'text-muted',
            caution: 'text-warning',
            poor: 'text-danger',
            closed: 'text-muted',
        }[entryContext.quality] || 'text-muted';
        timingText.className = qualityClass;
        timingWrap.classList.remove('d-none');
    }

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
 * Lazily load the required daily premium pace from portfolio history.
 * Resolves true only when a usable pace value was found (one-shot).
 */
async function fetchRequiredPace() {
    requiredPaceFetched = true;
    try {
        const resp = await fetch('/api/portfolio/history?limit=365');
        if (resp.ok) {
            const data = await resp.json();
            const pace = data?.pace?.required_premium_per_day;
            if (typeof pace === 'number' && pace > 0) {
                requiredPremiumPerDay = pace;
                return true;
            }
        }
    } catch (err) {
        console.debug('Pace contribution unavailable:', err);
    }
    return false;
}

/**
 * Render the active preset's actual strategy rules (read-only, from the
 * backend snapshot). Replaces the former Call/Put filter tabs with the
 * effective thresholds the listed signals were screened under.
 * @param {Object} result - API response
 */
function renderStrategyRules(result) {
    if (!strategyRulesEl || !strategyRulesTextEl) return;
    const preset = result?.preset || {};
    const sp = preset.screener_profile || {};
    const parts = [];
    if (preset.label) {
        parts.push(escapeHtml(String(preset.label).toUpperCase()) + (preset.version ? ` v${preset.version}` : ''));
    }
    if (sp.csp_target_delta != null) {
        parts.push(`CSP \u0394 ${Number(sp.csp_target_delta).toFixed(2)} \u00b1${Number(sp.csp_delta_tolerance ?? 0).toFixed(2)}`);
    }
    if (sp.csp_min_dte != null && sp.csp_max_dte != null) {
        const pref = sp.csp_preferred_dte != null ? ` (pref ${sp.csp_preferred_dte})` : '';
        parts.push(`CSP DTE ${sp.csp_min_dte}-${sp.csp_max_dte}${pref}`);
    }
    if (sp.csp_min_otm_pct != null && sp.csp_max_otm_pct != null) {
        parts.push(`CSP OTM ${sp.csp_min_otm_pct}-${sp.csp_max_otm_pct}%`);
    }
    if (sp.call_default_otm_pct != null) parts.push(`CC OTM ${sp.call_default_otm_pct}%`);
    if (sp.min_csp_buying_power != null) parts.push(`min CSP buying power ${formatCurrency(Number(sp.min_csp_buying_power))}`);
    if (sp.max_buying_power_pct_per_csp != null) parts.push(`\u2264${sp.max_buying_power_pct_per_csp}% buying power per CSP`);
    if (sp.min_premium_per_contract != null) parts.push(`min premium ${formatCurrency(Number(sp.min_premium_per_contract))}`);
    if (sp.min_mid_price != null) parts.push(`min mid $${Number(sp.min_mid_price).toFixed(2)}`);
    if (sp.max_spread_pct != null) parts.push(`max spread ${sp.max_spread_pct}%`);
    if (sp.min_open_interest != null) parts.push(`min OI ${sp.min_open_interest}`);
    if (sp.require_cash_fit) parts.push('cash-fit required');
    if (parts.length === 0) {
        strategyRulesEl.classList.add('d-none');
        strategyRulesTextEl.innerHTML = '';
        return;
    }
    strategyRulesTextEl.innerHTML = parts.join(' <span class="text-secondary">\u00b7</span> ');
    strategyRulesEl.classList.remove('d-none');
}

/**
 * Render the two always-visible strategy lanes from the backend lane lists.
 * ``csp_picks`` -> Top 3 cash-secured puts, ``cc_decisions`` -> Top 3 covered
 * calls. Each lane shows its top-3 cards plus an expandable list of the
 * remaining qualifying candidates (each one copy-addressable). Every candidate
 * is labeled an alternative, not a recommendation to sell every contract.
 * @param {Object} result - API response
 */
function renderLaneSections(result) {
    renderLane('csp', result?.csp_picks || []);
    renderLane('cc', result?.cc_decisions || []);
    // Pace data loads once; refresh the lanes a single time when it arrives so
    // per-card pace contributions populate without a second network request.
    if (!requiredPaceFetched) {
        fetchRequiredPace().then((found) => {
            if (found && signalsData) {
                renderLane('csp', signalsData.csp_picks || []);
                renderLane('cc', signalsData.cc_decisions || []);
            }
        });
    }
}

/**
 * Render a single strategy lane.
 * @param {'csp'|'cc'} kind - Lane key
 * @param {Array} list - Ranked candidate list from the backend
 */
function renderLane(kind, list) {
    const isCsp = kind === 'csp';
    const topEl = isCsp ? cspCardsEl : ccCardsEl;
    const remainingEl = isCsp ? cspRemainingEl : ccRemainingEl;
    const remainingListEl = isCsp ? cspRemainingListEl : ccRemainingListEl;
    const remainingCountEl = isCsp ? cspRemainingCountEl : ccRemainingCountEl;
    const laneEl = document.getElementById(isCsp ? 'csp-section' : 'cc-section');
    const cards = Array.isArray(list) ? list : [];

    // No lane containers in the DOM: allow the caller to fall back to the grid.
    if (!topEl) return;
    if (cards.length === 0) {
        topEl.innerHTML = '';
        if (remainingEl) remainingEl.classList.add('d-none');
        if (remainingListEl) remainingListEl.innerHTML = '';
        if (laneEl) laneEl.classList.add('d-none');
        return;
    }
    if (laneEl) laneEl.classList.remove('d-none');

    const topCards = cards.slice(0, 3);
    topEl.innerHTML = '';
    topCards.forEach((rec, index) => {
        const card = createRecommendationCard(rec, cards[index + 1] || null);
        applyGrowthFieldsToCard(card, rec);
        topEl.appendChild(card);
    });

    const rest = cards.slice(3);
    if (remainingListEl) remainingListEl.innerHTML = '';
    if (remainingEl) {
        if (rest.length > 0) {
            rest.forEach((rec) => remainingListEl.appendChild(buildRemainingCandidateRow(rec, kind)));
            if (remainingCountEl) remainingCountEl.textContent = String(rest.length);
            remainingEl.classList.remove('d-none');
            remainingEl.open = false;
        } else {
            remainingEl.classList.add('d-none');
        }
    }
}

/**
 * Build a compact "remaining qualifying candidate" row for a lane, with its
 * own server-revalidated copy button. All API-fed fields are escaped; missing
 * values render as an em-dash, never a hardcoded zero.
 * @param {Object} rec - Recommendation data
 * @param {'csp'|'cc'} kind - Lane key
 * @returns {HTMLElement}
 */
function buildRemainingCandidateRow(rec, kind) {
    const isCsp = kind === 'csp';
    const row = document.createElement('div');
    row.className = 'lane-candidate-row row g-2 align-items-center py-2 border-bottom border-light small';
    const optionType = String(rec.option_type || '').toUpperCase();
    const typeLabel = optionType === 'CALL' ? 'Covered call' : 'CSP';
    const eventTier = rec.event_tier || rec.wheel_decision?.event_tier || 'event_unknown';
    const qualityTier = rec.quality_tier || rec.wheel_decision?.quality_tier || 'marginal';
    const bid = rec.bid_premium_per_contract != null
        ? formatCurrency(Number(rec.bid_premium_per_contract))
        : (rec.premium_per_contract != null ? formatCurrency(Number(rec.premium_per_contract)) : '\u2014');
    const capVel = rec.capital_velocity_per_day;
    const roiDay = capVel != null && Number.isFinite(Number(capVel)) && Number(capVel) > 0
        ? `${(Number(capVel) * 100).toFixed(3)}%/day`
        : '\u2014';
    const capacity = isCsp
        ? (rec.collateral ?? rec.cash_required ?? null)
        : (rec.available_shares ?? null);
    const capacityText = capacity != null && Number.isFinite(Number(capacity))
        ? (isCsp ? formatCurrency(Number(capacity)) : `${Number(capacity).toLocaleString()} sh`)
        : '\u2014';
    const qty = Number(rec.recommended_contracts || 0);
    const maxQty = Number(rec.max_contracts || 0);
    const quoteAge = getQuoteAgeSec(rec) != null ? formatQuoteAge(getQuoteAgeSec(rec)) : '\u2014';
    const strikeText = rec.strike != null ? `$${Number(rec.strike).toFixed(2)}` : '';
    const expiryText = rec.expiration ? formatExpiration(rec.expiration) : '';
    const dteText = rec.dte != null ? `\u00b7 ${rec.dte} DTE` : '';

    row.innerHTML = `
        <div class="col-12 col-md-4">
            <strong class="me-1">${escapeHtml(rec.ticker)}</strong>
            <span class="badge ${optionType === 'CALL' ? 'bg-success' : 'bg-danger'}">${escapeHtml(typeLabel)}</span>
            <span class="ms-1">${escapeHtml(strikeText)}</span>
            <span class="text-muted"> \u00b7 ${escapeHtml(expiryText)}${escapeHtml(dteText)}</span>
        </div>
        <div class="col-6 col-md-2"><span class="text-muted">Bid</span> <strong>${escapeHtml(bid)}</strong></div>
        <div class="col-6 col-md-2"><span class="text-muted">ROI/day</span> <strong>${escapeHtml(roiDay)}</strong></div>
        <div class="col-6 col-md-2"><span class="text-muted">${isCsp ? 'Collateral' : 'Shares'}</span> <strong>${escapeHtml(capacityText)}</strong></div>
        <div class="col-6 col-md-2"><span class="text-muted">Qty</span> <strong>${qty}${maxQty > 0 ? `<small class="text-muted">/${maxQty}</small>` : ''}</strong></div>
        <div class="col-6 col-md-2"><span class="text-muted">Quote age</span> <strong>${escapeHtml(quoteAge)}</strong></div>
        <div class="col-6 col-md-2"><span class="text-muted">Event</span> <strong class="${qualityTier === 'qualified' ? 'text-success' : 'text-warning'}">${escapeHtml(qualityTier)} \u00b7 ${escapeHtml(eventTier.replaceAll('_', ' '))}</strong></div>
        <div class="col-12 col-md-2 text-md-end">
            <button type="button" class="btn btn-outline-primary btn-sm copy-ticket-btn lane-copy-btn">Copy</button>
        </div>
    `;

    const btn = row.querySelector('.copy-ticket-btn');
    if (btn) {
        const { canCopy, staged, reasons } = copyEligibility(rec);
        btn.disabled = !canCopy;
        if (canCopy) {
            btn.title = staged
                ? 'Stage ticket \u2014 US market closed; re-validated against OpenD before copy'
                : 'Copy a manual ticket draft (live broker quote)';
            btn.innerHTML = staged ? '<i class="bi bi-clock"></i> Stage' : '<i class="bi bi-clipboard"></i> Copy';
            btn.addEventListener('click', () => copyTicket(rec, btn));
        } else {
            btn.title = 'Review only: ' + reasons.join(' \u00b7 ');
            btn.innerHTML = '<i class="bi bi-eye"></i> Review only';
        }
    }
    return row;
}

/**
 * Render signals into the single combined grid (legacy/fallback path used when
 * the two-lane DOM is absent; the dashboard always renders per-lane sections).
 */
function renderFilteredSignals() {
    if (!signalsData || !cardsContainer) return;
    const allSignals = signalsData.signals || [];
    cardsContainer.innerHTML = '';
    allSignals.forEach((rec, index) => {
        const card = createRecommendationCard(rec, allSignals[index + 1] || null);
        applyGrowthFieldsToCard(card, rec);
        cardsContainer.appendChild(card);
    });
    // Pace data loads once; re-render a single time when it arrives.
    if (!requiredPaceFetched) {
        fetchRequiredPace().then((found) => {
            if (found && cardsContainer && signalsData) renderFilteredSignals();
        });
    }
    if (allSignals.length > 0) {
        showContent();
    }
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
    if (!container) return;
    signalsData = result;

    // Show market state badge (after-hours indicator)
    showMarketStateBadge(result?._freshness);
    // Active preset's actual screening rules — read-only replacement for the
    // former Call/Put filter tabs.
    renderStrategyRules(result);
    // Apply growth mode banner
    applyPreset(result);
    // Update buying power indicator
    updateBuyingPowerIndicator(result);
    // Full rejection explanations stay accessible under "Ticker diagnostics".
    // The served snapshot carries them as `rejected` (engine `blocked_signals`).
    renderBlockedSignals(result?.rejected ?? result?.blocked_signals ?? []);

    // Two always-visible lanes: Top 3 CSP (+ remaining) and Top 3 CC
    // (+ remaining). Fall back to the legacy combined grid only when the
    // two-lane DOM is absent (old layout / tests).
    if (cspCardsEl && ccCardsEl) {
        renderLaneSections(result);
    } else {
        renderFilteredSignals();
    }

    const hasSignals = (result?.signals?.length > 0)
        || (Array.isArray(result?.csp_picks) && result.csp_picks.length > 0)
        || (Array.isArray(result?.cc_decisions) && result.cc_decisions.length > 0);
    if (hasSignals) {
        showContent();
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
        takenRecommendedKeys = buildTakenKeySet(envelope.taken_links);
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
    initPresetSelector(() => loadTopRecommendations(true));

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
