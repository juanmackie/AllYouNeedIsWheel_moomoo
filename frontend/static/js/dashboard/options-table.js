/**
 * Options Table module for handling options display and interaction
 * Orchestrator that composes functionality from focused sub-modules.
 */
import { fetchTickers, fetchAccountData } from './api.js';
import { fetchWatchlistTickers } from './api-portfolio.js';
import { escapeHtml } from '../utils/formatters.js';
import { state, loadOtmSettings, ensureTickerDataState, getSavedTabPreference, setSavedTabPreference } from './options-table-state.js';
import { calculateEarningsSummary } from './options-table-calc.js';
import { buildTabsHTML, updateOptionsTable, addTickerRowToTable, displayPremiumSummary, addPutQtyInputEventListeners, initializeOptionsTableTooltips, insertProgressBanner, updateProgressBanner, finishProgressBanner, failProgressBanner } from './options-table-rendering.js';
import { addOptionsTableEventListeners } from './options-table-events.js';
import { refreshOptionsForTicker, refreshAllOptions, refreshOptionsForTickerByType, sellAllOptions } from './options-table-actions.js';

function canonicalTicker(ticker) {
    return String(ticker || '').replace(/^[A-Z]{2}\./, '').toUpperCase();
}

function uniqueTickersByUnderlying(...groups) {
    const seen = new Set();
    const result = [];

    groups.flat().forEach(ticker => {
        const key = canonicalTicker(ticker);
        if (!key || seen.has(key)) return;
        seen.add(key);
        result.push(ticker);
    });

    return result;
}

async function fetchScreeningConfig() {
    try {
        const resp = await fetch('/api/options/screening-config');
        if (resp.ok) {
            const data = await resp.json();
            if (data.success) {
                state.screeningConfig = {
                    presetKey: data.preset_key ?? 'balanced',
                    cspDefaultOtmPct: data.csp_default_otm_pct,
                    callDefaultOtmPct: data.call_default_otm_pct,
                    cspMinDte: data.csp_min_dte,
                    cspMaxDte: data.csp_max_dte,
                    cspPreferredDte: data.csp_preferred_dte,
                    cspMinOtmPct: data.csp_min_otm_pct,
                    cspMaxOtmPct: data.csp_max_otm_pct,
                    defaultTab: data.default_tab,
                    cspProfileSummary: data.csp_profile_summary,
                };
                return state.screeningConfig;
            }
        }
    } catch (e) {
        console.error('Error fetching screening config:', e);
    }
    state.screeningConfig = {
        presetKey: 'balanced',
        cspDefaultOtmPct: 10,
        callDefaultOtmPct: 10,
        cspMinDte: 30,
        cspMaxDte: 45,
        cspPreferredDte: 37,
        cspMinOtmPct: 5,
        cspMaxOtmPct: 15,
        defaultTab: 'PUT',
        cspProfileSummary: null,
    };
    return state.screeningConfig;
}

async function loadTickers() {
    const optionsTableContainer = document.getElementById('options-table-container');
    if (!optionsTableContainer) {
        console.error("Options table container not found");
        return;
    }

    const cfg = await fetchScreeningConfig();
    loadOtmSettings();
    const savedTab = getSavedTabPreference();
    const defaultTab = savedTab || cfg.defaultTab || 'CALL';
    const putTabWasActive = defaultTab === 'PUT';

    const tabsHTML = buildTabsHTML(putTabWasActive);

    optionsTableContainer.innerHTML = tabsHTML;

    addOptionsTableEventListeners();
    try {
        state.portfolioSummary = await fetchAccountData();
    } catch (error) {
        console.error('Error fetching portfolio data:', error);
    }

    const [data, watchlistResp] = await Promise.all([
        fetchTickers(),
        fetchWatchlistTickers()
    ]);

    let portfolioTickers = [];
    if (data && data.tickers) {
        portfolioTickers = data.tickers;
    }

    let watchlistTickers = [];
    if (watchlistResp && watchlistResp.tickers) {
        watchlistTickers = watchlistResp.tickers;
    }

    state.watchlistTickers = new Set(watchlistTickers);
    state.portfolioTickers = portfolioTickers;

    const putTickers = uniqueTickersByUnderlying(portfolioTickers, watchlistTickers);

    document.querySelector('#call-options-table tbody').innerHTML = '';
    document.querySelector('#put-options-table tbody').innerHTML = '';

    const totalTickers = putTickers.length;

    // ── Determine active vs inactive tab ──────────────────────────
    // Load the currently visible side first so the user sees useful
    // content quickly.  The other side is deferred to Phase 2.
    const activeType = putTabWasActive ? 'PUT' : 'CALL';
    const inactiveType = activeType === 'CALL' ? 'PUT' : 'CALL';
    const getStatusRowId = (type, sym) => type === 'CALL' ? `call-status-${sym}` : `put-status-${sym}`;

    /**
     * Load one option type across all applicable tickers with a progress banner.
     * Returns after all tickers in this phase have been attempted.
     */
    async function loadPhase(optionType) {
        const tableId = optionType === 'CALL' ? 'call-options-table' : 'put-options-table';
        const typeLabel = optionType === 'CALL' ? 'covered calls' : 'cash-secured puts';

        insertProgressBanner(totalTickers);
        updateProgressBanner(0, totalTickers, '', `Loading ${typeLabel}...`);

        for (let i = 0; i < totalTickers; i++) {
            const ticker = putTickers[i];
            const isPortfolioTicker = portfolioTickers.includes(ticker);

            // CALL data only matters for portfolio tickers (need 100+ shares)
            if (optionType === 'CALL' && !isPortfolioTicker) {
                updateProgressBanner(i + 1, totalTickers, ticker, 'skipped (no position)');
                continue;
            }

            ensureTickerDataState(ticker);
            updateProgressBanner(i + 1, totalTickers, ticker, 'starting...');

            // Create a status row inside the table so the user sees movement
            const statusRowId = getStatusRowId(optionType, ticker);
            const statusRow = document.createElement('tr');
            statusRow.id = statusRowId;
            statusRow.innerHTML = `
                <td colspan="13" class="text-center">
                    <div class="d-flex align-items-center justify-content-center">
                        <div class="spinner-border spinner-border-sm text-primary me-2" role="status"></div>
                        <span>Loading ${typeLabel} for ${escapeHtml(ticker)} (${i+1}/${totalTickers})...</span>
                    </div>
                </td>
            `;
            const tbody = document.querySelector(`#${tableId} tbody`);
            if (tbody) tbody.appendChild(statusRow);

            // Build progress callback for sub-steps within a ticker
            const makeOnProgress = () => {
                return (step) => {
                    updateProgressBanner(i + 1, totalTickers, ticker, step);
                };
            };

            try {
                // Load only the requested option type (with 20s per-request timeout)
                await refreshOptionsForTickerByType(ticker, optionType, false, makeOnProgress());

                document.getElementById(statusRowId)?.remove();
                addTickerRowToTable(tableId, optionType, ticker);

                addPutQtyInputEventListeners();
                updateProgressBanner(i + 1, totalTickers, ticker, 'done');
            } catch (error) {
                console.error(`Error loading ${optionType} for ${ticker}:`, error);

                const errorMessage = `Error loading data for ${ticker}: ${error.message}`;
                updateProgressBanner(i + 1, totalTickers, ticker, error.message, errorMessage);

                const row = document.getElementById(statusRowId);
                if (row) {
                    row.innerHTML = `
                        <td colspan="13" class="text-center text-danger">
                            <i class="bi bi-exclamation-triangle"></i> ${escapeHtml(errorMessage)}
                        </td>
                    `;
                    setTimeout(() => row.remove(), 3000);
                }
            }

            const earningsSummary = calculateEarningsSummary();
            displayPremiumSummary(earningsSummary);
        }
    }

    // ── Phase 1: Load the active (visible) tab first ──────────────
    //   This runs synchronously so the user sees the active tab
    //   populate as fast as possible.
    await loadPhase(activeType);
    finishProgressBanner();

    // ── Phase 2: Load the inactive tab in the background ──────────
    //   The inactive side loads one ticker at a time (same rate
    //   limiting as Phase 1) but the user can already interact with
    //   the dashboard.
    function showEmptyTableMessage(tableId, message) {
        const tbody = document.querySelector(`#${tableId} tbody`);
        if (!tbody || tbody.children.length > 0) return;
        const row = document.createElement('tr');
        row.innerHTML = `
            <td colspan="13" class="text-center p-3">
                <div class="alert alert-info m-0">${escapeHtml(message)}</div>
            </td>
        `;
        tbody.appendChild(row);
    }

    // Show empty state for the active table immediately after Phase 1
    if (activeType === 'CALL') {
        showEmptyTableMessage('call-options-table', 'No eligible covered calls. You need 100+ shares of a ticker and a ranked call contract.');
    } else {
        if (putTickers.length === 0) {
            showEmptyTableMessage('put-options-table', 'No watchlist tickers for cash-secured puts. Add tickers in the watchlist panel.');
        } else {
            showEmptyTableMessage('put-options-table', 'No cash-secured put opportunities found. Check that tickers have available put data or adjust cash-fit filters.');
        }
    }

    if (totalTickers > 0 && totalTickers <= 6) {
        // Fire-and-forget background loading for the inactive side.
        // NOT awaited so the rest of initialisation can proceed.
        loadPhase(inactiveType)
            .then(() => {
                finishProgressBanner();
                // Show empty state for the inactive table now that Phase 2 is done
                if (inactiveType === 'CALL') {
                    showEmptyTableMessage('call-options-table', 'No eligible covered calls. You need 100+ shares of a ticker and a ranked call contract.');
                } else {
                    const putTickersCount = [...new Set([...state.portfolioTickers, ...state.watchlistTickers])].length;
                    if (putTickersCount === 0) {
                        showEmptyTableMessage('put-options-table', 'No watchlist tickers for cash-secured puts. Add tickers in the watchlist panel.');
                    } else {
                        showEmptyTableMessage('put-options-table', 'No cash-secured put opportunities found. Check that tickers have available put data or adjust cash-fit filters.');
                    }
                }
            })
            .catch(err => console.error('Background phase error:', err));
    }

    const earningsSummary = calculateEarningsSummary();
    displayPremiumSummary(earningsSummary);
}

export {
    loadTickers,
    refreshOptionsForTicker,
    refreshOptionsForTickerByType,
    refreshAllOptions,
    sellAllOptions
};
