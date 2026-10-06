import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../frontend/static/js/dashboard/api-portfolio.js', () => ({
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

  it('escapes unknown watchlist origins before injecting badge HTML', async () => {
    document.body.innerHTML = `
      <div id="watchlist-tags"></div>
      <div id="watchlist-summary"></div>
      <div id="watchlist-infeasible"></div>
    `;
    fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        union: [{ ticker: 'AAPL', origins: ['<script>alert(1)</script>'] }],
        sources: {},
      }),
    });

    const { loadWatchlist } = await import('../../frontend/static/js/dashboard/watchlist-panel.js');
    await loadWatchlist();

    const tags = document.getElementById('watchlist-tags');
    expect(tags.innerHTML).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
    expect(tags.innerHTML).not.toContain('<script>alert(1)</script>');
  });

  it('allowlists alert types before building alert classes', async () => {
    document.body.innerHTML = '<main class="content-container"></main>';

    const { showAlert } = await import('../../frontend/static/js/utils/alerts.js');
    showAlert('Message', 'danger" onmouseover="alert(1)', 0);

    const alert = document.querySelector('.alert');
    expect(alert.className).toContain('alert-info');
    expect(alert.className).not.toContain('onmouseover');
  });

  it('renders transient alert text as text and dismisses without Bootstrap JS', async () => {
    document.body.innerHTML = '<main class="content-container"></main>';

    const { showAlert } = await import('../../frontend/static/js/utils/alerts.js');
    showAlert('<img src=x onerror=alert(1)>', 'warning', 5000);

    const alert = document.querySelector('.alert');
    expect(alert.textContent).toContain('<img src=x onerror=alert(1)>');
    expect(alert.querySelector('img')).toBeNull();
    expect(alert.className).toContain('show');
    expect(alert.classList.contains('d-none')).toBe(false);

    alert.querySelector('.btn-close').click();
    expect(alert.classList.contains('d-none')).toBe(true);
    await vi.waitFor(() => expect(document.querySelector('.alert')).toBeNull());
  });
});

describe('shared alert helper (status-alert)', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('toggles tone and visibility without wiping layout classes', async () => {
    const { setAlertState } = await import('../../frontend/static/js/utils/status-alert.js');
    document.body.innerHTML = '<div id="b" class="alert alert-danger d-none mb-3 custom"></div>';
    const banner = document.getElementById('b');

    setAlertState(banner, { tone: 'warning', visible: true });
    expect(banner.className).toContain('alert-warning');
    expect(banner.className).not.toContain('alert-danger');
    expect(banner.className).toContain('mb-3');
    expect(banner.className).toContain('custom');
    expect(banner.classList.contains('d-none')).toBe(false);
    expect(banner.classList.contains('show')).toBe(true);

    setAlertState(banner, { tone: 'danger', visible: false });
    expect(banner.className).toContain('alert-danger');
    expect(banner.className).toContain('mb-3');
    expect(banner.classList.contains('d-none')).toBe(true);
    expect(banner.classList.contains('show')).toBe(false);
  });

  it('falls back to info for unknown tones', async () => {
    const { normalizeAlertTone } = await import('../../frontend/static/js/utils/status-alert.js');
    expect(normalizeAlertTone('danger" onmouseover="alert(1)')).toBe('info');
    expect(normalizeAlertTone('warning')).toBe('warning');
  });

  it('sets banner text as text, never markup', async () => {
    const { setTextIfChanged } = await import('../../frontend/static/js/utils/status-alert.js');
    document.body.innerHTML = '<div id="t"></div>';
    const el = document.getElementById('t');
    setTextIfChanged(el, '<img src=x onerror=alert(1)>');
    expect(el.textContent).toBe('<img src=x onerror=alert(1)>');
    expect(el.querySelector('img')).toBeNull();
  });
});

describe('options-table rendering safety', () => {
  it('escapes API-fed ticker and error text in options-table rows', async () => {
    vi.mock('../../frontend/static/js/dashboard/options-table-state.js', () => ({
      state: {
        watchlistTickers: new Set(),
        tickersData: {
          'BAD<script>': {
            errors: { PUT: '<img src=x onerror=alert(1)>' },
            data: { data: {} },
            callOtmPercentage: 5,
            putOtmPercentage: 5,
          },
        },
      },
      getUnavailableTickerMessage: () => '',
      getRenderExpirationValue: () => null,
      formatExpirationLabel: (v) => v,
      loadExcludedTickers: () => [],
      saveOtmSettings: () => {},
      getOtmBounds: () => ({ min: 1, max: 40, defaultValue: 8 }),
      normalizeOtmValue: (_t, v) => v ?? 8,
    }));

    document.body.innerHTML = `
      <table id="put-options-table"><tbody></tbody></table>
    `;

    const { addTickerRowToTable } = await import(
      '../../frontend/static/js/dashboard/options-table-rendering.js'
    );
    const ok = addTickerRowToTable('put-options-table', 'PUT', 'BAD<script>');
    expect(ok).toBe(true);

    const html = document.querySelector('#put-options-table tbody').innerHTML;
    // Ticker text and error message must be entity-escaped, not injected raw.
    expect(html).toContain('&lt;script&gt;');
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
    expect(html).not.toContain('<img src=x');
  });
});
