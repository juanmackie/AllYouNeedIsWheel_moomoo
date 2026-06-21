import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../frontend/static/js/dashboard/api.js', () => ({
  fetchAccountData: vi.fn(),
  fetchPositions: vi.fn(),
  fetchEarningsStatus: vi.fn(),
  refreshAllEarnings: vi.fn(),
  updateSingleEarnings: vi.fn(),
  fetchRollPressure: vi.fn(),
  fetchOptionData: vi.fn(),
  fetchStockPrices: vi.fn(),
}));

vi.mock('../../frontend/static/js/utils/alerts.js', () => ({
  showAlert: vi.fn(),
}));

vi.mock('../../frontend/static/js/utils/formatters.js', () => ({
  escapeHtml: vi.fn((value) => String(value ?? '')),
  formatCurrency: vi.fn((value) => `$${Number(value || 0).toFixed(2)}`),
  formatPercent: vi.fn((value) => `${Number(value || 0).toFixed(1)}%`),
}));

describe('portfolio unavailable state', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <table><tbody id="stock-positions-table-body"></tbody></table>
      <table><tbody id="option-positions-table-body"></tbody></table>
      <span id="positions-count"></span>
      <span id="account-value"></span>
      <span id="cash-balance"></span>
      <span id="excess-liquidity"></span>
      <span id="initial-margin"></span>
      <span id="leverage-percentage"></span>
      <div id="leverage-bar"></div>
      <div id="data-status-indicator"></div>
      <div id="data-status-icon"><i></i></div>
      <div id="data-update-time"></div>
    `;
    window.appConnectionStatus = {
      status: 'unavailable',
      message: 'OpenD is unavailable.',
    };
  });

  afterEach(() => {
    document.body.innerHTML = '';
    delete window.appConnectionStatus;
    vi.resetModules();
  });

  it('shows explicit unavailable rows when positions cannot be loaded', async () => {
    const { fetchPositions } = await import('../../frontend/static/js/dashboard/api.js');
    fetchPositions.mockResolvedValue(null);

    const { loadPositionsTable } = await import('../../frontend/static/js/dashboard/account.js');
    await loadPositionsTable();

    expect(document.getElementById('stock-positions-table-body').textContent).toContain('OpenD is unavailable');
    expect(document.getElementById('option-positions-table-body').textContent).toContain('OpenD is unavailable');
  });
});

describe('rollover unavailable state', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <table><tbody id="option-positions-table-body"></tbody></table>
      <table><tbody id="rollover-suggestions-table-body"></tbody></table>
      <div id="otm-selector-row"></div>
    `;
    window.appConnectionStatus = {
      status: 'login_required',
      message: 'OpenD login required.',
    };
  });

  afterEach(() => {
    document.body.innerHTML = '';
    delete window.appConnectionStatus;
    vi.resetModules();
  });

  it('shows explicit unavailable rows when rollover positions cannot be loaded', async () => {
    const { fetchPositions } = await import('../../frontend/static/js/dashboard/api.js');
    fetchPositions.mockResolvedValue(null);
    const { fetchRollPressure } = await import('../../frontend/static/js/dashboard/api.js');
    fetchRollPressure.mockResolvedValue({ positions: [] });

    await import('../../frontend/static/js/rollover/rollover-ui.js');

    const { loadOptionPositions } = await import('../../frontend/static/js/rollover/rollover-api.js');
    await loadOptionPositions();

    expect(document.getElementById('option-positions-table-body').textContent).toContain('OpenD login is required');
  });
});
