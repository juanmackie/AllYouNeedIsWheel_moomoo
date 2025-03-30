/**
 * Portfolio module
 * Handles portfolio view and position management
 */
import { loadPositionsTable } from '../dashboard/account.js';
import { showAlert } from '../utils/alerts.js';

/**
 * Initialize the portfolio page
 */
async function initializePortfolio() {
    try {
        console.log('Initializing portfolio page...');
        
        // Create a container for alerts if it doesn't exist
        if (!document.querySelector('.content-container')) {
            const mainContainer = document.querySelector('main .container') || document.querySelector('main');
            if (mainContainer) {
                const contentContainer = document.createElement('div');
                contentContainer.className = 'content-container';
                mainContainer.prepend(contentContainer);
            }
        }
        
        // Add event listener for the global refresh button
        const refreshPortfolioButton = document.getElementById('refresh-portfolio');
        if (refreshPortfolioButton) {
            refreshPortfolioButton.addEventListener('click', async () => {
                await loadPositionsTable();
                showAlert('Portfolio refreshed successfully', 'success');
            });
        }
        
        // Load positions table
        await loadPositionsTable();
        
        console.log('Portfolio initialization complete');
    } catch (error) {
        console.error('Error initializing portfolio:', error);
        showAlert(`Error initializing portfolio: ${error.message}`, 'danger');
    }
}

// Initialize the portfolio when the DOM is loaded
document.addEventListener('DOMContentLoaded', initializePortfolio); 