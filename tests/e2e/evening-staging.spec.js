/**
 * Evening staging journey (closed-market complete run → manual copy draft).
 *
 * Exercises the REAL backend: seeded planning/closed runs + the real
 * /api/run/copy-check route. No network interception — only the fixture
 * server (deterministic stub broker at the service layer).
 */
import { test, expect } from '@playwright/test';
import { startFixtureServer, seedClipboard, readClipboard } from './server.js';

const PORT = 8101;
test.use({
    baseURL: `http://127.0.0.1:${PORT}`,
    permissions: ['clipboard-read', 'clipboard-write'],
});

let server;
test.beforeAll(async () => {
    server = await startFixtureServer(PORT);
});
test.afterAll(async () => {
    await server?.close();
});
test.beforeEach(async () => {
    await server.reset();
});

test('evening: complete closed-market run stages a copy ticket', async ({ page }) => {
    await server.publish('complete_closed');
    await page.goto('/');

    await expect(page.locator('#run-status')).toContainText('PLANNING');
    const cards = page.locator('#top-recommendations-cards .recommendation-card');
    await expect(cards).toHaveCount(2);

    const btn = cards.first().locator('.copy-ticket-btn');
    await expect(btn).toBeEnabled();
    await expect(btn).toContainText('Stage ticket');

    await btn.click();
    await expect(btn).toContainText('Copied');
    await expect(btn).toHaveClass(/btn-success/);

    const clip = await readClipboard(page);
    expect(clip).toContain('SELL TO OPEN CSP');
    expect(clip).toMatch(/PUT [A-Z]+ \d{8} \d+\.\d{2} x\d/);
    expect(clip).toContain('STAGED FOR US MARKET OPEN');
});

test('evening: planning run with partial scan coverage → review-only with visible blocker', async ({ page }) => {
    await server.publish('planning_partial');
    await page.goto('/');

    await expect(page.locator('#run-status')).toContainText('PLANNING');
    await expect(page.locator('#run-coverage')).toContainText('coverage 1/2');
    const cards = page.locator('#top-recommendations-cards .recommendation-card');
    await expect(cards).toHaveCount(2);

    const btn = cards.first().locator('.copy-ticket-btn');
    await expect(btn).toBeDisabled();
    await expect(btn).toContainText('Review only');
    // Visible blocker explaining why nothing can be staged.
    await expect(btn).toHaveAttribute('title', /coverage|planning|quota|scan/i);
});

test('evening: persisted-broker fallback evidence → review-only (nothing copied)', async ({ page }) => {
    await server.publish('persisted_fallback');
    await page.goto('/');

    await expect(page.locator('#run-status')).toContainText('PLANNING');
    const cards = page.locator('#top-recommendations-cards .recommendation-card');
    await expect(cards).toHaveCount(2);

    const btn = cards.first().locator('.copy-ticket-btn');
    await expect(btn).toBeDisabled();
    await expect(btn).toContainText('Review only');
    await expect(btn).toHaveAttribute('title', /persisted/i);

    // Guard against any copy side effect: the click is a no-op and the
    // clipboard is never written.
    const sentinel = await seedClipboard(page);
    const copyChecks = [];
    page.on('request', (req) => {
        if (req.url().includes('/api/run/copy-check')) copyChecks.push(req.url());
    });
    await page.evaluate(() => {
        const el = document.querySelector('.copy-ticket-btn');
        if (el) el.click();
    });
    await page.waitForTimeout(400);
    expect(copyChecks).toHaveLength(0);
    expect(await readClipboard(page)).toBe(sentinel);
});
