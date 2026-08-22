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
];

function setupDOM() {
  const els = {};
  for (const id of ELEMENT_IDS) {
    els[id] = { textContent: '', className: '' };
  }
  vi.stubGlobal('document', { getElementById: (id) => els[id] || null });
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

  it('renders the oldest valid quote age against the freshness window', async () => {
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

    expect(els['run-freshness'].textContent).toContain('quote age');
    expect(els['run-freshness'].textContent).toContain('(max 300s)');
    expect(els['run-freshness'].textContent).not.toContain('NaN');
  });
});
