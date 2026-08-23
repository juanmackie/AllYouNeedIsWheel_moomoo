import { escapeHtml } from './formatters.js';

/**
 * Alert utility functions for displaying messages to users
 */

/**
 * Show an alert message that disappears after a set time
 * @param {string} message - The message to display
 * @param {string} type - Alert type (success, info, warning, danger)
 * @param {number} duration - Time in milliseconds before alert disappears
 */
function showAlert(message, type = 'info', duration = 5000) {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
    alertDiv.role = 'alert';
    alertDiv.innerHTML = `
        ${escapeHtml(message)}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;

    // Add the alert at the top of the content container
    const contentContainer = document.querySelector('.content-container') || document.querySelector('main');
    if (contentContainer) {
        contentContainer.prepend(alertDiv);

        // Auto-dismiss after duration
        setTimeout(() => {
            alertDiv.classList.remove('show');
            setTimeout(() => alertDiv.remove(), 150);
        }, duration);
    }
}

// Export the functions
export { showAlert };