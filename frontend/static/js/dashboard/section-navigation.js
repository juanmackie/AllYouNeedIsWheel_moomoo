const NAV_SELECTOR = '.dashboard-section-nav';

function findNavigationTarget(hash, documentRef) {
    if (!hash || hash === '#') return null;
    try {
        return documentRef.getElementById(decodeURIComponent(hash.slice(1)));
    } catch {
        return null;
    }
}

function updateCurrentLink(nav, hash) {
    const links = nav.querySelectorAll('a[href^="#"]');
    let currentLink = null;

    for (const link of links) {
        if (link.getAttribute('href') === hash) {
            currentLink = link;
            link.setAttribute('aria-current', 'location');
        } else {
            link.removeAttribute('aria-current');
        }
    }

    return currentLink;
}

function getDisclosure(target) {
    if (target.matches('details')) return target;
    return target.closest('details') || target.querySelector('details');
}

function getFocusTarget(target, disclosure) {
    if (disclosure) return disclosure.querySelector(':scope > summary');
    return target.querySelector('h1, h2, h3, [role="heading"]') || target;
}

function focusTarget(target, { focus }) {
    if (!target) return;

    const disclosure = getDisclosure(target);
    if (disclosure) disclosure.open = true;

    if (!focus) return;

    const focusable = getFocusTarget(target, disclosure);
    if (!focusable) return;
    if (!focusable.hasAttribute('tabindex')) focusable.setAttribute('tabindex', '-1');
    focusable.classList.add('dashboard-section-nav-target');
    focusable.focus({ preventScroll: true });
}

/** Bind native dashboard anchors, disclosure opening, focus, and location state. */
export function initializeSectionNavigation(documentRef = document, windowRef = window) {
    const nav = documentRef.querySelector(NAV_SELECTOR);
    if (!nav || nav.dataset.bound === 'true') return;

    nav.dataset.bound = 'true';

    const syncLocation = (focus) => {
        const hash = windowRef.location.hash;
        const currentLink = updateCurrentLink(nav, hash);
        if (!currentLink) return;
        focusTarget(findNavigationTarget(hash, documentRef), { focus });
    };

    nav.addEventListener('click', (event) => {
        const link = event.target.closest('a[href^="#"]');
        if (!link || !nav.contains(link)) return;
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        if (link.target && link.target !== '_self') return;

        updateCurrentLink(nav, link.getAttribute('href'));
        focusTarget(findNavigationTarget(link.getAttribute('href'), documentRef), { focus: true });
    });

    windowRef.addEventListener('hashchange', () => syncLocation(true));
    syncLocation(false);
}
