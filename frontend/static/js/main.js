/**
 * Auto-Trader Frontend
 * Main JavaScript file
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
});

// Add CustomEvent polyfill for older browsers
(function() {
    if (typeof window.CustomEvent === 'function') return false;
    
    function CustomEvent(event, params) {
        params = params || { bubbles: false, cancelable: false, detail: null };
        const evt = document.createEvent('CustomEvent');
        evt.initCustomEvent(event, params.bubbles, params.cancelable, params.detail);
        return evt;
    }
    
    window.CustomEvent = CustomEvent;
})();

// Add Array.from polyfill for older browsers
if (!Array.from) {
    Array.from = function(arrayLike) {
        return [].slice.call(arrayLike);
    };
}

// Add Promise polyfill for older browsers (minimal implementation)
if (!window.Promise) {
    window.Promise = function(executor) {
        this.then = function(onFulfilled) {
            this.onFulfilled = onFulfilled;
            return this;
        };
        this.catch = function(onRejected) {
            this.onRejected = onRejected;
            return this;
        };
        
        const resolve = (value) => {
            setTimeout(() => {
                if (this.onFulfilled) this.onFulfilled(value);
            }, 0);
        };
        
        const reject = (reason) => {
            setTimeout(() => {
                if (this.onRejected) this.onRejected(reason);
            }, 0);
        };
        
        executor(resolve, reject);
    };
    
    window.Promise.all = function(promises) {
        return new Promise((resolve, reject) => {
            let results = [];
            let completedCount = 0;
            
            promises.forEach((promise, index) => {
                promise.then(value => {
                    results[index] = value;
                    completedCount++;
                    
                    if (completedCount === promises.length) {
                        resolve(results);
                    }
                }).catch(reject);
            });
        });
    };
}

// Add fetch polyfill (minimal implementation, for modern browsers that don't support fetch)
if (!window.fetch) {
    window.fetch = function(url, options) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open(options?.method || 'GET', url);
            
            if (options?.headers) {
                Object.keys(options.headers).forEach(key => {
                    xhr.setRequestHeader(key, options.headers[key]);
                });
            }
            
            xhr.onload = function() {
                const response = {
                    ok: xhr.status >= 200 && xhr.status < 300,
                    status: xhr.status,
                    json: function() {
                        return Promise.resolve(JSON.parse(xhr.responseText));
                    },
                    text: function() {
                        return Promise.resolve(xhr.responseText);
                    }
                };
                resolve(response);
            };
            
            xhr.onerror = function() {
                reject(new Error('Network error'));
            };
            
            xhr.send(options?.body || null);
        });
    };
}

// ── Error boundary ────────────────────────────────────────────────────
window.addEventListener('error', function (event) {
    const boundary = document.getElementById('global-error-boundary');
    if (boundary) {
        boundary.innerHTML = '<div class="alert alert-danger alert-dismissible fade show m-2" role="alert"><strong>Unexpected error</strong>: A widget failed to load. <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Dismiss error"></button></div>';
    }
    console.error('Global error caught:', event.error || event.message);
});

window.addEventListener('unhandledrejection', function (event) {
    console.error('Unhandled promise rejection:', event.reason);
}); 
