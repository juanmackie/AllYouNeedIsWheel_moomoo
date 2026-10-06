import { normalizeAlertTone, setAlertState } from './status-alert.js';

/**
 * Alert utility functions for displaying messages to users
 */

/**
 * Show a transient alert message that disappears after a set time.
 * Message text is set via textContent (never innerHTML). Dismiss is a
 * native button listener — no Bootstrap JS dependency.
 * @param {string} message - The message to display
 * @param {string} type - Alert type (success, info, warning, danger)
 * @param {number} duration - Time in milliseconds before alert disappears
 */
function showAlert(message, type = 'info', duration = 5000) {
    const tone = normalizeAlertTone(type);
    const alertDiv = document.createElement('div');
    alertDiv.classList.add('alert', 'alert-dismissible', 'fade');
    alertDiv.setAttribute('role', 'alert');

    const text = document.createElement('span');
    text.textContent = message ?? '';
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'btn-close';
    closeBtn.setAttribute('aria-label', 'Close');
    alertDiv.append(text, closeBtn);

    // Apply tone + visibility without wiping dismissible/fade classes.
    setAlertState(alertDiv, { tone, visible: true });

    // Add the alert at the top of the content container
    const contentContainer = document.querySelector('.content-container') || document.querySelector('main');
    if (!contentContainer) return;
    contentContainer.prepend(alertDiv);

    let dismissed = false;
    const dismiss = () => {
        if (dismissed) return;
        dismissed = true;
        clearTimeout(timer);
        setAlertState(alertDiv, { tone, visible: false });
        setTimeout(() => alertDiv.remove(), 150);
    };
    closeBtn.addEventListener('click', dismiss);
    const timer = setTimeout(dismiss, duration);
}

// Export the functions
export { showAlert };
