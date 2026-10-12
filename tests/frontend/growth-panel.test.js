import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderGrowthPanel } from '../../frontend/static/js/dashboard/growth-panel.js';

function payloadResponse(payload) {
    return { ok: true, json: async () => payload };
}

describe('growth panel data truth', () => {
    afterEach(() => vi.unstubAllGlobals());

    it('excludes non-positive NAV points and reports measured exits in the win-rate label', async () => {
        document.body.innerHTML = `
          <div id="growth-track-badge"></div><div id="growth-current-nav"></div><div id="growth-target-nav"></div>
          <div id="growth-heading"></div><div id="growth-eta-label"></div><div id="growth-progress"></div>
          <div id="growth-progress-bar"></div><div id="growth-annualized"></div><div id="growth-eta"></div>
          <div id="growth-equity-curve"></div><div id="journal-win-rate"></div><div id="journal-win-rate-detail"></div>
          <div id="journal-exits"></div><div id="journal-rolls"></div><div id="journal-entries"></div>
          <div id="journal-snapshots"></div><div id="mc-chart"></div><div id="mc-median"></div>
          <div id="mc-range"></div><div id="mc-prob"></div><div id="mc-eta"></div><div id="mc-meta"></div>
          <div id="mc-fills-note"></div>`;
        vi.stubGlobal('fetch', vi.fn(async (url) => {
            if (String(url).includes('/api/portfolio/history')) return payloadResponse({
                count: 3,
                series: [
                    { net_liquidation: 0, captured_at: '2026-01-01T00:00:00Z' },
                    { net_liquidation: 100, captured_at: '2026-01-02T00:00:00Z' },
                    { net_liquidation: 120, captured_at: '2026-01-03T00:00:00Z' },
                ],
                pace: { target_nav: 500, progress_pct: 4, annualized_pace: null, eta_days: null },
            });
            if (String(url).includes('/api/options/analytics/lifecycle')) return payloadResponse({
                analytics: { win_rate: 0, total_exits: 3, measured_exit_count: 0, measured_wins: 0, roll_count: 1, per_symbol: [{ entries: 2 }] },
            });
            return payloadResponse({ status: 'unavailable' });
        }));

        await renderGrowthPanel();
        expect(document.getElementById('journal-win-rate').textContent).toBe('—');
        expect(document.getElementById('journal-win-rate-detail').textContent).toBe('0 wins · 0 of 3 exits measured');
        expect(document.getElementById('journal-exits').textContent).toBe('3');
        expect(document.getElementById('growth-current-nav').textContent).toContain('120');
        expect(document.querySelector('#growth-equity-curve svg').getAttribute('aria-label')).toBe('Net liquidation across 2 runs');
        expect(document.querySelector('#growth-equity-curve p').textContent).toContain('2 snapshots');
    });
});
