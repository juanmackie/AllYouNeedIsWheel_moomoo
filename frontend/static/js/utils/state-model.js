/**
 * Unified State Model for dashboard sections
 * Provides consistent loading/empty/error/stale states across all sections
 */

const StateModel = {
    /**
     * Show loading state for a section
     * @param {string} containerId - The ID of the container element
     * @param {string} message - Optional loading message
     */
    showLoading(containerId, message = 'Loading...') {
        const container = document.getElementById(containerId);
        if (!container) return;

        container.innerHTML = `
            <div class="text-center py-4">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
                <p class="mt-3 text-muted mb-0">${message}</p>
            </div>
        `;
    },

    /**
     * Show empty state for a section
     * @param {string} containerId - The ID of the container element
     * @param {string} message - Optional empty message
     */
    showEmpty(containerId, message = 'No data available right now.') {
        const container = document.getElementById(containerId);
        if (!container) return;

        container.innerHTML = `
            <div class="text-center py-4">
                <i class="bi bi-inbox display-4 text-muted"></i>
                <p class="mt-3 text-muted mb-0">${message}</p>
            </div>
        `;
    },

    /**
     * Show error state for a section
     * @param {string} containerId - The ID of the container element
     * @param {string} message - Error message
     * @param {Function} retryCallback - Optional callback for retry button
     */
    showError(containerId, message = 'An error occurred.', retryCallback = null) {
        const container = document.getElementById(containerId);
        if (!container) return;

        const retryButton = retryCallback
            ? `<button class="btn btn-outline-primary mt-3" type="button" data-state-retry>
                   <i class="bi bi-arrow-clockwise"></i> Try again
               </button>`
            : '';

        container.innerHTML = `
            <div class="text-center py-4">
                <i class="bi bi-exclamation-triangle display-4 text-warning"></i>
                <p class="mt-3 text-muted mb-0">${message}</p>
                ${retryButton}
            </div>
        `;

        if (retryCallback) {
            container.querySelector('[data-state-retry]')?.addEventListener('click', retryCallback);
        }
    },

    /**
     * Show stale state indicator
     * @param {string} sectionId - The ID of the section element
     * @param {string} timestamp - Optional timestamp of when data was fetched
     */
    showStale(sectionId, timestamp = null) {
        const section = document.getElementById(sectionId);
        if (!section) return;

        let staleBadge = section.querySelector('.stale-badge');
        if (!staleBadge) {
            staleBadge = document.createElement('span');
            staleBadge.className = 'badge bg-warning ms-2 stale-badge';
            const header = section.querySelector('.app-section__header h2');
            if (header) {
                header.appendChild(staleBadge);
            }
        }

        const timeStr = timestamp ? ` (${new Date(timestamp).toLocaleTimeString()})` : '';
        staleBadge.textContent = `Stale${timeStr}`;
    },

    /**
     * Clear stale state indicator
     * @param {string} sectionId - The ID of the section element
     */
    clearStale(sectionId) {
        const section = document.getElementById(sectionId);
        if (!section) return;

        const staleBadge = section.querySelector('.stale-badge');
        if (staleBadge) {
            staleBadge.remove();
        }
    },

    /**
     * Show content (hide all state indicators)
     * @param {string} containerId - The ID of the container element
     */
    showContent(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;
        // Content is assumed to be already in the container
        // This function exists for API consistency
    }
};

export default StateModel;
