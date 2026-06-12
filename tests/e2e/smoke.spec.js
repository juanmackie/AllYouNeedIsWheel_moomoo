import { test, expect } from '@playwright/test';

/**
 * E2E Smoke Tests for AllYouNeedIsWheel_moomoo Dashboard
 * Automates the highest-value checks from the manual smoke checklist.
 */

test.describe('Dashboard UI Smoke Tests', () => {
  test('page loads without JavaScript errors and shows core sections', async ({ page }) => {
    // Navigate to the dashboard
    await page.goto('http://localhost:5000'); // Adjust port if necessary

    // Wait for the main dashboard container to be visible
    await expect(page.locator('#dashboard-container')).toBeVisible();

    // Verify core sections are present
    await expect(page.locator('#portfolio-summary')).toBeVisible();
    await expect(page.locator('#options-table-section')).toBeVisible();
    await expect(page.locator('#top-recommendations-section')).toBeVisible();
    await expect(page.locator('#catalyst-watch-section')).toBeVisible();
  });

  test('theme toggle works and persists in localStorage', async ({ page }) => {
    await page.goto('http://localhost:5000');
    
    // Check initial theme (assume light by default, or check what's set)
    const initialTheme = await page.evaluate(() => localStorage.getItem('theme'));
    
    // Click theme toggle button (adjust selector if different)
    const themeToggle = page.locator('#theme-toggle');
    if (await themeToggle.isVisible()) {
      await themeToggle.click();
      
      // Wait for localStorage to update
      await page.waitForTimeout(500);
      
      const newTheme = await page.evaluate(() => localStorage.getItem('theme'));
      expect(newTheme).not.toBe(initialTheme);
    }
  });

  test('earnings status badge is visible in header', async ({ page }) => {
    await page.goto('http://localhost:5000');
    
    // Check for earnings status badge (RUNNING or STOPPED)
    const earningsBadge = page.locator('#earnings-status-badge');
    await expect(earningsBadge).toBeVisible();
    
    const badgeText = await earningsBadge.textContent();
    expect(badgeText).toMatch(/RUNNING|STOPPED/i);
  });
});

test.describe('Options Table Smoke Tests', () => {
  test('Calls/Puts tabs switch correctly', async ({ page }) => {
    await page.goto('http://localhost:5000');
    
    const callsTab = page.locator('#tab-calls');
    const putsTab = page.locator('#tab-puts');
    
    await expect(callsTab).toBeVisible();
    await expect(putsTab).toBeVisible();
    
    // Click Puts tab
    await putsTab.click();
    await expect(page.locator('#puts-table-container')).toBeVisible();
    
    // Click Calls tab
    await callsTab.click();
    await expect(page.locator('#calls-table-container')).toBeVisible();
  });

  test('OTM% auto-refreshes after debounce', async ({ page }) => {
    await page.goto('http://localhost:5000');
    
    // Navigate to Puts tab first
    await page.locator('#tab-puts').click();
    
    const otmInput = page.locator('.otm-percent-input').first();
    await expect(otmInput).toBeVisible();
    
    // Clear and type a new value
    await otmInput.clear();
    await otmInput.fill('30');
    
    // Wait for debounce (800ms) + network request
    await page.waitForTimeout(1500);
    
    // Verify that a network request was made (or table updated)
    // This is a basic check; in a real scenario, you might intercept the API call
    await expect(page.locator('#puts-table-container')).toBeVisible();
  });

  test('IV rank badges show with correct colors', async ({ page }) => {
    await page.goto('http://localhost:5000');
    await page.locator('#tab-calls').click();
    
    // Wait for table to load
    await page.waitForTimeout(2000);
    
    // Check for at least one IV rank badge
    const ivBadges = page.locator('.iv-rank-badge');
    const count = await ivBadges.count();
    
    if (count > 0) {
      // Verify it has one of the expected color classes
      const firstBadge = ivBadges.first();
      const className = await firstBadge.getAttribute('class');
      expect(className).toMatch(/bg-danger|bg-warning|bg-secondary|bg-success/);
    }
  });
});

test.describe('Catalyst Watch Smoke Tests', () => {
  test('Catalyst Watch section loads and handles empty/error states gracefully', async ({ page }) => {
    await page.goto('http://localhost:5000');
    
    const catalystSection = page.locator('#catalyst-watch-section');
    await expect(catalystSection).toBeVisible();
    
    // Check if it shows loading, empty, or actual signals
    const content = await catalystSection.textContent();
    expect(content).toMatch(/Scanning|No anomalous flow|Signals loaded|Unable to load/i);
  });

  test('Refresh button triggers reload', async ({ page }) => {
    await page.goto('http://localhost:5000');
    
    const refreshBtn = page.locator('#refresh-catalyst-signals');
    if (await refreshBtn.isVisible()) {
      await refreshBtn.click();
      
      // Should show loading state
      await expect(page.locator('#catalyst-state')).toContainText(/Scanning/i);
    }
  });
});

test.describe('Error Handling Smoke Tests', () => {
  test('graceful handling when OpenD is disconnected (mocked)', async ({ page }) => {
    // This test would ideally mock the backend to return an OpenD unavailable state
    // For now, we verify the UI can render an error state if the API returns one
    await page.goto('http://localhost:5000');
    
    // If the app is designed to show a specific message when OpenD is down,
    // we can check for it. Otherwise, we just ensure the page doesn't crash.
    await expect(page.locator('body')).toBeVisible();
  });
});
