/**
 * Outcome panel — broker-verified outcome summaries (read-only).
 *
 * Data source: GET /api/options/analytics/outcomes (local SQLite read, no
 * OpenD gate) — quoted vs filled credit, net P&L after fees, capital-days,
 * and per-preset / DTE / ticker / event-tier aggregates.
 * POST /api/options/analytics/outcomes/ingest pulls fill/fee history from
 * OpenD (query-only) when the operator asks, then re-renders.
 *
 * API-fed text is inserted through textContent; markup is built with DOM nodes.
 */
import { formatCurrency } from '../utils/formatters.js';
import StateModel from '../utils/state-model.js';

const STATE_ID = 'outcome-state';
const TOTALS_ID = 'outcome-totals';
const RECORDS_ID = 'outcome-records';
const RECORDS_COUNT_ID = 'outcome-records-count';
const LINK_SUGGESTIONS_ID = 'outcome-link-suggestions';
const DTE_COMPARISONS_ID = 'outcome-dte-comparisons';
const GROUPS = {
  preset: { id: 'outcome-group-preset', api: 'by_preset' },
  dte: { id: 'outcome-group-dte', api: 'by_dte_bucket' },
  ticker: { id: 'outcome-group-ticker', api: 'by_ticker' },
  event: { id: 'outcome-group-event', api: 'by_event_tier' },
};
const INGEST_BTN_ID = 'outcome-ingest-btn';

let _stateEl = null;
let _recordsBody = null;
let _linkSuggestionsEl = null;
let _dteComparisonsEl = null;

function initElements() {
  _stateEl = document.getElementById(STATE_ID);
  _recordsBody = document.getElementById(RECORDS_ID);
  _linkSuggestionsEl = document.getElementById(LINK_SUGGESTIONS_ID);
  _dteComparisonsEl = document.getElementById(DTE_COMPARISONS_ID);
}

/** Numeric-ish count, 0 when undefined. */
function _count(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
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

function optionalCurrency(value) {
  if (value == null || value === '') return '—';
  return formatCurrency(value);
}

function metricCell(label, value, extraClass = '') {
  const wrapper = document.createElement('div');
  wrapper.className = 'outcome-metric';
  const title = document.createElement('p');
  title.className = 'outcome-metric__label';
  title.textContent = label;
  const metric = document.createElement('h3');
  metric.className = `outcome-metric__value ${extraClass}`.trim();
  metric.textContent = value;
  wrapper.append(title, metric);
  return wrapper;
}

function renderTotals(totals) {
  const el = document.getElementById(TOTALS_ID);
  if (!el) return;
  el.replaceChildren();
  if (!totals || totals.sample_size === 0) {
    el.appendChild(metricCell('Signals', '0', 'ft-td-mute'));
    return;
  }
  const coverage = Number(totals.coverage_pct);
  const efficiency = _optNum(totals.owner_efficiency);
  const avgSlippage = _optNum(totals.avg_slippage_per_contract);
  const netDollars = _optNum(totals.net_dollars);
  const metrics = [
    ['Signals (sample)', String(_count(totals.sample_size))],
    ['Coverage', Number.isFinite(coverage) ? `${coverage.toFixed(1)}%` : '—'],
    ['Measured / Unknown', `${_count(totals.measured_count)} / ${_count(totals.unknown_count)}`],
    ['Pending', String(_count(totals.pending_count))],
    ['Net outcome', _signedMoney(netDollars), netDollars < 0 ? 'ft-td-down' : 'ft-td-signal'],
    ['Capital-days', String(Math.round(_count(totals.capital_days)))],
    ['Owner $/day', Number.isFinite(efficiency) ? `$${efficiency.toFixed(2)}` : '—'],
    ['Avg slippage', Number.isFinite(avgSlippage) ? _signedMoney(avgSlippage) : '—'],
  ];
  for (const [label, value, extraClass = ''] of metrics) el.appendChild(metricCell(label, value, extraClass));
}

function groupRow(group) {
  const efficiency = _optNum(group.owner_efficiency);
  const coverage = Number(group.coverage_pct);
  const netDollars = _optNum(group.net_dollars);
  const row = document.createElement('tr');
  const addCell = (text, className = '') => {
    const cell = document.createElement('td');
    cell.className = className;
    cell.textContent = text;
    row.appendChild(cell);
    return cell;
  };
  addCell(String(group.key || '—'), 'ft-td-bold');
  addCell(String(_count(group.sample_size)), 'ft-td-right');
  const countCell = addCell(String(_count(group.measured_count)), 'ft-td-right');
  const unknown = document.createElement('span');
  unknown.className = 'ft-td-soft';
  unknown.textContent = `/${_count(group.unknown_count)}`;
  countCell.appendChild(unknown);
  addCell(Number.isFinite(coverage) ? `${coverage.toFixed(1)}%` : '—', 'ft-td-right');
  addCell(_signedMoney(netDollars), `ft-td-right ${netDollars < 0 ? 'ft-td-down' : ''}`.trim());
  addCell(String(Math.round(_count(group.capital_days))), 'ft-td-right');
  addCell(Number.isFinite(efficiency) ? `$${efficiency.toFixed(2)}` : '—', 'ft-td-right');
  return row;
}

function renderGroups(groups) {
  if (!groups) return;
  for (const { id, api } of Object.values(GROUPS)) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.replaceChildren();
    const rows = Array.isArray(groups[api]) ? groups[api] : [];
    if (rows.length === 0) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 7;
      cell.className = 'ft-td-center ft-td-mute';
      cell.textContent = 'No groups yet';
      row.appendChild(cell);
      el.appendChild(row);
      continue;
    }
    for (const group of rows) el.appendChild(groupRow(group));
  }
}

function fillRow(fill) {
  const row = document.createElement('tr');
  row.className = 'outcome-fill-row';
  row.style.display = 'none';
  row.appendChild(document.createElement('td'));
  const detailCell = document.createElement('td');
  detailCell.colSpan = 9;
  detailCell.className = 'outcome-fill-cell';
  const grid = document.createElement('div');
  grid.className = 'outcome-fill__grid';
  const addDetail = (text, className = 'outcome-fill__row') => {
    const span = document.createElement('span');
    span.className = className;
    span.textContent = text;
    grid.appendChild(span);
  };
  const side = String(fill.side || '—');
  let sideClass = '';
  if (side === 'SELL') sideClass = 'ft-td-signal';
  if (side === 'BUY') sideClass = 'ft-td-warn';
  addDetail(side, `outcome-fill__row ${sideClass}`.trim());
  addDetail(fill.captured_at ? String(fill.captured_at).replace('T', ' ').slice(0, 19) : '—');
  addDetail(`${String(_count(fill.qty))} × ${optionalCurrency(fill.price)}`);
  addDetail(`Fees: ${optionalCurrency(fill.fees)}`);
  addDetail(`#${String(fill.fill_id || '—')}`, 'outcome-fill__row ft-td-mute');
  detailCell.appendChild(grid);
  row.appendChild(detailCell);
  return row;
}

function recordRow(record) {
  const fills = Array.isArray(record.fills) ? record.fills : [];
  const net = _optNum(record.net_pnl);
  const efficiency = _optNum(record.owner_efficiency);
  const slippage = _optNum(record.slippage_per_contract);
  const status = String(record.outcome_status || 'pending');
  const row = document.createElement('tr');
  row.className = 'outcome-record-row';
  row.dataset.expandable = 'true';
  row.tabIndex = 0;
  row.setAttribute('aria-expanded', 'false');
  const cell = (text, className = '') => {
    const td = document.createElement('td');
    td.className = className;
    td.textContent = text;
    return td;
  };
  row.appendChild(cell(plainContractLabel(record), 'ft-td-bold'));
  row.appendChild(cell(String(record.signal_type || '—')));
  const statusCell = document.createElement('td');
  const badge = document.createElement('span');
  const statusClasses = { measured: 'bg-success', open: 'bg-info', unknown: 'bg-warning' };
  badge.className = `badge ${statusClasses[status] || 'bg-secondary'}`;
  badge.textContent = status;
  statusCell.appendChild(badge);
  row.appendChild(statusCell);
  row.appendChild(cell(optionalCurrency(record.quoted_credit_per_contract), 'ft-td-right'));
  row.appendChild(cell(optionalCurrency(record.filled_credit_per_contract), 'ft-td-right'));
  row.appendChild(cell(Number.isFinite(slippage) ? _signedMoney(slippage) : '—', `ft-td-right ${Number.isFinite(slippage) && slippage < 0 ? 'ft-td-down' : ''}`.trim()));
  row.appendChild(cell(Number.isFinite(net) ? _signedMoney(net) : '—', `ft-td-right ${Number.isFinite(net) && net < 0 ? 'ft-td-down' : 'ft-td-signal'}`));
  row.appendChild(cell(String(Math.round(_count(record.capital_days))), 'ft-td-right'));
  row.appendChild(cell(Number.isFinite(efficiency) ? `$${efficiency.toFixed(2)}` : '—', 'ft-td-right'));
  const fillsCell = document.createElement('td');
  fillsCell.className = 'ft-td-right';
  fillsCell.textContent = String(fills.length);
  if (record.open) {
    const open = document.createElement('span');
    open.className = 'ft-td-soft';
    open.textContent = ' · open';
    fillsCell.appendChild(open);
  }
  row.appendChild(fillsCell);
  const chevron = cell(fills.length ? '▸' : '', 'ft-td-center ft-td-mute');
  chevron.setAttribute('aria-hidden', 'true');
  row.appendChild(chevron);
  return [row, ...fills.map(fillRow)];
}

function plainContractLabel(contract) {
  const expiration = String(contract.expiration || '').replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3');
  let strike = '—';
  if (contract.strike != null) strike = `$${Number(contract.strike).toFixed(2)}`;
  const optionType = String(contract.option_type || '').toUpperCase();
  let side = optionType;
  if (optionType === 'PUT') side = 'Put';
  if (optionType === 'CALL') side = 'Call';
  return `${String(contract.ticker || '')} ${expiration} ${side} ${strike}`;
}

function renderLinkSuggestions(suggestions) {
  if (!_linkSuggestionsEl) return;
  const list = Array.isArray(suggestions) ? suggestions : [];
  _linkSuggestionsEl.replaceChildren();
  if (list.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'ft-td-mute';
    empty.textContent = 'No unlinked matching fills.';
    _linkSuggestionsEl.appendChild(empty);
    return;
  }
  for (const suggestion of list) {
    const recommendation = suggestion.recommendation || {};
    const traded = suggestion.traded || {};
    const days = Number(suggestion.days_from_run);
    const body = {
      run_id: suggestion.run_id,
      ticker: recommendation.ticker,
      option_type: recommendation.option_type,
      expiration: recommendation.expiration,
      strike: recommendation.strike,
      traded,
    };
    const item = document.createElement('div');
    item.className = 'outcome-link-suggestion';
    const contracts = document.createElement('span');
    contracts.textContent = `${plainContractLabel(recommendation)} ← ${plainContractLabel(traded)}`;
    const context = document.createElement('span');
    context.className = 'ft-td-mute';
    const distance = Math.abs(days);
    const timing = Number.isFinite(days) ? `${distance} ${distance === 1 ? 'day' : 'days'} ${days < 0 ? 'before' : 'after'} run` : 'date unknown';
    context.textContent = `Run ${String(suggestion.run_generated_at || '')} · ${timing} · ${_count(suggestion.fill_count)} fill(s)`;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'ft-btn ft-btn--ghost outcome-link-confirm';
    button.textContent = 'Confirm link';
    button.dataset.linkPayload = encodeURIComponent(JSON.stringify(body));
    button.addEventListener('click', () => confirmSuggestedLink(button));
    item.append(contracts, context, button);
    _linkSuggestionsEl.appendChild(item);
  }
}

function renderDteComparisons(comparisons) {
  if (!_dteComparisonsEl) return;
  const list = Array.isArray(comparisons) ? comparisons : [];
  _dteComparisonsEl.replaceChildren();
  if (list.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'ft-td-mute';
    empty.textContent = 'No matching owner fills with a saved 21–45 DTE candidate.';
    _dteComparisonsEl.appendChild(empty);
    return;
  }
  for (const item of list) {
    const row = document.createElement('div');
    row.className = 'outcome-link-suggestion';
    const traded = item.traded || {};
    const fill = document.createElement('span');
    fill.textContent = `Owner fill: ${plainContractLabel(traded)} · ${Number(traded.dte) || 0} DTE`;
    const candidate = document.createElement('span');
    candidate.className = 'ft-td-mute';
    if (item.candidate) {
      const pick = item.candidate;
      const rate = Number(pick.capital_velocity_per_day);
      const rateText = Number.isFinite(rate) ? ` · ${(rate * 100).toFixed(3)}%/day` : '';
      candidate.textContent = `Saved candidate #${Number(pick.saved_sample_rank) || 1}: ${plainContractLabel(pick)} · ${Number(pick.dte) || 0} DTE${rateText}`;
    } else {
      candidate.textContent = 'No 21–45 DTE candidate was retained in this saved scan.';
    }
    const scope = document.createElement('span');
    scope.className = 'ft-td-mute';
    scope.textContent = `Run ${String(item.run_generated_at || '')} · saved shortlist only`;
    row.append(fill, candidate, scope);
    _dteComparisonsEl.appendChild(row);
  }
}

async function confirmSuggestedLink(button) {
  button.disabled = true;
  button.textContent = 'Saving…';
  try {
    const body = JSON.parse(decodeURIComponent(button.dataset.linkPayload || ''));
    const response = await fetch('/api/run/taken', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload || payload.ok !== true) {
      StateModel.showError('outcome-state', `Could not confirm trade link: ${(payload && payload.error) || `HTTP ${response.status}`}`);
      return;
    }
    await renderOutcomePanel();
    if (_stateEl) {
      const note = document.createElement('div');
      note.className = 'ft-td-mute outcome-link-note';
      note.textContent = 'Trade link recorded as owner-confirmed.';
      _stateEl.appendChild(note);
    }
  } catch (error) {
    StateModel.showError('outcome-state', `Could not confirm trade link: ${error.message || error}`);
  } finally {
    button.disabled = false;
    if (button.isConnected) button.textContent = 'Confirm link';
  }
}

function renderRecords(outcomes) {
  if (!_recordsBody) return;
  const countEl = document.getElementById(RECORDS_COUNT_ID);
  const list = Array.isArray(outcomes) ? outcomes : [];
  if (countEl) countEl.textContent = `· ${list.length}`;
  _recordsBody.replaceChildren();
  if (list.length === 0) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 11;
    cell.className = 'ft-td-center ft-td-mute';
    cell.textContent = 'No verified signals yet — complete a scan, then pull broker fills.';
    row.appendChild(cell);
    _recordsBody.appendChild(row);
    return;
  }
  for (const record of list) _recordsBody.append(...recordRow(record));
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
  for (const row of _recordsBody.querySelectorAll('.outcome-record-row')) {
    if (row.dataset.bound) continue;
    row.dataset.bound = 'true';
    const openDetail = () => toggleFillDetail(row);
    row.addEventListener('click', openDetail);
    row.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openDetail();
      }
    });
  }
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
  renderLinkSuggestions(payload.link_suggestions);
  renderDteComparisons(payload.dte_comparisons);
  renderRecords(payload.outcomes);
  bindExpand();

  if (_stateEl) _stateEl.innerHTML = '';
}
