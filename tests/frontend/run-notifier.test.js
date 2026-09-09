import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// P1b regression coverage for the single shared run-state poll:
//   - exactly one shared poll instance (page load starts it once)
//   - one in-flight request max (slow responses never overlap)
//   - first-ever run pickup (no baseline)
//   - completion before the first tick (run already published at first fetch)
//   - publish fan-out on run_id change, without ever POSTing a refresh
//   - transient failures keep polling, never re-publish, and the strip keeps
//     its last-good render (failure-keeps-results is covered in run-strip tests)
//   - stops after confirmed idle and restarts on ensureRunStatePoll()

vi.mock('../../frontend/static/js/dashboard/run-strip.js', () => ({
  renderRunStrip: vi.fn(),
  initRunStrip: vi.fn(),
  loadRunStrip: vi.fn(),
}));

const POLL_INTERVAL_MS = 5000;

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

function countRunReads() {
  return global.fetch.mock.calls.filter(([url]) => url === '/api/run').length;
}

/** Advance fake timers by ms, then flush the microtask queue so an async tick
 *  (fetch resolution -> json -> notify) fully settles before assertions. */
async function settleTimers(ms) {
  await vi.advanceTimersByTimeAsync(ms);
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

const RUN = (runId, state, extra = {}) => ({
  attempt: { state },
  snapshot: {
    tradeable: true,
    run: { run_id: runId, status: 'ready', market_state: 'open', ...extra },
  },
});

const IDLE = { attempt: null, snapshot: null };

describe('shared run-state poll (P1b)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(async () => {
    const { stopRunStatePoll } = await loadModule();
    stopRunStatePoll();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('runs exactly one shared poll instance starting at page load', async () => {
    const { startRunStatePoll } = await loadModule();
    // Actively-refreshing run so the poll never stops mid-assertion.
    stubRunFetch([RUN('aaa', 'refreshing')]);

    startRunStatePoll();
    startRunStatePoll();
    startRunStatePoll();
    await settleTimers(0); // immediate first fetch resolves

    expect(countRunReads()).toBe(1); // three starts -> still one immediate fetch

    // Two intervals -> two more ticks. Three concurrent instances would have
    // made 9 fetches (3 immediate + 6 interval).
    await settleTimers(POLL_INTERVAL_MS);
    await settleTimers(POLL_INTERVAL_MS);
    expect(countRunReads()).toBe(3);
  });

  it('never overlaps an in-flight request', async () => {
    const { startRunStatePoll } = await loadModule();
    let release;
    let fetchCount = 0;
    vi.stubGlobal('fetch', vi.fn(() => {
      fetchCount += 1;
      return new Promise((resolve) => { release = resolve; });
    }));

    startRunStatePoll();
    await Promise.resolve();

    // One request is pending; advancing far beyond the interval must not start
    // a second fetch while the first is still in flight.
    await settleTimers(POLL_INTERVAL_MS * 5);
    expect(fetchCount).toBe(1);

    release({ ok: true, json: async () => ({ attempt: { state: 'refreshing' }, snapshot: null }) });
    await settleTimers(0);

    // Resolved -> next tick is scheduled only after completion; one interval
    // later exactly one more request fires.
    await settleTimers(POLL_INTERVAL_MS);
    expect(fetchCount).toBe(2);
  });

  it('adopts a run already complete before the first tick (completion-before-first-poll)', async () => {
    const { startRunStatePoll, onRunPublished } = await loadModule();
    const published = vi.fn();
    onRunPublished(published);

    // First-ever observation is already a completed run -> must not be missed.
    stubRunFetch([RUN('new-run', 'succeeded')]);
    startRunStatePoll();
    await settleTimers(0);

    expect(published).toHaveBeenCalledTimes(1);
    expect(published).toHaveBeenCalledWith('new-run');

    // The same run_id on later ticks never re-publishes.
    await settleTimers(POLL_INTERVAL_MS);
    expect(published).toHaveBeenCalledTimes(1);
  });

  it('picks up a first-ever run (no baseline) when it is published', async () => {
    const { startRunStatePoll, onRunPublished } = await loadModule();
    const published = vi.fn();
    onRunPublished(published);

    // Page load: no run yet, no attempt. Next tick: first-ever run appears.
    stubRunFetch([IDLE, RUN('first', 'succeeded')]);
    startRunStatePoll();
    await settleTimers(0);

    expect(published).not.toHaveBeenCalled();
    await settleTimers(POLL_INTERVAL_MS);
    expect(published).toHaveBeenCalledTimes(1);
    expect(published).toHaveBeenCalledWith('first');
  });

  it('publishes on run_id change for a later refresh and never POSTs a refresh', async () => {
    const { startRunStatePoll, onRunPublished } = await loadModule();
    const published = vi.fn();
    onRunPublished(published);

    stubRunFetch([RUN('aaa', 'refreshing'), RUN('bbb', 'succeeded'), RUN('bbb', 'succeeded')]);
    startRunStatePoll();
    await settleTimers(0); // baseline aaa (first observation adopt)
    await settleTimers(POLL_INTERVAL_MS); // bbb -> publish
    await settleTimers(POLL_INTERVAL_MS); // bbb again -> no publish

    expect(published).toHaveBeenCalledTimes(2);
    expect(published.mock.calls.map((c) => c[0])).toEqual(['aaa', 'bbb']);

    const posted = global.fetch.mock.calls.filter(([url, opts]) =>
      typeof url === 'string' && url.includes('/refresh')
    );
    expect(posted).toHaveLength(0);
  });

  it('keeps polling through a transient failure without re-publishing', async () => {
    const { startRunStatePoll, onRunPublished } = await loadModule();
    const published = vi.fn();
    onRunPublished(published);

    let failNext = false;
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (failNext) { failNext = false; throw new TypeError('network down'); }
      if (url === '/api/run') return { ok: true, json: async () => RUN('aaa', 'refreshing') };
      return { ok: false, json: async () => ({}) };
    }));

    startRunStatePoll();
    await settleTimers(0); // baseline aaa (adopt once)

    failNext = true;
    await settleTimers(POLL_INTERVAL_MS); // transient failure tick
    expect(published).toHaveBeenCalledTimes(1);

    await settleTimers(POLL_INTERVAL_MS); // recovery tick
    expect(published).toHaveBeenCalledTimes(1); // no duplicate publish
    expect(countRunReads()).toBeGreaterThanOrEqual(3); // poll survived the failure
  });

  it('stops after confirmed idle (fresh install) and restarts on refresh', async () => {
    const { startRunStatePoll, ensureRunStatePoll, onRunPublished } = await loadModule();
    const published = vi.fn();
    onRunPublished(published);

    let payload = IDLE;
    let fetchCount = 0;
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      fetchCount += 1;
      if (url === '/api/run') return { ok: true, json: async () => payload };
      return { ok: false, json: async () => ({}) };
    }));

    startRunStatePoll();
    await settleTimers(0); // idle observation 1 -> not yet
    await settleTimers(POLL_INTERVAL_MS); // idle observation 2 -> stop
    const stoppedAt = fetchCount;

    await settleTimers(POLL_INTERVAL_MS * 3); // 15s idle -> no polls
    expect(fetchCount).toBe(stoppedAt);

    payload = RUN('first', 'succeeded');
    ensureRunStatePoll(); // manual refresh click restarts the poll
    await settleTimers(0);
    expect(published).toHaveBeenCalledTimes(1);
    expect(published).toHaveBeenCalledWith('first');
  });
});
