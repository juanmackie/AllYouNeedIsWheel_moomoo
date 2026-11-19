import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Regression coverage for the shared run-state poll:
//   - exactly one shared poll instance (page load starts it once)
//   - one in-flight request max (slow responses never overlap)
//   - P6: a cheap revision token gates the full snapshot read, so an unchanged
//     screen costs no snapshot transfer and no panel re-render
//   - first-ever run pickup (no baseline) and completion before the first tick
//   - publish fan-out on run_id change, without ever POSTing a refresh
//   - transient failures keep polling, never re-publish
//   - stops after confirmed idle and restarts on ensureRunStatePoll()

vi.mock('../../frontend/static/js/dashboard/run-strip.js', () => ({
  renderRunStrip: vi.fn(),
  renderRunCommunicationError: vi.fn(),
  initRunStrip: vi.fn(),
  loadRunStrip: vi.fn(),
}));

const POLL_INTERVAL_MS = 5000;

async function loadModule() {
  return await import('../../frontend/static/js/dashboard/run-notifier.js');
}

const RUN = (runId, state, extra = {}) => ({
  attempt: state === 'idle' ? null : { state },
  snapshot: {
    tradeable: true,
    run: { run_id: runId, status: 'ready', market_state: 'open', ...extra },
  },
});

const IDLE = { attempt: null, snapshot: null };

/** Revision token payload for one observation. */
function rev(runId, { state = 'idle', stage = '', progress = 0 } = {}) {
  return {
    revision: `${runId || ''}|${state}|${stage}|${progress}`,
    run_id: runId,
    published_at: runId ? `2026-01-01T00:00:0${runId.length % 10}+00:00` : null,
    attempt: state === 'idle' ? null : { attempt_id: 'a1', state, stage, progress },
  };
}

/** One observation = the revision payload and the full run payload it gates. */
function observation(runId, runPayload, attempt = {}) {
  return { revision: rev(runId, attempt), run: runPayload };
}

/**
 * Stub both poll endpoints. Each '/api/run/revision' read advances to the next
 * observation (the last one repeats); '/api/run' answers with that observation's
 * payload, so a client that skips the gated read never sees a later state.
 */
function stubObservations(sequence) {
  let index = 0;
  let revisionReads = 0;
  let runReads = 0;
  const fetchMock = vi.fn(async (url) => {
    if (url === '/api/run/revision') {
      revisionReads += 1;
      const current = sequence[Math.min(index, sequence.length - 1)];
      index += 1;
      return { ok: true, json: async () => current.revision };
    }
    if (url === '/api/run') {
      runReads += 1;
      const current = sequence[Math.max(0, Math.min(index - 1, sequence.length - 1))];
      return { ok: true, json: async () => current.run };
    }
    return { ok: false, json: async () => ({}) };
  });
  vi.stubGlobal('fetch', fetchMock);
  return {
    fetchMock,
    revisionReads: () => revisionReads,
    runReads: () => runReads,
  };
}

/** Advance fake timers by ms, then flush microtasks so an async tick settles. */
async function settleTimers(ms) {
  await vi.advanceTimersByTimeAsync(ms);
  for (let i = 0; i < 6; i++) await Promise.resolve();
}

describe('shared run-state poll', () => {
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
    const poll = stubObservations([
      observation('aaa', RUN('aaa', 'refreshing'), { state: 'refreshing', stage: 'scan', progress: 0.4 }),
    ]);

    startRunStatePoll();
    startRunStatePoll();
    startRunStatePoll();
    await settleTimers(0); // immediate first tick resolves

    expect(poll.revisionReads()).toBe(1);

    // Two intervals -> two more ticks. Three concurrent instances would have
    // made 9 revision reads (3 immediate + 6 interval).
    await settleTimers(POLL_INTERVAL_MS);
    await settleTimers(POLL_INTERVAL_MS);
    expect(poll.revisionReads()).toBe(3);
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

    release({ ok: true, json: async () => rev(null) });
    await settleTimers(0);

    // Resolved -> next tick is scheduled only after completion; one interval
    // later exactly one more request fires.
    await settleTimers(POLL_INTERVAL_MS);
    expect(fetchCount).toBe(2);
  });

  it('does not re-send the snapshot while the revision token is unchanged', async () => {
    const { startRunStatePoll } = await loadModule();
    const poll = stubObservations([observation('aaa', RUN('aaa', 'idle'))]);

    startRunStatePoll();
    await settleTimers(0);
    expect(poll.runReads()).toBe(1); // first observation loads the full state

    await settleTimers(POLL_INTERVAL_MS);
    await settleTimers(POLL_INTERVAL_MS);
    expect(poll.revisionReads()).toBe(3);
    expect(poll.runReads()).toBe(1); // unchanged token => no snapshot transfer
  });

  it('fetches the full snapshot when the revision token changes', async () => {
    const { startRunStatePoll } = await loadModule();
    const poll = stubObservations([
      observation('aaa', RUN('aaa', 'idle')),
      observation('aaa', RUN('aaa', 'idle'), { state: 'refreshing', stage: 'scan', progress: 0.2 }),
    ]);

    startRunStatePoll();
    await settleTimers(0);
    await settleTimers(POLL_INTERVAL_MS);

    expect(poll.revisionReads()).toBe(2);
    expect(poll.runReads()).toBe(2);
  });

  it('adopts a run already complete before the first tick (completion-before-first-poll)', async () => {
    const { startRunStatePoll, onRunPublished } = await loadModule();
    const published = vi.fn();
    onRunPublished(published);

    stubObservations([observation('new-run', RUN('new-run', 'succeeded'))]);
    startRunStatePoll();
    await settleTimers(0);

    expect(published).toHaveBeenCalledTimes(1);
    expect(published).toHaveBeenCalledWith('new-run');

    // The same run_id on later ticks never re-publishes (and does not refetch).
    await settleTimers(POLL_INTERVAL_MS);
    expect(published).toHaveBeenCalledTimes(1);
  });

  it('picks up a first-ever run (no baseline) when it is published', async () => {
    const { startRunStatePoll, onRunPublished } = await loadModule();
    const published = vi.fn();
    onRunPublished(published);

    // Page load: no run yet, no attempt. Next tick: first-ever run appears.
    stubObservations([
      observation(null, IDLE),
      observation('first', RUN('first', 'succeeded')),
    ]);
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

    const poll = stubObservations([
      observation('aaa', RUN('aaa', 'refreshing')),
      observation('bbb', RUN('bbb', 'succeeded')),
      observation('bbb', RUN('bbb', 'succeeded')),
    ]);
    startRunStatePoll();
    await settleTimers(0); // baseline aaa (first observation adopt)
    await settleTimers(POLL_INTERVAL_MS); // bbb -> publish
    await settleTimers(POLL_INTERVAL_MS); // bbb again -> no publish, no refetch

    expect(published).toHaveBeenCalledTimes(2);
    expect(published.mock.calls.map((c) => c[0])).toEqual(['aaa', 'bbb']);
    expect(poll.runReads()).toBe(2);

    const posted = poll.fetchMock.mock.calls.filter(([url]) =>
      typeof url === 'string' && url.includes('/refresh')
    );
    expect(posted).toHaveLength(0);
  });

  it('retries the same publication when the gated snapshot read fails', async () => {
    const { startRunStatePoll, onRunPublished } = await loadModule();
    const published = vi.fn();
    onRunPublished(published);

    let runAttempts = 0;
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (url === '/api/run/revision') {
        return { ok: true, json: async () => rev('aaa', { state: 'refreshing' }) };
      }
      if (url === '/api/run') {
        runAttempts += 1;
        if (runAttempts === 1) return { ok: false, status: 500, json: async () => ({}) };
        return { ok: true, json: async () => RUN('aaa', 'refreshing') };
      }
      return { ok: false, json: async () => ({}) };
    }));

    startRunStatePoll();
    await settleTimers(0); // token changed, snapshot read fails -> warn, token not advanced
    const { renderRunCommunicationError } = await import('../../frontend/static/js/dashboard/run-strip.js');
    expect(renderRunCommunicationError).toHaveBeenCalledTimes(1);

    await settleTimers(POLL_INTERVAL_MS); // same token -> retries the snapshot
    expect(runAttempts).toBe(2);
    expect(published).toHaveBeenCalledTimes(1);
    expect(published).toHaveBeenCalledWith('aaa');
  });

  it('keeps polling through a transient failure without re-publishing', async () => {
    const { startRunStatePoll, onRunPublished } = await loadModule();
    const published = vi.fn();
    onRunPublished(published);

    let failNext = true;
    let revisionReads = 0;
    let runReads = 0;
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (url === '/api/run/revision') {
        revisionReads += 1;
        if (failNext) { failNext = false; throw new TypeError('network down'); }
        return { ok: true, json: async () => rev('aaa', { state: 'refreshing' }) };
      }
      if (url === '/api/run') {
        runReads += 1;
        return { ok: true, json: async () => RUN('aaa', 'refreshing') };
      }
      return { ok: false, json: async () => ({}) };
    }));

    startRunStatePoll();
    await settleTimers(0); // transient failure before any baseline
    const { renderRunCommunicationError, renderRunStrip } = await import('../../frontend/static/js/dashboard/run-strip.js');
    expect(renderRunCommunicationError).toHaveBeenCalledTimes(1);
    expect(published).not.toHaveBeenCalled();

    await settleTimers(POLL_INTERVAL_MS); // recovery tick publishes once
    expect(published).toHaveBeenCalledTimes(1);
    await settleTimers(POLL_INTERVAL_MS); // unchanged token: no duplicate publish
    expect(published).toHaveBeenCalledTimes(1);
    expect(revisionReads).toBeGreaterThanOrEqual(3);
    expect(runReads).toBe(1);
    expect(renderRunStrip).toHaveBeenCalledTimes(1);
  });

  it.each([503, 'malformed'])('warns on a bad poll response (%s) and continues polling', async (failure) => {
    const { startRunStatePoll } = await loadModule();
    let revisionReads = 0;
    vi.stubGlobal('fetch', vi.fn(async () => {
      revisionReads += 1;
      return failure === 'malformed'
        ? { ok: true, json: async () => null }
        : { ok: false, status: failure, json: async () => ({}) };
    }));
    startRunStatePoll();
    await settleTimers(0);
    const { renderRunCommunicationError } = await import('../../frontend/static/js/dashboard/run-strip.js');
    expect(renderRunCommunicationError).toHaveBeenCalledTimes(1);
    await settleTimers(POLL_INTERVAL_MS);
    expect(revisionReads).toBe(2);
  });

  it('stops after confirmed idle (fresh install) and restarts on refresh', async () => {
    const { startRunStatePoll, ensureRunStatePoll, onRunPublished } = await loadModule();
    const published = vi.fn();
    onRunPublished(published);

    const poll = stubObservations([
      observation(null, IDLE),
      observation(null, IDLE),
      observation('first', RUN('first', 'succeeded')),
    ]);

    startRunStatePoll();
    await settleTimers(0); // idle observation 1 -> not yet
    await settleTimers(POLL_INTERVAL_MS); // idle observation 2 -> stop
    const stoppedAt = poll.revisionReads();

    await settleTimers(POLL_INTERVAL_MS * 3); // 15s idle -> no polls
    expect(poll.revisionReads()).toBe(stoppedAt);

    ensureRunStatePoll(); // manual refresh click restarts the poll
    await settleTimers(0);
    expect(published).toHaveBeenCalledTimes(1);
    expect(published).toHaveBeenCalledWith('first');
  });
});
