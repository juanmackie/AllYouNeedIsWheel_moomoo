import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

function setupDOM() {
  document.body.innerHTML = `
    <section class="app-section outcome-section" id="outcome-panel">
      <div class="app-section__header">
        <div>
          <span class="app-section__eyebrow">Outcomes</span>
          <h2>Broker-verified outcomes</h2>
          <p>Quoted vs filled credit, net of fees, capital deployed per day.</p>
        </div>
        <div class="app-section__actions">
          <button id="outcome-ingest-btn" class="ft-btn ft-btn--ghost" type="button">Pull broker fills</button>
        </div>
      </div>
      <div id="outcome-state"></div>
      <div id="outcome-totals" class="outcome-totals-grid"></div>
      <div id="outcome-groups">
        <div class="outcome-group">
          <h3 class="outcome-group__title">By preset</h3>
          <div class="ft-table-wrap"><table class="ft-table"><thead><tr><th>Preset</th><th class="ft-th-right">Signals</th></tr></thead><tbody id="outcome-group-preset"></tbody></table></div>
        </div>
        <div class="outcome-group">
          <h3 class="outcome-group__title">By DTE bucket</h3>
          <div class="ft-table-wrap"><table class="ft-table"><thead><tr><th>Bucket</th><th class="ft-th-right">Signals</th></tr></thead><tbody id="outcome-group-dte"></tbody></table></div>
        </div>
        <div class="outcome-group">
          <h3 class="outcome-group__title">By ticker</h3>
          <div class="ft-table-wrap"><table class="ft-table"><thead><tr><th>Ticker</th><th class="ft-th-right">Signals</th></tr></thead><tbody id="outcome-group-ticker"></tbody></table></div>
        </div>
        <div class="outcome-group">
          <h3 class="outcome-group__title">By event tier</h3>
          <div class="ft-table-wrap"><table class="ft-table"><thead><tr><th>Tier</th><th class="ft-th-right">Signals</th></tr></thead><tbody id="outcome-group-event"></tbody></table></div>
        </div>
      </div>
      <div class="outcome-drill">
        <div class="outcome-drill__header">
          <h3 class="outcome-drill__title">Contracts <span id="outcome-records-count" class="outcome-drill__count"></span></h3>
          <p class="outcome-drill__hint">Click a row to view the supporting fill transactions.</p>
        </div>
        <div class="ft-table-wrap">
          <table class="ft-table">
            <thead>
              <tr>
                <th>Contract</th><th>Strategy</th><th>Status</th><th class="ft-th-right">Quoted</th>
                <th class="ft-th-right">Filled</th><th class="ft-th-right">Slippage</th><th class="ft-th-right">Net P&amp;L</th>
                <th class="ft-th-right">Capital-days</th><th class="ft-th-right">$/day</th><th class="ft-th-right">Fills</th><th></th>
              </tr>
            </thead>
            <tbody id="outcome-records"></tbody>
          </table>
        </div>
      </div>
    </section>
  `;
}

function samplePayload() {
  return {
    success: true,
    generated_at: '2026-05-10T12:00:00',
    filters: {},
    totals: {
      sample_size: 3,
      matched_count: 2,
      pending_count: 1,
      measured_count: 2,
      unknown_count: 0,
      coverage_pct: 66.7,
      net_dollars: 410,
      capital_days: 82000,
      owner_efficiency: 0.005,
      open_losses: 0,
      drawdown: 0,
      quoted_credit_avg_per_contract: 1.25,
      filled_credit_avg_per_contract: 1.18,
      avg_slippage_per_contract: -0.07,
      fees_total_known: 2.4,
      fees_unknown_count: 0,
    },
    groups: {
      by_preset: [{ key: 'balanced', sample_size: 3, measured_count: 2, unknown_count: 0, coverage_pct: 66.7, net_dollars: 410, capital_days: 82000, owner_efficiency: 0.005 }],
      by_dte_bucket: [{ key: '0-7', sample_size: 3, measured_count: 2, unknown_count: 0, coverage_pct: 66.7, net_dollars: 410, capital_days: 82000, owner_efficiency: 0.005 }],
      by_ticker: [
        { key: 'NVDA', sample_size: 2, measured_count: 2, unknown_count: 0, coverage_pct: 100, net_dollars: 400, capital_days: 52000, owner_efficiency: 0.0077 },
        { key: 'AAPL', sample_size: 1, measured_count: 0, unknown_count: 0, coverage_pct: 0, net_dollars: null, capital_days: 0, owner_efficiency: null },
      ],
      by_event_tier: [{ key: 'earnings', sample_size: 1, measured_count: 1, unknown_count: 0, coverage_pct: 100, net_dollars: 250, capital_days: 40000, owner_efficiency: 0.0063 }],
    },
    outcomes: [
      {
        identity: 'NVDA|20260619|PUT|150',
        ticker: 'NVDA',
        expiration: '20260619',
        option_type: 'PUT',
        strike: 150,
        signal_type: 'csp',
        preset_key: 'balanced',
        event_tier: 'earnings',
        quality_tier: 'A',
        dte: 40,
        dte_bucket: '31-45',
        first_recommended_at: '2026-05-09T00:00:00',
        run_id: 'run-1',
        quoted_credit_per_contract: 1.25,
        filled_credit_per_contract: 1.18,
        contracts_sold: 2,
        contracts_bought_back: 2,
        open_contracts: 0,
        slippage_per_contract: -0.07,
        slippage_dollars: -14,
        fees_known: true,
        fees_total: 2.4,
        gross_premium_pnl: 410,
        net_pnl: 408,
        outcome_status: 'measured',
        capital_days: 52000,
        owner_efficiency: 0.0078,
        open: false,
        fills: [
          { fill_id: 'F1', order_id: 'O1', captured_at: '2026-05-09T10:00:00', side: 'SELL', qty: 2, price: 1.18, fees: 1.2, security_type: 'OPT' },
          { fill_id: 'F2', order_id: 'O2', captured_at: '2026-06-18T10:00:00', side: 'BUY', qty: 2, price: 0.01, fees: 1.2, security_type: 'OPT' },
        ],
      },
      {
        identity: 'AAPL|20260717|PUT|180',
        ticker: 'AAPL',
        expiration: '20260717',
        option_type: 'PUT',
        strike: 180,
        signal_type: 'csp',
        preset_key: 'balanced',
        event_tier: 'untiered',
        quality_tier: 'B',
        dte: 68,
        dte_bucket: '61-90',
        first_recommended_at: '2026-05-10T00:00:00',
        run_id: 'run-2',
        quoted_credit_per_contract: 0.9,
        filled_credit_per_contract: null,
        contracts_sold: 0,
        contracts_bought_back: 0,
        open_contracts: 0,
        slippage_per_contract: null,
        slippage_dollars: null,
        fees_known: false,
        fees_total: null,
        gross_premium_pnl: null,
        net_pnl: null,
        outcome_status: 'pending',
        capital_days: null,
        owner_efficiency: null,
        open: false,
        fills: [],
      },
    ],
    count: 2,
  };
}

function mockFetchOnce(payload, options = {}) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: options.ok !== false,
    status: options.status || 200,
    json: async () => payload,
  }));
}

describe('outcome-panel rendering', () => {
  beforeEach(() => {
    setupDOM();
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('renders totals with sample, coverage, measured/unknown, net and capital-days', async () => {
    mockFetchOnce(samplePayload());
    const { renderOutcomePanel } = await import('../../frontend/static/js/dashboard/outcome-panel.js');
    await renderOutcomePanel();

    const totals = document.getElementById('outcome-totals').textContent;
    expect(totals).toContain('Signals (sample)');
    expect(totals).toContain('3');
    expect(totals).toContain('Coverage');
    expect(totals).toContain('66.7%');
    expect(totals).toContain('Measured / Unknown');
    expect(totals).toContain('2 / 0');
    expect(totals).toContain('Net outcome');
    expect(totals).toContain('$410.00');
    expect(totals).toContain('Capital-days');
  });

  it('shows em dash (never $0.00) for open/unknown net P&L, slippage and $/day', async () => {
    const payload = samplePayload();
    const open = payload.outcomes[1];
    open.outcome_status = 'open';
    open.open = true;
    open.open_contracts = 1;
    open.contracts_sold = 1;
    open.filled_credit_per_contract = 2.0;
    open.net_pnl = null;
    open.slippage_per_contract = null;
    mockFetchOnce(payload);
    const { renderOutcomePanel } = await import('../../frontend/static/js/dashboard/outcome-panel.js');
    await renderOutcomePanel();

    const rows = document.getElementById('outcome-records').querySelectorAll('.outcome-record-row');
    const cells = [...rows[1].querySelectorAll('td')].map((td) => td.textContent.trim());
    expect(cells.join('|')).toContain('open');
    expect(cells[5]).toBe('—'); // slippage unknown, not $0.00
    expect(cells[6]).toBe('—'); // net P&L unknown while the obligation is open
    expect(cells[8]).toBe('—'); // $/day unknown, not $0.00

    // Group with null net_dollars must render an em dash, not $0.00.
    const tickerRows = document.getElementById('outcome-group-ticker').querySelectorAll('tr');
    expect(tickerRows[1].textContent).not.toContain('$0.00');
  });


  it('renders per-group summaries with sample size and coverage', async () => {
    mockFetchOnce(samplePayload());
    const { renderOutcomePanel } = await import('../../frontend/static/js/dashboard/outcome-panel.js');
    await renderOutcomePanel();

    const tickerRows = document.getElementById('outcome-group-ticker').querySelectorAll('tr');
    expect(tickerRows.length).toBe(2);
    expect(tickerRows[0].textContent).toContain('NVDA');
    expect(tickerRows[0].textContent).toContain('100.0%');
    expect(tickerRows[1].textContent).toContain('AAPL');
    expect(tickerRows[1].textContent).toContain('0.0%');

    expect(document.getElementById('outcome-group-preset').textContent).toContain('balanced');
    expect(document.getElementById('outcome-group-dte').textContent).toContain('0-7');
    expect(document.getElementById('outcome-group-event').textContent).toContain('earnings');
  });

  it('renders contract rows with quoted/filled/slippage/net', async () => {
    mockFetchOnce(samplePayload());
    const { renderOutcomePanel } = await import('../../frontend/static/js/dashboard/outcome-panel.js');
    await renderOutcomePanel();

    const rows = document.getElementById('outcome-records').querySelectorAll('.outcome-record-row');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('NVDA');
    expect(rows[0].textContent).toContain('2026-06-19');
    expect(rows[0].textContent).toContain('Put');
    expect(rows[0].textContent).toContain('$150.00');
    expect(rows[0].textContent).toContain('$1.25');
    expect(rows[0].textContent).toContain('$1.18');
    expect(rows[0].textContent).toContain('measured');

    const countEl = document.getElementById('outcome-records-count');
    expect(countEl.textContent).toContain('2');
  });

  it('shows empty state when no outcomes exist', async () => {
    const payload = samplePayload();
    payload.totals.sample_size = 0;
    payload.outcomes = [];
    payload.count = 0;
    mockFetchOnce(payload);
    const { renderOutcomePanel } = await import('../../frontend/static/js/dashboard/outcome-panel.js');
    await renderOutcomePanel();

    const body = document.getElementById('outcome-records');
    expect(body.textContent).toContain('No verified signals yet');
  });

  it('escapes API-fed contract and strategy text', async () => {
    const payload = samplePayload();
    payload.outcomes[0].ticker = '<img src=x onerror=alert(1)>';
    payload.outcomes[0].signal_type = '<script>alert(1)</script>';
    payload.outcomes[0].event_tier = '<b>earnings</b>';
    payload.outcomes[0].fills[0].fill_id = '<img src=x onerror=alert(2)>';
    payload.groups.by_ticker[0].key = '<img src=x onerror=alert(3)>';
    mockFetchOnce(payload);
    const { renderOutcomePanel } = await import('../../frontend/static/js/dashboard/outcome-panel.js');
    await renderOutcomePanel();

    const html = document.getElementById('outcome-records').innerHTML;
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
    expect(html).not.toContain('<img src=x onerror=alert(1)>');
    expect(html).not.toContain('<script>alert(1)</script>');

    const groupHtml = document.getElementById('outcome-group-ticker').innerHTML;
    expect(groupHtml).toContain('&lt;img src=x onerror=alert(3)&gt;');
    expect(groupHtml).not.toContain('<img src=x onerror=alert(3)>');
  });

  it('expands and collapses supporting-fill transactions', async () => {
    mockFetchOnce(samplePayload());
    const { renderOutcomePanel } = await import('../../frontend/static/js/dashboard/outcome-panel.js');
    await renderOutcomePanel();

    const row = document.querySelector('.outcome-record-row');
    expect(row.getAttribute('aria-expanded')).toBe('false');
    const detail = row.nextElementSibling;
    expect(detail.classList.contains('outcome-fill-row')).toBe(true);
    expect(detail.style.display).toBe('none');

    row.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(row.getAttribute('aria-expanded')).toBe('true');
    expect(detail.style.display).not.toBe('none');
    expect(detail.textContent).toContain('F1');
    expect(detail.textContent).toContain('SELL');
    expect(detail.textContent).toContain('2 × $1.18');
  });

  it('shows an error state when the outcome fetch fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ success: false, error: 'boom' }),
    }));
    const { renderOutcomePanel } = await import('../../frontend/static/js/dashboard/outcome-panel.js');
    await renderOutcomePanel();

    expect(document.getElementById('outcome-state').textContent).toContain('Could not load outcome analytics');
  });

  it('ingests broker fills and re-renders on success', async () => {
    const ingestResponse = {
      success: true,
      ok: true,
      fills: { ok: true, ingested: 4 },
      cash_flows: { ok: true, ingested: 7 },
    };
    mockFetchOnce(ingestResponse);
    const { ingestBrokerFills, renderOutcomePanel } = await import('../../frontend/static/js/dashboard/outcome-panel.js');
    await ingestBrokerFills();

    const calls = fetch.mock.calls.map(c => [c[0], c[1] && c[1].method]);
    expect(calls).toContainEqual(['/api/options/analytics/outcomes/ingest?days=90&cash_flow_days=7', 'POST']);
    // POST happened, then a fresh GET re-render
    expect(calls.some(c => c[0] === '/api/options/analytics/outcomes?limit=500')).toBe(true);
  });
});
