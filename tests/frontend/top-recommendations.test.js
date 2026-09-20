import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../frontend/static/js/dashboard/api-run.js', () => ({
  fetchRunState: vi.fn(),
  refreshRun: vi.fn(),
  revalidateCopy: vi.fn(),
  markRecommendationTaken: vi.fn(),
}));

vi.mock('../../frontend/static/js/utils/state-model.js', () => ({
  default: {
    showEmpty: vi.fn(),
    showError: vi.fn(),
  },
}));

vi.mock('../../frontend/static/js/dashboard/options-table-rendering.js', () => ({
  showPanelLoading: vi.fn(() => 'banner-1'),
  finishPanelLoading: vi.fn(),
  failPanelLoading: vi.fn(),
}));

vi.mock('../../frontend/static/js/utils/formatters.js', () => ({
  escapeHtml: vi.fn((value) => String(value ?? '')),
  formatCurrency: vi.fn((v) => `$${v.toFixed(2)}`),
  formatPercent: vi.fn((v) => `${v.toFixed(1)}%`),
}));

import StateModel from '../../frontend/static/js/utils/state-model.js';

afterEach(async () => {
  const { cleanupTopRecommendations } = await import(
    '../../frontend/static/js/dashboard/top-recommendations.js'
  );
  cleanupTopRecommendations?.();
});

function setupDOM() {
  document.body.innerHTML = `
    <div id="top-recommendations-container">
      <div id="top-recommendations-state"></div>
      <div id="top-recommendations-content" class="d-none">
        <div id="top-recommendations-cards"></div>
      </div>
      <div id="top-recs-last-updated" class="d-none"></div>
      <div id="blocked-candidates-section" class="d-none"></div>
      <div id="buying-power-indicator" class="d-none">
        <span id="bp-amount"></span>
        <span id="bp-reserved"></span>
        <span id="bp-broker"></span>
        <div id="bp-diagnostics"></div>
      </div>
      <div id="signal-tabs" class="d-none"></div>
      <button id="research-long-options"></button>
      <button id="refresh-top-recommendations"></button>
    </div>
    <div id="growth-mode-banner" class="d-none"></div>
    <div id="growth-mode-objective"></div>
    <div id="growth-mode-drawdown"></div>
    <div id="growth-csp-profile-label" class="d-none">
      <span id="growth-csp-profile-text"></span>
    </div>
    <div id="top-recs-title"></div>
    <div id="top-recs-eyebrow"></div>
    <div id="top-recs-desc"></div>
    <template id="recommendation-card-template">
      <div class="recommendation-card">
        <span class="rank-badge"></span>
        <span class="ticker-badge"></span>
        <span class="signal-type-badge"></span>
        <span class="option-type-badge"></span>
        <span class="strike-price"></span>
        <span class="expiration-date"></span>
        <span class="dte-badge"></span>
        <span class="premium-velocity"></span>
        <span class="premium-amount"></span>
        <span class="annualized-return"></span>
        <span class="quality-badge"></span>
        <span class="confidence-badge"></span>
        <span class="underlying-quality-badge"></span>
        <span class="research-only-badge"></span>
        <span class="signal-data-source"></span>
        <button type="button" class="btn btn-sm copy-ticket-btn">Copy ticket</button>
        <input type="number" class="form-control traded-strike-input" />
        <button type="button" class="btn btn-sm mark-taken-btn">Mark taken</button>
        <div class="taken-status small"></div>
        <span class="recommendation-warnings"></span>
        <div class="missing-risk-badge small text-warning fw-semibold d-none"></div>
        <span class="otm-pct"></span>
        <span class="delta-value"></span>
        <span class="iv-rank"></span>
        <div class="macro-impact"></div>
        <div class="csp-details d-none">
          <span class="csp-cash-required"></span>
          <span class="csp-cash-pct"></span>
          <span class="csp-cash-remaining"></span>
          <span class="csp-breakeven-buffer"></span>
          <span class="csp-expected-move"></span>
        </div>
        <div class="cc-details d-none">
          <span class="cc-if-called-return"></span>
          <span class="cc-if-called-proceeds"></span>
          <span class="cc-cost-basis-dist"></span>
          <span class="cc-intent"></span>
        </div>
        <div class="hard-blockers d-none"></div>
        <div class="hard-blockers d-none">
          <span class="hard-blockers-list"></span>
        </div>
        <div class="recommendation-details">
          <div class="recommendation-detail-row otm-row"></div>
          <div class="recommendation-detail-row delta-row"></div>
          <div class="recommendation-detail-row iv-row"></div>
          <div class="recommendation-detail-row macro-row"></div>
        </div>
        <div class="growth-mode-details d-none"></div>
      </div>
    </template>
  `;
}

describe('top-recommendations empty state', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupDOM();
  });

  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('does not contain market-hours phrasing in empty state when signals are empty', async () => {
    const { initializeTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );

    const { fetchRunState } = await import(
      '../../frontend/static/js/dashboard/api-run.js'
    );

    fetchRunState.mockResolvedValue({
      success: true,
      signals: [],
      count: 0,
      generated_at: '2026-05-24T12:00:00',
    });

    await initializeTopRecommendations();
    await vi.dynamicImportSettled?.();

    await new Promise(r => setTimeout(r, 50));

    expect(fetchRunState).toHaveBeenCalledWith();
    const emptyCall = StateModel.showEmpty.mock.calls.find(c => c[0] === 'top-recommendations-state');
    expect(emptyCall).toBeDefined();
    const message = emptyCall[1];
    expect(message).toContain('No growth signals available');
    expect(message).toContain('Try refresh or adjust criteria');
    expect(message).not.toContain('market open');
    expect(message).not.toContain('trading day');
    expect(message).not.toContain('Check back after');
  });

  it('shows dominant blocker and cash diagnostics when no signals surface', async () => {
    const { initializeTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );

    const { fetchRunState } = await import(
      '../../frontend/static/js/dashboard/api-run.js'
    );

    fetchRunState.mockResolvedValue({
      success: true,
      signals: [],
      count: 0,
      generated_at: '2026-05-24T12:00:00',
      cash_available_for_csp: 0,
      blocked_reason_counts: {
        cash_fit: 8,
        low_premium: 2,
      },
      cash_diagnostics: {
        cash_available_for_csp_source: 'available_cash_minus_open_short_put_collateral',
        raw_summary_fields: {
          available_cash: 0,
          usd_net_cash_power: 25000,
        },
      },
    });

    await initializeTopRecommendations();
    await vi.dynamicImportSettled?.();
    await new Promise(r => setTimeout(r, 50));

    const emptyCall = StateModel.showEmpty.mock.calls.find(c => c[0] === 'top-recommendations-state');
    expect(emptyCall).toBeDefined();
    const message = emptyCall[1];
    expect(message).toContain('Dominant blocker: cash fit (8)');
    expect(message).toContain('CSP cash $0.00');
    expect(message).toContain('source available_cash_minus_open_short_put_collateral');
    expect(message).toContain('available_cash=$0.00');
    expect(message).toContain('usd_net_cash_power=$25000.00');
  });
});

describe('top-recommendations OpenD-unavailable state', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupDOM();
  });

  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('shows broker-unavailable message when response has error_code opend_unavailable', async () => {
    const { loadTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );

    const { fetchRunState } = await import(
      '../../frontend/static/js/dashboard/api-run.js'
    );

    fetchRunState.mockResolvedValue({
      signals: [],
      count: 0,
      error: 'OpenD unavailable',
      error_code: 'opend_unavailable',
    });

    await loadTopRecommendations(true);
    await vi.dynamicImportSettled?.();
    await new Promise(r => setTimeout(r, 50));

    const errorCall = StateModel.showError.mock.calls.find(c => c[0] === 'top-recommendations-state');
    expect(errorCall).toBeDefined();
    expect(errorCall[1]).toContain('OpenD unavailable');
  });

  it('shows broker-unavailable message when response has error_code opend_login_required', async () => {
    const { loadTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );

    const { fetchRunState } = await import(
      '../../frontend/static/js/dashboard/api-run.js'
    );

    fetchRunState.mockResolvedValue({
      signals: [],
      count: 0,
      error: 'Login required',
      error_code: 'opend_login_required',
    });

    await loadTopRecommendations(true);
    await vi.dynamicImportSettled?.();
    await new Promise(r => setTimeout(r, 50));

    const errorCall = StateModel.showError.mock.calls.find(c => c[0] === 'top-recommendations-state');
    expect(errorCall).toBeDefined();
    expect(errorCall[1]).toContain('OpenD unavailable');
  });
});

describe('top-recommendations generating state', () => {
  let consoleWarnSpy;
  let consoleDebugSpy;
  let cleanup;

  beforeEach(() => {
    vi.clearAllMocks();
    setupDOM();
    consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    consoleDebugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {});
  });

  afterEach(async () => {
    if (cleanup) {
      cleanup();
      cleanup = null;
    }
    document.body.innerHTML = '';
    consoleWarnSpy.mockRestore();
    consoleDebugSpy.mockRestore();
  });

  it('shows generating notice without calling console.warn when backend is generating', async () => {
    const { loadTopRecommendations, cleanupTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );
    cleanup = cleanupTopRecommendations;

    const { fetchRunState } = await import(
      '../../frontend/static/js/dashboard/api-run.js'
    );

    fetchRunState.mockResolvedValue({
      success: true,
      generating: true,
      count: 0,
      signals: [],
    });

    await loadTopRecommendations(true);
    await vi.dynamicImportSettled?.();
    await new Promise(r => setTimeout(r, 50));

    const emptyCall = StateModel.showEmpty.mock.calls.find(c => c[0] === 'top-recommendations-state');
    expect(emptyCall).toBeUndefined();

    const generatingNotice = document.querySelector('[data-generating-notice="true"]');
    expect(generatingNotice).toBeTruthy();
    expect(generatingNotice.textContent).toContain('Fresh growth signals are being computed');

    expect(consoleDebugSpy).toHaveBeenCalled();
    expect(consoleWarnSpy).not.toHaveBeenCalled();
  });
});

describe('top-recommendations unknown IV status', () => {
  let consoleWarnSpy;
  let consoleDebugSpy;
  let cleanup;

  beforeEach(() => {
    vi.clearAllMocks();
    setupDOM();
    consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    consoleDebugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {});
  });

  afterEach(async () => {
    if (cleanup) {
      cleanup();
      cleanup = null;
    }
    document.body.innerHTML = '';
    consoleWarnSpy.mockRestore();
    consoleDebugSpy.mockRestore();
  });

  it('shows "IV unavailable" when iv_status is unknown', async () => {
    const { initializeTopRecommendations, cleanupTopRecommendations, loadTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );
    cleanup = cleanupTopRecommendations;

    const { fetchRunState } = await import(
      '../../frontend/static/js/dashboard/api-run.js'
    );

    fetchRunState.mockResolvedValue({
      success: true,
      count: 1,
      signals: [
        {
          ticker: 'TEST',
          option_type: 'PUT',
          strike: 95.0,
          expiration: '20260515',
          dte: 21,
          bid: 2.0,
          ask: 2.10,
          mid_price: 2.05,
          annualized_return: 50.0,
          iv_rank: 50,
          iv_status: 'unknown',
          otm_pct: 5.0,
          delta: -0.25,
        },
      ],
      generated_at: '2026-05-24T12:00:00',
    });

    await initializeTopRecommendations();
    await loadTopRecommendations(true);
    await vi.dynamicImportSettled?.();
    await new Promise(r => setTimeout(r, 50));

    const cards = document.querySelectorAll('.recommendation-card');
    expect(cards.length).toBe(1);

    const ivRankEl = cards[0].querySelector('.iv-rank');
    expect(ivRankEl.textContent).toBe('IV unavailable');
    expect(ivRankEl.classList.contains('text-muted')).toBe(true);
    // Regression: multi-token class strings ('bg-warning text-dark') must be
    // applied whole (classList.add chokes on the space; addClassTokens does not).
    const tierBadgeEl = cards[0].querySelector('.underlying-quality-badge');
    expect(tierBadgeEl.classList.contains('bg-warning')).toBe(true);
    expect(tierBadgeEl.classList.contains('text-dark')).toBe(true);
    expect(cards[0].querySelectorAll('.recommendation-detail-row').length).toBe(4);
    expect(cards[0].querySelector('.csp-details')?.classList.contains('d-none')).toBe(false);
    expect(cards[0].querySelector('.cc-details')?.classList.contains('d-none')).toBe(true);
    expect(cards[0].querySelector('.hard-blockers')?.classList.contains('d-none')).toBe(true);
  });
});

describe('top-recommendations missing-risk badge (display-only event tier)', () => {
  let cleanup;

  beforeEach(() => {
    vi.clearAllMocks();
    setupDOM();
  });

  afterEach(() => {
    if (cleanup) {
      cleanup();
      cleanup = null;
    }
    document.body.innerHTML = '';
  });

  async function renderCard(signal) {
    const { initializeTopRecommendations, cleanupTopRecommendations, loadTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );
    cleanup = cleanupTopRecommendations;
    const { fetchRunState } = await import(
      '../../frontend/static/js/dashboard/api-run.js'
    );
    fetchRunState.mockResolvedValue({
      success: true,
      count: 1,
      signals: [signal],
      generated_at: '2026-05-24T12:00:00',
    });
    await initializeTopRecommendations();
    await loadTopRecommendations(true);
    await vi.dynamicImportSettled?.();
    await new Promise(r => setTimeout(r, 50));
    return document.querySelector('.missing-risk-badge');
  }

  it('shows "Earnings unknown — verify before placing" when event tier is event_unknown', async () => {
    const riskBadge = await renderCard({
      ticker: 'TEST',
      option_type: 'PUT',
      strike: 95.0,
      expiration: '20260515',
      dte: 21,
      bid: 2.0,
      ask: 2.10,
      mid_price: 2.05,
      annualized_return: 50.0,
      event_tier: 'event_unknown',
    });
    expect(riskBadge).toBeTruthy();
    expect(riskBadge.textContent).toBe('Earnings unknown — verify before placing');
    expect(riskBadge.classList.contains('d-none')).toBe(false);
    // No ranking claim anywhere.
    expect(riskBadge.textContent).not.toMatch(/rank/i);
  });

  it('shows no badge text for event_safe', async () => {
    const riskBadge = await renderCard({
      ticker: 'TEST',
      option_type: 'PUT',
      strike: 95.0,
      expiration: '20260515',
      dte: 21,
      bid: 2.0,
      ask: 2.10,
      mid_price: 2.05,
      annualized_return: 50.0,
      event_tier: 'event_safe',
    });
    expect(riskBadge).toBeTruthy();
    expect(riskBadge.textContent).toBe('');
    expect(riskBadge.classList.contains('d-none')).toBe(true);
  });

  it('shows no badge text for earnings_before_expiry', async () => {
    const riskBadge = await renderCard({
      ticker: 'TEST',
      option_type: 'PUT',
      strike: 95.0,
      expiration: '20260515',
      dte: 21,
      bid: 2.0,
      ask: 2.10,
      mid_price: 2.05,
      annualized_return: 50.0,
      event_tier: 'earnings_before_expiry',
    });
    expect(riskBadge).toBeTruthy();
    expect(riskBadge.textContent).toBe('');
    expect(riskBadge.classList.contains('d-none')).toBe(true);
  });

  it('shows no badge text when tier comes via wheel_decision as a known tier', async () => {
    const riskBadge = await renderCard({
      ticker: 'TEST',
      option_type: 'PUT',
      strike: 95.0,
      expiration: '20260515',
      dte: 21,
      bid: 2.0,
      ask: 2.10,
      mid_price: 2.05,
      annualized_return: 50.0,
      wheel_decision: { event_tier: 'earnings_before_expiry' },
    });
    expect(riskBadge).toBeTruthy();
    expect(riskBadge.textContent).toBe('');
    expect(riskBadge.classList.contains('d-none')).toBe(true);
  });
});

describe('top-recommendations source badges', () => {
  let cleanup;

  beforeEach(() => {
    vi.clearAllMocks();
    setupDOM();
  });

  afterEach(() => {
    if (cleanup) {
      cleanup();
      cleanup = null;
    }
    document.body.innerHTML = '';
  });

  it('renders separate price, chain, and IV provenance badges', async () => {
    const { initializeTopRecommendations, cleanupTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );
    cleanup = cleanupTopRecommendations;
    cleanupTopRecommendations();

    const { fetchRunState } = await import(
      '../../frontend/static/js/dashboard/api-run.js'
    );

    const now = new Date().toISOString();
    fetchRunState.mockResolvedValue({
      success: true,
      count: 1,
      generated_at: now,
      cash_available_for_csp: 20000,
      cash_reserved_for_csp: 10000,
      broker_buying_power: 100000,
      cash_diagnostics: {
        available_cash_source: 'usd_net_cash_power',
        cash_available_for_csp_source: 'available_cash_minus_open_short_put_collateral',
        raw_summary_fields: {
          us_avl_withdrawal_cash: 0,
          us_cash: 40000,
          usd_net_cash_power: 25000,
          cash: 0,
        },
      },
      signals: [{
        rank: 1,
        ticker: 'AAPL',
        option_type: 'PUT',
        strike: 150,
        expiration: '20260529',
        dte: 21,
        mid_price: 1.25,
        premium_per_contract: 125,
        annualized_return: 21,
        iv_adjusted_return: 18,
        otm_pct: 7.5,
        delta: -0.18,
        iv_rank: 52,
        iv_status: 'normal',
        warnings: [],
        rationale: ['Strong'],
        max_contracts: 1,
        existing_position: 0,
        profile_type: 'monthly',
        stock_price: 162,
        bid: 1.2,
        ask: 1.3,
        open_interest: 500,
        volume: 100,
        implied_volatility: 0.32,
        size_fit: 1,
        expected_move_buffer: 0,
        wheel_decision: {
          quote_timestamp: now,
          price_source: 'broker',
          chain_source: 'yfinance',
          iv_source: 'yfinance',
        },
        price_source: 'broker',
        chain_source: 'yfinance',
        iv_source: 'yfinance',
        from_yfinance: true,
        signal_type: 'csp',
        strategy: 'wheel',
        broker_feasible: true,
        capital_required: 15000,
        risk_budget_used: 0,
        data_source: 'broker',
        confidence: 74,
        quote_quality: 'tradable',
        blocked_reason_codes: [],
        research_only: true,
      }],
      blocked_signals: [],
    });

    await initializeTopRecommendations();
    await vi.dynamicImportSettled?.();
    await new Promise(r => setTimeout(r, 50));

    const sourceEl = document.querySelector('.signal-data-source');
    expect(sourceEl).toBeTruthy();
    expect(sourceEl.textContent).toContain('Price: Moomoo');
    expect(sourceEl.textContent).toContain('Chain: yfinance');
    expect(sourceEl.textContent).toContain('IV: yfinance');
    expect(sourceEl.querySelectorAll('.badge').length).toBeGreaterThanOrEqual(4);
    expect(document.querySelector('.csp-cash-required')?.textContent).toBe('$15000.00');
    expect(document.querySelector('.csp-cash-pct')?.textContent).toBe('75.0%');
    expect(document.getElementById('bp-amount')?.textContent).toBe('$20000.00');
    expect(document.getElementById('bp-broker')?.textContent).toBe('$100000.00');
    expect(document.getElementById('bp-diagnostics')?.textContent).toContain('available cash source: usd_net_cash_power');
    expect(document.getElementById('bp-diagnostics')?.textContent).toContain('raw: us_cash=$40000.00, usd_net_cash_power=$25000.00');
    expect(document.querySelector('.research-only-badge')?.textContent).toBe('Research only');
    expect(document.querySelector('.research-only-badge')?.classList.contains('d-none')).toBe(false);
  });
});

  it('copies an explicit ticket to the clipboard and reports success/failure', async () => {
    setupDOM();
    const { initializeTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );
    const { fetchRunState, revalidateCopy } = await import(
      '../../frontend/static/js/dashboard/api-run.js'
    );

    fetchRunState.mockResolvedValue({
      success: true,
      tradeable: true,
      run: { run_id: 'ad-hoc-live', market_state: 'open', status: 'ready' },
      signals: [{
        rank: 1, ticker: 'AAPL', option_type: 'PUT', strike: 140, expiration: '20240315', dte: 21,
        copy_eligible: true, recommended_contracts: 1,
        bid: 2.50, ask: 3.00, mid_price: 2.75, premium_per_contract: 275.0,
        max_contracts: 1, cash_required: 14000.0, chain_source: 'broker',
        signal_type: 'csp', profile_type: 'monthly',
        wheel_decision: { confidence_score: 100 },
        eligibility: { mode: 'live', reasons: [] },
      }],
      count: 1,
      generated_at: '2026-05-24T12:00:00',
    });
    revalidateCopy.mockResolvedValue({
      ok: true, matched_run: true, matched_contract: true, mode: 'live',
      run_id: 'ad-hoc-live', reasons: [], verified_at: '2026-05-24T12:00:01',
    });

    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });

    await initializeTopRecommendations();
    await vi.dynamicImportSettled?.();
    await new Promise(r => setTimeout(r, 50));

    const btn = document.querySelector('.copy-ticket-btn');
    expect(btn).toBeTruthy();
    btn.click();
    await vi.waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));

    const text = writeText.mock.calls[0][0];
    expect(text).toContain('SELL TO OPEN CSP');
    expect(text).toContain('AAPL');
    expect(text).toContain('140.00');
    expect(text).toContain('x1');
    expect(text).toContain('Source:');
    await vi.waitFor(() => expect(btn.classList.contains('btn-success')).toBe(true));

    // Failure path
    writeText.mockRejectedValueOnce(new Error('denied'));
    btn.click();
    await vi.waitFor(() => expect(btn.classList.contains('btn-danger')).toBe(true));
    vi.unstubAllGlobals();
  });

  it('copies a staged ticket when the run is not tradeable (US market closed)', async () => {
    setupDOM();
    const { initializeTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );
    const { fetchRunState, revalidateCopy } = await import(
      '../../frontend/static/js/dashboard/api-run.js'
    );

    fetchRunState.mockResolvedValue({
      success: true,
      tradeable: false,
      status: 'ready',
      run: { run_id: 'ad-hoc-staged', market_state: 'closed', status: 'ready' },
      signals: [{
        rank: 1, ticker: 'TSLA', option_type: 'PUT', strike: 200, expiration: '20240315', dte: 21,
        copy_eligible: true, recommended_contracts: 2,
        bid: 3.00, ask: 3.50, mid_price: 3.25, premium_per_contract: 325.0,
        max_contracts: 2, cash_required: 20000.0, chain_source: 'broker',
        signal_type: 'csp', profile_type: 'monthly', event_tier: 'event_unknown',
        wheel_decision: { confidence_score: 100 },
        eligibility: { mode: 'staged', reasons: [] },
      }],
      count: 1,
      generated_at: '2026-05-24T12:00:00',
    });
    revalidateCopy.mockResolvedValue({
      ok: true, matched_run: true, matched_contract: true, mode: 'staged',
      run_id: 'ad-hoc-staged', reasons: [], verified_at: '2026-05-24T12:00:01',
    });

    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });

    await initializeTopRecommendations();
    await vi.dynamicImportSettled?.();
    await new Promise(r => setTimeout(r, 50));

    const btn = document.querySelector('.copy-ticket-btn');
    expect(btn).toBeTruthy();
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toContain('Stage ticket');
    btn.click();
    await vi.waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));

    const text = writeText.mock.calls[0][0];
    expect(text).toContain('STAGED FOR US MARKET OPEN');
    expect(text).toContain('EVENT RISK');
    expect(text).toContain('TSLA');
    vi.unstubAllGlobals();
  });


describe('C03 copy eligibility at the point of use', () => {
  const candidate = {
    rank: 1, ticker: 'MSFT', option_type: 'PUT', strike: 300, expiration: '20240315', dte: 21,
    copy_eligible: true, recommended_contracts: 1,
    bid: 2.50, ask: 3.00, mid_price: 2.75, premium_per_contract: 275.0,
    max_contracts: 1, cash_required: 30000.0, chain_source: 'broker',
    signal_type: 'csp', profile_type: 'monthly',
    wheel_decision: { confidence_score: 100 },
    eligibility: { mode: 'live', reasons: [] },
  };

  beforeEach(() => {
    vi.clearAllMocks();
    document.body.innerHTML = '';
  });

  async function renderWith(envelope, reval) {
    setupDOM();
    const { initializeTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );
    const { fetchRunState, revalidateCopy } = await import(
      '../../frontend/static/js/dashboard/api-run.js'
    );
    fetchRunState.mockResolvedValue(envelope);
    revalidateCopy.mockResolvedValue(
      reval ?? {
        ok: true, matched_run: true, matched_contract: true,
        mode: envelope.signals[0].eligibility.mode,
        run_id: envelope.run.run_id, reasons: [], verified_at: '2026-05-24T12:00:01',
      }
    );
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    await initializeTopRecommendations();
    await vi.dynamicImportSettled?.();
    await new Promise(r => setTimeout(r, 50));
    return { writeText };
  }

  it('blocks any copy when the live run is partial (open session)', async () => {
    const { writeText } = await renderWith({
      success: true,
      tradeable: false,
      status: 'partial',
      run: { run_id: 'c03-run', market_state: 'open', status: 'partial', coverage_scanned: 2, coverage_total: 5 },
      signals: [{ ...candidate, eligibility: { mode: 'review_only', reasons: ['partial watchlist coverage — complete-universe needs coverage_scanned == coverage_total'] } }],
      count: 1, generated_at: '2026-05-24T12:00:00',
    });
    const btn = document.querySelector('.copy-ticket-btn');
    expect(btn).toBeTruthy();
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toContain('Review only');
    btn.click();
    expect(writeText).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it('blocks staging when the run is stale during an open market', async () => {
    const { writeText } = await renderWith({
      success: true,
      tradeable: false,
      status: 'stale',
      run: { run_id: 'c03-run', market_state: 'open', status: 'ready', coverage_scanned: 5, coverage_total: 5 },
      signals: [{ ...candidate, copy_eligible: true, eligibility: { mode: 'review_only', reasons: ['stale broker quote timestamps while the session is open'] } }],
      count: 1, generated_at: '2026-05-24T12:00:00',
    });
    const btn = document.querySelector('.copy-ticket-btn');
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toContain('Review only');
    btn.click();
    expect(writeText).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it('allows a staged ticket only for closed-market complete coverage', async () => {
    const { writeText } = await renderWith({
      success: true,
      tradeable: false,
      status: 'ready',
      run: { run_id: 'c03-run', market_state: 'closed', status: 'ready', coverage_scanned: 5, coverage_total: 5 },
      signals: [{ ...candidate, eligibility: { mode: 'staged', reasons: [] } }],
      count: 1, generated_at: '2026-05-24T12:00:00',
    });
    const btn = document.querySelector('.copy-ticket-btn');
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toContain('Stage ticket');
    btn.click();
    await vi.waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText.mock.calls[0][0]).toContain('STAGED FOR US MARKET OPEN');
    vi.unstubAllGlobals();
  });

  it('keeps the live copy path for a tradeable run', async () => {
    const { writeText } = await renderWith({
      success: true,
      tradeable: true,
      status: 'ready',
      run: { run_id: 'c03-run', market_state: 'open', status: 'ready', coverage_scanned: 5, coverage_total: 5 },
      signals: [candidate], count: 1, generated_at: '2026-05-24T12:00:00',
    });
    const btn = document.querySelector('.copy-ticket-btn');
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toContain('Copy ticket');
    btn.click();
    await vi.waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText.mock.calls[0][0]).not.toContain('STAGED FOR US MARKET OPEN');
    vi.unstubAllGlobals();
  });
});

describe('P1a revalidate-then-copy', () => {
  const candidate = {
    rank: 1, ticker: 'MSFT', option_type: 'PUT', strike: 300, expiration: '20240315', dte: 21,
    copy_eligible: true, recommended_contracts: 1,
    bid: 2.50, ask: 3.00, mid_price: 2.75, premium_per_contract: 275.0,
    max_contracts: 1, cash_required: 30000.0, chain_source: 'broker',
    signal_type: 'csp', profile_type: 'monthly',
    wheel_decision: { confidence_score: 100 },
    eligibility: { mode: 'live', reasons: [] },
  };

  beforeEach(() => {
    vi.clearAllMocks();
    document.body.innerHTML = '';
  });

  const baseRun = { run_id: 'c03-run', market_state: 'open', status: 'ready', coverage_scanned: 5, coverage_total: 5 };

  async function renderWith(signal, reval, revalCalls = null) {
    setupDOM();
    const { initializeTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );
    const { fetchRunState, revalidateCopy } = await import(
      '../../frontend/static/js/dashboard/api-run.js'
    );
    fetchRunState.mockResolvedValue({
      success: true, tradeable: true, status: 'ready',
      run: baseRun, signals: [signal], count: 1, generated_at: '2026-05-24T12:00:00',
    });
    if (revalCalls) {
      revalidateCopy.mockImplementation(revalCalls);
    } else {
      revalidateCopy.mockResolvedValue(reval);
    }
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    await initializeTopRecommendations();
    await vi.dynamicImportSettled?.();
    await new Promise(r => setTimeout(r, 50));
    return { writeText, revalidateCopy, fetchRunState };
  }

  it('aborts the copy and refreshes when the run changed under the click', async () => {
    const { writeText, revalidateCopy, fetchRunState } = await renderWith(
      candidate,
      {
        ok: true, matched_run: false, matched_contract: true,
        mode: 'live', run_id: 'new-run', reasons: [], verified_at: '2026-05-24T12:00:01',
      }
    );
    const btn = document.querySelector('.copy-ticket-btn');
    btn.click();
    await vi.waitFor(() => expect(revalidateCopy).toHaveBeenCalledTimes(1));
    expect(writeText).not.toHaveBeenCalled();
    // refresh re-fetches the run and requires a second click
    await vi.waitFor(() => expect(fetchRunState.mock.calls.length).toBeGreaterThan(1));
    vi.unstubAllGlobals();
  });

  it('writes nothing when the revalidated outcome is review_only', async () => {
    const { writeText, revalidateCopy, fetchRunState } = await renderWith(
      candidate,
      {
        ok: true, matched_run: true, matched_contract: true,
        mode: 'review_only', run_id: 'c03-run',
        reasons: ['staged evidence must be re-checked against OpenD at copy time'], verified_at: '2026-05-24T12:00:01',
      }
    );
    const btn = document.querySelector('.copy-ticket-btn');
    btn.click();
    await vi.waitFor(() => expect(revalidateCopy).toHaveBeenCalledTimes(1));
    expect(writeText).not.toHaveBeenCalled();
    expect(fetchRunState.mock.calls.length).toBe(1); // no auto-refresh after review_only
    await vi.waitFor(() => expect(btn.textContent).toContain('Review only'));
    vi.unstubAllGlobals();
  });

  it('writes nothing when the revalidation fetch itself fails', async () => {
    const { writeText, revalidateCopy, fetchRunState } = await renderWith(
      candidate,
      { ok: true, matched_run: true, matched_contract: true, mode: 'live', run_id: 'c03-run', reasons: [] },
      () => Promise.reject(new Error('network down'))
    );
    const btn = document.querySelector('.copy-ticket-btn');
    btn.click();
    await vi.waitFor(() => expect(revalidateCopy).toHaveBeenCalledTimes(1));
    expect(writeText).not.toHaveBeenCalled();
    expect(fetchRunState.mock.calls.length).toBe(1);
    vi.unstubAllGlobals();
  });

  it('aborts and refreshes on a market close/open transition since page load', async () => {
    // The card rendered when the market was closed (intent staged); the
    // read-time revalidation now reports the market is open (mode live).
    const { writeText, revalidateCopy, fetchRunState } = await renderWith(
      { ...candidate, eligibility: { mode: 'staged', reasons: [] } },
      {
        ok: true, matched_run: true, matched_contract: true,
        mode: 'live', run_id: 'c03-run', reasons: [], verified_at: '2026-05-24T12:00:01',
      }
    );
    const btn = document.querySelector('.copy-ticket-btn');
    expect(btn.textContent).toContain('Stage ticket');
    btn.click();
    await vi.waitFor(() => expect(revalidateCopy).toHaveBeenCalledTimes(1));
    expect(writeText).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(fetchRunState.mock.calls.length).toBeGreaterThan(1));
    vi.unstubAllGlobals();
  });
});

describe('top-recommendations strategy lanes (preset rules + two sections)', () => {
  let cleanup;

  beforeEach(() => {
    vi.clearAllMocks();
    document.body.innerHTML = '';
  });

  afterEach(() => {
    if (cleanup) {
      cleanup();
      cleanup = null;
    }
    document.body.innerHTML = '';
  });

  // Lane-enabled DOM: adds the strategy-rules banner, the csp/cc lane sections
  // with top-3 + remaining containers, and the rejected-candidates list.
  function setupLaneDOM() {
    setupDOM();
    const extra = document.createElement('div');
    extra.innerHTML = `
      <div id="strategy-rules" class="d-none"><span id="strategy-rules-text"></span></div>
      <div id="csp-section">
        <div id="top-csp-cards" class="row g-3"></div>
        <details id="csp-remaining"><summary>Remaining CSP candidates (<span id="csp-remaining-count"></span>)</summary><div id="csp-remaining-list"></div></details>
      </div>
      <div id="cc-section">
        <div id="top-cc-cards" class="row g-3"></div>
        <details id="cc-remaining"><summary>Remaining CC candidates (<span id="cc-remaining-count"></span>)</summary><div id="cc-remaining-list"></div></details>
      </div>
      <div id="blocked-candidates-count"></div>
      <div id="blocked-candidates-list"></div>
    `;
    // Extend the card template with the lane-era fields under test. The
    // template's nodes live in its content fragment, not the document tree.
    const tpl = document.getElementById('recommendation-card-template');
    const cardRoot = tpl && tpl.content ? tpl.content.querySelector('.recommendation-card') : null;
    if (cardRoot) {
      cardRoot.insertAdjacentHTML('beforeend', `
        <span class="capital-velocity"></span>
        <span class="quote-age"></span>
        <span class="cc-available-shares"></span>
      `);
    }
    document.body.appendChild(extra);
  }

  const csp = (i) => ({
    rank: i, ticker: `CSP${i}`, option_type: 'PUT', signal_type: 'csp',
    strike: 90 + i, expiration: '20260515', dte: 21,
    bid_premium_per_contract: 2.5 + i * 0.5, premium_per_contract: 250 + i * 50,
    capital_velocity_per_day: 0.008 + i * 0.001,
    recommended_contracts: 1, max_contracts: 2,
    collateral: 9500 + i * 100,
    quote_age_sec: 40 + i * 10,
    event_tier: 'event_safe', quality_tier: 'qualified',
    eligibility: { mode: 'live', reasons: [] },
    wheel_decision: { event_tier: 'event_safe', quality_tier: 'qualified' },
  });

  const cc = (i) => ({
    rank: i, ticker: `CC${i}`, option_type: 'CALL', signal_type: 'covered_call',
    strike: 180 + i, expiration: '20260515', dte: 21,
    bid_premium_per_contract: 1.8 + i * 0.4, premium_per_contract: 180 + i * 40,
    capital_velocity_per_day: 0.009 + i * 0.001,
    recommended_contracts: 1, max_contracts: 3,
    available_shares: 200 + i * 50,
    quote_age_sec: 50 + i * 10,
    event_tier: 'event_safe', quality_tier: 'qualified',
    eligibility: { mode: 'live', reasons: [] },
    wheel_decision: { event_tier: 'event_safe', quality_tier: 'qualified', avg_cost: 170, if_called_return: 8.5 },
  });

  async function renderWith(envelope) {
    const { initializeTopRecommendations, loadTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );
    const { fetchRunState } = await import(
      '../../frontend/static/js/dashboard/api-run.js'
    );
    fetchRunState.mockResolvedValue(envelope);
    await initializeTopRecommendations();
    await loadTopRecommendations(true);
    await vi.dynamicImportSettled?.();
    await new Promise(r => setTimeout(r, 50));
  }

  it('renders the active preset screening rules (read-only) and both lanes', async () => {
    setupLaneDOM();
    cleanup = (await import('../../frontend/static/js/dashboard/top-recommendations.js')).cleanupTopRecommendations;

    await renderWith({
      success: true, count: 3,
      tradeable: true,
      run: { run_id: 'lane-run', market_state: 'open', status: 'ready' },
      signals: [csp(1)],
      csp_picks: [csp(1), csp(2), csp(3)],
      cc_decisions: [cc(1), cc(2), cc(3)],
      rejected: [],
      preset: {
        key: 'wheel-conservative', label: 'Wheel Conservative', version: 2,
        screener_profile: {
          csp_target_delta: 0.25, csp_delta_tolerance: 0.05,
          csp_min_dte: 30, csp_max_dte: 75, csp_preferred_dte: 45,
          csp_min_otm_pct: 5, csp_max_otm_pct: 15, call_default_otm_pct: 8,
          min_csp_buying_power: 0, max_buying_power_pct_per_csp: 20,
          min_premium_per_contract: 50, min_mid_price: 0.4,
          max_spread_pct: 30, min_open_interest: 100, require_cash_fit: true,
        },
      },
      generated_at: '2026-05-24T12:00:00',
    });

    const ruleEl = document.getElementById('strategy-rules');
    expect(ruleEl.classList.contains('d-none')).toBe(false);
    const text = document.getElementById('strategy-rules-text').textContent;
    expect(text).toContain('WHEEL CONSERVATIVE v2');
    expect(text).toContain('CSP Δ 0.25 ±0.05');
    expect(text).toContain('CSP DTE 30-75 (pref 45)');
    expect(text).toContain('CSP OTM 5-15%');
    expect(text).toContain('CC OTM 8%');
    expect(text).toContain('min premium $50.00');
    expect(text).toContain('≤20% buying power per CSP');
    expect(text).toContain('max spread 30%');
    expect(text).toContain('min OI 100');
    expect(text).toContain('cash-fit required');

    const cspSection = document.getElementById('csp-section');
    const ccSection = document.getElementById('cc-section');
    expect(cspSection.classList.contains('d-none')).toBe(false);
    expect(ccSection.classList.contains('d-none')).toBe(false);
    expect(document.getElementById('top-csp-cards').querySelectorAll('.recommendation-card').length).toBe(3);
    expect(document.getElementById('top-cc-cards').querySelectorAll('.recommendation-card').length).toBe(3);
  });

  it('keeps both lanes always visible and lists remaining candidates below each', async () => {
    setupLaneDOM();
    cleanup = (await import('../../frontend/static/js/dashboard/top-recommendations.js')).cleanupTopRecommendations;

    await renderWith({
      success: true, count: 3,
      tradeable: true,
      run: { run_id: 'lane-run', market_state: 'open', status: 'ready' },
      signals: [csp(1)],
      csp_picks: [csp(1), csp(2), csp(3), csp(4), csp(5)],
      cc_decisions: [cc(1), cc(2)],
      rejected: [],
      preset: { label: 'Wheel Conservative', screener_profile: { csp_target_delta: 0.3 } },
      generated_at: '2026-05-24T12:00:00',
    });

    // CSP has 5 candidates: 3 top cards + 2 remaining; CC has 2: top only.
    expect(document.getElementById('top-csp-cards').querySelectorAll('.recommendation-card').length).toBe(3);
    expect(document.getElementById('top-cc-cards').querySelectorAll('.recommendation-card').length).toBe(2);

    const cspRemaining = document.getElementById('csp-remaining');
    expect(cspRemaining.classList.contains('d-none')).toBe(false);
    expect(cspRemaining.open).toBe(false);
    expect(document.getElementById('csp-remaining-count').textContent).toBe('2');
    const remainingRows = document.getElementById('csp-remaining-list').querySelectorAll('.lane-candidate-row');
    expect(remainingRows.length).toBe(2);
    expect(remainingRows[0].textContent).toContain('CSP4');
    expect(remainingRows[1].textContent).toContain('CSP5');

    // Every remaining candidate carries its own copy button.
    remainingRows.forEach(row => expect(row.querySelector('.copy-ticket-btn')).toBeTruthy());

    // CC lane has nothing remaining -> list hidden, not shown empty.
    expect(document.getElementById('cc-remaining').classList.contains('d-none')).toBe(true);
  });

  it('shows executable bid, ROI/day, collateral vs shares, qty, quote age, and event per candidate', async () => {
    setupLaneDOM();
    cleanup = (await import('../../frontend/static/js/dashboard/top-recommendations.js')).cleanupTopRecommendations;

    await renderWith({
      success: true, count: 2,
      tradeable: true,
      run: { run_id: 'lane-run', market_state: 'open', status: 'ready' },
      signals: [csp(1)],
      csp_picks: [csp(1), csp(2), csp(3), csp(4)],
      cc_decisions: [cc(1)],
      rejected: [],
      preset: { label: 'Wheel', screener_profile: {} },
      generated_at: '2026-05-24T12:00:00',
    });

    // CSP top card: executable bid premium + capital velocity + quote age.
    const card = document.querySelector('#top-csp-cards .recommendation-card');
    expect(card.querySelector('.premium-amount').textContent).toBe('$3.00');
    expect(card.querySelector('.capital-velocity').textContent).toContain('% / day');
    expect(card.querySelector('.quote-age').textContent).toBe('just now');

    const rows = document.querySelectorAll('#csp-remaining-list .lane-candidate-row');
    const row = rows[0];
    expect(row.textContent).toContain('CSP4');
    expect(row.textContent).toContain('Bid');
    expect(row.textContent).toContain('$4.50');
    expect(row.textContent).toContain('ROI/day');
    expect(row.textContent).toContain('%/day');
    expect(row.textContent).toContain('Collateral');
    expect(row.textContent).toContain('$9900.00');
    expect(row.textContent).toContain('Qty');
    expect(row.textContent).toContain('1');
    expect(row.textContent).toContain('Quote age');
    expect(row.textContent).toContain('m ago');

    const ccCard = document.querySelector('#top-cc-cards .recommendation-card');
    expect(ccCard.querySelector('.cc-available-shares').textContent).toContain('sh');
  });

  it('renders the full rejection explanations from the rejected list', async () => {
    setupLaneDOM();
    cleanup = (await import('../../frontend/static/js/dashboard/top-recommendations.js')).cleanupTopRecommendations;

    await renderWith({
      success: true, count: 1,
      tradeable: true,
      run: { run_id: 'lane-run', market_state: 'open', status: 'ready' },
      signals: [csp(1)],
      csp_picks: [csp(1)],
      cc_decisions: [],
      rejected: [
        { ticker: 'XYZ', ticker_count: 3, reason_code: 'low_premium', reason_text: 'Bid below the preset minimum', signal_type: 'csp' },
        { ticker: 'ABC', ticker_count: 1, reason_code: 'cash_fit', reason_text: 'Does not fit available cash', signal_type: 'cc' },
      ],
      preset: { label: 'Wheel', screener_profile: {} },
      generated_at: '2026-05-24T12:00:00',
    });

    const section = document.getElementById('blocked-candidates-section');
    expect(section.classList.contains('d-none')).toBe(false);
    expect(document.getElementById('blocked-candidates-count').textContent).toBe('4');
    const list = document.getElementById('blocked-candidates-list');
    expect(list.textContent).toContain('XYZ');
    expect(list.textContent).toContain('3 tickers');
    expect(list.textContent).toContain('Bid below the preset minimum');
    expect(list.textContent).toContain('ABC');
    expect(list.textContent).toContain('Does not fit available cash');
  });

  it('copies a ticket from a remaining (out-of-shortlist) lane candidate', async () => {
    setupLaneDOM();
    cleanup = (await import('../../frontend/static/js/dashboard/top-recommendations.js')).cleanupTopRecommendations;

    const { revalidateCopy } = await import('../../frontend/static/js/dashboard/api-run.js');
    revalidateCopy.mockResolvedValue({
      ok: true, matched_run: true, matched_contract: true, mode: 'live',
      run_id: 'lane-run', reasons: [], verified_at: '2026-05-24T12:00:01',
    });

    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });

    await renderWith({
      success: true, count: 2,
      tradeable: true,
      run: { run_id: 'lane-run', market_state: 'open', status: 'ready' },
      signals: [csp(1)],
      csp_picks: [csp(1), csp(2), csp(3), csp(4)],
      cc_decisions: [],
      rejected: [],
      preset: { label: 'Wheel', screener_profile: {} },
      generated_at: '2026-05-24T12:00:00',
    });

    const row = document.querySelector('#csp-remaining-list .lane-candidate-row');
    expect(row.textContent).toContain('CSP4');
    const btn = row.querySelector('.copy-ticket-btn');
    expect(btn.disabled).toBe(false);
    btn.click();
    await vi.waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const text = writeText.mock.calls[0][0];
    expect(text).toContain('SELL TO OPEN CSP');
    expect(text).toContain('CSP4');
    expect(text).toContain('x1');
    vi.unstubAllGlobals();
  });
});

describe('saved run reproduction (3 CSP + 4 CC + 25 rejected)', () => {
  let cleanup;

  beforeEach(() => {
    vi.clearAllMocks();
    document.body.innerHTML = '';
  });

  afterEach(() => {
    if (cleanup) {
      cleanup();
      cleanup = null;
    }
    document.body.innerHTML = '';
  });

  // Lane-enable the DOM exactly like the existing strategy-lanes block.
  function setupLaneDOM() {
    setupDOM();
    const extra = document.createElement('div');
    extra.innerHTML = `
      <div id="strategy-rules" class="d-none"><span id="strategy-rules-text"></span></div>
      <div id="csp-section">
        <div id="top-csp-cards" class="row g-3"></div>
        <details id="csp-remaining"><summary>Remaining CSP candidates (<span id="csp-remaining-count"></span>)</summary><div id="csp-remaining-list"></div></details>
      </div>
      <div id="cc-section">
        <div id="top-cc-cards" class="row g-3"></div>
        <details id="cc-remaining"><summary>Remaining CC candidates (<span id="cc-remaining-count"></span>)</summary><div id="cc-remaining-list"></div></details>
      </div>
      <div id="blocked-candidates-count"></div>
      <div id="blocked-candidates-list"></div>
    `;
    const tpl = document.getElementById('recommendation-card-template');
    const cardRoot = tpl && tpl.content ? tpl.content.querySelector('.recommendation-card') : null;
    if (cardRoot) {
      cardRoot.insertAdjacentHTML('beforeend', `
        <span class="capital-velocity"></span>
        <span class="quote-age"></span>
        <span class="cc-available-shares"></span>
      `);
    }
    document.body.appendChild(extra);
  }

  const cspPick = (i) => ({
    rank: i, ticker: `CSP${i}`, option_type: 'PUT', signal_type: 'csp',
    strike: 90 + i, expiration: '20260619', dte: 21,
    bid_premium_per_contract: 2.5 + i * 0.5, premium_per_contract: 250 + i * 50,
    capital_velocity_per_day: 0.008 + i * 0.001,
    recommended_contracts: 1, max_contracts: 2, collateral: 9500 + i * 100,
    quote_age_sec: 40 + i * 10,
    event_tier: 'event_safe', quality_tier: 'qualified',
    eligibility: { mode: 'live', reasons: [] },
    wheel_decision: { event_tier: 'event_safe', quality_tier: 'qualified' },
  });

  const ccPick = (i) => ({
    rank: i, ticker: `CC${i}`, option_type: 'CALL', signal_type: 'covered_call',
    strike: 180 + i, expiration: '20260619', dte: 21,
    bid_premium_per_contract: 1.8 + i * 0.4, premium_per_contract: 180 + i * 40,
    capital_velocity_per_day: 0.009 + i * 0.001,
    recommended_contracts: 1, max_contracts: 3, available_shares: 300,
    quote_age_sec: 50 + i * 10,
    event_tier: 'event_safe', quality_tier: 'qualified',
    eligibility: { mode: 'live', reasons: [] },
    wheel_decision: { event_tier: 'event_safe', quality_tier: 'qualified', avg_cost: 170, if_called_return: 8.5 },
  });

  const rejectedEntry = (i) => ({
    ticker: `REJ${String(i).padStart(2, '0')}`,
    ticker_count: 1,
    reason_code: `reject_${String(i).padStart(2, '0')}`,
    reason_text: `No OTM strike fits the active preset (explanation ${i})`,
    signal_type: 'csp',
  });

  const savedRunEnvelope = (overrides = {}) => ({
    success: true, count: 3, tradeable: true,
    run: { run_id: 'saved-run', market_state: 'open', status: 'ready' },
    signals: [ccPick(1)],
    csp_picks: [cspPick(1), cspPick(2), cspPick(3)],
    cc_decisions: [ccPick(1), ccPick(2), ccPick(3), ccPick(4)],
    rejected: Array.from({ length: 25 }, (_, i) => rejectedEntry(i + 1)),
    preset: { label: 'Wheel Conservative', screener_profile: { csp_target_delta: 0.3 } },
    generated_at: '2026-05-24T12:00:00',
    ...overrides,
  });

  async function renderWith(envelope) {
    const { initializeTopRecommendations, loadTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );
    const { fetchRunState } = await import(
      '../../frontend/static/js/dashboard/api-run.js'
    );
    fetchRunState.mockResolvedValue(envelope);
    await initializeTopRecommendations();
    await loadTopRecommendations(true);
    await vi.dynamicImportSettled?.();
    await new Promise(r => setTimeout(r, 50));
  }

  it('renders both strategy sections at the saved-run counts (3 CSP / 4 CC)', async () => {
    setupLaneDOM();
    cleanup = (await import('../../frontend/static/js/dashboard/top-recommendations.js')).cleanupTopRecommendations;
    await renderWith(savedRunEnvelope());

    const cspSection = document.getElementById('csp-section');
    const ccSection = document.getElementById('cc-section');
    expect(cspSection.classList.contains('d-none')).toBe(false);
    expect(ccSection.classList.contains('d-none')).toBe(false);
    // Cards always render top-3; lane extras fall into the per-lane remaining
    // list (CC4 is the 4th decision, so it lives below the top-3 cards).
    expect(document.getElementById('top-csp-cards').querySelectorAll('.recommendation-card').length).toBe(3);
    expect(document.getElementById('top-cc-cards').querySelectorAll('.recommendation-card').length).toBe(3);
    expect(document.getElementById('cc-remaining').classList.contains('d-none')).toBe(false);
    expect(document.getElementById('cc-remaining-list').querySelectorAll('.lane-candidate-row').length).toBe(1);
    expect(document.getElementById('cc-remaining-list').textContent).toContain('CC4');
  });

  it('exposes all 25 rejection explanations, not a top-3 truncation', async () => {
    setupLaneDOM();
    cleanup = (await import('../../frontend/static/js/dashboard/top-recommendations.js')).cleanupTopRecommendations;
    await renderWith(savedRunEnvelope());

    const section = document.getElementById('blocked-candidates-section');
    expect(section.classList.contains('d-none')).toBe(false);
    expect(document.getElementById('blocked-candidates-count').textContent).toBe('25');
    const rows = document.querySelectorAll('#blocked-candidates-list > div');
    expect(rows.length).toBe(25);
    const listText = document.getElementById('blocked-candidates-list').textContent;
    expect(listText).toContain('No OTM strike fits the active preset (explanation 1)');
    expect(listText).toContain('No OTM strike fits the active preset (explanation 25)');
    expect(listText).toContain('REJ01');
    expect(listText).toContain('REJ25');
  });

  it('renders an em-dash for unavailable candidate values, never a zero', async () => {
    setupLaneDOM();
    cleanup = (await import('../../frontend/static/js/dashboard/top-recommendations.js')).cleanupTopRecommendations;
    const missing = cspPick(4);
    delete missing.capital_velocity_per_day;
    delete missing.collateral;
    const missingCC = ccPick(5);
    delete missingCC.available_shares;
    delete missingCC.capital_velocity_per_day;

    await renderWith(
      savedRunEnvelope({
        csp_picks: [cspPick(1), cspPick(2), cspPick(3), missing],
        cc_decisions: [ccPick(1), ccPick(2), ccPick(3), ccPick(4), missingCC],
      }),
    );

    const cspRow = document.querySelector('#csp-remaining-list .lane-candidate-row');
    expect(cspRow.textContent).toContain('CSP4');
    expect(cspRow.textContent).toContain('ROI/day');
    expect(cspRow.textContent).toContain('—');
    expect(cspRow.textContent).toContain('Collateral');
    expect(cspRow.querySelectorAll('strong').length).toBeGreaterThan(0);
    // The missing fields render the em-dash, not a fabricated 0.00 / 0 sh.
    expect(cspRow.textContent).not.toMatch(/\$0\.00/);

    const ccRows = document.querySelectorAll('#cc-remaining-list .lane-candidate-row');
    const ccRow = ccRows[ccRows.length - 1]; // CCPick5 is the last remaining row
    expect(ccRow.textContent).toContain('CC5');
    expect(ccRow.textContent).toContain('Shares');
    expect(ccRow.textContent).toContain('—');
    expect(ccRow.textContent).not.toMatch(/0 sh/);
  });

  it('never renders execution-capable controls on the saved run', async () => {
    setupLaneDOM();
    cleanup = (await import('../../frontend/static/js/dashboard/top-recommendations.js')).cleanupTopRecommendations;
    await renderWith(savedRunEnvelope());

    const executionNeedles = [
      /place order/i, /apply to order/i, /modify order/i, /cancel order/i,
      /buy to open/i, /sell to open/i, /unlock/i, /submit order/i,
    ];
    const allButtons = [...document.querySelectorAll('button')];
    const executionButtons = allButtons.filter((b) =>
      executionNeedles.some((re) => re.test(`${b.textContent} ${b.title || ''} ${b.getAttribute('aria-label') || ''}`)),
    );
    expect(executionButtons).toHaveLength(0);
    // Inside every signal surface the only actionable controls are the
    // copy-to-ticket and the owner-recorded taken link (a local journal write).
    // No order-capable control may appear, and no action label may use an
    // execution verb. Button titles are excluded from the verb check because
    // they legitimately carry broker vocabulary (e.g. "buying power").
    const copySurfaces = [
      '#top-csp-cards',
      '#top-cc-cards',
      '#csp-remaining-list',
      '#cc-remaining-list',
      '#blocked-candidates-list',
    ];
    const copySurfaceButtons = copySurfaces.flatMap((sel) =>
      [...document.querySelectorAll(`${sel} button`)],
    );
    expect(copySurfaceButtons.length).toBeGreaterThan(0);
    const allowedControls = ['copy-ticket-btn', 'mark-taken-btn'];
    copySurfaceButtons.forEach((b) => {
      expect(allowedControls.some((cls) => b.classList.contains(cls))).toBe(true);
    });
    expect(document.querySelectorAll('.mark-taken-btn').length).toBeGreaterThan(0);
    const executionVerbs = ['place', 'buy', 'sell', 'cancel', 'modify', 'unlock', 'submit', 'execute'];
    copySurfaceButtons.forEach((b) => {
      const label = String(b.textContent || '').toLowerCase();
      executionVerbs.forEach((verb) => expect(label).not.toContain(verb));
    });
  });
});

describe('top-recommendations owner-recorded taken link', () => {
  let cleanup;
  const flush = () => new Promise((r) => setTimeout(r, 10));

  const SIGNAL = {
    ticker: 'SOXL',
    option_type: 'PUT',
    strike: 100.0,
    expiration: '20260918',
    dte: 19,
    bid: 6.2,
    ask: 6.4,
    mid_price: 6.3,
    annualized_return: 119.1,
    capital_velocity_per_day: 0.00326,
    delta: -0.295,
    otm_pct: 10.19,
    signal_type: 'csp',
  };

  beforeEach(() => {
    vi.clearAllMocks();
    setupDOM();
  });

  afterEach(() => {
    if (cleanup) {
      cleanup();
      cleanup = null;
    }
    document.body.innerHTML = '';
  });

  async function renderTakenCard(takenLinks = []) {
    const { initializeTopRecommendations, cleanupTopRecommendations, loadTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );
    cleanup = cleanupTopRecommendations;
    const api = await import('../../frontend/static/js/dashboard/api-run.js');
    api.fetchRunState.mockResolvedValue({
      success: true,
      count: 1,
      signals: [SIGNAL],
      run: { run_id: 'run-1', status: 'ready' },
      taken_links: takenLinks,
      generated_at: '2026-09-20T00:00:00',
    });
    await initializeTopRecommendations();
    await loadTopRecommendations(true);
    await vi.dynamicImportSettled?.();
    await flush();
    return {
      api,
      btn: document.querySelector('.mark-taken-btn'),
      input: document.querySelector('.traded-strike-input'),
      status: document.querySelector('.taken-status'),
    };
  }

  it('renders the taken state from the run payload, so it survives a reload', async () => {
    const { btn, status } = await renderTakenCard([
      {
        run_id: 'run-1',
        recommendation: { ticker: 'SOXL', option_type: 'PUT', expiration: '20260918', strike: 100 },
        traded: null,
      },
    ]);

    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toContain('Taken');
    expect(status.textContent).toContain('Linked to this recommendation');
  });

  it('records the recommended contract when no traded strike was entered', async () => {
    const { api, btn, status } = await renderTakenCard([]);
    api.markRecommendationTaken.mockResolvedValue({ ok: true, idempotent: false });

    btn.click();
    await flush();

    expect(api.markRecommendationTaken).toHaveBeenCalledTimes(1);
    const payload = api.markRecommendationTaken.mock.calls[0][0];
    expect(payload).toEqual({
      run_id: 'run-1',
      ticker: 'SOXL',
      option_type: 'PUT',
      expiration: '20260918',
      strike: 100,
    });
    // Nothing is claimed about the traded contract when the owner did not say.
    expect(payload).not.toHaveProperty('traded');
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toContain('Taken');
    expect(status.textContent).toContain('Linked to this recommendation');
  });

  it('carries the traded strike when it differs from the suggestion', async () => {
    const { api, btn, input } = await renderTakenCard([]);
    api.markRecommendationTaken.mockResolvedValue({ ok: true, idempotent: false });
    input.value = '106';

    btn.click();
    await flush();

    const payload = api.markRecommendationTaken.mock.calls[0][0];
    expect(payload.traded).toEqual({
      ticker: 'SOXL',
      option_type: 'PUT',
      expiration: '20260918',
      strike: 106,
    });
    expect(payload.strike).toBe(100);
  });

  it('shows a rejected link on the card instead of swallowing it', async () => {
    const { api, btn, status } = await renderTakenCard([]);
    api.markRecommendationTaken.mockRejectedValue(new Error('run_id does not match the published run'));

    btn.click();
    await flush();

    expect(status.textContent).toContain('Not recorded');
    expect(status.textContent).toContain('run_id does not match the published run');
    expect(status.classList.contains('text-danger')).toBe(true);
    // The control stays usable and the card never claims a link that failed.
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toContain('Mark taken');
  });
});
