import { test, expect } from '@playwright/test';
import { startFixtureServer } from './server.js';

const PORT = 8115;
let server;
test.use({ baseURL: `http://127.0.0.1:${PORT}` });
test.beforeAll(async () => { server = await startFixtureServer(PORT); });
test.afterAll(async () => { await server?.close(); });

// Browser-only synthetic accounting payloads, never production broker data.
for (const width of [320, 1440]) {
    test(`return units and unavailable history remain honest at ${width}px`, async ({ page }) => {
        await server.reset();
        await server.publish('complete_closed');
        await page.setViewportSize({ width, height: 900 });
        let unavailable = false;
        await page.route('**/api/portfolio/history*', route => route.fulfill(unavailable ? {
            status: 503, json: { error: 'test history outage' },
        } : {
            json: {
                count: 2,
                series: [
                    { captured_at: '2026-01-01', net_liquidation: 10000 },
                    { captured_at: '2026-02-01', net_liquidation: 15073 },
                ],
                pace: { target_nav: 50000, progress_pct: 12.7, annualized_pace: 0.5, eta_days: 1000 },
            },
        }));
        await page.route('**/api/options/analytics/outcomes?*', route => route.fulfill({
            json: {
                totals: { sample_size: 1, measured_count: 1, coverage_pct: 100,
                    net_dollars: 73, capital_days: 217000, owner_efficiency: 73 / 217000 },
                groups: {}, outcomes: [], count: 0,
            },
        }));
        await page.goto('/');
        for (const id of ['growth-panel', 'outcome-panel']) {
            await page.locator(`#${id}`).evaluate(el => { el.closest('details').open = true; });
        }
        await expect(page.locator('#outcome-totals')).toContainText('3.36 bp/day', { timeout: 20000 });
        await expect(page.locator('#growth-track-badge')).toHaveText('NAV RISING');
        await expect(page.locator('#growth-panel')).toContainText('Deposits and withdrawals are not removed');
        await expect(page.locator('#outcome-panel')).toContainText('not total portfolio return');
        await expect(page.locator('#outcome-panel')).toContainText('not lifetime performance totals');
        await expect(page.getByText('Gross premium / capital (annualized)').first()).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);

        unavailable = true;
        await page.evaluate(async () => {
            const { renderGrowthPanel } = await import('/static/js/dashboard/growth-panel.js');
            await renderGrowthPanel();
        });
        await expect(page.locator('#growth-track-badge')).toHaveText('HISTORY UNAVAILABLE');
        await expect(page.locator('#growth-annualized')).toHaveText('—');
        await expect(page.locator('#growth-eta')).toHaveText('—');
    });
}
