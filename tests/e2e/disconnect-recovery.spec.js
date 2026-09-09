/**
 * OpenD disconnect / recovery journey.
 *
 * Broker-down (fixture broker=down) must surface a degraded banner
 * (`alert-danger`, "OpenD is not running"), block a staged copy at
 * revalidation, and recover cleanly once the broker is reachable again.
 */
import { test, expect } from '@playwright/test';
import { startFixtureServer, seedClipboard, readClipboard } from './server.js';

const PORT = 8104;
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

test('broker down: degraded banner + copy blocked; recovery restores staging', async ({ page }) => {
    await server.publish('complete_closed');
    await page.goto('/');

    // Healthy at load: banner hidden, staged copy available.
    await expect(page.locator('#opend-status-banner')).toHaveClass(/d-none/);
    const cards = page.locator('#top-recommendations-cards .recommendation-card');
    await expect(cards).toHaveCount(2);
    const btn = cards.first().locator('.copy-ticket-btn');
    await expect(btn).toContainText('Stage ticket');

    // --- Disconnect: banner flips within a poll cycle (10s). ---
    await server.control('/__e2e/broker', { state: 'down' });
    await expect(page.locator('#opend-status-banner')).not.toHaveClass(/d-none/, { timeout: 20000 });
    await expect(page.locator('#opend-status-banner')).toHaveClass(/alert-danger/);
    await expect(page.locator('#opend-status-title')).toHaveText('OpenD is not running', { timeout: 20000 });

    // Copy is blocked at revalidation: broker has no session evidence.
    const sentinel = await seedClipboard(page);
    await btn.click();
    await expect(btn).toContainText('Review only');
    await expect(btn).toHaveAttribute('title', /fresh broker evidence|broker session|OpenD unavailable/i);
    expect(await readClipboard(page)).toBe(sentinel);

    // --- Recovery: banner hides again, a fresh refresh restores staging. ---
    await server.control('/__e2e/broker', { state: 'up' });
    await server.control('/__e2e/evidence', { source: 'broker', quote_age_sec: 7200 });
    await expect(page.locator('#opend-status-banner')).toHaveClass(/d-none/, { timeout: 20000 });

    await server.control('/__e2e/refresh', { mode: 'fast', duration_ms: 600 });
    await page.locator('#run-refresh-btn').click();
    await expect(page.locator('#run-status')).toContainText('PLANNING');
    await expect(btn).toContainText('Stage ticket', { timeout: 20000 });
});
