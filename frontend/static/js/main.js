/**
 * All You Need Is Wheel — shared frontend bootstrap (OpenD banner, footer year)
 */

function updateOpenDStatusBanner(status) {
    const banner = document.getElementById('opend-status-banner');
    const title = document.getElementById('opend-status-title');
    const message = document.getElementById('opend-status-message');
    const meta = document.getElementById('opend-status-meta');

    if (!banner || !title || !message || !meta) {
        return;
    }

    window.appConnectionStatus = status || null;
    document.dispatchEvent(new CustomEvent('opend-status-changed', { detail: status || {} }));

    if (!status || status.status === 'connected') {
        banner.className = 'alert alert-warning d-none';
        title.textContent = 'OpenD status';
        message.textContent = 'OpenD is connected.';
        meta.textContent = '';
        return;
    }

    let bannerClass = 'alert alert-warning';
    let heading = 'OpenD needs attention';

    if (status.status === 'unavailable') {
        bannerClass = 'alert alert-danger';
        heading = 'OpenD is not running';
    } else if (status.status === 'login_required') {
        bannerClass = 'alert alert-warning';
        heading = 'OpenD login required';
    } else if (status.status === 'real_account_unavailable') {
        bannerClass = 'alert alert-warning';
        heading = 'Real account unavailable in OpenD';
    } else if (status.status === 'error') {
        bannerClass = 'alert alert-danger';
        heading = 'OpenD status error';
    }

    banner.className = bannerClass;
    title.textContent = heading;
    message.textContent = status.message || 'OpenD is not ready yet.';
    meta.textContent = status.host && status.port ? `${status.host}:${status.port}` : '';
}

window.updateOpenDStatusBanner = updateOpenDStatusBanner;

async function pollOpenDStatus() {
    try {
        const response = await fetch('/api/system/opend-status', {
            headers: {
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0'
            }
        });

        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}`);
        }

        const status = await response.json();
        if (status.status === 'connected' && window.appConnectionStatus && window.appConnectionStatus.status === 'real_account_unavailable') {
            return;
        }
        updateOpenDStatusBanner(status);
    } catch (error) {
        updateOpenDStatusBanner({
            status: 'error',
            message: `Unable to check OpenD status: ${error.message}`
        });
    }
}

function toggleMobileSidebar() {
    const sidebar = document.getElementById('app-sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    if (sidebar) sidebar.classList.toggle('open');
    if (overlay) overlay.classList.toggle('open');
}

// Initialize tooltips and popovers when page loads
document.addEventListener('DOMContentLoaded', function() {
    // Initialize Bootstrap tooltips
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
    
    // Initialize Bootstrap popovers
    const popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    popoverTriggerList.map(function (popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });
});

// Staggered section entry (progressive enhancement).
// Only hides sections when IntersectionObserver + motion are both allowed;
// no-JS and reduced-motion users always see full content.
function initSectionReveal() {
    if (!('IntersectionObserver' in window)) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const sections = document.querySelectorAll('.ft-section, .app-section');
    if (!sections.length) return;

    const observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            if (!entry.isIntersecting) return;
            entry.target.style.transitionDelay = '';
            entry.target.classList.add('visible');
            observer.unobserve(entry.target);
        });
    }, { threshold: 0.05, rootMargin: '0px 0px -40px 0px' });

    let index = 0;
    sections.forEach(function (section) {
        section.classList.add('ft-fade-up');
        // Stagger the initially-visible sections; off-screen ones reveal on scroll.
        section.style.transitionDelay = `${Math.min(index * 60, 300)}ms`;
        observer.observe(section);
        index += 1;
    });
}

// Set the current year in the footer
document.addEventListener('DOMContentLoaded', function() {
    const currentYearElement = document.getElementById('current-year');
    if (currentYearElement) {
        currentYearElement.textContent = new Date().getFullYear();
    }
    
    // Add a content container for alerts if it doesn't exist
    const mainContainer = document.querySelector('main');
    if (mainContainer && !document.querySelector('.content-container')) {
        const contentContainer = document.createElement('div');
        contentContainer.className = 'content-container';
        mainContainer.prepend(contentContainer);
    }

    const mobileToggle = document.getElementById('mobile-nav-toggle');
    if (mobileToggle) {
        mobileToggle.addEventListener('click', toggleMobileSidebar);
    }

    pollOpenDStatus();
    window.setInterval(pollOpenDStatus, 10000);

    initSectionReveal();

    // Theme preference: initialize data-theme from localStorage; if 'auto', respect OS preference
    (function initTheme() {
        try {
            const stored = localStorage.getItem('ui-theme');
            const html = document.documentElement;
            if (stored === 'dark' || stored === 'light') {
                html.setAttribute('data-theme', stored);
            } else {
                html.setAttribute('data-theme', 'auto');
            }
        } catch (e) {
            document.documentElement.setAttribute('data-theme', 'auto');
        }
    })();
});

window.cycleTheme = function cycleTheme() {
    const html = document.documentElement;
    try {
        let current = html.getAttribute('data-theme');
        if (current === 'auto') {
            html.setAttribute('data-theme', 'dark');
            localStorage.setItem('ui-theme', 'dark');
        } else if (current === 'dark') {
            html.setAttribute('data-theme', 'light');
            localStorage.setItem('ui-theme', 'light');
        } else {
            html.setAttribute('data-theme', 'auto');
            localStorage.setItem('ui-theme', 'auto');
        }
    } catch (e) {
        html.setAttribute('data-theme', 'auto');
    }
};
