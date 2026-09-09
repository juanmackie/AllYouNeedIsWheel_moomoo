/**
 * Outcome panel — broker-verified outcome summaries (read-only).
 *
 * Data source: GET /api/options/analytics/outcomes (local SQLite read, no
 * OpenD gate) — quoted vs filled credit, net P&L after fees, capital-days,
 * and per-preset / DTE / ticker / event-tier aggregates.
 * POST /api/options/analytics/outcomes/ingest pulls fill/fee history from
 * OpenD (query-only) when the operator asks, then re-renders.
 *
 * Every text-bearing insert is escaped through escapeHtml (frontend DOX).
 */
import { escapeHtml, formatCurrency } from '../utils/formatters.js';
import StateModel from '../utils/state-model.js';

const STATE_ID = 'outcome-state';
const TOTALS_ID = 'outcome-totals';
const RECORDS_ID = 'outcome-records';
const RECORDS_COUNT_ID = 'outcome-records-count';
const GROUPS = {
  preset: { id: 'outcome-group-preset', api: 'by_preset' },
  dte: { id: 'outcome-group-dte', api: 'by_dte_bucket' },
  ticker: { id: 'outcome-group-ticker', api: 'by_ticker' },
  event: { id: 'outcome-group-event', api: 'by_event_tier' },
};
const INGEST_BTN_ID = 'outcome-ingest-btn';

let _stateEl = null;
let _recordsBody = null;

function initElements() {
  _stateEl = document.getElementById(STATE_ID);
  _recordsBody = document.getElementById(RECORDS_ID);
}

/** Numeric-ish count, 0 when undefined. */
function _count(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

/** Currency number → string, guarded for null/NaN. */
function _money(value) {
  const n = Number(value);
  return Number.isFinite(n) ? formatCurrency(n) : '—';
}

/** null/undefined/'' mean "unknown" → NaN, so the UI shows an em dash, never $0.00. */
function _optNum(value) {
  return value == null || value === '' ? NaN : Number(value);
}

/** Signed currency with explicit +/−, for deltas like slippage / net P&L. */
function _signedMoney(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  if (abs < 0.005) return '$0.00';
  return `${n > 0 ? '+' : '−'}${formatCurrency(abs)}`;
}

/** Full option-type label (Put / Call) for display. */
function _optionLabel(optionType) {
  const t = String(optionType || '').toUpperCase();
  if (t === 'PUT') return 'Put';
  if (t === 'CALL') return 'Call';
  return escapeHtml(t || '—');
}

/** Contract label: TICKER expiry C/P strike (all numeric, safe). */
function _contractLabel(record) {
  const ticker = escapeHtml(record.ticker || '');
  const exp = String(record.expiration || '').replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3');
  const strike = record.strike != null ? escapeHtml(`$${Number(record.strike).toFixed(2)}`) : '—';
  return `${ticker} ${escapeHtml(exp)} ${_optionLabel(record.option_type)} ${strike}`;
}

/** Status badge (status is a fixed API enum, escaped for safety). */
function _statusBadge(status) {
  const label = escapeHtml(String(status || 'pending'));
  const cls = status === 'measured'
    ? 'bg-success'
    : status === 'unknown'
      ? 'bg-warning'
      : 'bg-secondary';
  return `<span class="badge ${cls}">${label}</span>`;
}

function metricCell(label, value, extraClass = '') {
  return `<div class="outcome-metric">
    <p class="outcome-metric__label">${escapeHtml(label)}</p>
    <h3 class="outcome-metric__value ${extraClass}">${value}</h3>
  </div>`;
}

function renderTotals(totals) {
  const el = document.getElementById(TOTALS_ID);
  if (!el) return;
  if (!totals || totals.sample_size === 0) {
    el.innerHTML = metricCell('Signals', '0', 'ft-td-mute');
    return;
  }
  const coverage = Number(totals.coverage_pct);
  const efficiency = _optNum(totals.owner_efficiency);
  const avgSlippage = _optNum(totals.avg_slippage_per_contract);
  const netDollars = _optNum(totals.net_dollars);
  el.innerHTML = [
    metricCell('Signals (sample)', String(_count(totals.sample_size))),
    metricCell('Coverage', Number.isFinite(coverage) ? `${coverage.toFixed(1)}%` : '—'),
    metricCell('Measured / Unknown', `${_count(totals.measured_count)} / ${_count(totals.unknown_count)}`),
    metricCell('Pending', String(_count(totals.pending_count))),
    metricCell('Net outcome', _signedMoney(netDollars), netDollars < 0 ? 'ft-td-down' : 'ft-td-signal'),
    metricCell('Capital-days', String(Math.round(_count(totals.capital_days)))),
    metricCell('Owner $/day', Number.isFinite(efficiency) ? `$${efficiency.toFixed(2)}` : '—'),
    metricCell('Avg slippage', Number.isFinite(avgSlippage) ? _signedMoney(avgSlippage) : '—'),
  ].join('');
}

function groupRow(group) {
  const efficiency = _optNum(group.owner_efficiency);
  const coverage = Number(group.coverage_pct);
  const netDollars = _optNum(group.net_dollars);
  return `<tr>
    <td class="ft-td-bold">${escapeHtml(String(group.key || '—'))}</td>
    <td class="ft-td-right">${String(_count(group.sample_size))}</td>
    <td class="ft-td-right">${String(_count(group.measured_count))}
      <span class="ft-td-soft">/${_count(group.unknown_count)}</span></td>
    <td class="ft-td-right">${Number.isFinite(coverage) ? `${coverage.toFixed(1)}%` : '—'}</td>
    <td class="ft-td-right ${netDollars < 0 ? 'ft-td-down' : ''}">${_signedMoney(netDollars)}</td>
    <td class="ft-td-right">${String(Math.round(_count(group.capital_days)))}</td>
    <td class="ft-td-right">${Number.isFinite(efficiency) ? `$${efficiency.toFixed(2)}` : '—'}</td>
  </tr>`;
}

function renderGroups(groups) {
  if (!groups) return;
  const noneRow = '<tr><td colspan="7" class="ft-td-center ft-td-mute">No groups yet</td></tr>';
  for (const { id, api } of Object.values(GROUPS)) {
    const el = document.getElementById(id);
    if (!el) continue;
    const rows = Array.isArray(groups[api]) ? groups[api] : [];
    if (rows.length === 0) {
      el.innerHTML = noneRow;
      continue;
    }
    el.innerHTML = rows.map(groupRow).join('');
  }
}

function fillRow(fill) {
  const side = String(fill.side || '—');
  const sideCls = side === 'SELL' ? 'ft-td-signal' : side === 'BUY' ? 'ft-td-warn' : '';
  const captured = fill.captured_at
    ? escapeHtml(String(fill.captured_at).replace('T', ' ').slice(0, 19))
    : '—';
  return `<tr class="outcome-fill-row" style="display:none">
    <td></td>
    <td colspan="9" class="outcome-fill-cell">
      <div class="outcome-fill__grid">
        <span class="outcome-fill__row"><span class="${sideCls}">${escapeHtml(side)}</span></span>
        <span class="outcome-fill__row">${captured}</span>
        <span class="outcome-fill__row">${String(_count(fill.qty))} × ${fill.price != null ? formatCurrency(fill.price) : '—'}</span>
        <span class="outcome-fill__row">Fees: ${fill.fees != null ? formatCurrency(fill.fees) : '—'}</span>
        <span class="outcome-fill__row ft-td-mute">#${escapeHtml(String(fill.fill_id || '—'))}</span>
      </div>
    </td>
  </tr>`;
}

function recordRow(record) {
  const fills = Array.isArray(record.fills) ? record.fills : [];
  const net = _optNum(record.net_pnl);
  const efficiency = _optNum(record.owner_efficiency);
  const slippage = _optNum(record.slippage_per_contract);
  const status = String(record.outcome_status || 'pending');
  const rows = [`<tr class="outcome-record-row" data-expandable="true" tabindex="0" aria-expanded="false">
    <td class="ft-td-bold">${_contractLabel(record)}</td>
    <td>${escapeHtml(String(record.signal_type || '—'))}</td>
    <td>${_statusBadge(status)}</td>
    <td class="ft-td-right">${record.quoted_credit_per_contract != null ? formatCurrency(record.quoted_credit_per_contract) : '—'}</td>
    <td class="ft-td-right">${record.filled_credit_per_contract != null ? formatCurrency(record.filled_credit_per_contract) : '—'}</td>
    <td class="ft-td-right ${Number.isFinite(slippage) && slippage < 0 ? 'ft-td-down' : ''}">${Number.isFinite(slippage) ? _signedMoney(slippage) : '—'}</td>
    <td class="ft-td-right ${Number.isFinite(net) && net < 0 ? 'ft-td-down' : 'ft-td-signal'}">${Number.isFinite(net) ? _signedMoney(net) : '—'}</td>
    <td class="ft-td-right">${String(Math.round(_count(record.capital_days)))}</td>
    <td class="ft-td-right">${Number.isFinite(efficiency) ? `$${efficiency.toFixed(2)}` : '—'}</td>
    <td class="ft-td-right">${String(fills.length)} <span class="ft-td-soft">${record.open ? '· open' : ''}</span></td>
    <td class="ft-td-center ft-td-mute" aria-hidden="true">${fills.length ? '▸' : ''}</td>
  </tr>`];
  if (fills.length) rows.push(...fills.map(fillRow));
  return rows.join('');
}

function renderRecords(outcomes) {
  if (!_recordsBody) return;
  const countEl = document.getElementById(RECORDS_COUNT_ID);
  const list = Array.isArray(outcomes) ? outcomes : [];
  if (countEl) countEl.textContent = `· ${list.length}`;
  if (list.length === 0) {
    _recordsBody.innerHTML = '<tr><td colspan="11" class="ft-td-center ft-td-mute">No verified signals yet — complete a scan, then pull broker fills.</td></tr>';
    return;
  }
  _recordsBody.innerHTML = list.map(recordRow).join('');
}

/** Toggle a contract's supporting-fills detail rows (all adjacent fill rows). */
function toggleFillDetail(rowEl) {
  if (!rowEl) return;
  const expanded = rowEl.getAttribute('aria-expanded') === 'true';
  rowEl.setAttribute('aria-expanded', String(!expanded));
  const chevron = rowEl.querySelector('[aria-hidden="true"]');
  if (chevron) chevron.textContent = expanded ? '▸' : '▾';
  let sibling = rowEl.nextElementSibling;
  while (sibling && sibling.classList.contains('outcome-fill-row')) {
    sibling.style.display = expanded ? 'none' : '';
    sibling = sibling.nextElementSibling;
  }
}

function bindExpand() {
  if (!_recordsBody) return;
  _recordsBody.querySelectorAll('.outcome-record-row').forEach((row) => {
    if (row.dataset.bound) return;
    row.dataset.bound = 'true';
    const openDetail = () => toggleFillDetail(row);
    row.addEventListener('click', openDetail);
    row.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openDetail();
      }
    });
  });
}

async function fetchOutcomes() {
  try {
    const resp = await fetch('/api/options/analytics/outcomes?limit=500');
    if (!resp.ok) {
      console.error('Outcome panel: bad status', resp.status);
      return null;
    }
    return await resp.json();
  } catch (err) {
    console.error('Outcome panel: fetch failed:', err);
    return null;
  }
}

/** Pull fill/fee history from OpenD (query-only), then refresh the view. */
export async function ingestBrokerFills() {
  const btn = document.getElementById(INGEST_BTN_ID);
  if (btn && btn.disabled) return;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-arrow-repeat" aria-hidden="true"></i> Syncing…';
  }
  try {
    const resp = await fetch('/api/options/analytics/outcomes/ingest?days=90&cash_flow_days=7', { method: 'POST' });
    const payload = await resp.json().catch(() => null);
    if (!resp.ok || !payload || payload.success !== true) {
      const message = (payload && payload.error) || `HTTP ${resp.status}`;
      StateModel.showError(STATE_ID, `Broker fill sync failed: ${message}`);
      return;
    }
    const fills = payload.fills || {};
    const cash = payload.cash_flows || {};
    const note = `Synced ${_count(fills.ingested)} fills and ${_count(cash.ingested)} cash-flow days.`;
    await renderOutcomePanel();
    if (_stateEl) {
      const banner = document.createElement('div');
      banner.className = 'ft-td-mute outcome-sync-note';
      banner.textContent = note;
      _stateEl.appendChild(banner);
    }
  } catch (err) {
    console.error('Outcome panel: ingest failed:', err);
    StateModel.showError(STATE_ID, `Broker fill sync failed: ${err.message || err}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-arrow-repeat" aria-hidden="true"></i> Pull broker fills';
    }
  }
}

export async function renderOutcomePanel() {
  initElements();
  if (!_recordsBody && !_stateEl && !document.getElementById(TOTALS_ID)) return; // panel not in DOM

  const btn = document.getElementById(INGEST_BTN_ID);
  if (btn && !btn.dataset.bound) {
    btn.dataset.bound = 'true';
    btn.addEventListener('click', ingestBrokerFills);
  }

  if (_stateEl) {
    StateModel.showLoading(STATE_ID, 'Loading verified outcomes…');
  }

  const payload = await fetchOutcomes();
  if (!_stateEl && !_recordsBody) return;

  const totalsEl = document.getElementById(TOTALS_ID);
  if (!payload || payload.success === false) {
    const message = (payload && payload.error) || 'Could not load outcome analytics.';
    if (_stateEl) StateModel.showError(STATE_ID, message);
    if (totalsEl) totalsEl.innerHTML = '';
    if (_recordsBody) {
      _recordsBody.innerHTML = '<tr><td colspan="11" class="ft-td-center ft-td-mute">Outcome data unavailable.</td></tr>';
    }
    return;
  }

  renderTotals(payload.totals);
  renderGroups(payload.groups);
  renderRecords(payload.outcomes);
  bindExpand();

  if (_stateEl) _stateEl.innerHTML = '';
}
