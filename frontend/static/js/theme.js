const STORAGE_KEY = 'theme';

function getSystemTheme() {
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        return 'dark';
    }

    return 'light';
}

function getSavedTheme() {
    return localStorage.getItem(STORAGE_KEY);
}

function getEffectiveTheme() {
    return getSavedTheme() || getSystemTheme();
}

function applyTheme(theme) {
    document.documentElement.setAttribute('data-bs-theme', theme);
    updateToggleIcon(theme);
}

function updateToggleIcon(theme) {
    const toggle = document.getElementById('theme-toggle');
    if (!toggle) {
        return;
    }

    const icon = toggle.querySelector('i');
    if (!icon) {
        return;
    }

    if (theme === 'dark') {
        icon.className = 'bi bi-sun-fill';
        toggle.setAttribute('aria-label', 'Switch to light mode');
    } else {
        icon.className = 'bi bi-moon-fill';
        toggle.setAttribute('aria-label', 'Switch to dark mode');
    }
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-bs-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    localStorage.setItem(STORAGE_KEY, next);
    applyTheme(next);
}

function initTheme() {
    const theme = getEffectiveTheme();
    applyTheme(theme);

    const toggle = document.getElementById('theme-toggle');
    if (toggle) {
        toggle.addEventListener('click', function (event) {
            event.preventDefault();
            toggleTheme();
        });
    }

    if (window.matchMedia) {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
            if (!getSavedTheme()) {
                applyTheme(getSystemTheme());
            }
        });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTheme);
} else {
    initTheme();
}
