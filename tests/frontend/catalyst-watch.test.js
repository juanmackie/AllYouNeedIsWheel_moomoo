import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../frontend/static/js/dashboard/api.js', () => ({
  fetchWithTimeout: vi.fn(),
  readJsonSafely: vi.fn((r) => r.json()),
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
  formatCurrency: vi.fn((v) => `$${v.toFixed(2)}`),
}));

import { fetchWithTimeout, readJsonSafely } from '../../frontend/static/js/dashboard/api.js';
import StateModel from '../../frontend/static/js/utils/state-model.js';
import { showPanelLoading, finishPanelLoading, failPanelLoading } from '../../frontend/static/js/dashboard/options-table-rendering.js';

function setupDOM() {
  document.body.innerHTML = `
    <div id="catalyst-watch-section"></div>
    <div id="catalyst-content"></div>
    <div id="catalyst-state"></div>
    <div id="catalyst-last-updated"></div>
    <template id="catalyst-card-template">
      <div class="catalyst-card">
        <span class="catalyst-card__ticker"></span>
        <span class="catalyst-card__direction"></span>
        <span class="catalyst-card__signal"></span>
        <span class="catalyst-card__action"></span>
        <span class="catalyst-card__conflict-warning d-none"></span>
        <span class="catalyst-card__score-value"></span>
        <span class="catalyst-card__premium"></span>
        <span class="catalyst-card__fresh"></span>
        <span class="catalyst-card__otm"></span>
        <span class="catalyst-card__strike"></span>
        <span class="catalyst-card__cluster"></span>
        <span class="catalyst-card__hedged"></span>
        <span class="catalyst-card__earnings"></span>
        <span class="catalyst-card__rationale"></span>
        <span class="catalyst-card__blockers"></span>
        <span class="catalyst-card__social d-none"></span>
      </div>
    </template>
  `;
}

describe('catalyst-watch', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.resetModules();
    setupDOM();
  });

  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('shows "No anomalous flow" message when fresh scan succeeds with no signals', async () => {
    const { loadCatalystSignals } = await import('../../frontend/static/js/dashboard/catalyst-watch.js');

    const mockResponse = {
      ok: true,
      json: () => Promise.resolve({
        success: true,
        enabled: true,
        signals: [],
        count: 0,
        scanned: 5,
        generated_at: '2026-05-24T12:00:00',
        served_from_cache: false,
        cache_age_seconds: null,
        fresh_attempted: true,
        fresh_succeeded: true,
        last_successful_generated_at: '2026-05-24T12:00:00',
        scan_pending: false,
        thresholds: {
          min_premium_notional: 1_000_000,
          min_fresh_volume_ratio: 5,
          min_volume: 500,
          max_expirations: 1,
          max_dte: 60,
          max_scan_tickers: 2,
        },
        elapsed_seconds: 1.2,
      }),
    };

    fetchWithTimeout.mockResolvedValue(mockResponse);

    await loadCatalystSignals();

    const emptyCalls = StateModel.showEmpty.mock.calls.filter(c => c[0] === 'catalyst-state');
    const lastEmptyMsg = emptyCalls[emptyCalls.length - 1]?.[1];
    expect(lastEmptyMsg).toContain('No anomalous flow detected');
    expect(lastEmptyMsg).not.toContain('cached');
    expect(lastEmptyMsg).not.toContain('weekend');
    expect(lastEmptyMsg).not.toContain('market');
  });

  it('shows cached fallback message with age when served from cache', async () => {
    const { loadCatalystSignals } = await import('../../frontend/static/js/dashboard/catalyst-watch.js');

    const mockResponse = {
      ok: true,
      json: () => Promise.resolve({
        success: true,
        enabled: true,
        signals: [],
        count: 0,
        scanned: 5,
        generated_at: '2026-05-24T10:00:00',
        served_from_cache: true,
        cache_age_seconds: 7200,
        fresh_attempted: true,
        fresh_succeeded: false,
        last_successful_generated_at: '2026-05-24T10:00:00',
        scan_pending: false,
        thresholds: {
          min_premium_notional: 1_000_000,
          min_fresh_volume_ratio: 5,
          min_volume: 500,
          max_expirations: 1,
          max_dte: 60,
          max_scan_tickers: 2,
        },
        elapsed_seconds: 0,
      }),
    };

    fetchWithTimeout.mockResolvedValue(mockResponse);

    await loadCatalystSignals();

    const emptyCalls = StateModel.showEmpty.mock.calls.filter(c => c[0] === 'catalyst-state');
    const lastEmptyMsg = emptyCalls[emptyCalls.length - 1]?.[1];
    expect(lastEmptyMsg).toContain('Showing cached catalyst signals');
    expect(lastEmptyMsg).toContain('120 minutes ago');
    expect(lastEmptyMsg).toContain('refreshing in background');
  });

  it('shows cached fallback with singular minute when age is 60s', async () => {
    const { loadCatalystSignals } = await import('../../frontend/static/js/dashboard/catalyst-watch.js');

    const mockResponse = {
      ok: true,
      json: () => Promise.resolve({
        success: true,
        enabled: true,
        signals: [],
        count: 0,
        generated_at: '2026-05-24T11:59:00',
        served_from_cache: true,
        cache_age_seconds: 60,
        fresh_attempted: true,
        fresh_succeeded: false,
        last_successful_generated_at: '2026-05-24T11:59:00',
        scan_pending: false,
        thresholds: { min_premium_notional: 1_000_000 },
        elapsed_seconds: 0,
      }),
    };

    fetchWithTimeout.mockResolvedValue(mockResponse);

    await loadCatalystSignals();

    const emptyCalls = StateModel.showEmpty.mock.calls.filter(c => c[0] === 'catalyst-state');
    const lastEmptyMsg = emptyCalls[emptyCalls.length - 1]?.[1];
    expect(lastEmptyMsg).toContain('1 minute ago');
  });

  it('shows pending message when scan_pending is true', async () => {
    const { loadCatalystSignals } = await import('../../frontend/static/js/dashboard/catalyst-watch.js');

    const mockResponse = {
      ok: true,
      json: () => Promise.resolve({
        success: true,
        enabled: true,
        signals: [],
        count: 0,
        generated_at: '2026-05-24T12:00:00',
        served_from_cache: false,
        cache_age_seconds: null,
        fresh_attempted: true,
        fresh_succeeded: false,
        last_successful_generated_at: null,
        scan_pending: true,
        thresholds: { min_premium_notional: 1_000_000 },
        elapsed_seconds: 0,
      }),
    };

    fetchWithTimeout.mockResolvedValue(mockResponse);

    await loadCatalystSignals();

    const emptyCalls = StateModel.showEmpty.mock.calls.filter(c => c[0] === 'catalyst-state');
    const lastEmptyMsg = emptyCalls[emptyCalls.length - 1]?.[1];
    expect(lastEmptyMsg).toContain('Waiting for broker flow data');
  });

  it('shows last-updated timestamp with cache warning when served from cache', async () => {
    const { loadCatalystSignals } = await import('../../frontend/static/js/dashboard/catalyst-watch.js');

    const mockResponse = {
      ok: true,
      json: () => Promise.resolve({
        success: true,
        enabled: true,
        signals: [],
        count: 0,
        generated_at: '2026-05-24T10:00:00',
        served_from_cache: true,
        cache_age_seconds: 7200,
        fresh_attempted: true,
        fresh_succeeded: false,
        last_successful_generated_at: '2026-05-24T10:00:00',
        scan_pending: false,
        thresholds: { min_premium_notional: 1_000_000 },
        elapsed_seconds: 0,
      }),
    };

    fetchWithTimeout.mockResolvedValue(mockResponse);

    await loadCatalystSignals();

    const lastUpdatedEl = document.getElementById('catalyst-last-updated');
    expect(lastUpdatedEl.textContent).toContain('cached');
    expect(lastUpdatedEl.textContent).toContain('120m ago');
    expect(lastUpdatedEl.className).toContain('text-warning');
  });

  it('shows signals without cache warning when fresh', async () => {
    const { loadCatalystSignals } = await import('../../frontend/static/js/dashboard/catalyst-watch.js');

    const mockResponse = {
      ok: true,
      json: () => Promise.resolve({
        success: true,
        enabled: true,
        signals: [{
          ticker: 'AAPL',
          direction: 'BULLISH',
          signal: 'GREEN',
          label: 'Bullish',
          score: 85,
          premium_notional: 2_000_000,
          fresh_volume_ratio: 10,
          otm_pct: 5,
          strike: 200,
          cluster_expirations: ['20260619'],
          is_hedged: false,
          earnings_dte: 15,
          rationale: ['High premium volume'],
        }],
        count: 1,
        generated_at: '2026-05-24T12:00:00',
        served_from_cache: false,
        cache_age_seconds: null,
        fresh_attempted: true,
        fresh_succeeded: true,
        last_successful_generated_at: '2026-05-24T12:00:00',
        scanned: 5,
        elapsed_seconds: 2.3,
      }),
    };

    fetchWithTimeout.mockResolvedValue(mockResponse);

    await loadCatalystSignals();

    const lastUpdatedEl = document.getElementById('catalyst-last-updated');
    expect(lastUpdatedEl.textContent).not.toContain('cached');
    expect(lastUpdatedEl.className).not.toContain('text-warning');
  });

  it('renders social note when signal has social context', async () => {
    const { loadCatalystSignals } = await import('../../frontend/static/js/dashboard/catalyst-watch.js');

    const mockResponse = {
      ok: true,
      json: () => Promise.resolve({
        success: true,
        enabled: true,
        signals: [{
          ticker: 'GME',
          direction: 'BULLISH',
          signal: 'GREEN',
          label: 'Priority lead',
          score: 80,
          premium_notional: 3_000_000,
          fresh_volume_ratio: 12,
          otm_pct: 8,
          strike: 50,
          cluster_expirations: ['20260718'],
          is_hedged: false,
          earnings_dte: null,
          rationale: ['Scan source: social momentum (200 mentions)'],
          blockers: [],
          social: {
            source: 'apewisdom',
            rank: 5,
            rank_24h_ago: 20,
            mentions: 200,
            mentions_24h_ago: 40,
            upvotes: 500,
            momentum_score: 1200,
          },
        }],
        count: 1,
        generated_at: '2026-05-24T12:00:00',
        served_from_cache: false,
        cache_age_seconds: null,
        fresh_attempted: true,
        fresh_succeeded: true,
        last_successful_generated_at: '2026-05-24T12:00:00',
        scanned: 5,
        elapsed_seconds: 2.3,
      }),
    };

    fetchWithTimeout.mockResolvedValue(mockResponse);

    await loadCatalystSignals();

    const socialEl = document.querySelector('.catalyst-card__social');
    expect(socialEl).toBeTruthy();
    expect(socialEl.textContent).toContain('Scan source: social momentum');
    expect(socialEl.textContent).toContain('200 mentions');
    expect(socialEl.textContent).toContain('rank #5');
    expect(socialEl.textContent).toContain('\u219115');
  });

  it('hides social note when signal has no social context', async () => {
    const { loadCatalystSignals } = await import('../../frontend/static/js/dashboard/catalyst-watch.js');

    const mockResponse = {
      ok: true,
      json: () => Promise.resolve({
        success: true,
        enabled: true,
        signals: [{
          ticker: 'AAPL',
          direction: 'BULLISH',
          signal: 'YELLOW',
          label: 'Moderate',
          score: 45,
          premium_notional: 1_200_000,
          fresh_volume_ratio: 6,
          otm_pct: 3,
          strike: 210,
          cluster_expirations: ['20260620'],
          is_hedged: false,
          earnings_dte: null,
          rationale: ['Premium flow'],
          blockers: [],
        }],
        count: 1,
        generated_at: '2026-05-24T12:00:00',
        served_from_cache: false,
        cache_age_seconds: null,
        fresh_attempted: true,
        fresh_succeeded: true,
        last_successful_generated_at: '2026-05-24T12:00:00',
        scanned: 3,
        elapsed_seconds: 1.5,
      }),
    };

    fetchWithTimeout.mockResolvedValue(mockResponse);

    await loadCatalystSignals();

    const socialEl = document.querySelector('.catalyst-card__social');
    expect(socialEl).toBeTruthy();
    expect(socialEl.classList.contains('d-none')).toBe(true);
    expect(socialEl.textContent).toBe('');
  });

  it('shows rank trend arrow when rank improved over 24h', async () => {
    const { loadCatalystSignals } = await import('../../frontend/static/js/dashboard/catalyst-watch.js');

    const mockResponse = {
      ok: true,
      json: () => Promise.resolve({
        success: true,
        enabled: true,
        signals: [{
          ticker: 'TSLA',
          direction: 'BEARISH',
          signal: 'WATCH',
          label: 'Low significance',
          score: 25,
          premium_notional: 500_000,
          fresh_volume_ratio: 3,
          otm_pct: 10,
          strike: 180,
          cluster_expirations: ['20260627'],
          is_hedged: false,
          earnings_dte: null,
          rationale: [],
          blockers: [],
          social: {
            source: 'apewisdom',
            rank: 50,
            rank_24h_ago: 30,
            mentions: 80,
            mentions_24h_ago: 80,
            upvotes: 100,
            momentum_score: 160,
          },
        }],
        count: 1,
        generated_at: '2026-05-24T12:00:00',
        served_from_cache: false,
        cache_age_seconds: null,
        fresh_attempted: true,
        fresh_succeeded: true,
        last_successful_generated_at: '2026-05-24T12:00:00',
        scanned: 2,
        elapsed_seconds: 1.0,
      }),
    };

    fetchWithTimeout.mockResolvedValue(mockResponse);

    await loadCatalystSignals();

    const socialEl = document.querySelector('.catalyst-card__social');
    expect(socialEl.textContent).toContain('\u219320');
  });
});
