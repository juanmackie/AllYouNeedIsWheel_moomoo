import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../frontend/static/js/dashboard/api.js', () => ({
  fetchTopRecommendations: vi.fn(),
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
  formatPercent: vi.fn((v) => `${v.toFixed(1)}%`),
}));

import StateModel from '../../frontend/static/js/utils/state-model.js';

function setupDOM() {
  document.body.innerHTML = `
    <div id="top-recommendations-container">
      <div id="top-recommendations-state"></div>
      <div id="top-recommendations-content" class="d-none">
        <div id="top-recommendations-cards"></div>
      </div>
      <div id="top-recs-last-updated" class="d-none"></div>
      <div id="blocked-candidates-section" class="d-none"></div>
      <div id="buying-power-indicator" class="d-none"></div>
      <div id="signal-tabs" class="d-none"></div>
    </div>
    <div id="growth-mode-banner" class="d-none"></div>
    <div id="growth-mode-objective"></div>
    <div id="growth-mode-drawdown"></div>
    <div id="top-recs-title"></div>
    <div id="top-recs-eyebrow"></div>
    <div id="top-recs-desc"></div>
    <template id="recommendation-card-template">
      <div class="recommendation-card">
        <span class="ticker-badge"></span>
        <span class="signal-type-badge"></span>
        <span class="option-type-badge"></span>
        <span class="strike-price"></span>
        <span class="expiration-date"></span>
        <span class="dte-badge"></span>
        <span class="premium-amount"></span>
        <span class="annualized-return"></span>
        <span class="score-badge"></span>
        <span class="confidence-badge"></span>
        <span class="signal-data-source"></span>
        <span class="recommendation-warnings"></span>
        <span class="otm-pct"></span>
        <span class="delta-value"></span>
        <span class="iv-rank"></span>
        <div class="macro-impact"></div>
        <div class="csp-details d-none"></div>
        <div class="cc-details d-none"></div>
        <div class="score-drivers d-none"></div>
        <div class="hard-blockers d-none"></div>
        <div class="recommendation-details"></div>
        <div class="growth-mode-details d-none"></div>
      </div>
    </template>
  `;
}

describe('top-recommendations empty state', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupDOM();
  });

  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('does not contain market-hours phrasing in empty state when signals are empty', async () => {
    const { initializeTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/top-recommendations.js'
    );

    const { fetchTopRecommendations } = await import(
      '../../frontend/static/js/dashboard/api.js'
    );

    fetchTopRecommendations.mockResolvedValue({
      success: true,
      signals: [],
      count: 0,
      generated_at: '2026-05-24T12:00:00',
    });

    await initializeTopRecommendations();
    await vi.dynamicImportSettled?.();

    await new Promise(r => setTimeout(r, 50));

    const emptyCall = StateModel.showEmpty.mock.calls.find(c => c[0] === 'top-recommendations-state');
    expect(emptyCall).toBeDefined();
    const message = emptyCall[1];
    expect(message).toContain('No signals available');
    expect(message).toContain('Try refresh or adjust criteria');
    expect(message).not.toContain('market open');
    expect(message).not.toContain('trading day');
    expect(message).not.toContain('Check back after');
  });
});
