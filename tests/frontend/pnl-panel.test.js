import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

function setupDOM() {
  document.body.innerHTML = `
    <section id="pnl-panel">
      <div id="pnl-state" role="status">Loading…</div>
      <div id="pnl-totals"></div>
      <table><tbody id="pnl-rows"></tbody></table>
      <p id="pnl-footnote"></p>
    </section>
  `;
}

function samplePayload(overrides = {}) {
  return {
    success: true,
    as_of: '2026-10-20T02:46:59+00:00',
    history_from: '2026-02-25',
    totals: {
      net_pnl: -5865.64,
      option_pnl: 4523.0,
      share_pnl: -10346.44,
      fees: 42.2,
      unreconciled_count: 1,
      fees_unknown: 35,
      underlying_count: 3,
      net_pnl_with_income: -5633.0,
    },
    income: {
      by_currency: { USD: { 'Cash Dividend': 60.0 } },
      usd_total: 232.71,
      unclassified: { USD: { 'Trade Settlement': -12.0 } },
    },
    capital_movements: { count: 8, by_currency: { AUD: 75313.0 }, last_date: '2026-05-05' },
    nav_since_last_capital_movement: {
      start_at: '2026-05-05T00:00:00',
      start_nav: 59501.29,
      end_at: '2026-10-20T00:00:00',
      end_nav: 55986.75,
      change: -3514.54,
      change_pct: -5.91,
      verified: true,
    },
    underlyings: [
      {
        ticker: 'SOXL',
        status: 'reconciled',
        net_pnl: -2772.64,
        option_pnl: -2771.0,
        share_pnl: 0.0,
        fees: 1.64,
        fees_unknown: 2,
        shares_held: 0,
        open_contracts: 1,
        first_fill_at: '2026-03-01T10:00:00',
        last_fill_at: '2026-09-01T10:00:00',
        notes: [],
      },
      {
        ticker: 'UBER',
        status: 'partial_history',
        net_pnl: 558.96,
        option_pnl: 300.0,
        share_pnl: 260.0,
        fees: 0.0,
        fees_unknown: 0,
        shares_held: 126,
        open_contracts: 0,
        first_fill_at: null,
        last_fill_at: null,
        notes: ['126 shares were held before the fill history and use broker average cost'],
      },
      {
        ticker: 'AA',
        status: 'unreconciled',
        net_pnl: null,
        option_pnl: null,
        share_pnl: null,
        fees: 0.0,
        fees_unknown: 0,
        shares_held: 0,
        open_contracts: 0,
        first_fill_at: null,
        last_fill_at: null,
        notes: ['fills show 1 PUT 70.0 20260626, broker holds 0'],
      },
    ],
    fill_coverage: {},
    cash_flow_coverage: { earliest: '2026-02-25', latest: '2026-10-23' },
    generated_at: '2026-10-23T08:00:00',
    ...overrides,
  };
}

function metricValue(label) {
  const cells = [...document.querySelectorAll('#pnl-totals .summary-metric-cell')];
  const cell = cells.find((c) => c.querySelector('.summary-metric-label')?.textContent === label);
  return cell ? cell.querySelector('.summary-metric-value') : null;
}

function bodyRows() {
  return [...document.querySelectorAll('#pnl-rows tr')];
}

describe('P&L panel', () => {
  beforeEach(() => {
    setupDOM();
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders totals, NAV change, rows in API order, and row notes', async () => {
    fetch.mockResolvedValue({ ok: true, json: async () => samplePayload() });
    const { renderPnlPanel } = await import('../../frontend/static/js/dashboard/pnl-panel.js');

    await renderPnlPanel();

    expect(fetch).toHaveBeenCalledWith('/api/options/analytics/pnl');
    expect(metricValue('Net P&L').textContent).toBe('−$5,865.64');
    expect(metricValue('Fees').textContent).toBe('$42.20 (+35 pending)');
    expect(metricValue('Interest & dividends (USD)').textContent).toBe('+$232.71');
    expect(metricValue('NAV since last deposit').textContent).toBe('−$3,514.54 (−5.91%)');
    expect(metricValue('NAV since last deposit').textContent).toContain('−5.91%');
    expect(metricValue('Unreconciled').textContent).toBe('1');

    const rows = bodyRows();
    expect(rows.map((tr) => tr.querySelector('td').textContent)).toEqual(['SOXL', 'UBER', 'AA']);
    expect(rows.map((tr) => tr.children[1].textContent)).toEqual(['Reconciled', 'Partial history', 'Unknown']);

    expect(rows[0].children[4].textContent).toBe('$1.64 (+2 pending)');
    expect(rows[1].children[4].textContent).toBe('$0.00');
    const soxlNet = rows[0].children[5];
    expect(soxlNet.textContent).toBe('−$2,772.64');
    expect(soxlNet.classList.contains('ft-td-down')).toBe(true);
    expect(rows[0].hasAttribute('title')).toBe(false);

    const uberNet = rows[1].children[5];
    // A gain must not share the loss colour (the theme's signal colour is red).
    expect(uberNet.textContent.startsWith('+')).toBe(true);
    expect(uberNet.classList.contains('ft-td-down')).toBe(false);
    expect(uberNet.classList.contains('ft-td-signal')).toBe(false);
    expect(rows[1].getAttribute('title')).toBe(
      '126 shares were held before the fill history and use broker average cost',
    );

    const aaNet = rows[2].children[5];
    expect(aaNet.textContent).toBe('—');
    expect(rows[2].getAttribute('title')).toBe('fills show 1 PUT 70.0 20260626, broker holds 0');

    expect(document.getElementById('pnl-state').textContent).toBe(
      '1 underlying(s) cannot be reconciled yet; their P&L is unknown and excluded from totals.',
    );
    const footnote = document.getElementById('pnl-footnote').textContent;
    expect(footnote).toContain('Positions as of 2026-10-20T02:46:59+00:00.');
    expect(footnote).toContain('Fill history from 2026-02-25.');
    expect(footnote).toContain('Deposits/withdrawals excluded: 8.');
    expect(footnote).toContain('Unclassified cash-flow types not counted: Trade Settlement.');
    expect(metricValue('Interest & dividends (USD)').textContent).toBe('+$232.71');
  });

  it('shows the error text and no rows when the request rejects or returns HTTP 500', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const { renderPnlPanel } = await import('../../frontend/static/js/dashboard/pnl-panel.js');
    const errorText = 'Could not load P&L. Retry later; your records are unchanged.';

    fetch.mockRejectedValueOnce(new Error('network down'));
    await renderPnlPanel();
    expect(document.getElementById('pnl-state').textContent).toBe(errorText);
    expect(bodyRows()).toHaveLength(0);
    expect(document.getElementById('pnl-totals').children).toHaveLength(0);
    expect(document.getElementById('pnl-footnote').textContent).toBe('');

    fetch.mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({ success: false, error: 'boom' }) });
    await renderPnlPanel();
    expect(document.getElementById('pnl-state').textContent).toBe(errorText);
    expect(bodyRows()).toHaveLength(0);
    expect(document.getElementById('pnl-totals').children).toHaveLength(0);
  });

  it('shows the empty text when there are no underlyings', async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: async () => samplePayload({ underlyings: [], totals: { net_pnl: 0, unreconciled_count: 0, fees_unknown: 0 } }),
    });
    const { renderPnlPanel } = await import('../../frontend/static/js/dashboard/pnl-panel.js');

    await renderPnlPanel();

    expect(document.getElementById('pnl-state').textContent).toBe(
      'No broker fills yet. Run a refresh to pull fills from OpenD.',
    );
    expect(bodyRows()).toHaveLength(0);
    expect(document.getElementById('pnl-totals').children).toHaveLength(0);
  });

  it('renders API-fed ticker text as plain text, never as markup', async () => {
    const hostile = '<img src=x onerror=alert(1)>';
    fetch.mockResolvedValue({
      ok: true,
      json: async () =>
        samplePayload({
          underlyings: [
            {
              ticker: hostile,
              status: 'reconciled',
              net_pnl: 10,
              option_pnl: 10,
              share_pnl: 0,
              fees: 0,
              fees_unknown: 0,
              notes: [hostile],
            },
          ],
        }),
    });
    const { renderPnlPanel } = await import('../../frontend/static/js/dashboard/pnl-panel.js');

    await renderPnlPanel();

    expect(document.querySelectorAll('img')).toHaveLength(0);
    expect(bodyRows()[0].querySelector('td').textContent).toBe(hostile);
    expect(bodyRows()[0].getAttribute('title')).toBe(hostile);
  });

  it('marks an unverified NAV change and shows an em dash when NAV is absent', async () => {
    const { renderPnlPanel } = await import('../../frontend/static/js/dashboard/pnl-panel.js');

    fetch.mockResolvedValueOnce({
      ok: true,
      json: async () =>
        samplePayload({
          nav_since_last_capital_movement: { ...samplePayload().nav_since_last_capital_movement, verified: false },
        }),
    });
    await renderPnlPanel();
    expect(metricValue('NAV since last deposit').textContent).toContain('unverified');

    fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => samplePayload({ nav_since_last_capital_movement: null }),
    });
    await renderPnlPanel();
    expect(metricValue('NAV since last deposit').textContent).toBe('—');
  });
});
