import { describe, it, expect, vi, afterEach } from 'vitest';

// Regression coverage for the operational strip's freshness rendering.
// Empty quote_fetched_at (market closed / unscanned symbols) must render a
// truthful "quote stale" label — never "NaNs".

const ELEMENT_IDS = [
  'run-env',
  'run-market',
  'run-status',
  'run-last-success',
  'run-coverage',
  'run-freshness',
  'run-refresh-btn',
  'run-warning-title',
  'run-warning-reason',
  'run-warning-action',
];

function setupDOM() {
  document.body.innerHTML = ELEMENT_IDS.map((id) =>
    id === 'run-refresh-btn' ? `<button id="${id}">Refresh run</button>` : `<span id="${id}"></span>`
  ).join('') + '<div id="run-warning-banner" class="alert d-none" role="alert"></div>';
  const els = {};
  for (const id of ELEMENT_IDS) {
    els[id] = document.getElementById(id);
  }
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

    const { loadRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    await loadRunStrip();

    expect(els['run-status'].textContent).toBe('COMM ERROR');
    expect(els['run-coverage'].textContent).toBe('cannot reach run API');
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
