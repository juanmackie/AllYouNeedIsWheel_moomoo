import { afterEach, describe, expect, it, vi } from 'vitest';

import { loadHoldings, renderHoldings } from '../../frontend/static/js/dashboard/holdings.js';

function setup() {
    document.body.innerHTML = `
      <section id="holdings-panel">
        <p id="holdings-status"></p><div id="holdings-summary"></div>
        <table><tbody id="holdings-body"></tbody></table>
        <div id="holdings-changes"></div><button id="refresh-holdings"></button>
      </section>`;
}

afterEach(() => {
    document.body.replaceChildren();
    localStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe('holdings panel', () => {
    it('renders broker and cycle fields as text, without interpreting payload markup', () => {
        setup();
        renderHoldings({
            available: true,
            snapshot_captured_at: '2026-09-30T10:00:00-04:00',
            dead_money_share_of_nav: 0.25,
            fill_ingest_age_seconds: 3600,
            holdings: [{
                ticker: '<img src=x onerror=alert(1)>', shares: 100,
                broker_sellable_shares: 60,
                broker_avg_cost: 50, basis_source: 'moomoo_avg_cost', adjusted_basis: null,
                cycle_state: 'assigned_uncovered', open_legs: [], best_call_at_or_above_basis: null,
            }],
            changes: [], expiries_next_7_days: [],
        });

        expect(document.querySelector('#holdings-body img')).toBeNull();
        expect(document.querySelector('#holdings-body').textContent).toContain('<img src=x onerror=alert(1)>');
        expect(document.querySelector('#holdings-body').textContent).toContain('60');
        expect(document.querySelector('#holdings-summary').textContent).toContain('25.0%');
    });

    it('requests changes since local last visit and stores the next visit only after success', async () => {
        setup();
        localStorage.setItem('holdings:lastVisitAt', '2026-09-29T10:00:00.000Z');
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ available: true, holdings: [], changes: [], expiries_next_7_days: [] }),
        });
        vi.stubGlobal('fetch', fetchMock);

        await loadHoldings();

        expect(fetchMock).toHaveBeenCalledWith('/api/portfolio/holdings?since=2026-09-29T10%3A00%3A00.000Z');
        expect(localStorage.getItem('holdings:lastVisitAt')).not.toBe('2026-09-29T10:00:00.000Z');
    });
});
