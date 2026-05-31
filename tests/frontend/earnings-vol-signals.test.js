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
});
