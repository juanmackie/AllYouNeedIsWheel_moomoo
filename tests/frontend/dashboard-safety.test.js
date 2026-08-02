import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../frontend/static/js/dashboard/api.js', () => ({
  fetchWeeklyOptionIncome: vi.fn(),
}));

function setupCashDOM() {
  document.body.innerHTML = `
    <div id="cash-reserve-badge"></div>
    <div id="cash-reserved"></div>
    <div id="cash-available"></div>
    <input id="cash-reserve-toggle" type="checkbox" />
    <div id="cash-reserve-details"></div>
    <div id="open-puts-list"></div>
  `;
}

function setupRegimeDOM() {
  document.body.innerHTML = `
    <div id="locked-tickers-list"></div>
    <div id="earnings-lock-info" class="d-none"></div>
    <input id="earnings-lock-toggle" type="checkbox" />
  `;
}

function setupStateDOM() {
  document.body.innerHTML = '<div id="state-target"></div>';
}

describe('dashboard rendering safety', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('escapes API-fed open put content in the cash panel', async () => {
    setupCashDOM();
    fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        success: true,
        reserve_enabled: true,
        cash_reserved: 1234.56,
        cash_available: 9876.54,
        open_puts: [{
          ticker: '<img src=x onerror=alert(1)>',
          strike: '<b>100</b>',
          expiration: '20260123',
          contracts: 2,
        }],
      }),
    });

    const { updateCashReserveStatus } = await import('../../frontend/static/js/dashboard/dashboard-cash.js');
    await updateCashReserveStatus();

    const list = document.getElementById('open-puts-list');
    expect(list.innerHTML).toContain('&lt;img src=x onerror=alert(1)&gt;');
    expect(list.innerHTML).not.toContain('<img src=x onerror=alert(1)>');
  });

  it('escapes state-model messages before injecting HTML', async () => {
    setupStateDOM();

    const StateModel = (await import('../../frontend/static/js/utils/state-model.js')).default;
    StateModel.showLoading('state-target', '<img src=x onerror=alert(1)>');

    const target = document.getElementById('state-target');
    expect(target.innerHTML).toContain('&lt;img src=x onerror=alert(1)&gt;');
    expect(target.innerHTML).not.toContain('<img src=x onerror=alert(1)>');
  });
});
