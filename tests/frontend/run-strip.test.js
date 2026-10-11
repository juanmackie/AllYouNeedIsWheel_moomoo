import { describe, it, expect, vi, afterEach } from 'vitest';

// Regression coverage for the operational strip's freshness rendering.
// Empty quote_fetched_at (market closed / unscanned symbols) must render a
// truthful "quote stale" label — never "NaNs".

const ELEMENT_IDS = [
  'run-env',
  'run-preset',
  'run-snapshot-preset',
  'run-market',
  'run-status',
  'run-last-success',
  'run-coverage',
  'run-freshness',
  'run-refresh-btn',
  'run-progress-stage',
  'run-progress-elapsed',
  'run-progress-updated',
  'run-progress-message',
  'run-warning-title',
  'run-warning-reason',
  'run-warning-action',
];

function setupDOM() {
  document.body.innerHTML = ELEMENT_IDS.map((id) =>
    id === 'run-refresh-btn' ? `<button id="${id}">Refresh run</button>` : `<span id="${id}"></span>`
  ).join('') + '<div id="run-progress-details" hidden></div><div id="run-warning-banner" class="alert d-none" role="alert"></div>';
  const els = {};
  for (const id of ELEMENT_IDS) {
    els[id] = document.getElementById(id);
  }
  els['run-progress-details'] = document.getElementById('run-progress-details');
  els['run-warning-banner'] = document.getElementById('run-warning-banner');
  for (const id of ['run-warning-title', 'run-warning-reason', 'run-warning-action']) {
    els['run-warning-banner'].append(els[id]);
  }
  return els;
}

function stubFetch(runPayload) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url) => {
      if (url === '/api/run') return { ok: true, json: async () => runPayload };
      if (url === '/api/settings') {
        return { ok: true, json: async () => ({ active: 'balanced', presets: {} }) };
      }
      return { ok: false, json: async () => ({}) };
    })
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
  vi.restoreAllMocks();
  vi.resetModules();
});

describe('run-strip freshness rendering', () => {
  it.each(['refreshing', 'failed'])('distinguishes current and published presets after a %s attempt', async (state) => {
    const els = setupDOM();
    // Current settings are owned by preset-selector; the run poll must not
    // replace this newer value with a stale second settings request.
    els['run-preset'].textContent = 'CURRENT PRESET Aggressive';
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (url === '/api/run') return { ok: true, json: async () => ({
        attempt: { state, stage: state === 'refreshing' ? 'discover' : undefined },
        snapshot: {
          tradeable: false,
          effective_status: 'stale',
          run: {
            env: 'REAL', market_state: 'closed', status: 'ready',
            coverage_scanned: 67, coverage_total: 67, quote_fetched_at: {},
          },
          preset: { key: 'balanced', label: 'Balanced', version: 7 },
        },
      }) };
      return { ok: false, json: async () => ({}) };
    }));

    const { loadRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    await loadRunStrip();

    expect(els['run-preset'].textContent).toBe('CURRENT PRESET Aggressive');
    expect(els['run-snapshot-preset'].textContent).toBe('PUBLISHED SNAPSHOT Balanced v7');
    expect(els['run-status'].textContent).toBe(state.toUpperCase());
  });

  it('shows quote stale instead of NaN when quote_fetched_at is empty (market closed)', async () => {
    const els = setupDOM();
    stubFetch({
      attempt: { state: 'succeeded' },
      snapshot: {
        tradeable: false,
        effective_status: 'planning',
        run: {
          env: 'REAL',
          market_state: 'closed',
          status: 'planning',
          published_at: '2026-08-22T06:00:00+00:00',
          coverage_scanned: 27,
          coverage_total: 27,
          quote_fetched_at: { AAPL: '', MSFT: '' },
          max_tradeable_age_sec: 300,
        },
      },
    });

    const { loadRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    await loadRunStrip();

    expect(els['run-freshness'].textContent).toBe('quote stale (market closed)');
    expect(els['run-freshness'].textContent).not.toContain('NaN');
    expect(els['run-coverage'].textContent).toBe('coverage 27/27');
  });

  it('renders the UTC fetch time and data age against the freshness window', async () => {
    const els = setupDOM();
    const now = Date.now();
    stubFetch({
      attempt: null,
      snapshot: {
        tradeable: true,
        run: {
          env: 'SIMULATE',
          market_state: 'open',
          status: 'ready',
          published_at: new Date(now).toISOString(),
          coverage_scanned: 6,
          coverage_total: 6,
          quote_fetched_at: { AAPL: new Date(now - 10_000).toISOString() },
          max_tradeable_age_sec: 300,
        },
      },
    });

    const { loadRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    await loadRunStrip();

    // Actual broker/market fetch timestamp rendered in UTC (not local "now").
    const expectedUtc = new Date(now - 10_000).toISOString().slice(11, 19);
    expect(els['run-freshness'].textContent).toContain(`fetch ${expectedUtc}Z`);
    expect(els['run-freshness'].textContent).toContain('· data');
    expect(els['run-freshness'].textContent).toContain('(max 300s)');
    expect(els['run-freshness'].textContent).not.toContain('NaN');
  });

  it('keeps the FAILED badge visible while retaining the last-good snapshot', async () => {
    const els = setupDOM();
    stubFetch({
      attempt: { state: 'failed', latest_error: 'OpenD disconnected' },
      snapshot: {
        tradeable: false,
        effective_status: 'stale',
        run: {
          env: 'REAL',
          market_state: 'open',
          status: 'stale',
          published_at: new Date(Date.now() - 60_000).toISOString(),
          coverage_scanned: 27,
          coverage_total: 27,
          quote_fetched_at: { AAPL: new Date(Date.now() - 30_000).toISOString() },
          max_tradeable_age_sec: 300,
        },
      },
    });

    const { loadRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    await loadRunStrip();

    expect(els['run-status'].textContent).toBe('FAILED');
    expect(els['run-status'].className).toContain('bg-danger');
    expect(els['run-freshness'].textContent).toMatch(/fetch \d{2}:\d{2}:\d{2}Z/);
    expect(els['run-freshness'].textContent).toContain('(max 300s)');
    expect(els['run-warning-banner'].classList.contains('d-none')).toBe(false);
    expect(els['run-warning-reason'].textContent).toBe('OpenD disconnected');
    expect(els['run-warning-action'].textContent).toMatch(/previous.*results.*shown/i);
    expect(els['run-warning-action'].textContent).toContain('Refresh run');
  });

  it('surfaces a communication failure instead of returning silently', async () => {
    const els = setupDOM();
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (url === '/api/run') return { ok: false, status: 503, json: async () => ({}) };
      if (url === '/api/settings') return { ok: true, json: async () => ({ active: 'balanced', presets: {} }) };
      return { ok: false, json: async () => ({}) };
    }));

    const { loadRunStrip, renderRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    renderRunStrip({ state: 'refreshing', stage: 'discover', started_at: new Date().toISOString() }, null);
    await loadRunStrip();

    expect(els['run-status'].textContent).toBe('COMM ERROR');
    expect(els['run-coverage'].textContent).toBe('cannot reach run API');
    expect(els['run-progress-details'].hidden).toBe(true);
    expect(els['run-warning-banner'].classList.contains('d-none')).toBe(false);
    expect(els['run-warning-action'].textContent).toMatch(/retry automatically/i);
  });

  it('reports stale broker data with a recent fetch as old data, not quote age', async () => {
    const els = setupDOM();
    const now = Date.now();
    stubFetch({
      attempt: null,
      snapshot: {
        tradeable: false,
        effective_status: 'stale',
        run: {
          env: 'REAL',
          market_state: 'open',
          status: 'stale',
          published_at: new Date(now).toISOString(),
          coverage_scanned: 27,
          coverage_total: 27,
          // Quote was fetched long ago -> large data age, but it is fetch/data
          // age, never labeled as broker quote age.
          quote_fetched_at: { AAPL: new Date(now - 3600_000).toISOString() },
          max_tradeable_age_sec: 300,
        },
      },
    });

    const { loadRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    await loadRunStrip();

    expect(els['run-freshness'].textContent).toContain('fetch');
    expect(els['run-freshness'].textContent).toMatch(/· data \d+s old/);
    expect(els['run-freshness'].textContent).not.toContain('quote age');
  });

  it('surfaces a thrown network exception as COMM ERROR without losing last-good results', async () => {
    const els = setupDOM();
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url === '/api/run') return Promise.reject(new TypeError('network down'));
      if (url === '/api/settings') return Promise.resolve({ ok: true, json: async () => ({ active: 'balanced', presets: {} }) });
      return Promise.resolve({ ok: false, json: async () => ({}) });
    }));

    const { loadRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    await loadRunStrip();

    expect(els['run-status'].textContent).toBe('COMM ERROR');
    expect(els['run-coverage'].textContent).toBe('cannot reach run API');
  });
});

const GOOD_SNAPSHOT = {
  effective_status: 'planning',
  tradeable: false,
  run: {
    run_id: 'last-good', status: 'planning', market_state: 'closed',
    coverage_scanned: 2, coverage_total: 2, coverage_complete: true,
    published_at: '2026-10-01T06:00:00Z', quote_fetched_at: {},
  },
};

describe('visible run warnings and recovery', () => {
  it('shows stage and advancing elapsed time when reported progress stays at 41%', async () => {
    const els = setupDOM();
    const { renderRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    const now = Date.parse('2026-10-11T02:00:00Z');
    const clock = vi.spyOn(Date, 'now').mockReturnValue(now);
    const attempt = {
      state: 'refreshing',
      stage: 'discover',
      progress: 0.413,
      started_at: '2026-10-11T01:58:59Z',
    };

    renderRunStrip(attempt, GOOD_SNAPSHOT);
    expect(els['run-status'].textContent).toBe('REFRESHING');
    expect(els['run-progress-details'].hidden).toBe(false);
    expect(els['run-progress-stage'].textContent).toBe('Discovering option contracts');
    expect(els['run-progress-elapsed'].textContent).toBe('1m 1s');
    expect(els['run-progress-updated'].textContent).toBe('');

    clock.mockReturnValue(now + 65_000);
    renderRunStrip(attempt, GOOD_SNAPSHOT);
    expect(els['run-status'].textContent).toBe('REFRESHING');
    expect(els['run-progress-elapsed'].textContent).toBe('2m 6s');
  });

  it('shows progress details for a first refresh before any snapshot exists', async () => {
    const els = setupDOM();
    const { renderRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    renderRunStrip({ state: 'refreshing', stage: 'portfolio', started_at: new Date(Date.now() - 3000).toISOString() }, null);

    expect(els['run-status'].textContent).toBe('REFRESHING');
    expect(els['run-progress-details'].hidden).toBe(false);
    expect(els['run-progress-stage'].textContent).toBe('Loading portfolio context');
    expect(els['run-progress-elapsed'].textContent).toBe('3s');
  });

  it('shows a failed first refresh even when there is no completed snapshot', async () => {
    const els = setupDOM();
    const { renderRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    renderRunStrip({ state: 'failed', latest_error: 'OpenD login required' }, null);
    expect(els['run-status'].textContent).toBe('FAILED');
    expect(els['run-warning-title'].textContent).toBe('Refresh failed');
    expect(els['run-warning-reason'].textContent).toBe('OpenD login required');
    expect(els['run-warning-action'].textContent).toMatch(/no completed results/i);
  });

  it('shows quota diagnostics above retained CC results and clears after full coverage', async () => {
    const els = setupDOM();
    const { renderRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    const quotaSnapshot = {
      ...GOOD_SNAPSHOT,
      run: { ...GOOD_SNAPSHOT.run, coverage_scanned: 1, coverage_complete: false },
      rejected: [{ reason_code: 'scan_infeasible', reason_text: 'CSP scan needs 180s; budget is 120s.' }],
      recommendations: [{ ticker: 'AAPL', option_type: 'call' }],
    };
    renderRunStrip({ state: 'succeeded' }, quotaSnapshot);
    expect(els['run-warning-banner'].classList.contains('d-none')).toBe(false);
    expect(els['run-warning-banner'].className).toContain('alert-warning');
    expect(els['run-warning-title'].textContent).toMatch(/CSP.*quota/i);
    expect(els['run-warning-reason'].textContent).toBe('CSP scan needs 180s; budget is 120s.');
      expect(els['run-warning-action'].textContent).toMatch(/copy.*blocked/i);
      expect(els['run-warning-action'].textContent).toMatch(/contract discovery/i);
    renderRunStrip({ state: 'refreshing', progress: 0.5 }, quotaSnapshot);
    expect(els['run-warning-banner'].classList.contains('d-none')).toBe(false);
    renderRunStrip({ state: 'succeeded' }, GOOD_SNAPSHOT);
    expect(els['run-warning-banner'].classList.contains('d-none')).toBe(true);
    expect(els['run-warning-reason'].textContent).toBe('');
    expect(els['run-status'].textContent).toBe('PLANNING');
  });

  it('renders API failure text literally and removes the warning on recovery', async () => {
    const els = setupDOM();
    const { renderRunStrip, renderRunCommunicationError } = await import('../../frontend/static/js/dashboard/run-strip.js');
    const reason = '<img src=x onerror=alert(1)> OpenD failed';
    renderRunStrip({ state: 'failed', latest_error: reason }, GOOD_SNAPSHOT);
    expect(els['run-warning-reason'].textContent).toBe(reason);
    expect(els['run-warning-banner'].querySelector('img')).toBeNull();
    renderRunCommunicationError();
    expect(els['run-warning-title'].textContent).toBe('Run status unavailable');
    renderRunStrip({ state: 'succeeded' }, GOOD_SNAPSHOT);
    expect(els['run-warning-banner'].classList.contains('d-none')).toBe(true);
  });

  it('warns on malformed run responses instead of treating them as NO RUN', async () => {
    const els = setupDOM();
    stubFetch(null);
    const { loadRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    await loadRunStrip();
    expect(els['run-status'].textContent).toBe('COMM ERROR');
    expect(els['run-warning-banner'].classList.contains('d-none')).toBe(false);
  });

  it.each(['http', 'network'])('warns when the manual refresh request fails (%s), then clears on retry', async (failure) => {
    const els = setupDOM();
    let failNext = true;
    vi.stubGlobal('fetch', vi.fn(async () => {
      if (failNext) {
        if (failure === 'network') throw new TypeError('network down');
        return { ok: false, status: 503, json: async () => ({ error: 'OpenD unavailable' }) };
      }
      return { ok: true, status: 202, json: async () => ({ attempt: { state: 'queued' } }) };
    }));
    const { initRunStrip, renderRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    const priorAttempt = { state: 'failed', latest_error: 'Earlier engine failure' };
    renderRunStrip(priorAttempt, GOOD_SNAPSHOT);
    initRunStrip();
    els['run-refresh-btn'].click();
    await vi.waitFor(() => expect(els['run-warning-title'].textContent).toBe('Refresh could not start'));
    expect(els['run-warning-reason'].textContent).toBe(failure === 'http' ? 'OpenD unavailable' : 'network down');
    expect(els['run-refresh-btn'].disabled).toBe(false);
    // A healthy read of the unchanged run is not proof that a rejected refresh worked.
    renderRunStrip(priorAttempt, GOOD_SNAPSHOT);
    expect(els['run-warning-banner'].classList.contains('d-none')).toBe(false);
    expect(els['run-warning-reason'].textContent).toBe(failure === 'http' ? 'OpenD unavailable' : 'network down');
    failNext = false;
    els['run-refresh-btn'].click();
    await vi.waitFor(() => expect(els['run-refresh-btn'].disabled).toBe(false));
    renderRunStrip({ state: 'succeeded' }, { ...GOOD_SNAPSHOT, run: { ...GOOD_SNAPSHOT.run, run_id: 'recovered' } });
    expect(els['run-warning-banner'].classList.contains('d-none')).toBe(true);
  });

  it('shows a blocking banner naming symbols with no broker data and clears it on full coverage', async () => {
    const els = setupDOM();
    const { renderRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    const partialSnapshot = {
      ...GOOD_SNAPSHOT,
      effective_status: 'partial',
      run: {
        ...GOOD_SNAPSHOT.run, run_id: 'partial-run', status: 'partial',
        coverage_scanned: 64, coverage_total: 67, coverage_complete: false,
      },
      eligibility: { coverage: { truth: 'partial', reasons: ['partial coverage (64/67 symbols; missing: AMD, LRCX, TSLA) — copy blocked'] } },
      rejected: [
        { ticker: 'AMD', reason_code: 'broker_data_unavailable', reason_text: 'Contract discovery failed (2 attempts): request timeout' },
        { ticker: 'NVDA', reason_code: 'wide_spread', reason_text: 'Spread too wide' },
        { ticker: 'LRCX', reason_code: 'broker_data_unavailable', reason_text: 'Contract discovery failed (2 attempts): request timeout' },
        { ticker: 'TSLA', reason_code: 'broker_data_unavailable', reason_text: '<b>Option quotes missing</b> for 2 of 40 contracts (2 attempts)' },
      ],
    };
    renderRunStrip({ state: 'succeeded' }, partialSnapshot);
    expect(els['run-warning-banner'].classList.contains('d-none')).toBe(false);
    expect(els['run-warning-banner'].className).toContain('alert-danger');
    expect(els['run-warning-title'].textContent).toBe('Copy blocked: broker data missing for 3 of 67 watchlist symbols');
    expect(els['run-warning-reason'].textContent).toBe(
      'AMD, LRCX: Contract discovery failed (2 attempts): request timeout; ' +
      'TSLA: <b>Option quotes missing</b> for 2 of 40 contracts (2 attempts)'
    );
    expect(els['run-warning-banner'].querySelector('b')).toBeNull();
    expect(els['run-warning-action'].textContent).toContain('Refresh run');
    expect(els['run-coverage'].textContent).toBe('coverage 64/67 INCOMPLETE');
    expect(els['run-coverage'].classList.contains('text-danger')).toBe(true);
    renderRunStrip({ state: 'succeeded' }, GOOD_SNAPSHOT);
    expect(els['run-warning-banner'].classList.contains('d-none')).toBe(true);
    expect(els['run-coverage'].textContent).toBe('coverage 2/2');
    expect(els['run-coverage'].classList.contains('text-danger')).toBe(false);
  });

  it('uses the coverage reason for a partial run saved without per-symbol rows', async () => {
    const els = setupDOM();
    const { renderRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    const coverageReason = 'partial coverage (64/67 symbols; missing: AMD, LRCX, TSLA) — copy blocked';
    renderRunStrip({ state: 'succeeded' }, {
      ...GOOD_SNAPSHOT,
      run: { ...GOOD_SNAPSHOT.run, status: 'partial', coverage_scanned: 64, coverage_total: 67, coverage_complete: false },
      eligibility: { coverage: { truth: 'partial', reasons: [coverageReason] } },
      rejected: [{ ticker: 'AMD', reason_code: 'no_cash_fit', reason_text: 'No CSP strike fits buying power' }],
    });
    expect(els['run-warning-banner'].classList.contains('d-none')).toBe(false);
    expect(els['run-warning-title'].textContent).toMatch(/^Copy blocked/);
    expect(els['run-warning-reason'].textContent).toBe(coverageReason);
  });

  it('warns without blocking when only a covered-call holding has no broker data', async () => {
    const els = setupDOM();
    const { renderRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    renderRunStrip({ state: 'succeeded' }, {
      ...GOOD_SNAPSHOT,
      eligibility: { coverage: { truth: 'complete', reasons: [] } },
      rejected: [{
        ticker: 'ORCL', signal_type: 'covered_call', reason_code: 'broker_data_unavailable',
        reason_text: 'Covered call not assessed: No options data available from any source',
      }],
    });
    expect(els['run-warning-banner'].classList.contains('d-none')).toBe(false);
    expect(els['run-warning-banner'].className).toContain('alert-warning');
    expect(els['run-warning-title'].textContent).toBe('Broker data missing for 1 symbol');
    expect(els['run-warning-reason'].textContent).toBe(
      'ORCL: Covered call not assessed: No options data available from any source'
    );
    expect(els['run-warning-action'].textContent).toMatch(/not affected/i);
  });

  it('treats an already-running refresh (409) as normal progress', async () => {
    const els = setupDOM();
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 409, json: async () => ({ attempt: { state: 'refreshing' } }) })));
    const { initRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    initRunStrip();
    els['run-refresh-btn'].click();
    await vi.waitFor(() => expect(els['run-refresh-btn'].disabled).toBe(false));
    expect(els['run-warning-banner'].classList.contains('d-none')).toBe(true);
  });
});
