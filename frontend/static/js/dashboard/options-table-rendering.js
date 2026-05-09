import { state, getUnavailableTickerMessage, getRenderExpirationValue, formatExpirationLabel, loadExcludedTickers, saveOtmSettings } from './options-table-state.js';
import { calculatePremium, calculateEarningsSummary, updateEarningsSummary } from './options-table-calc.js';
import { formatCurrency, formatPercent } from '../utils/formatters.js';

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
                <strong>${title}</strong>: ${message}
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

            if (isNaN(otmPercentage) || otmPercentage < 1 || otmPercentage > 50) {
                showToast('warning', 'Invalid Value', 'OTM% must be between 1 and 50');
                return;
            }

            if (state.tickersData[ticker]) {
                if (optionType === 'CALL') {
                    state.tickersData[ticker].callOtmPercentage = otmPercentage;
                } else {
                    state.tickersData[ticker].putOtmPercentage = otmPercentage;
                }

                saveOtmSettings();

                const refreshBtn = this.closest('.input-group')?.querySelector('.refresh-otm');
                if (refreshBtn) {
                    refreshBtn.classList.add('btn-primary');
                    refreshBtn.classList.remove('btn-outline-secondary');
                    refreshBtn.innerHTML = '<i class="bi bi-arrow-repeat"></i> <strong>Apply</strong>';
                }

                showToast('info', 'OTM Updated', `Click the Apply button to refresh ${ticker} options with ${otmPercentage}% OTM`);
            }
        });
    });
}

export function addPutQtyInputEventListeners() {
    document.querySelectorAll('.put-qty-input').forEach(input => {
        input.addEventListener('change', function() {
            const ticker = this.dataset.ticker;
            const newQty = parseInt(this.value, 10);

            if (state.tickersData[ticker]) {
                state.tickersData[ticker].putQuantity = newQty;

                saveOtmSettings();
            }

            const row = this.closest('tr');
            if (row) {
                const premiumPerContract = parseFloat(row.dataset.premium) || 0;
                const strike = parseFloat(row.dataset.strike) || 0;

                const totalPremium = premiumPerContract * newQty;
                const cashRequired = strike * 100 * newQty;

                const totalPremiumCell = row.querySelector('.total-premium');
                const cashRequiredCell = row.querySelector('.cash-required');

                if (totalPremiumCell) totalPremiumCell.textContent = formatCurrency(totalPremium);
                if (cashRequiredCell) cashRequiredCell.textContent = formatCurrency(cashRequired);

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
        const sharesOwned = tickerData.data?.data?.[ticker]?.position || 0;

        if (excludedTickers.includes(ticker) && !state.customTickers.has(ticker)) {
            return false;
        }

        if (!state.customTickers.has(ticker) && sharesOwned < 100) {
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
                            min="1" max="50" step="1" 
                            value="${tickerData.callOtmPercentage || 10}">
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
                            min="1" max="50" step="1" 
                            value="${tickerData.putOtmPercentage || 10}">
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
                        min="1" max="100" step="1" style="width: 70px;">
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

    const midPrice = calculatePremium(option.bid, option.ask, option.last);

    row.dataset.premium = midPrice * 100;
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

        const premiumPerContract = midPrice * 100;
        const totalPremium = premiumPerContract * maxContracts;

        const returnOnCapital = option.strike > 0 ? ((totalPremium / (stockPrice * 100 * maxContracts)) * 100) : 0;

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
                    <span class="badge ${scoreBadgeClass}" style="font-size: 0.7rem;" 
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
            <td class="align-middle" data-field="mid-price" data-bs-toggle="tooltip" title="Midpoint between bid and ask - target this price when selling">${midPrice ? '$ ' + midPrice.toFixed(2) : 'N/A'}</td>
            <td class="align-middle" data-bs-toggle="tooltip" title="Delta: Probability this option finishes in-the-money (0-1 scale). Lower = safer.">${option.delta ? option.delta.toFixed(2) : 'N/A'}</td>
            <td class="align-middle">
                <div data-bs-toggle="tooltip" title="Implied Volatility: Market's expectation of price movement. Higher IV = more premium.">${ivPercent}%</div>
                <div>${ivRankBadge}</div>
            </td>
            <td class="align-middle" data-bs-toggle="tooltip" title="Number of contracts you can sell (1 contract = 100 shares)">${maxContracts}</td>
            <td class="align-middle">
                <div class="fw-bold" data-bs-toggle="tooltip" title="Total premium you'll receive immediately when selling">$ ${totalPremium.toFixed(2)}</div>
                <div class="small text-muted" data-bs-toggle="tooltip" title="Return if your shares get called away at the strike price">Max if-called: ${formatPercent(option.if_called_return || 0)}</div>
            </td>
            <td class="align-middle d-flex">
                <button class="btn btn-sm btn-outline-success sell-option me-2" 
                    data-ticker="${ticker}" 
                    data-option-type="CALL" 
                    data-strike="${option.strike || 0}" 
                    data-expiration="${option.expiration || ''}"
                    data-bid="${option.bid || 0}"
                    data-ask="${option.ask || 0}"
                    data-last="${option.last || 0}"
                    data-delta="${option.delta || 0}"
                    data-gamma="${option.gamma || 0}"
                    data-theta="${option.theta || 0}"
                    data-vega="${option.vega || 0}"
                    data-implied-volatility="${option.implied_volatility || 0}"
                    data-volume="${option.volume || 0}"
                    data-open-interest="${option.open_interest || 0}"
                    data-bs-toggle="tooltip" 
                    title="Add this covered call order to pending orders">
                    <i class="bi bi-check-circle"></i> Add
                </button>
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
                    <span class="badge ${scoreBadgeClass}" style="font-size: 0.7rem;" 
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
            <td class="align-middle" data-field="mid-price" data-bs-toggle="tooltip" title="Midpoint between bid and ask - target this price when selling">${midPrice ? '$ ' + midPrice.toFixed(2) : 'N/A'}</td>
            <td class="align-middle" data-bs-toggle="tooltip" title="Delta: Probability this option finishes in-the-money (0-1 scale). Lower = safer.">${option.delta ? option.delta.toFixed(2) : 'N/A'}</td>
            <td class="align-middle">
                <div data-bs-toggle="tooltip" title="Implied Volatility: Market's expectation of price movement. Higher IV = more premium.">${ivPercent}%</div>
                <div>${ivRankBadge}</div>
            </td>
            <td class="align-middle" data-bs-toggle="tooltip" title="Number of contracts to sell (1 contract = obligation to buy 100 shares)">
                <input type="number" class="form-control form-control-sm put-qty-input" 
                    data-ticker="${ticker}" 
                    value="${putQuantity}" 
                    min="1" max="100" step="1" style="width: 70px;">
            </td>
            <td class="align-middle total-premium">
                <div class="fw-bold" data-bs-toggle="tooltip" title="Total premium you'll receive immediately when selling">$ ${(midPrice * 100 * putQuantity).toFixed(2)}</div>
                <div class="small text-muted" data-bs-toggle="tooltip" title="Breakeven: Stock price where you neither gain nor lose">BE: ${formatCurrency(option.breakeven || 0)}</div>
            </td>
            <td class="align-middle cash-required">
                <div data-bs-toggle="tooltip" title="Cash needed in your account as collateral">$ ${((option.strike || 0) * 100 * putQuantity).toFixed(2)}</div>
                <div class="small text-muted" data-bs-toggle="tooltip" title="How far stock can fall before you lose money">Buffer: ${formatPercent(option.breakeven_buffer_pct || 0)}</div>
            </td>
            <td class="align-middle d-flex">
                <button class="btn btn-sm btn-outline-success sell-option me-2" 
                    data-ticker="${ticker}" 
                    data-option-type="PUT" 
                    data-strike="${option.strike || 0}" 
                    data-expiration="${option.expiration || ''}"
                    data-bid="${option.bid || 0}"
                    data-ask="${option.ask || 0}"
                    data-last="${option.last || 0}"
                    data-delta="${option.delta || 0}"
                    data-gamma="${option.gamma || 0}"
                    data-theta="${option.theta || 0}"
                    data-vega="${option.vega || 0}"
                    data-implied-volatility="${option.implied_volatility || 0}"
                    data-volume="${option.volume || 0}"
                    data-open-interest="${option.open_interest || 0}"
                    data-bs-toggle="tooltip" 
                    title="Add this cash-secured put order to pending orders">
                    <i class="bi bi-check-circle"></i> Add
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
        row.innerHTML = `
            <td colspan="13" class="text-center p-3">
                <div class="alert alert-info m-0">
                    No ${optionType === 'CALL' ? 'covered call' : 'cash secured put'} opportunities found.
                    ${optionType === 'PUT' ? 'Add a ticker to see put option opportunities.' : ''}
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

    let sufficientSharesCount = 0;
    let insufficientSharesCount = 0;
    let filteredTickers = [];
    let visibleTickers = [];

    const eligibleTickers = tickers.filter(ticker => {
        const tickerData = state.tickersData[ticker];

        if (!tickerData || !tickerData.data || !tickerData.data.data || !tickerData.data.data[ticker]) {
            return true;
        }

        const optionData = tickerData.data.data[ticker];
        const sharesOwned = optionData.position || 0;

        if (!state.customTickers.has(ticker) && sharesOwned < 100) {
            insufficientSharesCount++;
            return state.customTickers.has(ticker);
        }

        sufficientSharesCount++;
        return true;
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
                    <button class="btn btn-sm btn-outline-success me-2" id="sell-all-calls">
                        <i class="bi bi-check2-all"></i> Add All
                    </button>
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
                    <button class="btn btn-sm btn-outline-success me-2" id="sell-all-puts">
                        <i class="bi bi-check2-all"></i> Add All
                    </button>
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
