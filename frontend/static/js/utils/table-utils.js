/**
 * Updates the display of a legend in a table
 * @param {string} tableId - The ID of the table to update
 * @param {Object} legendConfig - Configuration for the legend display
 */
export function updateLegendDisplay(tableId, legendConfig = {}) {
    const table = document.getElementById(tableId);
    if (!table) return;
    
    // Find or create legend container
    let legendContainer = document.querySelector(`#${tableId}-legend`);
    if (!legendContainer) {
        legendContainer = document.createElement('div');
        legendContainer.id = `${tableId}-legend`;
        legendContainer.className = 'table-legend mt-2';
        table.parentNode.insertBefore(legendContainer, table.nextSibling);
    }
    
    // Clear existing content
    legendContainer.innerHTML = '';
    
    // Add legend items
    if (Object.keys(legendConfig).length > 0) {
        const legendTitle = document.createElement('small');
        legendTitle.className = 'text-muted me-2';
        legendTitle.textContent = 'Legend:';
        legendContainer.appendChild(legendTitle);
        
        // Add each legend item
        Object.entries(legendConfig).forEach(([key, value]) => {
            const legendItem = document.createElement('span');
            legendItem.className = 'badge me-2';
            legendItem.style.backgroundColor = value.color || '#6c757d';
            legendItem.textContent = key;
            legendContainer.appendChild(legendItem);
        });
    }
} 