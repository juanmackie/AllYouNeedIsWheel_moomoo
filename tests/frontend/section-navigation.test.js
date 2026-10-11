import { afterEach, describe, expect, it } from 'vitest';
import { initializeSectionNavigation } from '../../frontend/static/js/dashboard/section-navigation.js';

function setDashboardMarkup() {
    document.body.innerHTML = `
      <nav class="dashboard-section-nav" aria-label="Dashboard sections">
        <a href="#account-area">Account</a>
        <a href="#growth-area">Growth</a>
        <a href="#outcomes-area">Outcomes</a>
      </nav>
      <section id="account-area"><h2>Account &amp; income</h2></section>
      <details id="growth-area"><summary>Growth</summary><h2>Projection</h2></details>
      <details id="outcomes-area"><summary>Outcomes</summary><h2>Measured outcomes</h2></details>
      <main id="main-content" tabindex="-1"><h1>Dashboard</h1></main>
    `;
    window.history.replaceState(null, '', '/');
}

afterEach(() => {
    document.body.innerHTML = '';
    window.history.replaceState(null, '', '/');
});

describe('dashboard section navigation', () => {
    it('keeps native anchor behavior, opens disclosures, and focuses their summaries', () => {
        setDashboardMarkup();
        initializeSectionNavigation();

        const link = document.querySelector('a[href="#growth-area"]');
        const click = new MouseEvent('click', { bubbles: true, cancelable: true });
        link.dispatchEvent(click);

        expect(click.defaultPrevented).toBe(false);
        expect(document.getElementById('growth-area').open).toBe(true);
        expect(document.querySelector('#growth-area summary')).toBe(document.activeElement);
        expect(link.getAttribute('aria-current')).toBe('location');
        expect(document.querySelector('a[href="#account-area"]').hasAttribute('aria-current')).toBe(false);
    });

    it('focuses the section heading for visible areas and tracks browser hash navigation', () => {
        setDashboardMarkup();
        initializeSectionNavigation();

        const link = document.querySelector('a[href="#account-area"]');
        link.click();
        const heading = document.querySelector('#account-area h2');
        expect(heading).toBe(document.activeElement);
        expect(heading.getAttribute('tabindex')).toBe('-1');

        window.history.replaceState(null, '', '/#outcomes-area');
        window.dispatchEvent(new HashChangeEvent('hashchange'));

        const outcomesLink = document.querySelector('a[href="#outcomes-area"]');
        expect(document.getElementById('outcomes-area').open).toBe(true);
        expect(document.querySelector('#outcomes-area summary')).toBe(document.activeElement);
        expect(outcomesLink.getAttribute('aria-current')).toBe('location');
        expect(link.hasAttribute('aria-current')).toBe(false);
    });

    it('reflects a directly loaded section hash without taking focus on page load', () => {
        setDashboardMarkup();
        window.history.replaceState(null, '', '/#growth-area');

        initializeSectionNavigation();

        expect(document.getElementById('growth-area').open).toBe(true);
        expect(document.querySelector('a[href="#growth-area"]').getAttribute('aria-current')).toBe('location');
        expect(document.activeElement).toBe(document.body);
    });

    it('ignores malformed hashes without interrupting dashboard initialization', () => {
        setDashboardMarkup();
        window.history.replaceState(null, '', '/#%');

        expect(() => initializeSectionNavigation()).not.toThrow();
        expect(document.querySelectorAll('[aria-current="location"]')).toHaveLength(0);
    });

    it('leaves modified anchor clicks in the current page untouched', () => {
        setDashboardMarkup();
        initializeSectionNavigation();

        const link = document.querySelector('a[href="#growth-area"]');
        const click = new MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true });
        link.dispatchEvent(click);

        expect(click.defaultPrevented).toBe(false);
        expect(document.getElementById('growth-area').open).toBe(false);
        expect(link.hasAttribute('aria-current')).toBe(false);
        expect(document.activeElement).toBe(document.body);
    });

    it('does not take focus from a non-navigation hash such as the skip link', () => {
        setDashboardMarkup();
        initializeSectionNavigation();

        const main = document.getElementById('main-content');
        main.focus();
        window.history.replaceState(null, '', '/#main-content');
        window.dispatchEvent(new HashChangeEvent('hashchange'));

        expect(document.activeElement).toBe(main);
        expect(document.querySelectorAll('[aria-current="location"]')).toHaveLength(0);
    });
});
