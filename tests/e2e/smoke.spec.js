/**
 * Browser-level smoke checks for the one-screen dashboard.
 *
 * Requires the app running on 127.0.0.1:8000 (start via start_local.cmd) and
 * Playwright chromium installed (`npx playwright install chromium`).
 * Run: `npx playwright test tests/e2e/smoke.spec.js`
 *
 * The suite must not require live trading credentials; panels may show
 * empty/error states when OpenD is unavailable — the assertions check that
 * the page scaffolds render, not that broker data exists.
 */

import { expect, test } from '@playwright/test';

const BASE_URL = process.env.E2E_BASE_URL || 'http://127.0.0.1:8000';

test.describe('dashboard smoke', () => {
  test('dashboard page loads with operational strip', async ({ page }) => {
    await page.goto(BASE_URL);
    await expect(page).toHaveTitle(/wheel/i);

    // Operational strip scaffolding renders.
    await expect(page.locator('#run-strip')).toBeVisible();
    await expect(page.locator('#run-env')).toBeVisible();
    await expect(page.locator('#run-readonly')).toBeVisible();
    await expect(page.locator('#run-status')).toBeVisible();

    // Signals-only contract is visible in the UI.
    await expect(page.locator('#run-readonly')).toContainText(/read.?only/i);
  });

  test('core panels are present', async ({ page }) => {
    await page.goto(BASE_URL);

    const optionsTable = page.locator('#options-table-container');
    await expect(optionsTable).toBeAttached();
    await page.locator('details.ft-section').filter({ hasText: 'Options table' }).locator('summary').click();
    await expect(optionsTable).toBeVisible();
    await expect(page.locator('#position-monitor')).toBeVisible();
    await expect(page.locator('#top-recommendations-container')).toBeVisible();
  });

  test('watchlist API and dashboard panel load the merged union', async ({ page }) => {
    const response = await page.request.get(`${BASE_URL}/api/watchlist`);
    expect(response.status()).toBe(200);
    const payload = await response.json();
    expect(payload.sources.config).toEqual(['AAPL', 'TSLA']);
    expect(payload.union).toEqual([
      { ticker: 'AAPL', origins: ['config'] },
      { ticker: 'TSLA', origins: ['config'] },
    ]);

    await page.goto(BASE_URL);
    await expect(page.locator('#watchlist-summary')).toHaveText(
      '2 canonical symbols (2 raw entries: Moomoo 0, App 0, Config 2)'
    );
    await expect(page.locator('#watchlist-tags')).toContainText('AAPL');
    await expect(page.locator('#watchlist-tags')).toContainText('TSLA');
  });

  test('earnings status API returns its provider and cache diagnostics', async ({ page }) => {
    const response = await page.request.get(`${BASE_URL}/api/earnings/status`);
    expect(response.status()).toBe(200);
    const payload = await response.json();
    expect(payload.status).toBe('manual');
    expect(payload.scheduler.running).toBe(false);
    expect(payload.provider.alpha_vantage.status).toBe('missing_key');
    expect(payload.provider.cache).toEqual({});
  });

  test('no execution-capable controls exist', async ({ page }) => {
    await page.goto(BASE_URL);

    const orderButtons = page.locator('button:has-text("Place Order"), button:has-text("Apply to Order")');
    await expect(orderButtons).toHaveCount(0);
  });
});
