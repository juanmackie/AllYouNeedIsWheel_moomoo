import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderGrowthPanel } from '../../frontend/static/js/dashboard/growth-panel.js';

function payloadResponse(payload) {
    return { ok: true, json: async () => payload };
}

const PANEL_DOM = `
          <div id="growth-track-badge"></div><div id="growth-current-nav"></div><div id="growth-target-nav"></div>
          <div id="growth-heading"></div><div id="growth-starting-nav"></div><div id="growth-contributed"></div>
          <div id="growth-investment"></div><div id="growth-investment-note"></div>
          <div id="growth-progress"></div>
          <div id="growth-progress-bar"></div>
          <div id="growth-equity-curve"></div><div id="journal-win-rate"></div><div id="journal-win-rate-detail"></div>
          <div id="journal-exits"></div><div id="journal-rolls"></div><div id="journal-entries"></div>
          <div id="journal-snapshots"></div><div id="mc-chart"></div><div id="mc-median"></div>
          <div id="mc-range"></div><div id="mc-prob"></div><div id="mc-eta"></div><div id="mc-meta"></div>
          <div id="mc-fills-note"></div>`;

const MEASURED = {
    status: 'measured',
    investment_multiple: 1.1,
    investment_return_pct: 10.0,
    window: { from: '2026-01-01T00:00:00Z', to: '2026-01-11T00:00:00Z', days: 10 },
    contributed: { net: 40000, count: 1, currency: 'USD', complete: true },
    gaps: [],
    since_last_capital_movement: { verified: true, change_pct: 1.0 },
};

function stubGrowthApis(lifecycleAnalytics, overrides = {}) {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
        if (String(url).includes('/api/portfolio/history')) return payloadResponse({
            count: 3,
            series: [
                { net_liquidation: 0, captured_at: '2026-01-01T00:00:00Z' },
                { net_liquidation: 100, captured_at: '2026-01-02T00:00:00Z' },
                { net_liquidation: 120, captured_at: '2026-01-03T00:00:00Z' },
            ],
            pace: { target_nav: 500, baseline_nav: 100, progress_pct: 4, reached: false },
            investment: overrides.investment ?? MEASURED,
            target_multiple: 5,
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
        document.getElementById('growth-investment').textContent = '1.40×';
        document.getElementById('growth-contributed').textContent = '+$1,000';
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));
        await renderGrowthPanel();
        for (const id of ['growth-current-nav', 'growth-target-nav', 'growth-progress',
            'growth-investment', 'growth-contributed', 'growth-starting-nav',
            'journal-win-rate', 'journal-exits',
            'journal-rolls', 'journal-entries', 'journal-snapshots']) {
            expect(document.getElementById(id).textContent, id).toBe('—');
        }
        expect(document.getElementById('growth-progress-bar').style.width).toBe('0%');
        expect(document.getElementById('growth-investment').classList.contains('text-success')).toBe(false);
        expect(document.getElementById('growth-track-badge').textContent).toBe('HISTORY UNAVAILABLE');
        expect(document.getElementById('growth-equity-curve').textContent).toContain('Could not load');
        expect(document.getElementById('journal-win-rate-detail').textContent).toBe('Journal unavailable');
        expect(document.getElementById('growth-investment-note').textContent).toBe('');
    });

    it('shows the flow-adjusted multiple instead of an unadjusted NAV change', async () => {
        document.body.innerHTML = PANEL_DOM;
        stubGrowthApis({ win_rate: 50, total_exits: 3, measured_exit_count: 2, measured_wins: 1, roll_count: 0, per_symbol: [] });
        await renderGrowthPanel();
        expect(document.getElementById('growth-investment').textContent).toBe('1.10×');
        expect(document.getElementById('growth-investment').classList.contains('text-success')).toBe(true);
        expect(document.getElementById('growth-contributed').textContent).toBe('+$40,000.00');
        expect(document.getElementById('growth-investment-note').textContent).toContain('Flow-adjusted return +10.00%');
        expect(document.getElementById('growth-investment-note').textContent).toContain('Since the last capital movement: 1.00%');
        expect(document.getElementById('growth-track-badge').textContent).toBe('INVESTMENT GROWTH MEASURED');
    });

    it('reports unknown investment growth with a named gap instead of a number', async () => {
        document.body.innerHTML = PANEL_DOM;
        stubGrowthApis({ win_rate: 0, total_exits: 0, measured_exit_count: 0, measured_wins: 0, roll_count: 0, per_symbol: [] }, {
            investment: {
                status: 'unknown',
                investment_multiple: null,
                investment_return_pct: null,
                contributed: { net: 0, count: 0, currency: 'USD', complete: false },
                gaps: ['cash_flow_coverage_incomplete'],
            },
        });
        await renderGrowthPanel();
        expect(document.getElementById('growth-investment').textContent).toBe('unknown');
        expect(document.getElementById('growth-investment').classList.contains('text-success')).toBe(false);
        expect(document.getElementById('growth-investment-note').textContent).toContain('Investment growth is unknown.');
        expect(document.getElementById('growth-investment-note').textContent).toContain('Cash-flow history does not yet cover this window');
        expect(document.getElementById('growth-track-badge').textContent).toBe('PERFORMANCE UNKNOWN');
    });

    it('does not claim growth when the investment multiple is a deposit-only 1.00x', async () => {
        document.body.innerHTML = PANEL_DOM;
        stubGrowthApis({ win_rate: 0, total_exits: 0, measured_exit_count: 0, measured_wins: 0, roll_count: 0, per_symbol: [] }, {
            investment: { ...MEASURED, investment_multiple: 1.0, investment_return_pct: 0.0, since_last_capital_movement: null },
        });
        await renderGrowthPanel();
        expect(document.getElementById('growth-investment').textContent).toBe('1.00×');
        expect(document.getElementById('growth-investment-note').textContent).toContain('Flow-adjusted return 0.00%');
        expect(document.getElementById('growth-investment-note').textContent).not.toContain('Since the last capital movement');
    });

    it('keeps the measured-exit count label when some exits have measured P&L', async () => {
        document.body.innerHTML = PANEL_DOM;
        stubGrowthApis({ win_rate: 50, total_exits: 3, measured_exit_count: 2, measured_wins: 1, roll_count: 0, per_symbol: [] });

        await renderGrowthPanel();
        expect(document.getElementById('journal-win-rate').textContent).toBe('50%');
        expect(document.getElementById('journal-win-rate-detail').textContent).toBe('1 wins · 2 of 3 exits measured');
    });
});
