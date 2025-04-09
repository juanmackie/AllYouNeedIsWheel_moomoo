/**
 * Format currency value for display
 * @param {number} value - The currency value to format
 * @returns {string} Formatted currency string
 */
export function formatCurrency(value) {
    if (value === null || value === undefined) return '$0.00';
    return new Intl.NumberFormat('en-US', { 
        style: 'currency', 
        currency: 'USD' 
    }).format(value);
}

/**
 * Format percentage for display
 * @param {number} value - The percentage value
 * @returns {string} Formatted percentage string
 */
export function formatPercent(value) {
    if (value === null || value === undefined) return '0.00%';
    return `${value.toFixed(2)}%`;
} 