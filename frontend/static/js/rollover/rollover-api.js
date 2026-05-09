import { rolloverState } from './rollover-state.js';
import { calculatePercentDifference, parseExpirationDate, formatDateToAPIfmt, addDaysToDate, calculateTargetStrike, findClosestStrike, isValidStrike, calculateMidPrice } from './rollover-calculator.js';
import { formatCurrency } from '../utils/formatters.js';
import { fetchPositions, fetchOptionData, fetchPendingOrders, cancelOrder, executeOrder, fetchStockPrices as apiFetchStockPrices, fetchRollPressure } from '../dashboard/api.js';

async function processOptionPositions(optionPositions) {
    const validOptions = optionPositions.filter(position =>
        position.market_price !== undefined && position.market_price !== null);

    const tickers = validOptions.map(position => {
        const fullSymbol = position.symbol || '';
        return fullSymbol.split(' ')[0];
    });

    const stockPrices = await fetchStockPrices(tickers);

    const processedOptions = validOptions.map(position => {
        let strike = 0;
        let optionType = '';

        if (position.contract && position.contract.strike) {
            strike = position.contract.strike;
            optionType = position.contract.right === 'P' ? 'PUT' : 'CALL';
        } else {
            strike = position.strike || 0;
            optionType = position.option_type || '';
        }

        const ticker = (position.symbol || '').split(' ')[0];
        const stockPrice = stockPrices[ticker] || position.underlying_price || position.stock_price || 0;

        const { difference, percentDifference } = calculatePercentDifference(stockPrice, strike, optionType);
        const isApproachingStrike = percentDifference >= 0 && percentDifference < 10;

        return {
            ...position,
            strike,
            optionType,
            stockPrice,
            difference,
            percentDifference,
            isApproachingStrike
        };
    });

    return processedOptions.sort((a, b) => {
        if (a.percentDifference < 0 && b.percentDifference >= 0) return -1;
        if (a.percentDifference >= 0 && b.percentDifference < 0) return 1;
        return Math.abs(a.percentDifference) - Math.abs(b.percentDifference);
    });
}

async function fetchStockPrices(tickers) {
    try {
        const uniqueTickers = [...new Set(tickers)].filter(Boolean);
        if (uniqueTickers.length === 0) {
            return {};
        }
        return await apiFetchStockPrices(uniqueTickers);
    } catch (error) {
        console.error('Error in fetchStockPrices:', error);
        return {};
    }
}

async function loadOptionPositions() {
    const { clearRolloverSuggestions, populateOptionsTable } = await import('./rollover-ui.js');
    try {
        const rollPressureData = await fetchRollPressure();

        if (rollPressureData.positions && rollPressureData.positions.length > 0) {
            const processedOptions = rollPressureData.positions.map(pos => ({
                ...pos,
                strike: pos.strike,
                optionType: pos.option_type,
                stockPrice: pos.stock_price,
                percentDifference: pos.otm_pct,
                isApproachingStrike: pos.otm_pct >= 0 && pos.otm_pct < 10,
                difference: pos.option_type === 'CALL'
                    ? pos.strike - pos.stock_price
                    : pos.stock_price - pos.strike,
                roll_pressure: pos.roll_pressure,
                extrinsic_remaining: pos.extrinsic_remaining,
                profit_target_progress: pos.profit_target_progress,
                wheel_decision: pos.wheel_decision,
                symbol: pos.ticker,
                contract: {
                    strike: pos.strike,
                    right: pos.option_type === 'PUT' ? 'P' : 'C',
                    lastTradeDateOrContractMonth: pos.expiration
                        ? pos.expiration.substring(0, 4) + '-' + pos.expiration.substring(4, 6) + '-' + pos.expiration.substring(6, 8)
                        : '-',
                },
                market_price: pos.mid_price,
                bid: pos.bid,
                ask: pos.ask,
            }));

            rolloverState.optionsData = processedOptions;
            populateOptionsTable(processedOptions);

            if (!rolloverState.selectedOption && (!rolloverState.rolloverSuggestions || rolloverState.rolloverSuggestions.length === 0)) {
                clearRolloverSuggestions();
            }
            return;
        }

        const positionsData = await fetchPositions();
        if (!positionsData) {
            throw new Error('Failed to fetch positions data');
        }

        const optionPositions = positionsData.filter(position =>
            position.security_type === 'OPT' || position.securityType === 'OPT' || position.sec_type === 'OPT');

        const processedOptions = await processOptionPositions(optionPositions);
        rolloverState.optionsData = processedOptions;
        populateOptionsTable(processedOptions);

        if (!rolloverState.selectedOption && (!rolloverState.rolloverSuggestions || rolloverState.rolloverSuggestions.length === 0)) {
            clearRolloverSuggestions();
        }
    } catch (error) {
        console.error('Error loading option positions:', error);
    }
}

async function loadPendingOrders() {
    const { populatePendingOrdersTable } = await import('./rollover-ui.js');
    try {
        const result = await fetchPendingOrders(false, true);
        if (!result) {
            throw new Error('Failed to fetch pending orders');
        }
        rolloverState.pendingOrders = result.orders || [];
        populatePendingOrdersTable(rolloverState.pendingOrders);
    } catch (error) {
        console.error('Error loading pending orders:', error);
    }
}

async function fetchRolloverSuggestions() {
    const { populateRolloverSuggestionsTable } = await import('./rollover-ui.js');
    try {
        if (!rolloverState.selectedOption) {
            throw new Error('No option selected for rollover');
        }

        const st = rolloverState.selectedOption;
        const otmSelectorRow = document.getElementById('otm-selector-row');
        const tableBody = document.getElementById('rollover-suggestions-table-body');

        if (tableBody && otmSelectorRow) {
            tableBody.innerHTML = '';
            tableBody.appendChild(otmSelectorRow);

            const loadingRow = document.createElement('tr');
            loadingRow.innerHTML = `
                <td colspan="11" class="text-center">
                    <div class="spinner-border text-primary" role="status">
                        <span class="visually-hidden">Loading expiration dates...</span>
                    </div>
                    <p class="mt-2">Loading expiration dates for ${st.symbol.split(' ')[0]}...</p>
                </td>
            `;
            tableBody.appendChild(loadingRow);
        }

        const ticker = st.symbol.split(' ')[0];

        const stockPrices = await apiFetchStockPrices(ticker);
        const latestStockPrice = stockPrices[ticker] || st.stockPrice;
        st.stockPrice = latestStockPrice;

        if (latestStockPrice > 0 && st.strike > 0) {
            const { difference, percentDifference } = calculatePercentDifference(
                latestStockPrice, st.strike, st.optionType
            );
            st.difference = difference;
            st.percentDifference = percentDifference;
        }

        let otmPercentage = 10;
        const otmDropdown = document.getElementById('otm-percentage');
        if (otmDropdown) {
            const dropdownValue = parseInt(otmDropdown.value);
            if (!isNaN(dropdownValue)) {
                otmPercentage = dropdownValue;
                st.otmPercentage = otmPercentage;
            }
        } else if (st.otmPercentage) {
            otmPercentage = st.otmPercentage;
        }

        let targetExpirationForAPI;
        const expDropdown = document.getElementById('expiration-select');
        if (expDropdown && expDropdown.value && expDropdown.value !== 'estimated') {
            targetExpirationForAPI = expDropdown.value;
            st.targetExpiration = targetExpirationForAPI;
        } else if (st.targetExpiration) {
            targetExpirationForAPI = st.targetExpiration;
        } else {
            if (!st.expiration) {
                throw new Error("Option has no expiration date");
            }

            const currentExpiry = parseExpirationDate(st.expiration);
            if (!currentExpiry) {
                throw new Error(`Couldn't parse option expiration date: ${st.expiration}`);
            }

            const oneWeekLater = addDaysToDate(currentExpiry, 7);
            targetExpirationForAPI = formatDateToAPIfmt(oneWeekLater);
        }

        const optionData = await fetchOptionData(
            ticker,
            otmPercentage,
            st.optionType,
            targetExpirationForAPI
        );

        if (!optionData || !optionData.data || !optionData.data[ticker]) {
            throw new Error(`Failed to fetch option data for ${ticker}`);
        }

        const optionType = st.optionType.toUpperCase();
        let availableOptions = [];

        if (optionType === 'CALL' || optionType === 'C') {
            availableOptions = optionData.data[ticker].calls || [];
        } else {
            availableOptions = optionData.data[ticker].puts || [];
        }

        if (availableOptions.length > 0) {
            const targetStrike = calculateTargetStrike(latestStockPrice, otmPercentage, st.optionType);

            const availableStrikes = availableOptions
                .map(option => option.strike)
                .filter(strike => isValidStrike(strike))
                .sort((a, b) => a - b);

            if (availableStrikes.length === 0) {
                throw new Error("No valid strikes found for the options");
            }

            const closestStrike = findClosestStrike(availableStrikes, targetStrike);

            const selectedNewOption = availableOptions.find(
                option => option.strike === closestStrike
            );

            if (!selectedNewOption) {
                throw new Error("Could not find specific option contract for rollover");
            }

            rolloverState.rolloverSuggestions = [selectedNewOption];
            populateRolloverSuggestionsTable(rolloverState.rolloverSuggestions);
            return;
        }

        const allExpirations = [...new Set(availableOptions.map(opt => opt.expiration))]
            .filter(expDate => {
                const d = parseExpirationDate(expDate);
                return d !== null;
            })
            .sort((a, b) => {
                const da = parseExpirationDate(a);
                const db = parseExpirationDate(b);
                if (!da || !db) return 0;
                return da - db;
            });

        if (allExpirations.length === 0) {
            throw new Error("No valid expiration dates found for rolling options");
        }

        const nextExpiration = allExpirations[0];

        const strikesForExpiration = availableOptions
            .filter(option => option.expiration === nextExpiration)
            .map(option => option.strike)
            .filter(strike => isValidStrike(strike))
            .sort((a, b) => a - b);

        if (strikesForExpiration.length === 0) {
            throw new Error("No valid strikes found for the next expiration date");
        }

        const targetStrike = calculateTargetStrike(latestStockPrice, otmPercentage, st.optionType);
        const closestStrike = findClosestStrike(strikesForExpiration, targetStrike);

        let selectedNewOption = availableOptions.find(
            option => option.expiration === nextExpiration && option.strike === closestStrike
        );

        if (!selectedNewOption) {
            selectedNewOption = availableOptions.find(
                option => option.expiration === nextExpiration &&
                    Math.abs(option.strike - closestStrike) < 0.01
            );
        }

        if (!selectedNewOption) {
            throw new Error("Could not find specific option contract for rollover");
        }

        rolloverState.rolloverSuggestions = [selectedNewOption];
        populateRolloverSuggestionsTable(rolloverState.rolloverSuggestions);
    } catch (error) {
        console.error('Error fetching rollover suggestions:', error);

        const otmSelectorRow = document.getElementById('otm-selector-row');
        const tableBody = document.getElementById('rollover-suggestions-table-body');
        if (tableBody && otmSelectorRow && tableBody.childElementCount <= 1) {
            tableBody.innerHTML = '';
            tableBody.appendChild(otmSelectorRow);

            const errorRow = document.createElement('tr');
            errorRow.innerHTML = `<td colspan="11" class="text-center text-danger">Error: ${error.message}</td>`;
            tableBody.appendChild(errorRow);
        }
    }
}

async function addRolloverOrder(suggestionId) {
    const { populateRolloverSuggestionsTable } = await import('./rollover-ui.js');
    try {
        if (!rolloverState.selectedOption || !rolloverState.rolloverSuggestions || suggestionId < 0 || suggestionId >= rolloverState.rolloverSuggestions.length) {
            throw new Error('Invalid rollover suggestion selected');
        }

        const suggestion = rolloverState.rolloverSuggestions[suggestionId];
        const st = rolloverState.selectedOption;
        const quantity = Math.abs(st.position);

        const sellBid = suggestion.bid || 0;
        const sellAsk = suggestion.ask || 0;
        const sellMidPrice = calculateMidPrice(sellBid, sellAsk);

        if (sellMidPrice <= 0) {
            throw new Error('Cannot determine a valid limit price for the sell order');
        }

        const buyAsk = st.ask || st.market_price;
        const buyBid = st.bid || 0;

        if (buyAsk <= 0) {
            throw new Error('Cannot determine a valid limit price for the buy order');
        }

        const buyLimitPricePerContract = buyAsk * 100;
        const sellLimitPrice = sellMidPrice;

        const rolloverData = {
            ticker: st.symbol.split(' ')[0],
            current_option_type: st.optionType,
            current_strike: st.strike,
            current_expiration: st.expiration,
            new_strike: suggestion.strike,
            new_expiration: suggestion.expiration,
            quantity: quantity,
            current_order_type: 'LIMIT',
            new_order_type: 'LIMIT',
            current_limit_price: buyLimitPricePerContract,
            new_limit_price: sellLimitPrice,
            current_bid: buyBid,
            current_ask: buyAsk,
            new_bid: sellBid,
            new_ask: sellAsk,
            isRollover: true
        };

        const currentSuggestions = [...rolloverState.rolloverSuggestions];
        const currentOption = { ...st };

        const response = await fetch('/api/options/rollover', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(rolloverData)
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`HTTP error ${response.status}: ${errorText}`);
        }

        const result = await response.json();

        if (result.success) {
            await Promise.all([
                loadPendingOrders(),
                loadOptionPositions()
            ]);

            rolloverState.rolloverSuggestions = currentSuggestions;
            rolloverState.selectedOption = currentOption;

            if (rolloverState.rolloverSuggestions.length > 0) {
                populateRolloverSuggestionsTable(rolloverState.rolloverSuggestions);
            }
        } else {
            throw new Error(result.error || 'Failed to submit rollover orders');
        }
    } catch (error) {
        console.error('Error preparing rollover order:', error);
    }
}

async function executeOrderById(orderId) {
    try {
        if (!orderId) {
            throw new Error('Invalid order ID');
        }

        const order = rolloverState.pendingOrders.find(o => o.id === orderId);
        if (!order) {
            throw new Error(`Order with ID ${orderId} not found`);
        }

        if (order.order_type === 'LIMIT') {
            if (order.action === 'BUY' && order.ask > 0) {
                // Log buy order details if needed
            } else if (order.action === 'SELL' && (order.bid > 0 || order.ask > 0)) {
                const sellMidPrice = calculateMidPrice(order.bid, order.ask);
                if (sellMidPrice > 0) {
                    // Log sell order details if needed
                }
            }
        }

        const result = await executeOrder(orderId);

        if (result && result.success) {
            await loadPendingOrders();
        } else {
            throw new Error(result.error || 'Failed to execute order');
        }
    } catch (error) {
        console.error('Error executing order:', error);
    }
}

async function cancelOrderById(orderId) {
    try {
        if (!orderId) {
            throw new Error('Invalid order ID');
        }

        const result = await cancelOrder(orderId);

        if (result && result.success) {
            await loadPendingOrders();
        } else {
            throw new Error(result.error || 'Failed to cancel order');
        }
    } catch (error) {
        console.error('Error cancelling order:', error);
    }
}

export {
    processOptionPositions,
    fetchStockPrices,
    loadOptionPositions,
    loadPendingOrders,
    fetchRolloverSuggestions,
    addRolloverOrder,
    executeOrderById,
    cancelOrderById,
};
