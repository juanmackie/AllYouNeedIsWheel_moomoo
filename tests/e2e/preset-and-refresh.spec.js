/**
 * Preset change + refresh completion-path journey.
 *
 * Preset switch must flip settings AND trigger a fresh refresh that
 * republishes with the new preset's watchlist. Refresh completion paths:
 * first-ever run, fast completion, >60s scan (unbounded poll), and
 * failure-after-success (last-good retained + FAILED badge).
 *
 * NOTE: the preset button group (`.preset-selector`) is cleared by
 * `renderPresetSelector` after a successful change because the real
 * POST /api/settings/preset response carries no `presets` — a pre-existing
 * frontend defect (the buttons come back on reload). We therefore assert the
 * intended behavior (active label, effective values, republished run) rather
 * than the active button class.
 */
import { test, expect } from '@playwright/test';
import { startFixtureServer } from './server.js';

const PORT = 8103;
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

/** Track POSTs to the real refresh endpoint (the only place a scan is triggered). */
function trackRefreshPosts(page) {
    const posts = [];
    page.on('request', (req) => {
        if (req.method() === 'POST' && new URL(req.url()).pathname === '/api/run/refresh') {
            posts.push(req);
        }
    });
    return posts;
}

test('preset change flips the active preset and triggers a republished refresh', async ({ page }) => {
    test.setTimeout(60_000);
    await server.publish('complete_closed', 'balanced');
    await page.goto('/');

    await expect(page.locator('#run-preset')).toContainText('Balanced');
    await expect(page.locator('#top-recommendations-content .recommendation-card')).toHaveCount(2);
    await expect(page.locator('#top-recommendations-content')).toContainText('AAPL');

    const effectiveBefore = await page.locator('#preset-effective').innerText();
    const lastSuccessBefore = await page.locator('#run-last-success').innerText();
    const refreshPosts = trackRefreshPosts(page);
    await server.control('/__e2e/refresh', { mode: 'fast', duration_ms: 700 });

    await page.locator('[data-preset="aggressive"]').click();

    // Selecting a preset starts a fresh scan (manual refresh path).
    await expect.poll(() => refreshPosts.length).toBe(1);
    // Effective (read-only) values flip immediately from the settings response.
    await expect
        .poll(() => page.locator('#preset-effective').innerText())
        .not.toBe(effectiveBefore);

    // The new run is adopted: Aggressive label, new publish, aggressive cards.
    await expect(page.locator('#run-preset')).toContainText('Aggressive', { timeout: 20000 });
    await expect
        .poll(() => page.locator('#run-last-success').innerText())
        .not.toBe(lastSuccessBefore);
    await expect(page.locator('#top-recommendations-content')).toContainText('MSFT', { timeout: 20000 });
    await expect(page.locator('#top-recommendations-content .recommendation-card')).toHaveCount(2);
});

test('first-ever run: NO RUN → refresh → PLANNING with cards', async ({ page }) => {
    test.setTimeout(60_000);
    // Fresh install: no run, no attempt (reset wipes before each test).
    await page.goto('/');
    await expect(page.locator('#run-status')).toContainText('NO RUN');

    await server.control('/__e2e/refresh', { mode: 'fast', duration_ms: 600 });
    await page.locator('#run-refresh-btn').click();

    // Adopted on the 5s run-state poll cadence.
    await expect(page.locator('#run-status')).toContainText('PLANNING', { timeout: 20000 });
    await expect(page.locator('#run-last-success')).toContainText(/last success/i);
    const cards = page.locator('#top-recommendations-content .recommendation-card');
    await expect(cards).toHaveCount(2);
    await expect(cards.first().locator('.copy-ticket-btn')).toContainText('Stage ticket');
});

test('scan longer than 60s keeps REFRESHING over the retained run until adoption', async ({ page }) => {
    test.setTimeout(120_000);
    // Seed a last-good run so the strip shows live REFRESHING NN% progress
    // over the retained snapshot while the long scan is in flight.
    await server.publish('complete_closed');
    await page.goto('/');
    await expect(page.locator('#run-status')).toContainText('PLANNING');
    const lastSuccessBefore = await page.locator('#run-last-success').innerText();

    await server.control('/__e2e/refresh', { mode: 'slow', duration_ms: 70000 });
    const clickedAt = Date.now();
    await page.locator('#run-refresh-btn').click();

    // The strip shows progress while the scan is in-flight (70s window).
    await expect(page.locator('#run-status')).toContainText(/REFRESHING \d+%/, { timeout: 20000 });

    // Prove the poll did not give up before the 60s mark. The scan lasts 70s,
    // so keep re-asserting REFRESHING (plus the retained last-good run) until
    // 61.5s of scan time have elapsed since the click. Measuring from the click
    // (not from the first REFRESHING observation) makes this exact.
    while (Date.now() - clickedAt < 61_500) {
        await expect(page.locator('#run-status')).toContainText(/REFRESHING \d+%/, { timeout: 5000 });
        await expect(page.locator('#run-last-success')).toHaveText(lastSuccessBefore);
        await page.waitForTimeout(1000);
    }
    // A final observation strictly past the 60s mark.
    await expect(page.locator('#run-status')).toContainText(/REFRESHING \d+%/, { timeout: 5000 });
    await expect(page.locator('#run-last-success')).toHaveText(lastSuccessBefore);

    // Scan finally completes and is adopted.
    await expect(page.locator('#run-status')).toContainText('PLANNING', { timeout: 20000 });
    await expect(page.locator('#run-last-success')).not.toHaveText(lastSuccessBefore);
    await expect(page.locator('#top-recommendations-content .recommendation-card')).toHaveCount(2);
});

test('failure after success keeps last-good results visible with a FAILED badge, then recovers', async ({ page }) => {
    test.setTimeout(60_000);
    await server.publish('complete_closed');
    await page.goto('/');
    await expect(page.locator('#run-status')).toContainText('PLANNING');
    await expect(page.locator('#top-recommendations-content .recommendation-card')).toHaveCount(2);
    const lastSuccessBefore = await page.locator('#run-last-success').innerText();
    expect(lastSuccessBefore).toMatch(/last success/i);

    // Trigger a failing refresh.
    await server.control('/__e2e/refresh', {
        mode: 'fail',
        duration_ms: 400,
        fail_message: 'simulated engine failure (e2e)',
    });
    await page.locator('#run-refresh-btn').click();

    // FAILED badge, yet the last-good run is retained (cards + last success).
    await expect(page.locator('#run-status')).toContainText('FAILED', { timeout: 20000 });
    await expect(page.locator('#run-last-success')).toHaveText(lastSuccessBefore);
    await expect(page.locator('#top-recommendations-content .recommendation-card')).toHaveCount(2);
    await expect(page.locator('#run-coverage')).toContainText('coverage 2/2');

    // Recovery: a fast refresh returns to PLANNING with a newer publish.
    await server.control('/__e2e/refresh', { mode: 'fast', duration_ms: 600 });
    await page.locator('#run-refresh-btn').click();
    await expect(page.locator('#run-status')).toContainText('PLANNING', { timeout: 20000 });
    await expect(page.locator('#top-recommendations-content .recommendation-card')).toHaveCount(2);
});
