/**
 * Shared alert/banner state — shadcn Alert behavior as a vanilla port.
 *
 * Single owner for tone allowlisting and visibility toggling so banner
 * callers never overwrite layout classes with `className = ...` and never
 * depend on Bootstrap JS for dismiss. Visuals stay in `ft.css` (telemetry
 * tokens, zero radius); this module only manages classes and text.
 *
 * Contracts: API-fed text must reach the DOM via `textContent` only.
 * Visibility uses `d-none`/`show` (both preserved for test/E2E compat);
 * never hide stale/disconnected/error states with CSS-only rules.
 */

export const ALERT_TONES = ['danger', 'warning', 'info', 'success'];

const TONE_CLASS = {
    danger: 'alert-danger',
    warning: 'alert-warning',
    info: 'alert-info',
    success: 'alert-success',
};
const TONE_CLASSES = Object.values(TONE_CLASS);

/** Allowlist a tone string; unknown input falls back to `info`. */
export function normalizeAlertTone(tone) {
    return Object.prototype.hasOwnProperty.call(TONE_CLASS, tone) ? tone : 'info';
}

/**
 * Apply tone + visibility to a banner element, preserving every other
 * class (spacing, layout, dismissible, fade). Never assigns `className`.
 */
export function setAlertState(el, { tone = 'info', visible = true } = {}) {
    if (!el || !el.classList) return;
    el.classList.add('alert');
    for (const cls of TONE_CLASSES) el.classList.remove(cls);
    el.classList.add(TONE_CLASS[normalizeAlertTone(tone)]);
    if (visible) {
        el.classList.remove('d-none');
        el.classList.add('show');
    } else {
        el.classList.add('d-none');
        el.classList.remove('show');
    }
}

/** Assign plain text only when changed (avoids live-region re-announce). */
export function setTextIfChanged(el, text) {
    if (!el) return;
    const next = text ?? '';
    if (el.textContent !== next) el.textContent = next;
}
