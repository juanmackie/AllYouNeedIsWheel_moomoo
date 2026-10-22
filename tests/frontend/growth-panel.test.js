import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderGrowthPanel } from '../../frontend/static/js/dashboard/growth-panel.js';

function payloadResponse(payload) {
    return { ok: true, json: async () => payload };
}

const PANEL_DOM = `
          <div id="growth-track-badge"></div><div id="growth-current-nav"></div><div id="growth-target-nav"></div>
          <div id="growth-heading"></div><div id="growth-eta-label"></div><div id="growth-progress"></div>
          <div id="growth-progress-bar"></div><div id="growth-annualized"></div><div id="growth-eta"></div>
          <div id="growth-equity-curve"></div><div id="journal-win-rate"></div><div id="journal-win-rate-detail"></div>
          <div id="journal-exits"></div><div id="journal-rolls"></div><div id="journal-entries"></div>
          <div id="journal-snapshots"></div><div id="mc-chart"></div><div id="mc-median"></div>
          <div id="mc-range"></div><div id="mc-prob"></div><div id="mc-eta"></div><div id="mc-meta"></div>
          <div id="mc-fills-note"></div>`;

function stubGrowthApis(lifecycleAnalytics) {
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
            analytics: lifecycleAnalytics,
        });
        return payloadResponse({ status: 'unavailable' });
    }));
}

describe('growth panel data truth', () => {
    afterEach(() => vi.unstubAllGlobals());

    it('excludes non-positive NAV points and reports measured exits in the win-rate label', async () => {
        document.body.innerHTML = PANEL_DOM;
        stubGrowthApis({ win_rate: 0, total_exits: 3, measured_exit_count: 0, measured_wins: 0, roll_count: 1, per_symbol: [{ entries: 2 }] });

        await renderGrowthPanel();
        expect(document.getElementById('journal-win-rate').textContent).toBe('—');
        expect(document.getElementById('journal-win-rate-detail').textContent).toBe('Exit P&L is not inferred; see Outcomes for broker-verified results');
        expect(document.getElementById('journal-exits').textContent).toBe('3');
        expect(document.getElementById('growth-current-nav').textContent).toContain('120');
        expect(document.querySelector('#growth-equity-curve svg').getAttribute('aria-label')).toBe('Net liquidation across 2 runs');
        expect(document.querySelector('#growth-equity-curve p').textContent).toContain('2 snapshots');
    });

    it('clears previous growth and journal values when history becomes unavailable', async () => {
        document.body.innerHTML = PANEL_DOM;
        stubGrowthApis({ win_rate: 50, total_exits: 3, measured_exit_count: 2, measured_wins: 1, roll_count: 1, per_symbol: [{ entries: 2 }] });
        await renderGrowthPanel();
        document.getElementById('growth-annualized').textContent = '40%/yr';
        document.getElementById('growth-annualized').classList.add('text-success');
        document.getElementById('growth-eta').textContent = '~3y';
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));
        await renderGrowthPanel();
        for (const id of ['growth-current-nav', 'growth-target-nav', 'growth-progress',
            'growth-annualized', 'growth-eta', 'journal-win-rate', 'journal-exits',
            'journal-rolls', 'journal-entries', 'journal-snapshots']) {
            expect(document.getElementById(id).textContent, id).toBe('—');
        }
        expect(document.getElementById('growth-progress-bar').style.width).toBe('0%');
        expect(document.getElementById('growth-annualized').classList.contains('text-success')).toBe(false);
        expect(document.getElementById('growth-track-badge').textContent).toBe('HISTORY UNAVAILABLE');
        expect(document.getElementById('growth-equity-curve').textContent).toContain('Could not load');
        expect(document.getElementById('journal-win-rate-detail').textContent).toBe('Journal unavailable');
    });

    it('labels positive raw NAV change without claiming strategy compounding', async () => {
        document.body.innerHTML = PANEL_DOM;
        vi.stubGlobal('fetch', vi.fn(async (url) => payloadResponse(
            String(url).includes('/api/portfolio/history') ? {
                count: 2, series: [{ net_liquidation: 10000 }, { net_liquidation: 15000 }],
                pace: { target_nav: 50000, progress_pct: 12.5, annualized_pace: 0.5, eta_days: 1000 },
            } : {}
        )));
        await renderGrowthPanel();
        expect(document.getElementById('growth-track-badge').textContent).toBe('NAV RISING');
    });

    it('keeps the measured-exit count label when some exits have measured P&L', async () => {
        document.body.innerHTML = PANEL_DOM;
        stubGrowthApis({ win_rate: 50, total_exits: 3, measured_exit_count: 2, measured_wins: 1, roll_count: 0, per_symbol: [] });

        await renderGrowthPanel();
        expect(document.getElementById('journal-win-rate').textContent).toBe('50%');
        expect(document.getElementById('journal-win-rate-detail').textContent).toBe('1 wins · 2 of 3 exits measured');
    });
});
