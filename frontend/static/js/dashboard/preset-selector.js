/**
 * Preset selector: three versioned presets; effective values are read-only.
 * Extracted from top-recommendations.js (F-S1). Calls `onPresetChanged`
 * after a successful switch so the signals panel can reload.
 */

let _presetState = null;

export async function initPresetSelector(onPresetChanged) {
    const buttonsEl = document.getElementById('preset-buttons');
    if (!buttonsEl || buttonsEl.dataset.bound) return;
    buttonsEl.dataset.bound = 'true';
    try {
        const resp = await fetch('/api/settings');
        if (!resp.ok) return;
        _presetState = await resp.json();
        renderPresetSelector(onPresetChanged);
    } catch (err) {
        console.error('Preset selector failed to load:', err);
    }
}

function renderPresetSelector(onPresetChanged) {
    const buttonsEl = document.getElementById('preset-buttons');
    const effectiveEl = document.getElementById('preset-effective');
    if (!buttonsEl || !_presetState) return;
    buttonsEl.innerHTML = '';
    const labels = { conservative: 'Conservative', balanced: 'Balanced', aggressive: 'Aggressive' };
    for (const [key, preset] of Object.entries(_presetState.presets || {})) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm ' + (key === _presetState.active ? 'btn-primary' : 'btn-outline-secondary');
        btn.textContent = labels[key] || preset.label || key;
        btn.dataset.preset = key;
        btn.addEventListener('click', () => selectPreset(key, onPresetChanged));
        buttonsEl.appendChild(btn);
    }
    if (effectiveEl) {
        const eff = _presetState.effective || {};
        effectiveEl.textContent =
            `Effective: DTE ${eff.csp_min_dte}-${eff.csp_max_dte} (pref ${eff.csp_preferred_dte}), ` +
            `delta ${eff.csp_target_delta}±${eff.csp_delta_tolerance}, OTM ${eff.csp_min_otm_pct}-${eff.csp_max_otm_pct}%, ` +
            `min BP $${Math.round(eff.min_csp_buying_power || 0)}, max ${eff.max_buying_power_pct_per_csp || 0}% BP per CSP — read-only`;
    }
}

async function selectPreset(key, onPresetChanged) {
    try {
        const resp = await fetch('/api/settings/preset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ preset: key }),
        });
        if (!resp.ok) return;
        _presetState = await resp.json();
        renderPresetSelector(onPresetChanged);
        if (typeof onPresetChanged === 'function') onPresetChanged();
    } catch (err) {
        console.error('Preset selection failed:', err);
    }
}
