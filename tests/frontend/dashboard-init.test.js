import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../frontend/static/js/dashboard/account.js', () => ({
  loadPortfolioData: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../frontend/static/js/dashboard/top-recommendations.js', () => ({
  initializeTopRecommendations: vi.fn(),
  isBackendGenerating: vi.fn().mockReturnValue(false),
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
    <tbody id="positions-command-body"></tbody>
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
      json: () => Promise.resolve({ positions: [] }),
    });
  });

  afterEach(() => {
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
});
