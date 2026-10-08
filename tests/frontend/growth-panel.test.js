import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderGrowthPanel } from '../../frontend/static/js/dashboard/growth-panel.js';

function mountGrowthPanel() {
  document.body.innerHTML = `
    <span id="growth-track-badge"></span>
    <span id="growth-current-nav"></span>
    <span id="growth-target-nav"></span>
    <h2 id="growth-heading"></h2>
    <span id="growth-eta-label"></span>
    <span id="growth-target-label"></span>
    <span id="growth-progress"></span>
    <div id="growth-progress-bar"></div>
    <span id="growth-annualized"></span>
    <span id="growth-required-cagr"></span>
    <span id="growth-gap-pp"></span>
    <span id="growth-eta"></span>
    <span id="goal-flow-status"></span>
    <div id="goal-owner-flows"></div>
  `;
}

function mockPanelFetch(pace) {
  global.fetch = vi.fn((url) =>
    Promise.resolve({
      ok: true,
      json: () =>
        Promise.resolve(
          String(url).includes('/history')
            ? {
                count: 1,
                series: [{ captured_at: '2026-09-30T00:00:00Z', net_liquidation: 1000 }],
                pace,
              }
            : { analytics: null },
        ),
    }),
  );
}

function pacePayload(overrides = {}) {
  return {
    target_multiple: 5,
    target_nav: 5000,
    reached: false,
    progress_pct: 12.5,
    annualized_pace: 0.2,
    required_cagr: 0.71,
    gap_pp: -51,
    eta_days: 730.5,
    years_at_current_pace: 2,
    observation_ready: true,
    on_track: false,
    ...overrides,
  };
}

describe('growth panel pace ETA', () => {
  beforeEach(() => {
    mountGrowthPanel();
  });

  it('renders the server-calculated years at current pace', async () => {
    mockPanelFetch(pacePayload());

    await renderGrowthPanel();

    expect(document.getElementById('growth-eta').textContent).toBe('~2.0 years');
  });

  it('shows no ETA until the server can estimate one', async () => {
    mockPanelFetch(
      pacePayload({
        eta_days: null,
        years_at_current_pace: null,
        annualized_pace: null,
        observation_ready: false,
      }),
    );

    await renderGrowthPanel();

    expect(document.getElementById('growth-eta').textContent).toBe('—');
  });

  it('reports never when the mature realized pace is non-positive', async () => {
    mockPanelFetch(
      pacePayload({ eta_days: null, years_at_current_pace: null, annualized_pace: 0, observation_ready: true }),
    );

    await renderGrowthPanel();

    expect(document.getElementById('growth-eta').textContent).toBe('Never at current pace');
  });

  it('labels the ETA as reached when the server marks the goal complete', async () => {
    mockPanelFetch(pacePayload({ reached: true, eta_days: 0, years_at_current_pace: 0 }));

    await renderGrowthPanel();

    expect(document.getElementById('growth-eta').textContent).toBe('Reached');
  });

  it('withholds pace metrics and shows the incomplete-flow state', async () => {
    mockPanelFetch(
      pacePayload({
        confidence_state: 'flows_incomplete',
        progress_pct: null,
        annualized_pace: null,
        required_cagr: null,
        gap_pp: null,
        years_at_current_pace: null,
        observation_ready: false,
      }),
    );

    await renderGrowthPanel();

    expect(document.getElementById('growth-track-badge').textContent).toBe('FLOWS INCOMPLETE');
    expect(document.getElementById('growth-progress').textContent).toBe('— · flows incomplete');
    expect(document.getElementById('growth-annualized').textContent).toBe('—');
    expect(document.getElementById('growth-eta').textContent).toBe('—');
  });
});
