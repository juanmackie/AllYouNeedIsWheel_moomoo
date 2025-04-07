/**
 * Rollover module
 * Handles options approaching strike price and rollover suggestions
 */
import { fetchPositions, fetchOptionData, saveOptionOrder, fetchPendingOrders, cancelOrder, executeOrder, fetchStockPrices as apiFetchStockPrices } from '../dashboard/api.js';
import { showAlert } from '../utils/alerts.js';

// Store data
let optionsData = null;
let selectedOption = null;
let rolloverSuggestions = [];
let pendingOrders = [];

/**
 * Format currency value for display
 * @param {number} value - The currency value to format
 * @returns {string} Formatted currency string
 */
function formatCurrency(value) {
    if (value === null || value === undefined) return '$0.00';
    return new Intl.NumberFormat('en-US', { 
        style: 'currency', 
        currency: 'USD' 
    }).format(value);
}

/**
 * Format percentage for display
 * @param {number} value - The percentage value
 * @returns {string} Formatted percentage string with color classes
 */
function formatPercentage(value, includeColorClass = true) {
    if (value === null || value === undefined) return '0.00%';
    const percentStr = `${Math.abs(value).toFixed(2)}%`;
    
    if (!includeColorClass) return percentStr;
    
    // Add color class based on proximity to strike
    if (value < 5) {
        return `<span class="text-danger fw-bold">${percentStr}</span>`;
    } else if (value < 10) {
        return `<span class="text-danger">${percentStr}</span>`;
    } else {
        return `<span>${percentStr}</span>`;
    }
}

/**
 * Initialize the rollover page
 */
async function initializeRollover() {
    try {
        console.log('Initializing rollover page...');
        
        // Create a container for alerts if it doesn't exist
        if (!document.querySelector('.content-container')) {
            const mainContainer = document.querySelector('main .container') || document.querySelector('main');
            if (mainContainer) {
                const contentContainer = document.createElement('div');
                contentContainer.className = 'content-container';
                mainContainer.prepend(contentContainer);
            }
        }
        
        // Add event listener for the refresh button
        const refreshButton = document.getElementById('refresh-rollover');
        if (refreshButton) {
            refreshButton.addEventListener('click', async () => {
                await loadOptionPositions();
                showAlert('Rollover options refreshed successfully', 'success');
            });
        }

        // Add event listener for the refresh pending orders button
        const refreshPendingOrdersButton = document.getElementById('refresh-pending-orders');
        if (refreshPendingOrdersButton) {
            refreshPendingOrdersButton.addEventListener('click', async () => {
                await loadPendingOrders();
                showAlert('Pending orders refreshed successfully', 'success');
            });
        }
        
        // Load options positions and pending orders
        await Promise.all([
            loadOptionPositions(),
            loadPendingOrders()
        ]);
        
        console.log('Rollover initialization complete');
    } catch (error) {
        console.error('Error initializing rollover:', error);
        showAlert(`Error initializing rollover: ${error.message}`, 'danger');
    }
}

/**
 * Load option positions and identify those approaching strike price
 */
async function loadOptionPositions() {
    try {
        // Fetch positions data
        const positionsData = await fetchPositions();
        if (!positionsData) {
            throw new Error('Failed to fetch positions data');
        }
        
        // Filter only option positions
        const optionPositions = positionsData.filter(position => 
            position.security_type === 'OPT' || position.securityType === 'OPT' || position.sec_type === 'OPT');
        
        console.log('Option positions loaded:', optionPositions.length);
        
        // Process all option positions with stock prices
        const processedOptions = await processOptionPositions(optionPositions);
        
        // Store the options data
        optionsData = processedOptions;
        
        // Populate the options table
        populateOptionsTable(processedOptions);
        
        // Clear rollover suggestions if no option is selected
        if (!selectedOption) {
            clearRolloverSuggestions();
        }
    } catch (error) {
        console.error('Error loading option positions:', error);
        showAlert(`Error loading option positions: ${error.message}`, 'danger');
    }
}

/**
 * Process all option positions with strike price and stock price information
 * @param {Array} optionPositions - Array of option positions
 * @returns {Array} Processed options with additional information
 */
async function processOptionPositions(optionPositions) {
    // Filter options that have market price data
    const validOptions = optionPositions.filter(position => 
        position.market_price !== undefined && position.market_price !== null);
    
    // Extract tickers from options to fetch current stock prices
    const tickers = validOptions.map(position => {
        // Get base ticker symbol (without option specifics)
        const fullSymbol = position.symbol || '';
        return fullSymbol.split(' ')[0];
    });
    
    // Fetch current stock prices for all tickers
    const stockPrices = await fetchStockPrices(tickers);
    
    // Calculate difference from strike price for each option
    const processedOptions = validOptions.map(position => {
        // Extract option details
        let strike = 0;
        let optionType = '';
        
        // Get strike price and option type from either contract object or direct properties
        if (position.contract && position.contract.strike) {
            strike = position.contract.strike;
            optionType = position.contract.right === 'P' ? 'PUT' : 'CALL';
        } else {
            strike = position.strike || 0;
            optionType = position.option_type || '';
        }
        
        // Get base ticker symbol
        const ticker = (position.symbol || '').split(' ')[0];
        
        // Get current stock price - first try from fetched prices, then position data, then default to 0
        const stockPrice = stockPrices[ticker] || position.underlying_price || position.stock_price || 0;
        
        // Calculate difference between current price and strike
        let difference = 0;
        let percentDifference = 0;
        
        if (stockPrice > 0 && strike > 0) {
            // For calls: how far stock price is from strike (strike - stock)
            // For puts: how far stock price is from strike (stock - strike)
            if (optionType === 'CALL' || optionType === 'C' || optionType === 'Call') {
                difference = strike - stockPrice;
                percentDifference = (difference / strike) * 100;
            } else {
                difference = stockPrice - strike;
                percentDifference = (difference / strike) * 100;
            }
        }
        
        // Add a flag to indicate if the option is approaching strike price
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
    
    // Return all processed options, sorted by percentage difference in ascending order
    return processedOptions.sort((a, b) => {
        // Sort by percentage difference (closest to strike by percentage first)
        return Math.abs(a.percentDifference) - Math.abs(b.percentDifference);
    });
}

/**
 * Populate options table with all option positions
 * @param {Array} options - Array of option positions
 */
function populateOptionsTable(options) {
    const tableBody = document.getElementById('option-positions-table-body');
    if (!tableBody) return;
    
    // Clear table
    tableBody.innerHTML = '';
    
    if (options.length === 0) {
        const noDataRow = document.createElement('tr');
        noDataRow.innerHTML = '<td colspan="9" class="text-center">No option positions found</td>';
        tableBody.appendChild(noDataRow);
        return;
    }
    
    // Add options to table
    options.forEach(option => {
        const row = document.createElement('tr');
        
        // Add row class based on percentage difference for approaching strike options
        if (option.isApproachingStrike) {
            if (option.percentDifference < 5) {
                row.classList.add('table-danger'); // Very close to strike
            } else if (option.percentDifference < 10) {
                row.classList.add('table-warning'); // Getting close to strike
            }
        }
        
        // Extract expiration date
        let expiration = '-';
        if (option.contract && option.contract.lastTradeDateOrContractMonth) {
            expiration = option.contract.lastTradeDateOrContractMonth;
        } else {
            expiration = option.expiration || '-';
        }
        
        // Make sure stockPrice is not undefined or zero
        const stockPrice = option.stockPrice > 0 ? option.stockPrice : 'Fetching...';
        
        // Format the absolute difference as a percentage
        const absoluteDifference = Math.abs(option.difference);
        const absolutePercentDifference = Math.abs(option.percentDifference);
        
        // Color-code based on how close to strike (smaller is closer)
        let differenceColorClass = '';
        if (absolutePercentDifference < 5) {
            differenceColorClass = 'text-danger fw-bold';
        } else if (absolutePercentDifference < 10) {
            differenceColorClass = 'text-danger';
        }
        
        // Format percent difference display
        const percentDifferenceDisplay = `<span class="${differenceColorClass}">${absolutePercentDifference.toFixed(2)}%</span>`;
        
        row.innerHTML = `
            <td>${option.symbol}</td>
            <td>${option.position}</td>
            <td>${option.optionType}</td>
            <td>${formatCurrency(option.strike)}</td>
            <td>${expiration}</td>
            <td>${typeof stockPrice === 'number' ? formatCurrency(stockPrice) : stockPrice}</td>
            <td>${formatCurrency(absoluteDifference)}</td>
            <td>${percentDifferenceDisplay}</td>
            <td>
                <button class="btn btn-sm btn-primary roll-option-btn" data-option-id="${options.indexOf(option)}">
                    Roll
                </button>
            </td>
        `;
        
        tableBody.appendChild(row);
    });
    
    // Add event listeners to roll buttons
    const rollButtons = tableBody.querySelectorAll('.roll-option-btn');
    rollButtons.forEach(button => {
        button.addEventListener('click', async (event) => {
            const optionId = event.target.getAttribute('data-option-id');
            await selectOptionToRoll(parseInt(optionId));
        });
    });
}

/**
 * Load pending orders from the API
 */
async function loadPendingOrders() {
    try {
        // Fetch pending orders
        const result = await fetchPendingOrders();
        if (!result) {
            throw new Error('Failed to fetch pending orders');
        }
        
        // Store orders
        pendingOrders = result.orders || [];
        
        // Populate the pending orders table
        populatePendingOrdersTable(pendingOrders);
        
        console.log('Pending orders loaded:', pendingOrders.length);
    } catch (error) {
        console.error('Error loading pending orders:', error);
        showAlert(`Error loading pending orders: ${error.message}`, 'danger');
    }
}

/**
 * Fetch current stock prices for the given tickers
 * @param {Array} tickers - Array of ticker symbols
 * @returns {Object} Object with ticker symbols as keys and stock prices as values
 */
async function fetchStockPrices(tickers) {
    try {
        const uniqueTickers = [...new Set(tickers)].filter(Boolean);
        
        if (uniqueTickers.length === 0) {
            console.log('No valid tickers to fetch prices for');
            return {};
        }
        
        console.log(`Fetching stock prices for ${uniqueTickers.length} tickers:`, uniqueTickers);
        
        // Call the dedicated API endpoint for stock prices
        const stockPrices = await apiFetchStockPrices(uniqueTickers);
        
        console.log('Fetched stock prices:', stockPrices);
        return stockPrices;
    } catch (error) {
        console.error('Error in fetchStockPrices:', error);
        return {};
    }
}

/**
 * Select an option to roll and fetch rollover suggestions
 * @param {number} optionId - Index of the selected option in optionsData array
 */
async function selectOptionToRoll(optionId) {
    try {
        if (!optionsData || optionId < 0 || optionId >= optionsData.length) {
            throw new Error('Invalid option selected');
        }
        
        // Get the selected option
        selectedOption = optionsData[optionId];
        console.log('Selected option to roll:', selectedOption);
        
        // Get ticker symbol (remove option-specific parts if needed)
        const ticker = selectedOption.symbol.split(' ')[0];
        
        // Fetch current stock price using the dedicated endpoint
        const stockPrices = await apiFetchStockPrices(ticker);
        const latestStockPrice = stockPrices[ticker] || selectedOption.stockPrice;
        
        // Update the stock price with the latest data
        selectedOption.stockPrice = latestStockPrice;
        
        // Recalculate difference and percentage with updated stock price
        if (latestStockPrice > 0 && selectedOption.strike > 0) {
            if (selectedOption.optionType === 'CALL' || selectedOption.optionType === 'C' || selectedOption.optionType === 'Call') {
                selectedOption.difference = selectedOption.strike - latestStockPrice;
                selectedOption.percentDifference = (selectedOption.difference / selectedOption.strike) * 100;
            } else {
                selectedOption.difference = latestStockPrice - selectedOption.strike;
                selectedOption.percentDifference = (selectedOption.difference / selectedOption.strike) * 100;
            }
        }
        
        // Fetch suggested replacement options (10% less than current strike)
        const suggestedOtmPercentage = 10;
        
        // Fetch option data for the ticker
        const optionData = await fetchOptionData(ticker, suggestedOtmPercentage, selectedOption.optionType);
        
        if (!optionData || !optionData.data || !optionData.data[ticker]) {
            throw new Error(`Failed to fetch option data for ${ticker}`);
        }
        
        // Get options based on the selected option type
        const optionType = selectedOption.optionType.toUpperCase();
        let availableOptions = [];
        
        if (optionType === 'CALL' || optionType === 'C') {
            availableOptions = optionData.data[ticker].calls || [];
        } else {
            availableOptions = optionData.data[ticker].puts || [];
        }
        
        // Calculate new suggested strike (10% less than current)
        const newStrikePercentage = 0.9; // 10% less
        const suggestedStrike = selectedOption.strike * newStrikePercentage;
        
        // Find options that expire one week after the selected option
        const currentExpiry = new Date(selectedOption.expiration);
        const oneWeekLater = new Date(currentExpiry);
        oneWeekLater.setDate(oneWeekLater.getDate() + 7);
        
        // Filter options to get those expiring closest to one week later
        const suggestedOptions = availableOptions.filter(option => {
            const optionExpiry = new Date(option.expiration);
            return optionExpiry > currentExpiry;
        }).sort((a, b) => {
            const dateA = new Date(a.expiration);
            const dateB = new Date(b.expiration);
            
            // Calculate days difference from target date
            const diffA = Math.abs(dateA - oneWeekLater);
            const diffB = Math.abs(dateB - oneWeekLater);
            
            // Sort by closest to one week later
            return diffA - diffB;
        });
        
        // Take top 3 suggestions
        rolloverSuggestions = suggestedOptions.slice(0, 3);
        
        // Populate rollover suggestions table
        populateRolloverSuggestionsTable(rolloverSuggestions);
    } catch (error) {
        console.error('Error selecting option to roll:', error);
        showAlert(`Error selecting option to roll: ${error.message}`, 'danger');
    }
}

/**
 * Populate rollover suggestions table
 * @param {Array} suggestions - Array of rollover suggestions
 */
function populateRolloverSuggestionsTable(suggestions) {
    const tableBody = document.getElementById('rollover-suggestions-table-body');
    if (!tableBody) return;
    
    // Clear table
    tableBody.innerHTML = '';
    
    if (!selectedOption || suggestions.length === 0) {
        const noDataRow = document.createElement('tr');
        noDataRow.innerHTML = '<td colspan="9" class="text-center">No rollover suggestions available</td>';
        tableBody.appendChild(noDataRow);
        return;
    }
    
    // For each suggestion, add two rows (buy current, sell new)
    suggestions.forEach((suggestion, index) => {
        // Calculate the price estimate for closing the current position
        const closePrice = selectedOption.market_price * 100; // Convert to per-contract price
        
        // Row 1: Buy to close current position
        const buyRow = document.createElement('tr');
        buyRow.classList.add('table-light');
        
        buyRow.innerHTML = `
            <td class="fw-bold">BUY</td>
            <td>${selectedOption.symbol}</td>
            <td>${selectedOption.optionType}</td>
            <td>${formatCurrency(selectedOption.strike)}</td>
            <td>${selectedOption.expiration}</td>
            <td>${Math.abs(selectedOption.position)}</td>
            <td>${formatCurrency(closePrice)}</td>
            <td>MARKET</td>
            <td>Close existing position</td>
        `;
        
        // Row 2: Sell to open new position
        const sellRow = document.createElement('tr');
        sellRow.classList.add('table-light');
        // Add button to the second row
        const actionCell = document.createElement('td');
        actionCell.colSpan = 9;
        actionCell.innerHTML = `
            <div class="d-flex justify-content-between align-items-center">
                <span class="fw-bold">SELL ${suggestion.strike} ${selectedOption.optionType} expiring ${suggestion.expiration}</span>
                <button class="btn btn-sm btn-success rollover-btn" data-suggestion-id="${index}">
                    Add Both Orders
                </button>
            </div>
        `;
        
        // Add rows
        tableBody.appendChild(buyRow);
        
        // Row 2: Sell to open new position
        const sellDetailsRow = document.createElement('tr');
        sellDetailsRow.classList.add('table-success');
        
        sellDetailsRow.innerHTML = `
            <td class="fw-bold">SELL</td>
            <td>${selectedOption.symbol.split(' ')[0]}</td>
            <td>${selectedOption.optionType}</td>
            <td>${formatCurrency(suggestion.strike)}</td>
            <td>${suggestion.expiration}</td>
            <td>${Math.abs(selectedOption.position)}</td>
            <td>${formatCurrency(suggestion.bid * 100)}</td>
            <td>LIMIT</td>
            <td>Open new position</td>
        `;
        
        tableBody.appendChild(sellDetailsRow);
        tableBody.appendChild(sellRow);
        
        // Add a spacer row between suggestions
        if (index < suggestions.length - 1) {
            const spacerRow = document.createElement('tr');
            spacerRow.innerHTML = '<td colspan="9" class="p-0"></td>';
            spacerRow.style.height = '10px';
            tableBody.appendChild(spacerRow);
        }
    });
    
    // Add event listeners to rollover buttons
    const rolloverButtons = tableBody.querySelectorAll('.rollover-btn');
    rolloverButtons.forEach(button => {
        button.addEventListener('click', async (event) => {
            const suggestionId = event.target.getAttribute('data-suggestion-id');
            await addRolloverOrder(parseInt(suggestionId));
        });
    });
}

/**
 * Populate pending orders table
 * @param {Array} orders - Array of pending orders
 */
function populatePendingOrdersTable(orders) {
    const tableBody = document.getElementById('pending-orders-table-body');
    if (!tableBody) return;
    
    // Clear table
    tableBody.innerHTML = '';
    
    if (!orders || orders.length === 0) {
        const noDataRow = document.createElement('tr');
        noDataRow.innerHTML = '<td colspan="10" class="text-center">No pending orders found</td>';
        tableBody.appendChild(noDataRow);
        return;
    }
    
    // Sort orders by date created (most recent first)
    orders.sort((a, b) => {
        const dateA = new Date(a.date_created || 0);
        const dateB = new Date(b.date_created || 0);
        return dateB - dateA;
    });
    
    // Add orders to table
    orders.forEach(order => {
        const row = document.createElement('tr');
        
        // Determine price display
        let priceDisplay = '-';
        if (order.order_type === 'LIMIT' && order.limit_price) {
            priceDisplay = formatCurrency(order.limit_price);
        } else if (order.order_type === 'MARKET') {
            priceDisplay = 'Market';
        }
        
        // Determine row class based on status
        let rowClass = '';
        let statusText = order.status || 'pending';
        
        if (statusText === 'executed' || statusText === 'filled') {
            rowClass = 'table-success';
            statusText = 'Executed';
        } else if (statusText === 'cancelled' || statusText === 'rejected') {
            rowClass = 'table-danger';
            statusText = statusText === 'cancelled' ? 'Cancelled' : 'Rejected';
        } else if (statusText === 'processing') {
            rowClass = 'table-warning';
            statusText = 'Processing';
        } else {
            statusText = 'Pending';
        }
        
        // Button display based on status
        let actionButtons = '';
        if (statusText === 'Pending') {
            actionButtons = `
                <button class="btn btn-sm btn-success execute-order-btn me-1" data-order-id="${order.id}">
                    <i class="bi bi-check-circle"></i>
                </button>
                <button class="btn btn-sm btn-danger cancel-order-btn" data-order-id="${order.id}">
                    <i class="bi bi-x-circle"></i>
                </button>
            `;
        } else if (statusText === 'Processing') {
            actionButtons = `
                <button class="btn btn-sm btn-warning cancel-order-btn" data-order-id="${order.id}">
                    <i class="bi bi-x-circle"></i> Cancel
                </button>
            `;
        } else {
            actionButtons = '-';
        }
        
        row.className = rowClass;
        row.innerHTML = `
            <td>${order.id}</td>
            <td>${order.action}</td>
            <td>${order.ticker}</td>
            <td>${order.option_type}</td>
            <td>${formatCurrency(order.strike)}</td>
            <td>${order.expiration}</td>
            <td>${order.quantity}</td>
            <td>${priceDisplay}</td>
            <td>${statusText}</td>
            <td>${actionButtons}</td>
        `;
        
        tableBody.appendChild(row);
    });
    
    // Add event listeners to execute and cancel buttons
    const executeButtons = tableBody.querySelectorAll('.execute-order-btn');
    executeButtons.forEach(button => {
        button.addEventListener('click', async (event) => {
            const orderId = event.target.closest('.execute-order-btn').getAttribute('data-order-id');
            await executeOrderById(parseInt(orderId));
        });
    });
    
    const cancelButtons = tableBody.querySelectorAll('.cancel-order-btn');
    cancelButtons.forEach(button => {
        button.addEventListener('click', async (event) => {
            const orderId = event.target.closest('.cancel-order-btn').getAttribute('data-order-id');
            await cancelOrderById(parseInt(orderId));
        });
    });
}

/**
 * Execute an order by ID
 * @param {number} orderId - The order ID to execute
 */
async function executeOrderById(orderId) {
    try {
        if (!orderId) {
            throw new Error('Invalid order ID');
        }
        
        // Confirm with user
        if (!confirm('Are you sure you want to execute this order?')) {
            return;
        }
        
        // Execute the order
        const result = await executeOrder(orderId);
        
        if (result && result.success) {
            showAlert('Order execution request sent successfully', 'success');
            
            // Reload pending orders
            await loadPendingOrders();
        } else {
            throw new Error(result.error || 'Failed to execute order');
        }
    } catch (error) {
        console.error('Error executing order:', error);
        showAlert(`Error executing order: ${error.message}`, 'danger');
    }
}

/**
 * Cancel an order by ID
 * @param {number} orderId - The order ID to cancel
 */
async function cancelOrderById(orderId) {
    try {
        if (!orderId) {
            throw new Error('Invalid order ID');
        }
        
        // Confirm with user
        if (!confirm('Are you sure you want to cancel this order?')) {
            return;
        }
        
        // Cancel the order
        const result = await cancelOrder(orderId);
        
        if (result && result.success) {
            showAlert('Order cancelled successfully', 'success');
            
            // Reload pending orders
            await loadPendingOrders();
        } else {
            throw new Error(result.error || 'Failed to cancel order');
        }
    } catch (error) {
        console.error('Error cancelling order:', error);
        showAlert(`Error cancelling order: ${error.message}`, 'danger');
    }
}

/**
 * Clear rollover suggestions table
 */
function clearRolloverSuggestions() {
    const tableBody = document.getElementById('rollover-suggestions-table-body');
    if (!tableBody) return;
    
    tableBody.innerHTML = '<tr><td colspan="9" class="text-center">Select an option to roll to view suggested replacements.</td></tr>';
}

/**
 * Add rollover order (buy current option and sell new option)
 * @param {number} suggestionId - Index of the selected suggestion
 */
async function addRolloverOrder(suggestionId) {
    try {
        if (!selectedOption || !rolloverSuggestions || suggestionId < 0 || suggestionId >= rolloverSuggestions.length) {
            throw new Error('Invalid rollover suggestion selected');
        }
        
        const suggestion = rolloverSuggestions[suggestionId];
        
        // Create rollover data object for API call
        const rolloverData = {
            ticker: selectedOption.symbol.split(' ')[0],
            current_option_type: selectedOption.optionType,
            current_strike: selectedOption.strike,
            current_expiration: selectedOption.expiration,
            new_strike: suggestion.strike,
            new_expiration: suggestion.expiration,
            quantity: Math.abs(selectedOption.position),
            current_order_type: 'MARKET',
            new_order_type: 'LIMIT',
            new_limit_price: suggestion.bid * 100,  // Use the bid price
        };
        
        // Call the rollover API endpoint
        const response = await fetch('/api/options/rollover', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(rolloverData)
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}`);
        }
        
        const result = await response.json();
        
        if (result.success) {
            showAlert('Rollover orders added successfully', 'success');
            
            // Reload pending orders and option positions
            await Promise.all([
                loadPendingOrders(),
                loadOptionPositions()
            ]);
        } else {
            throw new Error(result.error || 'Failed to add rollover orders');
        }
    } catch (error) {
        console.error('Error adding rollover order:', error);
        showAlert(`Error adding rollover order: ${error.message}`, 'danger');
    }
}

// Initialize the rollover page when the DOM is loaded
document.addEventListener('DOMContentLoaded', initializeRollover);