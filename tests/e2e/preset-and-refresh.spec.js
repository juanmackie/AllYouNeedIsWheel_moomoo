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

test('saved signals and refresh controls load while the account request is blocked', async ({ page }) => {
    await server.publish('complete_closed');
    let releaseAccount;
    const accountGate = new Promise((resolve) => { releaseAccount = resolve; });
    let accountRequested = false;
    await page.route('**/api/portfolio', async (route) => {
        accountRequested = true;
        await accountGate;
        await route.continue();
    });
    const refreshPosts = trackRefreshPosts(page);

    try {
        await page.goto('/', { waitUntil: 'domcontentloaded' });
        await expect.poll(() => accountRequested).toBe(true);
        await expect(page.locator('#top-recommendations-content .recommendation-card')).toHaveCount(2);
        await expect(page.locator('#run-status')).toContainText('PLANNING');
        await expect(page.locator('#run-refresh-btn')).toBeEnabled();
        expect(refreshPosts).toHaveLength(0);

        // A newly published run must also reach the cards while account loading
        // remains blocked, rather than waiting for dashboard initialization.
        await server.publish('complete_closed', 'aggressive');
        await expect(page.locator('#top-recommendations-content')).toContainText('MSFT', { timeout: 10000 });
        expect(refreshPosts).toHaveLength(0);

        // The refresh control is bound before the blocked account panel finishes.
        await page.locator('#run-refresh-btn').click();
        await expect.poll(() => refreshPosts.length).toBe(1);
    } finally {
        releaseAccount();
        await page.unrouteAll({ behavior: 'wait' });
    }
});

test('mobile and desktop layouts keep all disclosure content inside the viewport', async ({ page }) => {
    await server.publish('complete_closed');
    await page.setViewportSize({ width: 320, height: 900 });
    await page.goto('/');
    await expect(page.locator('#top-recommendations-content .recommendation-card')).toHaveCount(2);

    // Exercise the actual flex-wrap utility with the largest normal watchlist
    // shape without changing scan coverage or fixture signal identity.
    await page.locator('#watchlist-tags').evaluate((tags) => {
        tags.replaceChildren(...Array.from({ length: 67 }, (_, index) => {
            const tag = document.createElement('span');
            tag.className = 'badge bg-secondary';
            tag.textContent = `SYMBOL${String(index).padStart(2, '0')}`;
            return tag;
        }));
    });

    for (const width of [320, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        for (const open of [true, false]) {
            await page.locator('details').evaluateAll((sections, shouldOpen) => {
                sections.forEach((section) => { section.open = shouldOpen; });
            }, open);
            await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth))
                .toBeLessThanOrEqual(width);
        }
    }
});

test('dashboard panels receive successful API responses, including the opened market-data panel', async ({ page }) => {
    await server.publish('complete_closed');
    const responses = new Map();
    page.on('response', (response) => {
        const url = new URL(response.url());
        if (url.pathname.startsWith('/api/')) {
            const path = url.pathname.replace(/\/+$/, '');
            responses.set(`${response.request().method()} ${path}`, response.status());
        }
    });
    await page.goto('/');
    await page.locator('#market-data-section > summary').click();
    await page.locator('#load-options-scanner').click();

    const expected = [
        'GET /api/settings',
        'GET /api/run',
        'GET /api/system/opend-status',
        'GET /api/portfolio',
        'GET /api/portfolio/positions',
        'GET /api/portfolio/alerts',
        'GET /api/portfolio/roll-pressure',
        'GET /api/portfolio/weekly-income',
        'GET /api/options/cash-status',
        'GET /api/portfolio/history',
        'GET /api/portfolio/projection',
        'GET /api/options/analytics/lifecycle',
        'GET /api/options/analytics/outcomes',
        'GET /api/earnings/status',
        'GET /api/watchlist',
        'GET /api/options/screening-config',
        'GET /api/options/watchlist-tickers',
    ];
    await expect.poll(() => expected.every((key) => responses.has(key)), { timeout: 20000 }).toBe(true);
    for (const key of expected) expect(responses.get(key), key).toBe(200);

    await expect(page.locator('#weekly-income-section')).toBeVisible();
    await expect(page.locator('#position-monitor-body')).toContainText(/No open short option positions/i);
    await expect(page.locator('#watchlist-tags')).toContainText('AAPL');
    await expect(page.locator('#options-table-container')).toBeVisible();
});

test('preset change flips the active preset and triggers a republished refresh', async ({ page }) => {
    test.setTimeout(60_000);
    await server.publish('complete_closed', 'balanced');
    await page.goto('/');

    await expect(page.locator('#run-preset')).toContainText('Balanced');
    await expect(page.locator('#run-snapshot-preset')).toContainText('Balanced v7');
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
    await expect(page.locator('#preset-buttons [data-preset]')).toHaveCount(3);
    await expect(page.locator('[data-preset="aggressive"]')).toHaveClass(/btn-primary/);

    // The new run is adopted: Aggressive label, new publish, aggressive cards.
    await expect(page.locator('#run-preset')).toContainText('Aggressive', { timeout: 20000 });
    await expect(page.locator('#run-snapshot-preset')).toContainText('Aggressive v7', { timeout: 20000 });
    await expect
        .poll(() => page.locator('#run-last-success').innerText())
        .not.toBe(lastSuccessBefore);
    await expect(page.locator('#top-recommendations-content')).toContainText('MSFT', { timeout: 20000 });
    await expect(page.locator('#top-recommendations-content .recommendation-card')).toHaveCount(2);
    await expect(page.locator('#strategy-rules-text')).toContainText('AGGRESSIVE v7');
    await expect(page.locator('#strategy-rules-text')).toContainText('0.35');
    await expect(page.locator('#strategy-rules-text')).toContainText('0.15');
    await expect(page.locator('#strategy-rules-text')).toContainText('CSP DTE 7-35 (pref 14)');
    await expect(page.locator('#strategy-rules-text')).toContainText('CSP OTM 3-15%');
    await expect(page.locator('#strategy-rules-text')).toContainText('CC OTM 12%');
    await expect(page.locator('#strategy-rules-text')).toContainText('min CSP buying power $3,000.00');
    await expect(page.locator('#strategy-rules-text')).toContainText('90% buying power per CSP');
    await expect(page.locator('#strategy-rules-text')).toContainText('min premium $5.00');
    await expect(page.locator('#strategy-rules-text')).toContainText('min mid $0.03');
    await expect(page.locator('#strategy-rules-text')).toContainText('max spread 70%');
    await expect(page.locator('#strategy-rules-text')).toContainText('min OI 5');
    await expect(page.locator('#strategy-rules-text')).toContainText('cash-fit required');
    await expect(page.locator('#bp-amount')).toHaveText('$78,500.00');
    await expect(page.locator('#bp-broker')).toHaveText('$74,000.00');
    await expect(page.locator('#bp-reserved')).toHaveText('$0.00');
    await expect(page.locator('.csp-cash-after').first()).toHaveText('$59,500.00');
    await expect(page.locator('.csp-cash-pct').first()).toHaveText('24.2%');
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
    await expect(page.locator('#run-status')).toHaveText('REFRESHING', { timeout: 20000 });
    await expect(page.locator('#run-progress-details')).toBeVisible();
    await expect(page.locator('#run-progress-stage')).toContainText('Preparing watchlist scan');
    const elapsedBefore = await page.locator('#run-progress-elapsed').innerText();

    // Prove the poll did not give up before the 60s mark. The scan lasts 70s,
    // so keep re-asserting REFRESHING (plus the retained last-good run) until
    // 61.5s of scan time have elapsed since the click. Measuring from the click
    // (not from the first REFRESHING observation) makes this exact.
    while (Date.now() - clickedAt < 61_500) {
        await expect(page.locator('#run-status')).toHaveText('REFRESHING', { timeout: 5000 });
        await expect(page.locator('#run-last-success')).toHaveText(lastSuccessBefore);
        await page.waitForTimeout(1000);
    }
    // A final observation strictly past the 60s mark.
    await expect(page.locator('#run-status')).toHaveText('REFRESHING', { timeout: 5000 });
    await expect(page.locator('#run-progress-elapsed')).not.toHaveText(elapsedBefore);
    await expect(page.locator('#run-last-success')).toHaveText(lastSuccessBefore);

    // Scan finally completes and is adopted.
    await expect(page.locator('#run-status')).toContainText('PLANNING', { timeout: 20000 });
    await expect(page.locator('#run-progress-details')).toBeHidden();
    await expect(page.locator('#run-last-success')).not.toHaveText(lastSuccessBefore);
    await expect(page.locator('#top-recommendations-content .recommendation-card')).toHaveCount(2);
});

test('failure after success shows the reason, retains results, then recovers', async ({ page }, testInfo) => {
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
    const warning = page.locator('#run-warning-banner');
    await expect(warning).toBeVisible();
    await expect(warning).toContainText('simulated engine failure (e2e)');
    await expect(warning).toContainText('Previous successful results are still shown');
    await expect(warning).toContainText('Refresh run');
    await page.screenshot({ path: testInfo.outputPath('refresh-failure.png') });

    // Recovery: a fast refresh returns to PLANNING with a newer publish.
    await server.control('/__e2e/refresh', { mode: 'fast', duration_ms: 600 });
    await page.locator('#run-refresh-btn').click();
    await expect(page.locator('#run-status')).toContainText('PLANNING', { timeout: 20000 });
    await expect(page.locator('#top-recommendations-content .recommendation-card')).toHaveCount(2);
    await expect(warning).toBeHidden();
});

test('a failed first refresh has a visible reason and a retry recovers', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#run-status')).toContainText('NO RUN');
    await server.control('/__e2e/refresh', {
        mode: 'fail', duration_ms: 400, fail_message: 'OpenD login required (e2e)',
    });
    await page.locator('#run-refresh-btn').click();
    await expect(page.locator('#run-status')).toContainText('FAILED', { timeout: 20000 });
    await expect(page.locator('#run-warning-banner')).toBeVisible();
    await expect(page.locator('#run-warning-banner')).toContainText('OpenD login required (e2e)');
    await expect(page.locator('#run-warning-banner')).toContainText('No completed results are available');
    await server.control('/__e2e/refresh', { mode: 'fast', duration_ms: 600 });
    await page.locator('#run-refresh-btn').click();
    await expect(page.locator('#run-status')).toContainText('PLANNING', { timeout: 20000 });
    await expect(page.locator('#run-warning-banner')).toBeHidden();
});

test('quota warning is visible above review-only covered calls and clears on complete coverage', async ({ page }, testInfo) => {
    await server.publish('quota_partial');
    await page.goto('/');
    const warning = page.locator('#run-warning-banner');
    await expect(warning).toBeVisible();
    await expect(warning).toContainText('CSP scan blocked by OpenD quota');
    await expect(warning).toContainText('180s');
    await expect(warning).toContainText('discovery budget is 120s');
    await expect(warning).toContainText('Copy actions stay blocked');
    const cards = page.locator('#top-recommendations-content .recommendation-card');
    await expect(cards).toHaveCount(1);
    await expect(cards.first()).toContainText('TSLA');
    await expect(cards.first().locator('.copy-ticket-btn')).toBeDisabled();
    // Warning and its next action are also readable on a narrow screen.
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(warning).toBeVisible();
    const warningBounds = await warning.boundingBox();
    expect(warningBounds.x).toBeGreaterThanOrEqual(0);
    expect(warningBounds.x + warningBounds.width).toBeLessThanOrEqual(390);
    expect(await warning.evaluate((el) => el.scrollWidth)).toBeLessThanOrEqual(warningBounds.width);
    await page.screenshot({ path: testInfo.outputPath('quota-warning-mobile.png') });
    await server.control('/__e2e/refresh', { mode: 'fast', duration_ms: 600 });
    await page.locator('#run-refresh-btn').click();
    await expect(page.locator('#run-coverage')).toContainText('coverage 2/2', { timeout: 20000 });
    await expect(warning).toBeHidden();
    await expect(cards).toHaveCount(2);
});

test('periodic API failure warns without losing cards and clears when reachable', async ({ page }) => {
    await server.publish('complete_closed');
    await page.goto('/');
    const cards = page.locator('#top-recommendations-content .recommendation-card');
    await expect(cards).toHaveCount(2);
    await page.route('**/api/run', (route) => route.fulfill({ status: 503, json: { error: 'app unavailable' } }));
    await expect(page.locator('#run-status')).toContainText('COMM ERROR', { timeout: 20000 });
    await expect(page.locator('#run-warning-banner')).toBeVisible();
    await expect(page.locator('#run-warning-banner')).toContainText('retry automatically');
    await expect(cards).toHaveCount(2);
    await page.unroute('**/api/run');
    await expect(page.locator('#run-status')).toContainText('PLANNING', { timeout: 20000 });
    await expect(page.locator('#run-warning-banner')).toBeHidden();
});

test('rejected refresh POST warns until an accepted retry', async ({ page }) => {
    await server.publish('complete_closed');
    await page.goto('/');
    await expect(page.locator('#run-status')).toContainText('PLANNING');
    await page.route('**/api/run/refresh', (route) => route.fulfill({ status: 503, json: { error: 'OpenD unavailable (e2e)' } }));
    await page.locator('#run-refresh-btn').click();
    await expect(page.locator('#run-warning-banner')).toBeVisible();
    await expect(page.locator('#run-warning-banner')).toContainText('OpenD unavailable (e2e)');
    await expect(page.locator('#run-refresh-btn')).toBeEnabled();
    await expect(page.locator('#top-recommendations-content .recommendation-card')).toHaveCount(2);
    await page.unroute('**/api/run/refresh');
    await server.control('/__e2e/refresh', { mode: 'fast', duration_ms: 600 });
    await page.locator('#run-refresh-btn').click();
    await expect(page.locator('#run-warning-banner')).toBeHidden({ timeout: 20000 });
    await expect(page.locator('#run-status')).toContainText('PLANNING', { timeout: 20000 });
});
