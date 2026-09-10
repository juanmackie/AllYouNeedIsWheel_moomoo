/**
 * Open-session copying journey.
 *
 * The live (market-open) path is exercised deterministically at any wall
 * clock by intercepting /api/run and /api/run/copy-check at the browser
 * network layer with live-mode payloads derived from the real backend's
 * snapshot shape (staged whenever the real market is actually open).
 *
 * Plus a real-backend live copy test that only runs while the real US
 * market is open (seed `live_ready` + open clock through the fixture).
 */
import { test, expect } from '@playwright/test';
import { startFixtureServer, seedClipboard, readClipboard } from './server.js';

const PORT = 8102;
test.use({
    baseURL: `http://127.0.0.1:${PORT}`,
    permissions: ['clipboard-read', 'clipboard-write'],
});

let server;
let realMarketOpen;

test.beforeAll(async () => {
    server = await startFixtureServer(PORT);
    const ping = await server.ping();
    realMarketOpen = Boolean(ping.real_market_open);
});
test.afterAll(async () => {
    await server?.close();
});
test.beforeEach(async () => {
    await server.reset();
});

/** Rewrite a closed-market snapshot view into a live-market one. */
function makeLiveRun(baseView) {
    const view = structuredClone(baseView);
    const now = new Date().toISOString();
    view.run.status = 'ready';
    view.run.market_state = 'open';
    view.run.quote_fetched_at = {};
    for (const s of view.signals) {
        view.run.quote_fetched_at[s.ticker] = now;
        s.quote_fetched_at_utc = now;
    }
    view.eligibility = {
        ...(view.eligibility || {}),
        run_id: view.run.run_id,
        session: { state: 'open' },
        coverage: { truth: 'complete' },
        quote_freshness: { fresh: true },
    };
    // Eligibility is attached per candidate across ALL lanes (signals, csp_picks,
    // cc_decisions) — the two-lane dashboard renders from the lane fields, so
    // every lane must be rewritten to live intent, not just `signals`.
    for (const lane of ['signals', 'csp_picks', 'cc_decisions']) {
        for (const s of view[lane] || []) {
            s.eligibility = { mode: 'live', reasons: [] };
            s.copy_eligible = true;
        }
    }
    return view;
}

function succeededAttempt() {
    return { state: 'succeeded', stage: 'publish', progress: 1 };
}

/** Shared: serve a live run payload for /api/run. */
function serveLiveRun(route, view) {
    return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ attempt: succeededAttempt(), snapshot: view }),
    });
}

/** Build a copy-check response body for the given signal/view. */
function copyCheckBody(overrides = {}, sig, view) {
    const now = new Date().toISOString();
    return {
        ok: true,
        matched_run: true,
        matched_contract: true,
        mode: 'live',
        run_id: view.run.run_id,
        contract: sig || null,
        reasons: [],
        verified_at: now,
        ...overrides,
    };
}

test('open session: fresh evidence copies the live ticket', async ({ page }) => {
    await server.publish('complete_closed');
    const { snapshot } = await server.getJson('/api/run');
    const live = makeLiveRun(snapshot);

    await page.route('**/api/run', (route) => serveLiveRun(route, live));
    await page.route('**/api/run/copy-check*', (route) => {
        const url = new URL(route.request().url());
        const ticker = url.searchParams.get('ticker');
        const sig = live.signals.find((s) => s.ticker === ticker) || live.signals[0];
        return route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(copyCheckBody({}, sig, live)),
        });
    });

    await page.goto('/');
    const cards = page.locator('#top-recommendations-content .recommendation-card');
    await expect(cards).toHaveCount(2);

    const btn = cards.first().locator('.copy-ticket-btn');
    await expect(btn).toBeEnabled();
    await expect(btn).toContainText('Copy ticket'); // live intent — not staged

    await btn.click();
    await expect(btn).toContainText('Copied');
    await expect(btn).toHaveClass(/btn-success/);

    const clip = await readClipboard(page);
    expect(clip).toContain('SELL TO OPEN CSP');
    expect(clip).toMatch(/PUT [A-Z]+ \d{8} \d+\.\d{2} x\d/);
    // Live ticket carries no staging note.
    expect(clip).not.toContain('STAGED FOR US MARKET OPEN');
    // The copied contract comes from the revalidated response.
    expect(clip).toContain(live.signals[0].ticker);
});

test('open session: evidence expiry between load and click blocks copy (clipboard untouched)', async ({ page }) => {
    await server.publish('complete_closed');
    const { snapshot } = await server.getJson('/api/run');
    const live = makeLiveRun(snapshot);

    await page.route('**/api/run', (route) => serveLiveRun(route, live));
    await page.route('**/api/run/copy-check*', (route) => {
        const sig = live.signals[0];
        return route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(
                copyCheckBody(
                    {
                        mode: 'review_only',
                        contract: null,
                        reasons: ['broker quote evidence is stale — no fresh broker session'],
                    },
                    sig,
                    live
                )
            ),
        });
    });

    await page.goto('/');
    const cards = page.locator('#top-recommendations-content .recommendation-card');
    await expect(cards).toHaveCount(2);

    const btn = cards.first().locator('.copy-ticket-btn');
    await expect(btn).toContainText('Copy ticket'); // looks live before the click

    const sentinel = await seedClipboard(page);
    await btn.click();

    // Blocked by revalidation: review-only, nothing written.
    await expect(btn).toContainText('Review only');
    await expect(btn).toBeDisabled();
    await expect(btn).toHaveAttribute('title', /stale/i);
    expect(await readClipboard(page)).toBe(sentinel);
});

test('open session: run changed between load and click requires a second click', async ({ page }) => {
    await server.publish('complete_closed');
    const { snapshot } = await server.getJson('/api/run');
    const liveA = makeLiveRun(snapshot);
    // A different run: same shape but a new run_id and a new ticker so the
    // second click plainly copies the refreshed card.
    const liveB = structuredClone(liveA);
    liveB.run.run_id = 'live-0002';
    liveB.run.generated_at = new Date().toISOString();
    liveB.run.published_at = new Date().toISOString();
    // The two-lane dashboard renders from csp_picks/cc_decisions, so the new
    // ticker must replace the first candidate in every lane, not just signals.
    for (const lane of ['signals', 'csp_picks', 'cc_decisions']) {
        if (!Array.isArray(liveB[lane]) || liveB[lane].length === 0) continue;
        const bCand = structuredClone(liveB[lane][0]);
        bCand.ticker = 'MSFT';
        liveB[lane] = [bCand, ...structuredClone(liveB[lane].slice(1))];
    }

    let currentView = liveA;
    let runChanged = false;

    await page.route('**/api/run', (route) => serveLiveRun(route, currentView));
    await page.route('**/api/run/copy-check*', (route) => {
        const url = new URL(route.request().url());
        const ticker = url.searchParams.get('ticker');
        if (!runChanged) {
            // First revalidation: the run changed underneath the click.
            runChanged = true;
            currentView = liveB;
            return route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify(
                    copyCheckBody(
                        {
                            matched_run: false,
                            mode: 'review_only',
                            run_id: 'some-other-run',
                            contract: null,
                            reasons: ['run changed — review and click again'],
                        },
                        null,
                        liveA
                    )
                ),
            });
        }
        const sig = currentView.signals.find((s) => s.ticker === ticker) || currentView.signals[0];
        return route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(copyCheckBody({}, sig, currentView)),
        });
    });

    await page.goto('/');
    const sentinel = await seedClipboard(page);
    let btn = page.locator('#top-recommendations-content .recommendation-card').first().locator('.copy-ticket-btn');
    await expect(btn).toContainText('Copy ticket');

    await btn.click();
    // The reload to the fresh run replaces the card (the transient blocked
    // state is not durable), so the durable contract to assert is: the first
    // click must NOT write anything...
    await expect(page.locator('#top-recommendations-content')).toContainText('MSFT');
    expect(await readClipboard(page)).toBe(sentinel);

    // ...and a second click on the refreshed card copies the new contract.
    btn = page.locator('#top-recommendations-content .recommendation-card').first().locator('.copy-ticket-btn');
    await expect(btn).toContainText('Copy ticket');
    await btn.click();
    await expect(btn).toContainText('Copied');
    const clip = await readClipboard(page);
    expect(clip).toContain('SELL TO OPEN CSP');
    expect(clip).toContain('MSFT');
});

test('live copy against the real backend (skipped unless the real US market is open)', async ({ page }) => {
    test.skip(!realMarketOpen, 'real US market is closed right now — live journey needs open market');

    await server.control('/__e2e/clock', { session: 'open' });
    await server.publish('live_ready');
    await page.goto('/');

    const cards = page.locator('#top-recommendations-content .recommendation-card');
    await expect(cards).toHaveCount(2);
    const btn = cards.first().locator('.copy-ticket-btn');
    await expect(btn).toContainText('Copy ticket');

    await btn.click();
    await expect(btn).toContainText('Copied');
    const clip = await readClipboard(page);
    expect(clip).toContain('SELL TO OPEN CSP');
    expect(clip).not.toContain('STAGED FOR US MARKET OPEN');
});
