import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
  document.body.innerHTML = '';
});

describe('preset selector status label', () => {
  it('keeps the current-preset badge aligned after selecting a new preset', async () => {
    document.body.innerHTML = `
      <div id="preset-buttons"></div>
      <div id="preset-effective"></div>
      <span id="run-preset">CURRENT PRESET --</span>
    `;
    let selected = 'balanced';
    const presets = {
      balanced: { label: 'Balanced' },
      aggressive: { label: 'Aggressive' },
    };
    const response = (includePresets = true) => ({
      active: selected,
      ...(includePresets ? { presets } : {}),
      effective: {
        csp_min_dte: 7, csp_max_dte: 35, csp_preferred_dte: 14,
        csp_target_delta: 0.35, csp_delta_tolerance: 0.15,
        csp_min_otm_pct: 3, csp_max_otm_pct: 15,
        min_csp_buying_power: 3000, max_buying_power_pct_per_csp: 90,
      },
    });
    vi.stubGlobal('fetch', vi.fn(async (_url, options) => {
      if (options?.method === 'POST') selected = 'aggressive';
      return { ok: true, json: async () => response(options?.method !== 'POST') };
    }));

    const { initPresetSelector } = await import('../../frontend/static/js/dashboard/preset-selector.js');
    await initPresetSelector();
    expect(document.getElementById('run-preset').textContent).toBe('CURRENT PRESET Balanced');

    document.querySelector('[data-preset="aggressive"]').click();
    await vi.waitFor(() => {
      expect(document.getElementById('run-preset').textContent).toBe('CURRENT PRESET Aggressive');
    });
    expect(document.querySelectorAll('#preset-buttons [data-preset]')).toHaveLength(2);
    expect(document.querySelector('[data-preset="aggressive"]').className).toContain('btn-primary');
    expect(document.querySelector('[data-preset="balanced"]').className).toContain('btn-outline-secondary');
  });
});
