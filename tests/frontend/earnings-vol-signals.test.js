import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../frontend/static/js/dashboard/options-table-rendering.js', () => ({
  showPanelLoading: vi.fn(() => 'banner-1'),
  finishPanelLoading: vi.fn(),
  failPanelLoading: vi.fn(),
}));

vi.mock('../../frontend/static/js/utils/state-model.js', () => ({
  default: {
    showEmpty: vi.fn(),
    showError: vi.fn(),
  },
}));

import StateModel from '../../frontend/static/js/utils/state-model.js';

function setupDOM() {
  document.body.innerHTML = `
    <section class="app-section earnings-vol-stage" id="earnings-vol-signals-section">
      <div class="app-section__header">
        <div>
          <span class="app-section__eyebrow">Read-only signal lab</span>
          <h2>Earnings vol signals</h2>
        </div>
        <div class="app-section__actions">
          <small id="earnings-vol-last-updated" class="text-muted d-none"></small>
          <button id="refresh-earnings-vol-signals" class="btn btn-outline-primary btn-sm" type="button">Refresh</button>
        </div>
      </div>
      <div id="earnings-vol-state"></div>
      <div id="earnings-vol-content" class="earnings-vol-grid d-none"></div>
    </section>
    <template id="earnings-vol-card-template">
      <article class="earnings-vol-card">
        <strong class="earnings-vol-card__ticker"></strong>
        <small class="earnings-vol-card__date text-muted"></small>
        <small class="earnings-vol-card__source text-muted"></small>
        <span class="earnings-vol-card__signal badge"></span>
        <span class="earnings-vol-card__score-value"></span>
        <div class="earnings-vol-card__structure"></div>
        <div class="earnings-vol-card__strike"></div>
        <div class="earnings-vol-card__sell"></div>
        <div class="earnings-vol-card__buy"></div>
        <div class="earnings-vol-card__debit"></div>
        <div class="earnings-vol-card__entry"></div>
        <div class="earnings-vol-card__exit"></div>
        <div class="earnings-vol-card__target"></div>
        <div class="earnings-vol-card__cut"></div>
        <div class="earnings-vol-card__iv"></div>
        <div class="earnings-vol-card__ivrv"></div>
        <div class="earnings-vol-card__spread"></div>
        <div class="earnings-vol-card__risk"></div>
        <div class="earnings-vol-card__notes"></div>
      </article>
    </template>
  `;
}

describe('earnings-vol empty state diagnostics', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupDOM();
  });

  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('includes scanned count in empty state message', async () => {
    const { loadEarningsVolSignals } = await import(
      '../../frontend/static/js/dashboard/earnings-vol-signals.js'
    );

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        success: true,
        signals: [],
        count: 0,
        scanned: 12,
        errors: [],
        generated_at: '2026-05-24T12:00:00',
      }),
    });

    await loadEarningsVolSignals(true);
    await new Promise(r => setTimeout(r, 50));

    const emptyCall = StateModel.showEmpty.mock.calls.find(c => c[0] === 'earnings-vol-state');
    expect(emptyCall).toBeDefined();
    const message = emptyCall[1];
    expect(message).toContain('Scanned 12 tickers');
    expect(message).not.toContain('error');

    fetchSpy.mockRestore();
  });

  it('includes error diagnostics when some tickers failed', async () => {
    const { loadEarningsVolSignals } = await import(
      '../../frontend/static/js/dashboard/earnings-vol-signals.js'
    );

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        success: true,
        signals: [],
        count: 0,
        scanned: 15,
        errors: ['TSLA', 'META'],
        generated_at: '2026-05-24T12:00:00',
      }),
    });

    await loadEarningsVolSignals(true);
    await new Promise(r => setTimeout(r, 50));

    const emptyCall = StateModel.showEmpty.mock.calls.find(c => c[0] === 'earnings-vol-state');
    expect(emptyCall).toBeDefined();
    const message = emptyCall[1];
    expect(message).toContain('Scanned 15 tickers');
    expect(message).toContain('2 errors');
    expect(message).toContain('TSLA');
    expect(message).toContain('META');

    fetchSpy.mockRestore();
  });

  it('shows simple empty message when no scanned data available', async () => {
    const { loadEarningsVolSignals } = await import(
      '../../frontend/static/js/dashboard/earnings-vol-signals.js'
    );

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        success: true,
        signals: [],
        count: 0,
        generated_at: '2026-05-24T12:00:00',
      }),
    });

    await loadEarningsVolSignals(true);
    await new Promise(r => setTimeout(r, 50));

    const emptyCall = StateModel.showEmpty.mock.calls.find(c => c[0] === 'earnings-vol-state');
    expect(emptyCall).toBeDefined();
    const message = emptyCall[1];
    expect(message).toContain('No earnings-vol signals found');

    fetchSpy.mockRestore();
  });

  it('includes dominant blocker reasons in the empty-state message', async () => {
    const { loadEarningsVolSignals } = await import(
      '../../frontend/static/js/dashboard/earnings-vol-signals.js'
    );

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        success: true,
        signals: [],
        count: 0,
        scanned: 8,
        blocker_counts: {
          'Missing front/back IV': 6,
          'Options spread is too wide': 2,
        },
        errors: [],
        generated_at: '2026-05-24T12:00:00',
      }),
    });

    await loadEarningsVolSignals(true);
    await new Promise(r => setTimeout(r, 50));

    const emptyCall = StateModel.showEmpty.mock.calls.find(c => c[0] === 'earnings-vol-state');
    expect(emptyCall).toBeDefined();
    const message = emptyCall[1];
    expect(message).toContain('Scanned 8 tickers');
    expect(message).toContain('6 missing front/back iv');
    expect(message).toContain('2 options spread is too wide');

    fetchSpy.mockRestore();
  });

  it('renders both blockers and notes on a watch card', async () => {
    vi.resetModules();
    const { loadEarningsVolSignals } = await import(
      '../../frontend/static/js/dashboard/earnings-vol-signals.js'
    );

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        success: true,
        signals: [{
          ticker: 'NVDA',
          signal: 'WATCH',
          label: 'Watch',
          score: 42,
          earnings_date: '2026-06-20',
          days_to_earnings: 4,
          front_iv: 0.72,
          back_iv: 0.58,
          iv_rv_ratio: 1.9,
          spread_pct: 12.5,
          max_risk_per_contract: 250,
          structure: 'ATM calendar',
          front_expiration: '2026-06-19',
          back_expiration: '2026-07-17',
          estimated_calendar_debit: 2.5,
          entry_plan: 'Enter while front IV premium is positive',
          exit_plan: 'Close after earnings IV crush',
          profit_target: 'Target 20-40% where liquidity allows',
          invalidation: 'Spread widens',
          blockers: ['Missing front/back IV'],
          notes: ['Options spread is acceptable but not tight'],
          generated_at: '2026-05-24T12:00:00',
        }],
        count: 1,
        scanned: 3,
        errors: [],
        generated_at: '2026-05-24T12:00:00',
      }),
    });

    await loadEarningsVolSignals(true);
    await vi.waitFor(() => {
      expect(document.querySelectorAll('.earnings-vol-card').length).toBe(1);
    });

    const notes = document.querySelector('.earnings-vol-card__notes');
    expect(notes.textContent).toContain('Missing front/back IV');
    expect(notes.textContent).toContain('Options spread is acceptable but not tight');

    fetchSpy.mockRestore();
  });
});
