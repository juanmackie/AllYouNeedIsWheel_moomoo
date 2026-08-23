/**
 * Main dashboard module — bootstrap entry point.
 *
 * Re-exports the primary initialization function so that consumer code
 * (e.g. the HTML template) does not need to change.
 */
import { initializeDashboard } from './dashboard-init.js';

export { initializeDashboard };

// Bootstrap dashboard on page load
document.addEventListener('DOMContentLoaded', () => {
    initializeDashboard().catch(err => {
        console.error('Dashboard initialization failed:', err);
    });
});
