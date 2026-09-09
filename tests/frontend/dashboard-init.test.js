import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../frontend/static/js/dashboard/account.js', () => ({
  loadPortfolioData: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../frontend/static/js/dashboard/top-recommendations.js', () => ({
  initializeTopRecommendations: vi.fn(),
  loadTopRecommendations: vi.fn().mockResolvedValue(undefined),
  isBackendGenerating: vi.fn().mockReturnValue(false),
}));

// P1b: dashboard-init now statically imports these and reloads them on a
// published run (see reloadAllPanelsAfterPublish), so they are mocked here to
// track the fan-out deterministically.
vi.mock('../../frontend/static/js/dashboard/watchlist-panel.js', () => ({
  initWatchlistPanel: vi.fn(),
  loadWatchlist: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../frontend/static/js/dashboard/run-strip.js', () => ({
  initRunStrip: vi.fn(),
  loadRunStrip: vi.fn().mockResolvedValue(undefined),
  renderRunStrip: vi.fn(),
}));

vi.mock('../../frontend/static/js/dashboard/growth-panel.js', () => ({
  renderGrowthPanel: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../frontend/static/js/dashboard/outcome-panel.js', () => ({
  renderOutcomePanel: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../frontend/static/js/dashboard/llm-advisor.js', () => ({
  initializeLLMAdvisor: vi.fn(),
}));

vi.mock('../../frontend/static/js/dashboard/dashboard-cash.js', () => ({
  updateCashReserveStatus: vi.fn().mockResolvedValue(undefined),
  updateIdleCashPanel: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../frontend/static/js/dashboard/weekly-income.js', () => ({
  renderWeeklyIncome: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../frontend/static/js/dashboard/options-table.js', () => ({
  loadTickers: vi.fn().mockResolvedValue(undefined),
}));

function setupDOM() {
  document.body.innerHTML = `
    <main>
      <div class="content-container"></div>
    </main>
    <div id="wave1-loading" class="d-none"></div>
    <div id="wave2-loading" class="d-none"></div>
    <div id="wave3-loading" class="d-none"></div>
    <table style="display:none"><tbody id="positions-command-body"></tbody></table>
    <table style="display:none"><tbody id="position-monitor-body"></tbody></table>
    <div id="position-monitor-loading" class="d-none"></div>
    <button id="refresh-filled-orders" type="button">Refresh weekly income</button>
    <button id="load-options-scanner" type="button">Load scanner</button>
    <button id="refresh-all-btn" type="button">Refresh all</button>
    <button id="cash-reserve-toggle" type="button"></button>
    <input id="sizing-conservative" name="sizing-mode" value="conservative" type="radio">
    <input id="sizing-aggressive" name="sizing-mode" value="aggressive" type="radio">
  `;
}

describe('dashboard signal-panel initialization', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    setupDOM();
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        positions: [],
      }),
    });
  });

  afterEach(async () => {
    // P1b: initializeDashboard starts the shared run-state poll; stop it so the
    // module-level poll state does not leak into the next test.
    const { stopRunStatePoll } = await import('../../frontend/static/js/dashboard/run-notifier.js');
    stopRunStatePoll();
    const { state: optionsTableState } = await import('../../frontend/static/js/dashboard/options-table-state.js');
    optionsTableState.tickersData = {};
    optionsTableState.portfolioSummary = null;
    vi.useRealTimers();
    document.body.innerHTML = '';
    delete global.fetch;
  });

  it('starts visible signal panels after account, position, and market waves', async () => {
    const { initializeDashboard } = await import('../../frontend/static/js/dashboard/dashboard-init.js');
    const { loadPortfolioData } = await import('../../frontend/static/js/dashboard/account.js');
    const { initializeTopRecommendations } = await import('../../frontend/static/js/dashboard/top-recommendations.js');
    const { renderWeeklyIncome } = await import('../../frontend/static/js/dashboard/weekly-income.js');
    const { loadTickers } = await import('../../frontend/static/js/dashboard/options-table.js');
    const { updateIdleCashPanel } = await import('../../frontend/static/js/dashboard/dashboard-cash.js');

    await initializeDashboard();
    await vi.dynamicImportSettled?.();

    expect(loadPortfolioData).toHaveBeenCalledTimes(1);
    expect(initializeTopRecommendations).toHaveBeenCalledTimes(1);
    expect(renderWeeklyIncome).toHaveBeenCalledTimes(1);
    expect(loadTickers).not.toHaveBeenCalled();
    expect(updateIdleCashPanel).toHaveBeenCalledTimes(1);

    document.getElementById('refresh-filled-orders').click();
    await vi.dynamicImportSettled?.();
    expect(renderWeeklyIncome).toHaveBeenCalledTimes(2);

    document.getElementById('load-options-scanner').click();
    await Promise.resolve();
    await vi.dynamicImportSettled?.();

    expect(loadTickers).toHaveBeenCalledTimes(1);
  });

  it('P1b: a published run fans out explicit reloads to every panel', async () => {
    // The poll uses real timers here so the immediate first fetch and the
    // fan-out microtasks settle deterministically.
    vi.useRealTimers();
    const { initializeDashboard } = await import('../../frontend/static/js/dashboard/dashboard-init.js');
    const { loadPortfolioData } = await import('../../frontend/static/js/dashboard/account.js');
    const { loadTopRecommendations } = await import('../../frontend/static/js/dashboard/top-recommendations.js');
    const { loadWatchlist } = await import('../../frontend/static/js/dashboard/watchlist-panel.js');
    const { renderRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    const { loadTickers } = await import('../../frontend/static/js/dashboard/options-table.js');

    // /api/run already carries a completed run on the very first observation
    // (completion before the first tick) — the shared poll adopts it once and
    // fans out reloads to every panel via read-only fetches.
    global.fetch = vi.fn(async (url) => {
      if (url === '/api/run') {
        return {
          ok: true,
          json: async () => ({
            attempt: { state: 'succeeded' },
            snapshot: {
              tradeable: true,
              run: { run_id: 'run-xyz', status: 'ready', market_state: 'open' },
            },
          }),
        };
      }
      return { ok: true, json: async () => ({ positions: [] }) };
    });

    await initializeDashboard();
    await vi.dynamicImportSettled?.();
    await new Promise((resolve) => setTimeout(resolve, 50));

    // Panels only reloaded by the publish fan-out (never during init):
    expect(loadWatchlist).toHaveBeenCalledTimes(1);
    expect(loadTopRecommendations).toHaveBeenCalledTimes(1);
    expect(loadTopRecommendations).toHaveBeenCalledWith(false); // no-scan reload
    // Panel already loaded at init and reloaded once by the fan-out:
    expect(loadPortfolioData).toHaveBeenCalledTimes(2);
    // The poll rendered the strip from its own /api/run payload (no second fetch):
    expect(renderRunStrip).toHaveBeenCalled();
    // Options table is not reloaded on a publish until the scanner was loaded:
    expect(loadTickers).not.toHaveBeenCalled();
    // A publish never POSTs a refresh (a viewer refresh must not roll into a
    // broker scan / order path):
    const posted = global.fetch.mock.calls.filter(([u, o]) =>
      typeof u === 'string' && u.includes('/refresh')
    );
    expect(posted).toHaveLength(0);
  });

  it('P1b: a publish reloads the options table once the scanner has been loaded', async () => {
    vi.useRealTimers();
    const { initializeDashboard } = await import('../../frontend/static/js/dashboard/dashboard-init.js');
    const { loadTickers } = await import('../../frontend/static/js/dashboard/options-table.js');
    const { state: optionsTableState } = await import('../../frontend/static/js/dashboard/options-table-state.js');
    // The scanner has already loaded tickers before this publish.
    optionsTableState.tickersData = { AAPL: { data: { data: { AAPL: { stock_price: 190 } } } } };

    global.fetch = vi.fn(async (url) => {
      if (url === '/api/run') {
        return {
          ok: true,
          json: async () => ({
            attempt: { state: 'succeeded' },
            snapshot: {
              tradeable: true,
              run: { run_id: 'run-abc', status: 'ready', market_state: 'open' },
            },
          }),
        };
      }
      return { ok: true, json: async () => ({ positions: [] }) };
    });

    await initializeDashboard();
    await vi.dynamicImportSettled?.();
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(loadTickers).toHaveBeenCalled();
  });
});

describe('C09 position P&L with unknown marks', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
    setupDOM();
  });

  afterEach(() => {
    document.body.innerHTML = '';
    delete global.fetch;
  });

  async function renderPositions(payloadPositions) {
    const { loadPositionsCommandPanel } = await import('../../frontend/static/js/dashboard/dashboard-init.js');
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ positions: payloadPositions }),
    });
    const tbody = document.getElementById('position-monitor-body');
    await loadPositionsCommandPanel();
    return tbody.innerHTML;
  }

  it('does not report +100% profit when the current mark is missing', async () => {
    const html = await renderPositions([{
      symbol: 'AAPL231215P140', option_type: 'PUT', strike: 140, position: -1,
      avg_cost: 2.00, // entry credit known, no mid_price -> unknown mark
    }]);
    expect(html).toContain('—');
    expect(html).not.toContain('100%');
  });

  it('does not report profit when the current mark is zero', async () => {
    const html = await renderPositions([{
      symbol: 'AAPL231215P140', option_type: 'PUT', strike: 140, position: -1,
      avg_cost: 2.00, mid_price: 0,
    }]);
    expect(html).toContain('—');
    expect(html).not.toContain('100%');
  });

  it('reports a real P&L when a valid mark is present', async () => {
    const html = await renderPositions([{
      symbol: 'AAPL231215P140', option_type: 'PUT', strike: 140, position: -1,
      avg_cost: 2.00, mid_price: 0.35, bid: 0.30, ask: 0.40,
    }]);
    // (2.00 - 0.35) / 2.00 = 82.5% captured, presented as a real P&L cell.
    // Assert on the P&L cell itself: other columns legitimately render '—'
    // for missing data (days-to-earnings, delta), so we check the rendered
    // P&L value is present rather than the unknown-mark placeholder.
    expect(html).toContain('+83%</span><br><small class="text-muted">$165</small>');
    expect(html).not.toContain('100%');
  });
});
