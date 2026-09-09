import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../frontend/static/js/dashboard/api-run.js', () => ({
  fetchRunState: vi.fn(),
  refreshRun: vi.fn(),
  revalidateCopy: vi.fn(),
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
        <span class="score-badge"></span>
        <span class="confidence-badge"></span>
        <span class="underlying-quality-badge"></span>
        <span class="research-only-badge"></span>
        <span class="signal-data-source"></span>
        <button type="button" class="btn btn-sm copy-ticket-btn">Copy ticket</button>
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
        <div class="score-drivers d-none">
          <span class="score-drivers__positive"></span>
          <span class="score-drivers__negative"></span>
        </div>
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
          score: 65.0,
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
    // Regression: score 75 maps to multi-token 'bg-warning text-dark'; both
    // tokens must be applied (classList.add chokes on the space, addClassTokens does not).
    const scoreBadgeEl = cards[0].querySelector('.score-badge');
    expect(scoreBadgeEl.classList.contains('bg-warning')).toBe(true);
    expect(scoreBadgeEl.classList.contains('text-dark')).toBe(true);
    expect(cards[0].querySelectorAll('.recommendation-detail-row').length).toBe(4);
    expect(cards[0].querySelector('.csp-details')?.classList.contains('d-none')).toBe(false);
    expect(cards[0].querySelector('.cc-details')?.classList.contains('d-none')).toBe(true);
    expect(cards[0].querySelector('.score-drivers')?.classList.contains('d-none')).toBe(true);
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
      score: 65.0,
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
      score: 65.0,
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
      score: 65.0,
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
      score: 65.0,
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
        score: 88,
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
        score_details: {},
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
