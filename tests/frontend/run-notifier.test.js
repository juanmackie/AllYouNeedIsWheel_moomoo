import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// C10 regression: one bounded run-state poll adopts a newly published run and
// notifies every panel WITHOUT issuing another refresh POST (exactly one
// broker scan per manual refresh, and the notifier itself never scans).

const POLL_INTERVAL_MS = 2000;

async function loadModule() {
  return await import('../../frontend/static/js/dashboard/run-notifier.js');
}

function stubRunFetch(payloadSeq) {
  // payloadSeq: array of /api/run payloads returned in order; last one repeats.
  let calls = 0;
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    if (url === '/api/run') {
      const idx = Math.min(calls, payloadSeq.length - 1);
      calls += 1;
      return { ok: true, json: async () => payloadSeq[idx] };
    }
    return { ok: false, json: async () => ({}) };
  }));
}

const RUN = (runId, state, extra = {}) => ({
  attempt: { state },
  snapshot: {
    tradeable: true,
    run: { run_id: runId, status: 'ready', market_state: 'open', ...extra },
  },
});

describe('run-notifier bounded run-state poll (C10)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('calls the adopt handler once when run_id changes', async () => {
    const { startRunWatcher, stopRunWatcher, onRunAdopted } = await loadModule();
    const adopted = vi.fn();
    onRunAdopted(adopted);

    // First observation establishes baseline run AAA; next becomes run BBB.
    stubRunFetch([RUN('aaa', 'refreshing'), RUN('bbb', 'succeeded')]);

    startRunWatcher();
    // Poll #1 (baseline), Poll #2 (new run => adopt).
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);

    expect(adopted).toHaveBeenCalledTimes(1);
    expect(adopted).toHaveBeenCalledWith('bbb');

    stopRunWatcher();
    onRunAdopted(null);
  });

  it('never POSTs a refresh — the notifier only reads /api/run', async () => {
    const { startRunWatcher, stopRunWatcher, onRunAdopted } = await loadModule();
    const adopted = vi.fn();
    onRunAdopted(adopted);

    stubRunFetch([RUN('aaa', 'refreshing'), RUN('bbb', 'succeeded')]);

    startRunWatcher();
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);

    const posted = global.fetch.mock.calls.filter(([url, opts]) =>
      typeof url === 'string' && url.includes('/refresh')
    );
    expect(posted).toHaveLength(0);
    // Only GET-style reads of /api/run (no method implies default GET).
    global.fetch.mock.calls.forEach(([url, opts]) => {
      expect((opts || {}).method ?? 'GET').toBe('GET');
    });

    stopRunWatcher();
    onRunAdopted(null);
  });

  it('stops polling after the bounded budget when no new run appears', async () => {
    const { startRunWatcher, stopRunWatcher, onRunAdopted } = await loadModule();
    const adopted = vi.fn();
    onRunAdopted(adopted);

    // Refresh stays in-progress forever with the same run_id: never adopts,
    // but must stop on its own after the bounded window (~30 polls).
    stubRunFetch([RUN('aaa', 'refreshing')]);

    startRunWatcher();
    for (let i = 0; i < 40; i++) {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    }

    expect(adopted).not.toHaveBeenCalled();
    // Bounded: fewer than 40 fetches occurred despite 40 timer ticks.
    const runReads = global.fetch.mock.calls.filter(([url]) => url === '/api/run');
    expect(runReads.length).toBeLessThan(40);
    expect(runReads.length).toBeGreaterThan(0);

    stopRunWatcher();
    onRunAdopted(null);
  });
});
