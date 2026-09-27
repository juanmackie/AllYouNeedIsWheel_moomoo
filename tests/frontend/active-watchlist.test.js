import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { renderActiveWatchlist } from '../../frontend/static/js/dashboard/active-watchlist.js';

/**
 * ACTIVE WATCHLIST page-foot renderer.
 * Covers the distinct group statuses (ok / missing_group / empty_group /
 * connection_failed), the no-payload fallbacks, per-ticker scan status chips,
 * holdings checked, unsupported-symbol explanations, the last-sync badge, and
 * XSS safety (hostile group/explanation/symbol text rendered via textContent
 * can never inject markup).
 */

function setupDOM() {
  const section = document.createElement('section');
  section.id = 'active-watchlist-foot';

  const head = document.createElement('div');
  const sync = document.createElement('span');
  sync.id = 'active-watchlist-sync';
  head.appendChild(sync);

  const render = document.createElement('div');
  render.id = 'active-watchlist-render';

  section.appendChild(head);
  section.appendChild(render);
  document.body.appendChild(section);

  const blocking = document.createElement('div');
  blocking.id = 'watchlist-blocking-banner';
  blocking.className = 'd-none';
  document.body.appendChild(blocking);

  return { section, sync, render, blocking };
}

function aw(overrides = {}) {
  return {
    group_name: 'My Watchlist',
    group_status: 'ok',
    explanation: '',
    groups_available: ['My Watchlist'],
    fetched_at: '2026-09-10T12:00:00.000Z',
    last_successful_sync: '',
    tickers: [
      { symbol: 'AAPL', raw_code: 'US.AAPL', status: 'scanned', quote_fetched_at: '' },
      { symbol: 'MSFT', raw_code: 'US.MSFT', status: 'error', quote_fetched_at: '' },
      { symbol: 'BRK-B', raw_code: 'US.BRK.B', status: 'scanned', quote_fetched_at: '' },
    ],
    unsupported: [{ symbol: 'HK.0700', reason: 'non-US listing HK.0700.' }],
    holdings_checked: ['AAPL', 'NVDA'],
    ...overrides,
  };
}

beforeEach(() => {
  setupDOM();
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('renderActiveWatchlist', () => {
  it('renders the healthy group with per-ticker status, holdings, unsupported, and sync', () => {
    renderActiveWatchlist({ active_watchlist: aw({ last_successful_sync: '2026-09-10T12:00:00.000Z' }) });
    const render = document.getElementById('active-watchlist-render');

    expect(render.textContent).toContain('scan universe: My Watchlist');
    expect(render.textContent).toContain('group ok');
    expect(render.textContent).toContain('AAPL · scanned');
    expect(render.textContent).toContain('MSFT · error');
    expect(render.textContent).toContain('BRK-B · scanned');
    expect(render.textContent).toContain('CC holdings checked: AAPL, NVDA');
    expect(render.textContent).toContain('HK.0700 — not scanned: non-US listing HK.0700.');
    expect(document.getElementById('active-watchlist-sync').textContent).toContain('last sync 2026-09-10 12:00:00Z');
  });

  it('offers only groups discovered from the active watchlist payload and persists the chosen group', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ success: true }) });
    vi.stubGlobal('fetch', fetchMock);
    renderActiveWatchlist({ active_watchlist: aw({ group_name: 'US', groups_available: ['US', 'Favorites'] }) });

    const select = document.getElementById('watchlist-group-select');
    expect([...select.options].map((option) => option.value)).toEqual(['US', 'Favorites']);
    expect(select.value).toBe('US');
    select.value = 'US';
    document.getElementById('watchlist-group-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledWith('/api/settings/watchlist-group', expect.objectContaining({ method: 'POST' }));
    vi.unstubAllGlobals();
  });

  it('surfaces an unhealthy CSP group in the blocking signal banner', () => {
    renderActiveWatchlist({ active_watchlist: aw({ group_status: 'missing_group', explanation: 'Group unavailable', tickers: [] }) });
    const banner = document.getElementById('watchlist-blocking-banner');
    expect(banner.className).not.toContain('d-none');
    expect(banner.textContent).toContain('Group unavailable');
  });

  it('shows the distinct missing_group explanation and no success sync', () => {
    renderActiveWatchlist({
      active_watchlist: aw({
        group_status: 'missing_group',
        group_name: 'Does Not Exist',
        explanation: "Watchlist group 'Does Not Exist' was not found.",
        tickers: [],
      }),
    });
    const render = document.getElementById('active-watchlist-render');

    expect(render.textContent).toContain('GROUP MISSING');
    expect(render.textContent).toContain('was not found');
    expect(render.textContent).not.toContain('AAPL · scanned');
    expect(document.getElementById('active-watchlist-sync').textContent).toContain('no successful sync');
  });

  it('shows the distinct empty_group explanation', () => {
    renderActiveWatchlist({
      active_watchlist: aw({
        group_status: 'empty_group',
        explanation: 'The Moomoo watchlist group My Watchlist returned no symbols.',
        tickers: [],
      }),
    });
    const render = document.getElementById('active-watchlist-render');

    expect(render.textContent).toContain('GROUP EMPTY');
    expect(render.textContent).toContain('returned no symbols');
  });

  it('shows the distinct connection_failed status', () => {
    renderActiveWatchlist({
      active_watchlist: aw({
        group_status: 'connection_failed',
        explanation: 'Watchlist group read failed: OpenD not reachable.',
        tickers: [],
      }),
    });
    const render = document.getElementById('active-watchlist-render');

    expect(render.textContent).toContain('UNREACHABLE');
    expect(render.textContent).toContain('not reachable');
    expect(document.getElementById('active-watchlist-sync').textContent).toContain('no successful sync');
  });

  it('shows the waiting placeholder when there is no snapshot', () => {
    renderActiveWatchlist(undefined);
    expect(document.getElementById('active-watchlist-render').textContent).toContain('Waiting for a run snapshot…');
  });

  it('shows a notice when a run exists but carried no active-watchlist payload', () => {
    renderActiveWatchlist({ run: { run_id: 'r1' } });
    expect(document.getElementById('active-watchlist-render').textContent).toContain(
      'This run carried no active-watchlist payload.'
    );
    expect(document.getElementById('active-watchlist-sync').textContent).toContain('no successful sync');
  });

  it('does not render the foot at all when the element is missing', () => {
    document.body.innerHTML = '';
    // Must not throw when the foot is absent from the page.
    renderActiveWatchlist({ active_watchlist: aw() });
  });

  it('never injects markup from hostile group/explanation/symbol text (XSS)', () => {
    renderActiveWatchlist({
      active_watchlist: aw({
        group_status: 'empty_group', // exercises the explanation render path
        group_name: '<img src=x onerror=alert(1)>',
        explanation: '<script>alert(2)</script><img src=y onerror=alert(3)>',
        tickers: [{ symbol: '<img src=z onerror=alert(4)>', raw_code: '', status: 'scanned', quote_fetched_at: '' }],
        unsupported: [{ symbol: '<script>alert(5)</script>', reason: 'x' }],
      }),
    });
    const render = document.getElementById('active-watchlist-render');

    // No script/img elements may ever reach the DOM, even though the hostile
    // text is present as plain text inside the container.
    expect(render.querySelectorAll('script,img').length).toBe(0);
    expect(render.textContent).toContain('<script>alert(2)</script>');
  });
});
