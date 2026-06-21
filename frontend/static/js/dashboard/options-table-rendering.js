import { state, getUnavailableTickerMessage, getRenderExpirationValue, formatExpirationLabel, loadExcludedTickers, saveOtmSettings, getOtmBounds, normalizeOtmValue } from './options-table-state.js';
import { calculateEarningsSummary, getPremiumPerContract, updateEarningsSummary } from './options-table-calc.js';
import { formatCurrency, formatPercent } from '../utils/formatters.js';

function sanitize(str) {
    const el = document.createElement('div');
    el.textContent = str;
    return el.innerHTML;
}

export function showToast(type, title, message) {
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        const container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'position-fixed bottom-0 end-0 p-3';
        container.style.zIndex = '5';
        document.body.appendChild(container);
        toastContainer = container;
    }

    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-white bg-${type === 'success' ? 'success' : type === 'warning' ? 'warning' : type === 'error' ? 'danger' : 'primary'}`;
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');

    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                <strong>${sanitize(title)}</strong>: ${sanitize(message)}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
    `;

    toastContainer.appendChild(toast);

    const bsToast = new bootstrap.Toast(toast, { autohide: true, delay: 3000 });
    bsToast.show();

    toast.addEventListener('hidden.bs.toast', () => {
        toast.remove();
    });
}

export function displayPremiumSummary(summary) {
    const optionsTableContainer = document.getElementById('options-table-container');
    if (!optionsTableContainer) return;

    const existingSummary = optionsTableContainer.querySelector('.card.shadow-sm.mt-4');
    if (existingSummary) {
        existingSummary.remove();
    }

    const earningsSummaryHTML = `
        <div class="card shadow-sm mt-4">
            <div class="card-header d-flex justify-content-between align-items-center bg-body-tertiary py-2">
                <h6 class="mb-0">Estimated Earnings Summary</h6>
            </div>
            <div class="card-body py-2">
                <table class="table table-sm table-borderless mb-0">
                    <tbody>
                        <tr>
                            <td width="14%" class="fw-bold">Weekly Premium:</td>
                            <td width="14%">Calls: ${formatCurrency(summary.totalWeeklyCallPremium)}</td>
                            <td width="14%">Puts: ${formatCurrency(summary.totalWeeklyPutPremium)}</td>
                            <td width="18%" class="fw-bold">Total: ${formatCurrency(summary.totalWeeklyPremium)}</td>
                            <td width="14%" class="fw-bold">Weekly Return:</td>
                            <td width="12%">${formatPercent(summary.weeklyReturn)}</td>
                            <td width="14%" class="fw-bold text-success">Annual: ${formatPercent(summary.projectedAnnualReturn)}</td>
                        </tr>
                        <tr>
                            <td class="fw-bold">Projected Income:</td>
                            <td colspan="6">${formatCurrency(summary.projectedAnnualEarnings)}</td>
                        </tr>
                    </tbody>
                </table>
            </div>
            <div class="card-footer py-1">
                <small class="text-muted">Projected earnings assume selling the same options weekly for 52 weeks (annualized).</small>
            </div>
        </div>
    `;

    optionsTableContainer.insertAdjacentHTML('beforeend', earningsSummaryHTML);
}

export function addOtmInputEventListeners() {
    document.querySelectorAll('.otm-input').forEach(input => {
        const newInput = input.cloneNode(true);
        input.parentNode.replaceChild(newInput, input);

        newInput.addEventListener('change', function() {
            const ticker = this.dataset.ticker;
            const otmPercentage = parseInt(this.value, 10);
            const optionType = this.dataset.optionType || 'CALL';
            const bounds = getOtmBounds(optionType);
            const normalizedOtm = normalizeOtmValue(optionType, otmPercentage);
            this.value = normalizedOtm;
            if (isNaN(otmPercentage) || otmPercentage < bounds.min || otmPercentage > bounds.max) {
                showToast('warning', 'Invalid Value', `${optionType === 'PUT' ? 'CSP' : 'Call'} OTM% must be between ${bounds.min} and ${bounds.max}`);
            }

            if (state.tickersData[ticker]) {
                if (optionType === 'CALL') {
                    state.tickersData[ticker].callOtmPercentage = normalizedOtm;
                } else {
                    state.tickersData[ticker].putOtmPercentage = normalizedOtm;
                }

                saveOtmSettings();

                const refreshBtn = this.closest('.input-group')?.querySelector('.refresh-otm');
                if (refreshBtn) {
                    refreshBtn.classList.add('btn-primary');
                    refreshBtn.classList.remove('btn-outline-secondary');
                    refreshBtn.innerHTML = '<i class="bi bi-arrow-repeat"></i> <strong>Apply</strong>';
                }

                showToast('info', 'OTM Updated', `Click the Apply button to refresh ${ticker} options with ${normalizedOtm}% OTM`);
            }
        });
    });
}

export function addPutQtyInputEventListeners() {
    document.querySelectorAll('.put-qty-input').forEach(input => {
        input.addEventListener('change', function() {
            const ticker = this.dataset.ticker;
            const maxQty = parseInt(this.max || '100', 10);
            let newQty = parseInt(this.value, 10);

            if (Number.isNaN(newQty) || newQty < 1) {
                newQty = 1;
            }
            if (!Number.isNaN(maxQty) && maxQty > 0 && newQty > maxQty) {
                newQty = maxQty;
                showToast('warning', 'Quantity capped', `CSP quantity capped at ${maxQty} by available buying power.`);
            }
            this.value = newQty;

            if (state.tickersData[ticker]) {
                state.tickersData[ticker].putQuantity = newQty;

                saveOtmSettings();
            }

            const row = this.closest('tr');
            if (row) {
                const premiumPerContract = row.dataset.premium === '' ? null : parseFloat(row.dataset.premium);
                const strike = parseFloat(row.dataset.strike) || 0;

                const totalPremium = premiumPerContract != null ? premiumPerContract * newQty : null;
                const cashRequired = strike * 100 * newQty;

                const totalPremiumCell = row.querySelector('.total-premium');
                const cashRequiredCell = row.querySelector('.cash-required');

                if (totalPremiumCell) {
                    totalPremiumCell.innerHTML = totalPremium != null
                        ? `<div class="fw-bold">${formatCurrency(totalPremium)}</div>`
                        : `<div class="fw-bold">&mdash;</div>`;
                }
                if (cashRequiredCell) {
                    cashRequiredCell.innerHTML = `<div>${formatCurrency(cashRequired)}</div>`;
                }

                updateEarningsSummary();
            }
        });
    });
}

export function initializeOptionsTableTooltips() {
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('#call-options-table [data-bs-toggle="tooltip"], #put-options-table [data-bs-toggle="tooltip"]'));
    tooltipTriggerList.forEach(function (tooltipTriggerEl) {
        const existingTooltip = bootstrap.Tooltip.getInstance(tooltipTriggerEl);
        if (!existingTooltip) {
            new bootstrap.Tooltip(tooltipTriggerEl);
        }
    });
}

export function addTickerRowToTable(tableId, optionType, ticker) {
    const table = document.getElementById(tableId);
    if (!table) {
        console.error(`Table with ID ${tableId} not found in the DOM`);
        return false;
    }

    const tbody = table.querySelector('tbody');
    if (!tbody) {
        console.error(`Table body not found in table with ID ${tableId}`);
        return false;
    }

    const tickerData = state.tickersData[ticker];
    if (!tickerData) {
        return false;
    }

    const excludedTickers = loadExcludedTickers();

    if (optionType === 'CALL') {
        const sharesOwned = tickerData.data?.data?.[ticker]?.position || 0;

        if (sharesOwned < 100) {
            return false;
        }
    } else if (optionType === 'PUT') {
        const isCustom = state.customTickers.has(ticker);
        const isWatchlist = state.watchlistTickers?.has(ticker);

        if (excludedTickers.includes(ticker) && !isCustom && !isWatchlist) {
            return false;
        }
    }

    let optionData, options;

    if (tickerData.data?.data?.[ticker]) {
        optionData = tickerData.data.data[ticker];
        options = optionType === 'CALL' ? optionData.calls : optionData.puts;
    } else {
        options = [];
    }

    const row = document.createElement('tr');

    row.dataset.ticker = ticker;

    const stockPrice = optionData?.stock_price || 0;
    const sharesOwned = optionData?.position || 0;
    const optionError = tickerData.errors?.[optionType] || '';
    const callOtmBounds = getOtmBounds('CALL');
    const putOtmBounds = getOtmBounds('PUT');

    if (!options || options.length === 0) {
        if (optionError) {
            row.innerHTML = `
                <td colspan="13" class="text-center p-3">
                    <div class="alert alert-warning m-0">
                        <div class="fw-semibold">${ticker} ${optionType === 'CALL' ? 'covered call' : 'cash-secured put'} data unavailable</div>
                        <div class="small mt-1">${optionError}</div>
                    </div>
                </td>
            `;
            tbody.appendChild(row);
            return true;
        }

        if (optionType === 'PUT') {
            return false;
        }

        if (optionType === 'CALL') {
            const maxContracts = Math.floor(sharesOwned / 100);
            const selectedExpirationValue = getRenderExpirationValue(ticker, 'CALL');

            let expirationOptionsHtml = '';
            const expirations = state.tickersData[ticker].expirations || [];
            if (expirations.length > 0) {
                expirations.forEach((exp, index) => {
                    const selected = exp.value === selectedExpirationValue || (!selectedExpirationValue && index === 0) ? 'selected' : '';
                    expirationOptionsHtml += `<option value="${exp.value}" ${selected}>${exp.label}</option>`;
                });
            } else {
                expirationOptionsHtml = `<option value="">No expirations available</option>`;
            }

            row.innerHTML = `
                <td class="align-middle">${ticker}</td>
                <td class="align-middle">${sharesOwned}</td>
                <td class="align-middle">${stockPrice ? '$ ' + stockPrice.toFixed(2) : 'N/A'}</td>
                <td class="align-middle">
                    <div class="input-group input-group-sm">
                        <input type="number" class="form-control form-control-sm otm-input" 
                            data-ticker="${ticker}" 
                            data-option-type="CALL"
                            min="${callOtmBounds.min}" max="${callOtmBounds.max}" step="1" 
                            value="${normalizeOtmValue('CALL', tickerData.callOtmPercentage ?? callOtmBounds.defaultValue)}">
                        <button class="btn btn-outline-secondary btn-sm refresh-otm" data-ticker="${ticker}">
                            <i class="bi bi-arrow-repeat"></i>
                        </button>
                    </div>
                </td>
                <td class="align-middle">-</td>
                <td class="align-middle">
                    <select class="form-select form-select-sm expiration-select" data-ticker="${ticker}" data-option-type="CALL">
                        ${expirationOptionsHtml}
                    </select>
                    <div class="small text-muted mt-1">No ranked calls for this expiration yet</div>
                </td>
                <td class="align-middle">-</td>
                <td class="align-middle">-</td>
                <td class="align-middle">-</td>
                <td class="align-middle">${maxContracts}</td>
                <td class="align-middle">$ 0.00</td>
                <td class="align-middle">
                    <button class="btn btn-sm btn-outline-secondary refresh-option" 
                        data-ticker="${ticker}" 
                        data-type="CALL"
                        data-bs-toggle="tooltip" 
                        title="Refresh options data">
                        <i class="bi bi-arrow-repeat"></i> Refresh
                    </button>
                </td>
            `;
        } else {
            const putQuantity = tickerData.putQuantity || 1;
            const selectedExpirationValue = getRenderExpirationValue(ticker, 'PUT');

            let expirationOptionsHtml = '';
            const expirations = state.tickersData[ticker].expirations || [];
            if (expirations.length > 0) {
                expirations.forEach((exp, index) => {
                    const selected = exp.value === selectedExpirationValue || (!selectedExpirationValue && index === 0) ? 'selected' : '';
                    expirationOptionsHtml += `<option value="${exp.value}" ${selected}>${exp.label}</option>`;
                });
            } else {
                expirationOptionsHtml = `<option value="">No expirations available</option>`;
            }

            row.innerHTML = `
                <td class="align-middle">
                    ${ticker}
                    <button class="btn btn-sm btn-outline-danger ms-2 delete-ticker" data-ticker="${ticker}">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>
                <td class="align-middle">${stockPrice ? '$ ' + stockPrice.toFixed(2) : 'N/A'}</td>
                <td class="align-middle">
                    <div class="input-group input-group-sm">
                        <input type="number" class="form-control form-control-sm otm-input" 
                            data-ticker="${ticker}" 
                            data-option-type="PUT"
                            min="${putOtmBounds.min}" max="${putOtmBounds.max}" step="1" 
                            value="${normalizeOtmValue('PUT', tickerData.putOtmPercentage ?? putOtmBounds.defaultValue)}">
                        <button class="btn btn-outline-secondary btn-sm refresh-otm" data-ticker="${ticker}"
                            data-bs-toggle="tooltip" data-bs-placement="top" title="Refresh with new OTM %">
                            <i class="bi bi-arrow-repeat"></i>
                        </button>
                    </div>
                </td>
                <td class="align-middle">-</td>
                <td class="align-middle">
                    <select class="form-select form-select-sm expiration-select" data-ticker="${ticker}" data-option-type="PUT">
                        ${expirationOptionsHtml}
                    </select>
                    <div class="small text-muted mt-1">No ranked puts for this expiration yet</div>
                </td>
                <td class="align-middle">-</td>
                <td class="align-middle">-</td>
                <td class="align-middle">-</td>
                <td class="align-middle">
                    <input type="number" class="form-control form-control-sm put-qty-input" 
                        data-ticker="${ticker}" 
                        value="${putQuantity}" 
                        min="1" max="100" step="1">
                </td>
                <td class="align-middle total-premium">$ 0.00</td>
                <td class="align-middle cash-required">$ 0.00</td>
                <td class="align-middle d-flex">
                    <button class="btn btn-sm btn-outline-secondary refresh-option me-2" 
                        data-ticker="${ticker}" 
                        data-type="PUT"
                        data-bs-toggle="tooltip" 
                        title="Refresh options data">
                        <i class="bi bi-arrow-repeat"></i> Refresh
                    </button>
                    <button class="btn btn-sm btn-outline-danger delete-ticker" 
                        data-ticker="${ticker}" 
                        data-bs-toggle="tooltip" 
                        title="Remove ticker">
                        <i class="bi bi-x"></i>
                    </button>
                </td>
            `;
        }
        tbody.appendChild(row);
        return true;
    }

    const option = options[0];
    if (!option) {
        return false;
    }

    const score = option.score || 0;
    const annualizedReturn = option.annualized_return || 0;
    const rationaleText = (option.rationale || []).join(' | ').replace(/"/g, '&quot;');

    const warnings = option.warnings || [];
    const warningHtml = warnings.length > 0 ? 
        `<div class="small mt-1">
            ${warnings.map(w => {
                let context = '';
                let icon = 'bi-exclamation-triangle';
                if (w.toLowerCase().includes('spread')) {
                    context = ' - May be hard to exit profitably';
                    icon = 'bi-arrows-angle-expand';
                } else if (w.toLowerCase().includes('volume')) {
                    context = ' - Low liquidity risk';
                    icon = 'bi-graph-down';
                } else if (w.toLowerCase().includes('iv')) {
                    context = ' - Volatility may be declining';
                    icon = 'bi-activity';
                } else if (w.toLowerCase().includes('dte') || w.toLowerCase().includes('expiration')) {
                    context = ' - Time decay accelerates';
                    icon = 'bi-clock';
                } else if (w.toLowerCase().includes('delta')) {
                    context = ' - Higher assignment risk';
                    icon = 'bi-shield-exclamation';
                }
                return `<div class="text-warning"><i class="bi ${icon} me-1"></i>${w}${context}</div>`;
            }).join('')}
        </div>` : '';

    const scoreBadgeClass = score >= 80 ? 'bg-success' : (score >= 65 ? 'bg-primary' : 'bg-warning text-dark');

    const premiumPerContract = getPremiumPerContract(option);
    const midPrice = option.mid_price != null
        ? Number(option.mid_price)
        : (premiumPerContract != null ? premiumPerContract / 100 : null);

    row.dataset.premium = premiumPerContract != null ? premiumPerContract : '';
    row.dataset.strike = option.strike || 0;

    const ivPercent = option.implied_volatility ? option.implied_volatility.toFixed(2) : 'N/A';

    const ivRankBadge = option.iv_rank !== undefined ? 
        `<span class="badge ${option.iv_rank < 30 ? 'bg-danger' : option.iv_rank > 70 ? 'bg-success' : 'bg-secondary'}" 
               data-bs-toggle="tooltip" 
               data-bs-placement="top" 
               title="IV Rank shows if volatility is ${option.iv_rank < 30 ? 'LOW (cheaper options)' : option.iv_rank > 70 ? 'HIGH (expensive options - good for selling)' : 'moderate'} relative to the past year">
            IV Rank: ${option.iv_rank.toFixed(0)}%
        </span>` : '';

    if (optionType === 'CALL') {
        const maxContracts = Math.floor(sharesOwned / 100);
        const selectedExpirationValue = getRenderExpirationValue(ticker, 'CALL', option.expiration);

        const totalPremium = premiumPerContract != null ? premiumPerContract * maxContracts : null;

        let expirationOptionsHtml = '';
        const expirations = state.tickersData[ticker].expirations || [];
        if (expirations.length > 0) {
            expirations.forEach(exp => {
                const selected = exp.value === selectedExpirationValue ? 'selected' : '';
                expirationOptionsHtml += `<option value="${exp.value}" ${selected}>${exp.label}</option>`;
            });
        } else {
            expirationOptionsHtml = `<option value="${selectedExpirationValue}" selected>${formatExpirationLabel(selectedExpirationValue)}</option>`;
        }

        row.innerHTML = `
            <td class="align-middle">
                <div class="fw-semibold" title="${rationaleText}">${ticker}</div>
                <div class="d-flex align-items-center gap-1 mt-1">
                    <span class="badge ${scoreBadgeClass} badge--compact" 
                          data-bs-toggle="tooltip" 
                          title="Quality score: ${score >= 80 ? 'High' : score >= 65 ? 'Good' : 'Fair - check warnings'} quality opportunity">
                        Score: ${score.toFixed(1)}
                    </span>
                    <span class="small text-muted">|</span>
                    <span class="small" data-bs-toggle="tooltip" title="Annualized return if you sell this option every week for a year">
                        ${annualizedReturn.toFixed(1)}% annualized
                    </span>
                </div>
                ${warningHtml}
            </td>
            <td class="align-middle">${sharesOwned}</td>
            <td class="align-middle">${stockPrice ? '$ ' + stockPrice.toFixed(2) : 'N/A'}</td>
            <td class="align-middle">
                <div class="input-group input-group-sm">
                    <input type="number" class="form-control form-control-sm otm-input" 
                        data-ticker="${ticker}" 
                        data-option-type="CALL"
                        min="1" max="50" step="1" 
                        value="${tickerData.callOtmPercentage || 10}"
                        data-bs-toggle="tooltip" 
                        title="Out-of-the-Money %: Distance from current stock price. Higher = safer but less premium.">
                    <button class="btn btn-outline-secondary btn-sm refresh-otm" data-ticker="${ticker}"
                        data-bs-toggle="tooltip" data-bs-placement="top" title="Refresh with new OTM %">
                        <i class="bi bi-arrow-repeat"></i>
                    </button>
                </div>
            </td>
            <td class="align-middle" data-bs-toggle="tooltip" title="Price where your shares would be called away">${option.strike ? '$ ' + option.strike.toFixed(2) : 'N/A'}</td>
            <td class="align-middle">
                <select class="form-select form-select-sm expiration-select" data-ticker="${ticker}" data-option-type="CALL">
                    ${expirationOptionsHtml}
                </select>
                <div class="small text-muted mt-1" data-bs-toggle="tooltip" title="Days To Expiration: Time until option expires">${option.dte || '-'} DTE</div>
            </td>
            <td class="align-middle" data-field="mid-price" data-bs-toggle="tooltip" title="Midpoint between bid and ask - target this price when selling">${midPrice != null ? '$ ' + midPrice.toFixed(2) : '&mdash;'}</td>
            <td class="align-middle" data-bs-toggle="tooltip" title="Delta: Probability this option finishes in-the-money (0-1 scale). Lower = safer.">${option.delta ? option.delta.toFixed(2) : 'N/A'}</td>
            <td class="align-middle">
                <div data-bs-toggle="tooltip" title="Implied Volatility: Market's expectation of price movement. Higher IV = more premium.">${ivPercent}%</div>
                <div>${ivRankBadge}</div>
            </td>
            <td class="align-middle" data-bs-toggle="tooltip" title="Number of contracts you can sell (1 contract = 100 shares)">${maxContracts}</td>
            <td class="align-middle">
                <div class="fw-bold" data-bs-toggle="tooltip" title="Total premium you'll receive immediately when selling">${totalPremium != null ? `$ ${totalPremium.toFixed(2)}` : '—'}</div>
                <div class="small text-muted" data-bs-toggle="tooltip" title="Return if your shares get called away at the strike price">Max if-called: ${formatPercent(option.if_called_return || 0)}</div>
            </td>
            <td class="align-middle d-flex">
                <span class="badge bg-light text-muted border small me-2"
                    data-bs-toggle="tooltip" 
                    title="Signal only — review trades in your broker app">
                    <i class="bi bi-eye"></i> Signal
                </span>
                <button class="btn btn-sm btn-outline-danger delete-ticker" 
                    data-ticker="${ticker}" 
                    data-bs-toggle="tooltip" 
                    title="Remove ticker">
                    <i class="bi bi-x"></i>
                </button>
            </td>
        `;
    } else {
        const putQuantity = tickerData.putQuantity || 1;
        const selectedExpirationValue = getRenderExpirationValue(ticker, 'PUT', option.expiration);
        const cspBuyingPower = state.portfolioSummary?.cash_available_for_csp ||
            state.portfolioSummary?.available_cash ||
            state.portfolioSummary?.cash_balance ||
            0;
        const maxAffordableContracts = option.strike > 0 && cspBuyingPower > 0
            ? Math.max(1, Math.floor(cspBuyingPower / (option.strike * 100)))
            : 100;

        let expirationOptionsHtml = '';
        const expirations = state.tickersData[ticker].expirations || [];
        if (expirations.length > 0) {
            expirations.forEach((exp, index) => {
                const selected = exp.value === selectedExpirationValue || (!selectedExpirationValue && index === 0) ? 'selected' : '';
                expirationOptionsHtml += `<option value="${exp.value}" ${selected}>${exp.label}</option>`;
            });
        } else {
            expirationOptionsHtml = `<option value="${selectedExpirationValue}" selected>${formatExpirationLabel(selectedExpirationValue)}</option>`;
        }

        // ── Concentration risk warning for held tickers ──────────────
        // When the user already holds shares and adds a CSP on the same
        // ticker, the combined cash outlay can create concentration risk.
        let concWarningHtml = '';
        const cashBalance = state.portfolioSummary?.cash_balance || 0;
        const sharesOwned = optionData?.position || 0;
        const isHeld = state.portfolioTickers?.includes(ticker) && sharesOwned > 0;
        if (isHeld && cashBalance > 0 && option.strike > 0) {
            const cspCost = option.strike * 100 * putQuantity;
            const concRatio = cspCost / cashBalance;
            if (concRatio > 0.30) {
                const concPct = Math.round(concRatio * 100);
                concWarningHtml = `<div class="small mt-1 text-warning"><i class="bi bi-exclamation-triangle me-1"></i>Held + CSP: ${concPct}% of cash</div>`;
            }
        }

        row.innerHTML = `
            <td class="align-middle">
                <div class="fw-semibold" title="${rationaleText}">${ticker}</div>
                <div class="d-flex align-items-center gap-1 mt-1">
                    <span class="badge ${scoreBadgeClass} badge--compact" 
                          data-bs-toggle="tooltip" 
                          title="Quality score: ${score >= 80 ? 'High' : score >= 65 ? 'Good' : 'Fair - check warnings'} quality opportunity">
                        Score: ${score.toFixed(1)}
                    </span>
                    <span class="small text-muted">|</span>
                    <span class="small" data-bs-toggle="tooltip" title="Annualized return if you sell this option every week for a year">
                        ${annualizedReturn.toFixed(1)}% annualized
                    </span>
                </div>
                ${warningHtml}
                ${concWarningHtml}
            </td>
            <td class="align-middle">${stockPrice ? '$ ' + stockPrice.toFixed(2) : 'N/A'}</td>
            <td class="align-middle">
                <div class="input-group input-group-sm">
                    <input type="number" class="form-control form-control-sm otm-input" 
                        data-ticker="${ticker}" 
                        data-option-type="PUT"
                        min="1" max="50" step="1" 
                        value="${tickerData.putOtmPercentage || 10}"
                        data-bs-toggle="tooltip" 
                        title="Out-of-the-Money %: Distance from current stock price. Higher = safer but less premium.">
                    <button class="btn btn-outline-secondary btn-sm refresh-otm" data-ticker="${ticker}"
                        data-bs-toggle="tooltip" data-bs-placement="top" title="Refresh with new OTM %">
                        <i class="bi bi-arrow-repeat"></i>
                    </button>
                </div>
            </td>
            <td class="align-middle" data-bs-toggle="tooltip" title="Price where you'd be obligated to buy shares">${option.strike ? '$ ' + option.strike.toFixed(2) : 'N/A'}</td>
            <td class="align-middle">
                <select class="form-select form-select-sm expiration-select" data-ticker="${ticker}" data-option-type="PUT">
                    ${expirationOptionsHtml}
                </select>
                <div class="small text-muted mt-1" data-bs-toggle="tooltip" title="Days To Expiration: Time until option expires">${option.dte || '-'} DTE</div>
            </td>
            <td class="align-middle" data-field="mid-price" data-bs-toggle="tooltip" title="Midpoint between bid and ask - target this price when selling">${midPrice != null ? '$ ' + midPrice.toFixed(2) : '&mdash;'}</td>
            <td class="align-middle" data-bs-toggle="tooltip" title="Delta: Probability this option finishes in-the-money (0-1 scale). Lower = safer.">${option.delta ? option.delta.toFixed(2) : 'N/A'}</td>
            <td class="align-middle">
                <div data-bs-toggle="tooltip" title="Implied Volatility: Market's expectation of price movement. Higher IV = more premium.">${ivPercent}%</div>
                <div>${ivRankBadge}</div>
            </td>
            <td class="align-middle" data-bs-toggle="tooltip" title="Number of contracts to sell (1 contract = obligation to buy 100 shares)">
                <input type="number" class="form-control form-control-sm put-qty-input" 
                    data-ticker="${ticker}" 
                    value="${putQuantity}" 
                    min="1" max="${maxAffordableContracts}" step="1">
            </td>
            <td class="align-middle total-premium">
                <div class="fw-bold" data-bs-toggle="tooltip" title="Total premium you'll receive immediately when selling">${premiumPerContract != null ? `$ ${(premiumPerContract * putQuantity).toFixed(2)}` : '—'}</div>
                <div class="small text-muted" data-bs-toggle="tooltip" title="Breakeven: Stock price where you neither gain nor lose">BE: ${formatCurrency(option.breakeven || 0)}</div>
            </td>
            <td class="align-middle cash-required">
                <div data-bs-toggle="tooltip" title="Cash needed in your account as collateral">$ ${((option.strike || 0) * 100 * putQuantity).toFixed(2)}</div>
                <div class="small text-muted" data-bs-toggle="tooltip" title="How far stock can fall before you lose money">Buffer: ${formatPercent(option.breakeven_buffer_pct || 0)}</div>
            </td>
            <td class="align-middle d-flex">
                <span class="badge bg-light text-muted border small me-2"
                    data-bs-toggle="tooltip" 
                    title="Signal only — review trades in your broker app">
                    <i class="bi bi-eye"></i> Signal
                </span>
                <button class="btn btn-sm btn-outline-danger delete-ticker" 
                    data-ticker="${ticker}" 
                    data-bs-toggle="tooltip" 
                    title="Remove ticker">
                    <i class="bi bi-x"></i>
                </button>
            </td>
        `;
    }

    tbody.appendChild(row);
    return true;
}

/**
 * Insert a compact inline progress banner above the options tab content.
 * Shows overall percent, ticker count, current ticker, and step text.
 */
export function insertProgressBanner(totalTickers) {
    const container = document.getElementById('options-table-container');
    if (!container) return;

    const existing = container.querySelector('.options-table-progress');
    if (existing) existing.remove();

    const banner = document.createElement('div');
    banner.className = 'options-table-progress alert alert-info py-2 px-3 mb-3';
    banner.id = 'options-load-progress';
    banner.innerHTML = `
        <div class="d-flex align-items-center w-100 gap-inline-3">
            <div class="spinner-border spinner-border-sm text-primary flex-shrink-0" role="status"></div>
            <div class="flex-grow-1 min-w-0">
                <div class="d-flex align-items-center gap-inline-2">
                    <span class="fw-semibold small" id="progress-percent">0%</span>
                    <div class="progress flex-grow-1 progress--sm">
                        <div class="progress-bar progress-bar-striped progress-bar-animated" id="progress-bar" role="progressbar" style="width: 0%"></div>
                    </div>
                    <span class="small text-muted" id="progress-count">0/${totalTickers}</span>
                </div>
                <div class="small text-muted mt-1" id="progress-detail">Initializing...</div>
            </div>
        </div>
    `;

    const tabContent = container.querySelector('.tab-content');
    if (tabContent) {
        container.insertBefore(banner, tabContent);
    } else {
        container.appendChild(banner);
    }
}

/**
 * Update the progress banner with current loading state.
 * @param {number} current - Current ticker index (1-based)
 * @param {number} total - Total number of tickers
 * @param {string} ticker - Current ticker symbol
 * @param {string} step - Current step description (e.g. 'loading expirations', 'loading calls')
 * @param {string|null} error - Optional error message to display for the current ticker
 */
export function updateProgressBanner(current, total, ticker, step, error) {
    const percent = total > 0 ? Math.round((current / total) * 100) : 0;
    const bar = document.getElementById('progress-bar');
    const percentEl = document.getElementById('progress-percent');
    const countEl = document.getElementById('progress-count');
    const detailEl = document.getElementById('progress-detail');

    if (bar) bar.style.width = `${percent}%`;
    if (percentEl) percentEl.textContent = `${percent}%`;
    if (countEl) countEl.textContent = `${current}/${total}`;
    if (detailEl) {
        if (error) {
            detailEl.innerHTML = `<span class="text-danger">${ticker}: ${error}</span>`;
        } else {
            detailEl.textContent = `${ticker}: ${step}`;
        }
    }
}

/**
 * Mark progress banner as finished and auto-dismiss after a short delay.
 */
export function finishProgressBanner() {
    const banner = document.getElementById('options-load-progress');
    if (!banner) return;

    banner.className = 'options-table-progress alert alert-success py-2 px-3 mb-3';
    banner.innerHTML = `
        <div class="d-flex align-items-center w-100 gap-inline-3">
            <i class="bi bi-check-circle-fill text-success flex-shrink-0"></i>
            <div class="flex-grow-1">
                <span class="fw-semibold small">Loading complete</span>
            </div>
        </div>
    `;
    setTimeout(() => {
        banner.remove();
    }, 2500);
}

/**
 * Mark progress banner as failed with a message.
 */
export function failProgressBanner(message) {
    const banner = document.getElementById('options-load-progress');
    if (!banner) return;

    banner.className = 'options-table-progress alert alert-danger py-2 px-3 mb-3';
    banner.innerHTML = `
        <div class="d-flex align-items-center w-100 gap-inline-3">
            <i class="bi bi-exclamation-triangle-fill text-danger flex-shrink-0"></i>
            <div class="flex-grow-1">
                <span class="fw-semibold small">${sanitize(message)}</span>
            </div>
        </div>
    `;
}

// ── Shared panel-level progress helpers ──────────────────────────────────
// These are lightweight wrappers for single-request panels (Top Recommendations,
// Earnings Vol Signals) that need visible loading feedback without the full
// multi-step progress bar machinery of the options table.

/**
 * Insert a compact loading banner inside a panel's container.
 * The banner sits at the top of the section and does not block other panels.
 * @param {string} containerId - ID of the container element to insert into
 * @param {string} message - Loading message text
 * @returns {string} The banner element id (for later updates)
 */
export function showPanelLoading(containerId, message) {
    const container = document.getElementById(containerId);
    if (!container) return null;

    // Remove any existing panel progress banner
    const existing = container.querySelector('.panel-progress-banner');
    if (existing) existing.remove();

    const bannerId = `${containerId}-progress`;
    const banner = document.createElement('div');
    banner.id = bannerId;
    banner.className = 'panel-progress-banner alert alert-info py-2 px-3 mb-0 d-flex align-items-center gap-inline-3';
    banner.innerHTML = `
        <div class="spinner-border spinner-border-sm text-primary flex-shrink-0" role="status"></div>
        <span class="small fw-semibold flex-grow-1" id="${bannerId}-text">${sanitize(message)}</span>
    `;

    // Insert at the top of the container (before any content/state elements)
    container.insertBefore(banner, container.firstChild);
    return bannerId;
}

/**
 * Update the message text of an active panel loading banner.
 * @param {string} bannerId - The banner element id returned by showPanelLoading
 * @param {string} message - New message text
 */
export function updatePanelLoading(bannerId, message) {
    const textEl = document.getElementById(`${bannerId}-text`);
    if (textEl) textEl.textContent = message;
}

/**
 * Finish a panel loading banner: show a brief "done" state then remove.
 * @param {string} bannerId - The banner element id returned by showPanelLoading
 * @param {string} [doneMessage] - Optional completion message (default: 'Complete')
 */
export function finishPanelLoading(bannerId, doneMessage = 'Complete') {
    const banner = document.getElementById(bannerId);
    if (!banner) return;

    banner.className = 'panel-progress-banner alert alert-success py-2 px-3 mb-0 d-flex align-items-center gap-inline-3';
    banner.innerHTML = `
        <i class="bi bi-check-circle-fill text-success flex-shrink-0"></i>
        <span class="small fw-semibold flex-grow-1">${sanitize(doneMessage)}</span>
    `;
    setTimeout(() => {
        banner.remove();
    }, 2000);
}

/**
 * Fail a panel loading banner with an error message. Keeps the banner visible.
 * @param {string} bannerId - The banner element id returned by showPanelLoading
 * @param {string} errorMessage - Error message to display
 */
export function failPanelLoading(bannerId, errorMessage) {
    const banner = document.getElementById(bannerId);
    if (!banner) return;

    banner.className = 'panel-progress-banner alert alert-danger py-2 px-3 mb-0 d-flex align-items-center gap-inline-3';
    banner.innerHTML = `
        <i class="bi bi-exclamation-triangle-fill text-danger flex-shrink-0"></i>
        <span class="small fw-semibold flex-grow-1">${sanitize(errorMessage)}</span>
    `;
}

export function buildOptionsTable(tableId, optionType) {
    const table = document.getElementById(tableId);
    if (!table) {
        console.error(`Table with ID ${tableId} not found in the DOM`);
        return;
    }

    const tbody = table.querySelector('tbody');
    if (!tbody) {
        console.error(`Table body not found in table with ID ${tableId}`);
        return;
    }

    const thead = table.querySelector('thead');
    if (thead && thead.querySelector('tr')) {
        const headerRow = thead.querySelector('tr');
        const existingIvHeader = Array.from(headerRow.querySelectorAll('th')).find(th => th.textContent === 'IV%');
        if (!existingIvHeader) {
            const deltaHeader = Array.from(headerRow.querySelectorAll('th')).find(th => th.textContent === 'Delta');
            if (deltaHeader) {
                const ivHeader = document.createElement('th');
                ivHeader.textContent = 'IV%';
                deltaHeader.after(ivHeader);
            }
        }
    }

    tbody.innerHTML = '';

    let atLeastOneRowAdded = false;

    Object.keys(state.tickersData).forEach(ticker => {
        if (addTickerRowToTable(tableId, optionType, ticker)) {
            atLeastOneRowAdded = true;
        }
    });

    if (!atLeastOneRowAdded) {
        const row = document.createElement('tr');
        const emptyMsg = optionType === 'CALL'
            ? 'No eligible covered calls. You need 100+ shares of a ticker and a ranked call contract.'
            : 'No cash-secured put opportunities found. Check that tickers have available put data or adjust cash-fit filters.';
        row.innerHTML = `
            <td colspan="13" class="text-center p-3">
                <div class="alert alert-info m-0">
                    ${emptyMsg}
                </div>
            </td>
        `;
        tbody.appendChild(row);
    }

    initializeOptionsTableTooltips();
}

export function updateOptionsTable() {
    const optionsTableContainer = document.getElementById('options-table-container');
    if (!optionsTableContainer) {
        console.error("Options table container not found in the DOM");
        return;
    }

    const putTabWasActive = document.querySelector('#put-options-tab.active') !== null ||
                           document.querySelector('#put-options-section.active') !== null;

    optionsTableContainer.innerHTML = '';

    const tickers = Object.keys(state.tickersData);

    if (tickers.length === 0) {
        optionsTableContainer.innerHTML = `<div class="alert alert-info">${getUnavailableTickerMessage()}</div>`;
        return;
    }

    const eligibleTickers = tickers.filter(ticker => {
        const tickerData = state.tickersData[ticker];

        if (!tickerData || !tickerData.data || !tickerData.data.data || !tickerData.data.data[ticker]) {
            return true;
        }

        const optionData = tickerData.data.data[ticker];

        if (state.customTickers.has(ticker) || state.watchlistTickers?.has(ticker)) {
            return true;
        }

        const sharesOwned = optionData.position || 0;
        return sharesOwned > 0;
    });

    const tabsHTML = `
        <ul class="nav nav-tabs mb-3" id="options-tabs" role="tablist">
            <li class="nav-item" role="presentation">
                <button class="nav-link ${putTabWasActive ? '' : 'active'}" id="call-options-tab" data-bs-toggle="tab" data-bs-target="#call-options-section" type="button" role="tab" aria-controls="call-options-section" aria-selected="${putTabWasActive ? 'false' : 'true'}">
                    Covered Calls
                </button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link ${putTabWasActive ? 'active' : ''}" id="put-options-tab" data-bs-toggle="tab" data-bs-target="#put-options-section" type="button" role="tab" aria-controls="put-options-section" aria-selected="${putTabWasActive ? 'true' : 'false'}">
                    Cash-Secured Puts
                </button>
            </li>
        </ul>

        <div class="tab-content" id="options-tabs-content">
            <div class="tab-pane fade ${putTabWasActive ? '' : 'show active'}" id="call-options-section" role="tabpanel" aria-labelledby="call-options-tab">
                <div class="d-flex justify-content-end mb-2">
                    <span class="badge bg-light text-muted border small me-2" id="sell-all-calls"
                        data-bs-toggle="tooltip" title="Signal only — review trades in your broker app">
                        <i class="bi bi-eye"></i> Signal Only
                    </span>
                    <button class="btn btn-sm btn-outline-primary" id="refresh-all-calls">
                        <i class="bi bi-arrow-repeat"></i> Refresh All Calls
                    </button>
                </div>
                <div class="table-responsive">
                    <table class="table table-striped table-hover table-sm" id="call-options-table">
                        <thead>
                            <tr>
                                <th data-bs-toggle="tooltip" title="Stock symbol and quality metrics">Ticker</th>
                                <th data-bs-toggle="tooltip" title="Number of shares you own (need 100+ to sell calls)">Shares</th>
                                <th data-bs-toggle="tooltip" title="Current stock market price">Stock Price</th>
                                <th data-bs-toggle="tooltip" title="Out-of-the-Money %: Distance from current price. Higher = safer but less premium.">OTM %</th>
                                <th data-bs-toggle="tooltip" title="Price where your shares would be called away">Strike</th>
                                <th data-bs-toggle="tooltip" title="When the option expires">Expiration</th>
                                <th data-bs-toggle="tooltip" title="Midpoint between bid and ask - target this price">Mid Price</th>
                                <th data-bs-toggle="tooltip" title="Probability option finishes in-the-money (0-1 scale). Lower = safer.">Delta</th>
                                <th data-bs-toggle="tooltip" title="Implied Volatility: Market's price movement expectation. Higher IV = more premium.">IV%</th>
                                <th data-bs-toggle="tooltip" title="Number of contracts you can sell (1 = 100 shares)">Qty</th>
                                <th data-bs-toggle="tooltip" title="Total income you'll receive from selling">Total Premium</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>

            <div class="tab-pane fade ${putTabWasActive ? 'show active' : ''}" id="put-options-section" role="tabpanel" aria-labelledby="put-options-tab">
                <div class="d-flex justify-content-end mb-2">
                    <span class="badge bg-light text-muted border small me-2" id="sell-all-puts"
                        data-bs-toggle="tooltip" title="Signal only — review trades in your broker app">
                        <i class="bi bi-eye"></i> Signal Only
                    </span>
                    <button class="btn btn-sm btn-outline-primary" id="refresh-all-puts">
                        <i class="bi bi-arrow-repeat"></i> Refresh All Puts
                    </button>
                </div>
                <div class="table-responsive">
                    <table class="table table-striped table-hover table-sm" id="put-options-table">
                        <thead>
                            <tr>
                                <th data-bs-toggle="tooltip" title="Stock symbol and quality metrics">Ticker</th>
                                <th data-bs-toggle="tooltip" title="Current stock market price">Stock Price</th>
                                <th data-bs-toggle="tooltip" title="Out-of-the-Money %: Distance from current price. Higher = safer but less premium.">OTM %</th>
                                <th data-bs-toggle="tooltip" title="Price where you'd be obligated to buy shares">Strike</th>
                                <th data-bs-toggle="tooltip" title="When the option expires">Expiration</th>
                                <th data-bs-toggle="tooltip" title="Midpoint between bid and ask - target this price">Mid Price</th>
                                <th data-bs-toggle="tooltip" title="Probability option finishes in-the-money (0-1 scale). Lower = safer.">Delta</th>
                                <th data-bs-toggle="tooltip" title="Implied Volatility: Market's price movement expectation. Higher IV = more premium.">IV%</th>
                                <th data-bs-toggle="tooltip" title="Number of contracts to sell (1 = obligation to buy 100 shares)">Qty</th>
                                <th data-bs-toggle="tooltip" title="Total income you'll receive from selling">Total Premium</th>
                                <th data-bs-toggle="tooltip" title="Cash needed in your account as collateral">Cash Required</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>
        </div>
    `;

    optionsTableContainer.innerHTML = tabsHTML;

    buildOptionsTable('call-options-table', 'CALL');
    buildOptionsTable('put-options-table', 'PUT');

    const earningsSummary = calculateEarningsSummary();
    displayPremiumSummary(earningsSummary);

    addPutQtyInputEventListeners();
}
