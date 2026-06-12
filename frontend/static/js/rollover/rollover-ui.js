import { rolloverState } from './rollover-state.js';
import { formatPercentage, formatDate, getBadgeColor, calculateMidPrice, calculateTargetStrike, roundStrikeToNearestHalf, parseExpirationDate, formatExpirationDisplay, addDaysToDate, formatDateToAPIfmt } from './rollover-calculator.js';
import { formatCurrency } from '../utils/formatters.js';
import { loadOptionPositions, loadPendingOrders, fetchRolloverSuggestions } from './rollover-api.js';
import { fetchOptionExpirations } from '../dashboard/api.js';

function populateOptionsTable(options, emptyMessage = 'No option positions found') {
    const tableBody = document.getElementById('option-positions-table-body');
    if (!tableBody) return;

    tableBody.innerHTML = '';

    if (options.length === 0) {
        const noDataRow = document.createElement('tr');
        noDataRow.innerHTML = `<td colspan="12" class="text-center">${emptyMessage}</td>`;
        tableBody.appendChild(noDataRow);
        return;
    }

    options.forEach((option, index) => {
        const row = document.createElement('tr');

        const rollPressure = option.roll_pressure || 0;
        if (rollPressure >= 70) {
            row.classList.add('table-danger');
        } else if (rollPressure >= 40) {
            row.classList.add('table-warning');
        } else if (option.isApproachingStrike) {
            if (option.percentDifference < 5) {
                row.classList.add('table-danger');
            } else if (option.percentDifference < 10) {
                row.classList.add('table-warning');
            }
        }

        const expiration = formatExpirationDisplay(option.expiration);

        const stockPrice = option.stockPrice > 0 ? option.stockPrice : 'Fetching...';
        const formattedDifference = formatCurrency(option.difference);
        const absolutePercentDifference = Math.abs(option.percentDifference || 0);

        let differenceColorClass = '';
        if (absolutePercentDifference < 5) {
            differenceColorClass = 'text-danger fw-bold';
        } else if (absolutePercentDifference < 10) {
            differenceColorClass = 'text-danger';
        }
        const percentDifferenceDisplay = `<span class="${differenceColorClass}">${(option.percentDifference || 0).toFixed(2)}%</span>`;

        const pressureDisplay = rollPressure > 0
            ? `<span class="${rollPressure >= 70 ? 'text-danger fw-bold' : rollPressure >= 40 ? 'text-warning fw-bold' : ''}">${rollPressure.toFixed(0)}%</span>`
            : '<span class="text-muted">—</span>';

        const profitProgress = option.profit_target_progress || 0;
        const profitProgressDisplay = profitProgress > 0
            ? `<div class="progress" style="height: 6px; min-width: 60px;">
                 <div class="progress-bar" role="progressbar" style="width: ${Math.min(profitProgress, 100)}%;"
                      aria-valuenow="${profitProgress}" aria-valuemin="0" aria-valuemax="100"></div>
               </div>
               <small class="text-muted">${profitProgress.toFixed(0)}%</small>`
            : '<span class="text-muted">—</span>';

        row.innerHTML = `
            <td>${option.ticker || option.symbol}</td>
            <td>${Math.abs(option.position || 0)}</td>
            <td>${option.optionType}</td>
            <td>${formatCurrency(option.strike)}</td>
            <td>${expiration}</td>
            <td>${typeof stockPrice === 'number' ? formatCurrency(stockPrice) : stockPrice}</td>
            <td>${option.otm_pct !== undefined ? `${option.otm_pct.toFixed(1)}%` : '<span class="text-muted">—</span>'}</td>
            <td>${pressureDisplay}</td>
            <td>${profitProgressDisplay}</td>
            <td>${formattedDifference}</td>
            <td>${percentDifferenceDisplay}</td>
            <td>
                <button class="btn btn-sm btn-primary roll-option-btn" data-option-index="${index}"
                    data-bs-toggle="tooltip" data-bs-placement="top" title="Select this option to roll">
                    Roll
                </button>
            </td>
        `;

        tableBody.appendChild(row);
    });

    const rollButtons = tableBody.querySelectorAll('.roll-option-btn');
    rollButtons.forEach(button => {
        button.addEventListener('click', async (event) => {
            const optionIndex = parseInt(event.target.getAttribute('data-option-index'));
            await selectOptionToRoll(optionIndex);
        });
    });

    initializeRolloverTooltips();
}

function populateRolloverSuggestionsTable(suggestions) {
    const tableBody = document.getElementById('rollover-suggestions-table-body');
    if (!tableBody) return;

    const otmSelectorRow = document.getElementById('otm-selector-row');

    if (otmSelectorRow) {
        tableBody.innerHTML = '';
        tableBody.appendChild(otmSelectorRow);
    } else {
        tableBody.innerHTML = '';
    }

    const st = rolloverState.selectedOption;
    if (!st || suggestions.length === 0) {
        const noDataRow = document.createElement('tr');
        noDataRow.innerHTML = '<td colspan="11" class="text-center">No rollover suggestions available</td>';
        tableBody.appendChild(noDataRow);
        return;
    }

    const buyHeaderRow = document.createElement('tr');
    buyHeaderRow.className = 'table-primary';
    buyHeaderRow.innerHTML = '<td colspan="11" class="fw-bold">BUY TO CLOSE</td>';
    tableBody.appendChild(buyHeaderRow);

    const buyRow = document.createElement('tr');
    const buyAsk = st.ask || st.market_price;
    const buyBid = st.bid || 0;
    const quantity = Math.abs(st.position);

    const delta = st.delta || 'N/A';
    const iv = st.implied_volatility || 'N/A';
    const formattedDelta = typeof delta === 'number' ? delta.toFixed(2) : delta;
    const formattedIV = typeof iv === 'number' ? `${iv.toFixed(1)}%` : iv;

    buyRow.innerHTML = `
        <td>BUY</td>
        <td>${st.symbol}</td>
        <td>${st.optionType}</td>
        <td>${formatCurrency(st.strike)}</td>
        <td>${st.expiration}</td>
        <td>${quantity}</td>
        <td>${formatCurrency(buyAsk)} <small class="text-muted" title="Ask price per share">(ask)</small></td>
        <td>LIMIT</td>
        <td>${formattedDelta}</td>
        <td>${formattedIV}</td>
        <td><span class="badge bg-info">Current Position</span></td>
    `;
    tableBody.appendChild(buyRow);

    const sellHeaderRow = document.createElement('tr');
    sellHeaderRow.className = 'table-success';
    sellHeaderRow.innerHTML = '<td colspan="11" class="fw-bold">SELL TO OPEN (NEW POSITION)</td>';
    tableBody.appendChild(sellHeaderRow);

    suggestions.forEach((suggestion, index) => {
        const sellRow = document.createElement('tr');

        const bid = suggestion.bid || 0;
        const ask = suggestion.ask || 0;
        const midPrice = calculateMidPrice(bid, ask);
        const bidAskTooltip = `bid: ${formatCurrency(bid)}, ask: ${formatCurrency(ask)}`;

        const delta = suggestion.delta || 'N/A';
        const iv = suggestion.implied_volatility || 'N/A';
        const formattedDelta = typeof delta === 'number' ? delta.toFixed(2) : delta;
        const formattedIV = typeof iv === 'number' ? `${iv.toFixed(1)}%` : iv;

        sellRow.innerHTML = `
            <td>SELL</td>
            <td>${st.symbol.split(' ')[0]}</td>
            <td>${st.optionType}</td>
            <td>${formatCurrency(suggestion.strike)}</td>
            <td>${suggestion.expiration}</td>
            <td>${quantity}</td>
            <td>${formatCurrency(midPrice)} <small class="text-muted" title="${bidAskTooltip}">(mid)</small></td>
            <td>LIMIT</td>
            <td>${formattedDelta}</td>
            <td>${formattedIV}</td>
            <td>
                <button class="btn btn-sm btn-success rollover-btn" data-suggestion-id="${index}"
                    data-bs-toggle="tooltip" data-bs-placement="top" title="Review this rollover signal">
                    Review Rollover
                </button>
            </td>
        `;

        tableBody.appendChild(sellRow);
    });

    if (suggestions.length > 1) {
        const reviewAllRow = document.createElement('tr');
        reviewAllRow.className = 'bg-body-tertiary';
        reviewAllRow.innerHTML = `
            <td colspan="11" class="text-center">
                <button id="review-rollover-btn" class="btn btn-primary mt-2">
                    <i class="bi bi-check2-all"></i> Review Rollover with First Option
                </button>
            </td>
        `;
        tableBody.appendChild(reviewAllRow);
    }

    initializeRolloverTooltips();
}

function clearRolloverSuggestions() {
    const tableBody = document.getElementById('rollover-suggestions-table-body');
    if (!tableBody) return;

    if (tableBody.childElementCount > 1) {
        return;
    }

    tableBody.innerHTML = '<tr><td colspan="9" class="text-center">Select an option to roll to view suggested replacements.</td></tr>';
}

function initializeRolloverTooltips() {
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('.roll-option-btn[data-bs-toggle="tooltip"], .rollover-btn[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.forEach(function (tooltipTriggerEl) {
        const existingTooltip = bootstrap.Tooltip.getInstance(tooltipTriggerEl);
        if (!existingTooltip) {
            new bootstrap.Tooltip(tooltipTriggerEl);
        }
    });
}

async function selectOptionToRoll(optionId) {
    try {
        if (!rolloverState.optionsData || optionId < 0 || optionId >= rolloverState.optionsData.length) {
            throw new Error('Invalid option selected');
        }

        rolloverState.selectedOption = rolloverState.optionsData[optionId];
        const st = rolloverState.selectedOption;

        const ticker = st.symbol.split(' ')[0];

        let currentOtmValue = 10;
        const existingOtmSelect = document.getElementById('otm-percentage');
        if (existingOtmSelect) {
            currentOtmValue = parseInt(existingOtmSelect.value) || 10;
        }

        const tableBody = document.getElementById('rollover-suggestions-table-body');
        if (!tableBody) return;

        tableBody.innerHTML = '';

        const otmSelectorRow = document.createElement('tr');
        otmSelectorRow.className = 'bg-body-tertiary';
        otmSelectorRow.id = 'otm-selector-row';

        let optionsHTML = '';
        for (let i = 1; i <= 30; i++) {
            const selected = i === currentOtmValue ? 'selected' : '';
            optionsHTML += `<option value="${i}" ${selected}>${i}%</option>`;
        }

        otmSelectorRow.innerHTML = `
            <td colspan="11">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <div class="d-flex align-items-center">
                        <label for="otm-percentage" class="me-2 mb-0">OTM Percentage:</label>
                        <select id="otm-percentage" class="form-select form-select-sm" style="width: auto;">
                            ${optionsHTML}
                        </select>
                    </div>
                    <button id="fetch-suggestions-btn" class="btn btn-sm btn-primary">
                        <i class="bi bi-arrow-repeat"></i> Fetch Rollover Options
                    </button>
                </div>
            </td>
        `;
        tableBody.appendChild(otmSelectorRow);

        const loadingRow = document.createElement('tr');
        loadingRow.innerHTML = `
            <td colspan="11" class="text-center">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Loading expiration dates...</span>
                </div>
                <p class="mt-2">Loading expiration dates for ${ticker}...</p>
            </td>
        `;
        tableBody.appendChild(loadingRow);

        let expirationDates = [];
        try {
            const expirationData = await fetchOptionExpirations(ticker);
            if (expirationData && expirationData.expirations) {
                expirationDates = expirationData.expirations;
            }
        } catch (error) {
            const isTimeout = error?.message?.includes('Request timed out');
            (isTimeout ? console.warn : console.error)(`Error fetching expiration dates for ${ticker}:`, error);
        }

        tableBody.innerHTML = '';
        tableBody.appendChild(otmSelectorRow);

        const buyHeaderRow = document.createElement('tr');
        buyHeaderRow.className = 'table-primary';
        buyHeaderRow.innerHTML = '<td colspan="9" class="fw-bold">BUY TO CLOSE</td>';
        tableBody.appendChild(buyHeaderRow);

        const buyRow = document.createElement('tr');
        const buyAsk = st.ask || st.market_price;
        const buyBid = st.bid || 0;
        const buyLimitPricePerContract = buyAsk * 100;
        const quantity = Math.abs(st.position);

        const delta = st.delta || 'N/A';
        const iv = st.implied_volatility || 'N/A';
        const formattedDelta = typeof delta === 'number' ? delta.toFixed(2) : delta;
        const formattedIV = typeof iv === 'number' ? `${iv.toFixed(1)}%` : iv;

        buyRow.innerHTML = `
            <td>BUY</td>
            <td>${st.symbol}</td>
            <td>${st.optionType}</td>
            <td>${formatCurrency(st.strike)}</td>
            <td>${st.expiration}</td>
            <td>${quantity}</td>
            <td>${formatCurrency(buyAsk)} <small class="text-muted" title="Ask price per share">(ask)</small></td>
            <td>LIMIT</td>
            <td>${formattedDelta}</td>
            <td>${formattedIV}</td>
            <td><span class="badge bg-info">Current Position</span></td>
        `;
        tableBody.appendChild(buyRow);

        const sellHeaderRow = document.createElement('tr');
        sellHeaderRow.className = 'table-success';
        sellHeaderRow.innerHTML = '<td colspan="9" class="fw-bold">SELL TO OPEN (NEW POSITION)</td>';
        tableBody.appendChild(sellHeaderRow);

        const optionType = st.optionType;
        const defaultOtm = currentOtmValue;

        const estimatedStrike = calculateTargetStrike(st.stockPrice, defaultOtm, optionType);
        const roundedStrike = roundStrikeToNearestHalf(estimatedStrike);

        let oneWeekLaterFormatted = "Exp. date + 1 week";

        const currentExpiry = parseExpirationDate(st.expiration);
        if (currentExpiry) {
            const oneWeekLater = addDaysToDate(currentExpiry, 7);
            const month = String(oneWeekLater.getMonth() + 1).padStart(2, '0');
            const day = String(oneWeekLater.getDate()).padStart(2, '0');
            const year = oneWeekLater.getFullYear();
            oneWeekLaterFormatted = `${month}/${day}/${year}`;
            const apiFormat = formatDateToAPIfmt(oneWeekLater);
            st.estimatedNextExpiration = apiFormat;
        }

        let expirationOptionsHtml = '';
        let defaultExpirationFound = false;

        if (expirationDates.length > 0) {
            expirationDates.forEach(exp => {
                let selected = '';
                if (st.estimatedNextExpiration) {
                    if (exp.value >= st.estimatedNextExpiration && !defaultExpirationFound) {
                        selected = 'selected';
                        defaultExpirationFound = true;
                    }
                }
                expirationOptionsHtml += `<option value="${exp.value}" ${selected}>${exp.label}</option>`;
            });
        } else {
            expirationOptionsHtml = `<option value="estimated" selected>${oneWeekLaterFormatted}</option>`;
        }

        const sellRow = document.createElement('tr');
        sellRow.innerHTML = `
            <td>SELL</td>
            <td>${ticker}</td>
            <td>${optionType}</td>
            <td>${formatCurrency(roundedStrike)} (est.)</td>
            <td>
                <select id="expiration-select" class="form-select form-select-sm">
                    ${expirationOptionsHtml}
                </select>
            </td>
            <td>${quantity}</td>
            <td>-- (fetch to see)</td>
            <td>LIMIT</td>
            <td>-- (fetch to see)</td>
        `;
        tableBody.appendChild(sellRow);

        setTimeout(() => {
            const fetchBtn = document.getElementById('fetch-suggestions-btn');
            if (fetchBtn) {
                fetchBtn.addEventListener('click', async () => {
                    const otmSelect = document.getElementById('otm-percentage');
                    const expSelect = document.getElementById('expiration-select');

                    const otmValue = otmSelect ? parseInt(otmSelect.value) : 10;
                    const expValue = expSelect ? expSelect.value : 'estimated';

                    if (rolloverState.selectedOption && otmValue) {
                        rolloverState.selectedOption.otmPercentage = otmValue;

                        if (expValue !== 'estimated') {
                            rolloverState.selectedOption.targetExpiration = expValue;
                        }

                        await fetchRolloverSuggestions();
                    }
                });
            }
        }, 0);
    } catch (error) {
        console.error('Error selecting option to roll:', error);
    }
}

async function initializeRollover() {
    try {
        await loadOptionPositions();

        const optionsTable = document.getElementById('options-approaching-table');
        if (optionsTable) {
            optionsTable.addEventListener('click', async (event) => {
                const rollButton = event.target.closest('.roll-btn');
                if (rollButton) {
                    const optionId = parseInt(rollButton.getAttribute('data-option-id'));
                    if (!isNaN(optionId)) {
                        await selectOptionToRoll(optionId);
                    }
                }
            });
        }

        const refreshBtn = document.getElementById('refresh-rollover');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', async () => {
                try {
                    const currentSelectedOption = rolloverState.selectedOption ? JSON.parse(JSON.stringify(rolloverState.selectedOption)) : null;
                    const currentSuggestions = rolloverState.rolloverSuggestions ? JSON.parse(JSON.stringify(rolloverState.rolloverSuggestions)) : [];

                    await loadOptionPositions();

                    if (currentSelectedOption && currentSuggestions && currentSuggestions.length > 0) {
                        rolloverState.selectedOption = currentSelectedOption;
                        rolloverState.rolloverSuggestions = currentSuggestions;

                        populateRolloverSuggestionsTable(rolloverState.rolloverSuggestions);
                    }
                } catch (error) {
                    console.error('Error during refresh:', error);
                }
            });
        }

        // Event delegation for rollover review button
        const suggestionsTable = document.getElementById('rollover-suggestions-table-body');
        if (suggestionsTable) {
            suggestionsTable.addEventListener('click', (event) => {
                const reviewBtn = event.target.closest('#review-rollover-btn');
                if (reviewBtn) {
                    // Show confirmation modal for rolling with first suggestion
                    const modalEl = document.getElementById('confirmRollModal');
                    if (modalEl) {
                        modalEl.dataset.suggestionId = '0'; // First suggestion
                        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
                        modal.show();
                    }
                }

                const rollBtn = event.target.closest('.rollover-btn');
                if (rollBtn) {
                    const suggestionId = rollBtn.getAttribute('data-suggestion-id');
                    // Show confirmation modal for rolling
                    const modalEl = document.getElementById('confirmRollModal');
                    if (modalEl) {
                        modalEl.dataset.suggestionId = suggestionId;
                        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
                        modal.show();
                    }
                }
            });
        }
    } catch (error) {
        console.error('Error initializing rollover page:', error);
    }
}

export {
    populateOptionsTable,
    populateRolloverSuggestionsTable,
    clearRolloverSuggestions,
    initializeRolloverTooltips,
    selectOptionToRoll,
    initializeRollover,
};
