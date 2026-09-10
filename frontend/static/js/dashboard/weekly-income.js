import { escapeHtml, formatCurrency } from '../utils/formatters.js';
import { fetchWeeklyOptionIncome } from './api.js';
import { isOpenDUnavailable } from './api.js';
import StateModel from '../utils/state-model.js';

const TABLE_BODY_ID = 'filled-orders-table';
const STATE_CONTAINER_ID = 'weekly-income-state';
const SUMMARY = {
  total: 'weekly-earnings-total',
  count: 'weekly-order-count',
  avgPremium: 'weekly-average-premium',
  notional: 'weekly-notional-value',
};

let _stateEl = null;
let _tableBody = null;
let _summaryEls = null;

function initElements() {
  _tableBody = document.getElementById(TABLE_BODY_ID);
  _stateEl = document.getElementById(STATE_CONTAINER_ID);
  _summaryEls = {
    total: document.getElementById(SUMMARY.total),
    count: document.getElementById(SUMMARY.count),
    avgPremium: document.getElementById(SUMMARY.avgPremium),
    notional: document.getElementById(SUMMARY.notional),
  };
}

function resetElements() {
  _tableBody = null;
  _stateEl = null;
  _summaryEls = null;
}

function row(position) {
  const isPut = ['P', 'PUT'].includes(position.option_type);
  const isCall = ['C', 'CALL'].includes(position.option_type);
  const typeLabel = isPut ? 'Put' : isCall ? 'Call' : position.option_type || '—';
  const typeClass = isPut ? 'text-danger' : isCall ? 'text-success' : '';
  const strike = position.strike != null ? `$${Number(position.strike).toFixed(2)}` : '—';
  const expiration = position.expiration
    ? `${position.expiration.slice(0, 4)}-${position.expiration.slice(4, 6)}-${position.expiration.slice(6, 8)}`
    : '—';
  const avgCost = position.avg_cost != null ? formatCurrency(position.avg_cost) : '—';
  const qty = position.position || 0;
  const income = position.income != null ? formatCurrency(position.income) : '—';
  const notional = position.strike && qty
    ? formatCurrency(position.strike * 100 * Math.abs(qty))
    : '—';

  return `<tr>
    <td><strong>${escapeHtml(position.symbol) || '—'}</strong></td>
    <td><span class="${typeClass} fw-semibold">${escapeHtml(typeLabel)}</span></td>
    <td>${escapeHtml(strike)}</td>
    <td class="small">${escapeHtml(expiration)}</td>
    <td>${escapeHtml(avgCost)}</td>
    <td class="text-center">${escapeHtml(qty)}</td>
    <td class="fw-semibold">${escapeHtml(income)}</td>
    <td class="text-muted small">${escapeHtml(notional)}</td>
  </tr>`;
}

function updateSummary(positions, unavailable = false) {
  if (!_summaryEls) return;
  const totalIncome = positions.reduce((s, p) => s + (p.income || 0), 0);
  const totalQty = positions.reduce((s, p) => s + Math.abs(p.position || 0), 0);
  const putNotional = positions
    .filter(p => ['P', 'PUT'].includes(p.option_type))
    .reduce((s, p) => s + (p.strike || 0) * 100 * Math.abs(p.position || 0), 0);
  const avgPremium = totalQty > 0 ? totalIncome / totalQty : 0;

  // unavailable: data failed to load — show the em-dash, never a bogus zero.
  if (_summaryEls.total) _summaryEls.total.textContent = unavailable ? '—' : formatCurrency(totalIncome);
  if (_summaryEls.count) _summaryEls.count.textContent = unavailable ? '—' : positions.length;
  if (_summaryEls.avgPremium) _summaryEls.avgPremium.textContent = unavailable ? '—' : formatCurrency(avgPremium);
  if (_summaryEls.notional) _summaryEls.notional.textContent = unavailable ? '—' : formatCurrency(putNotional);
}

/**
 * Format the Friday cutoff date the backend reports (YYYY-MM-DD or YYYYMMDD)
 * into a short human label, or the em-dash when unavailable.
 * @param {string|undefined|null} dateStr
 * @returns {string}
 */
function formatFridayDate(dateStr) {
  if (!dateStr) return '—';
  const s = String(dateStr);
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s) || /^(\d{4})(\d{2})(\d{2})$/.exec(s);
  if (!m) return s;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isFinite(d.getTime())
    ? d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    : s;
}

/**
 * Account-level "Option premium" summary (weekly + open short). Uses the same
 * /api/portfolio/weekly-income payload the Friday table renders from, taking
 * the authoritative backend totals. Missing values render as an em-dash, never
 * a hardcoded zero. No-op when the summary panel is absent.
 * @param {Object|null} data - weekly-income payload, or null when unavailable
 */
function updateAccountPremiumSummary(data) {
  const set = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value == null || value === '' ? '—' : value;
  };
  const money = (v) => (v != null && Number.isFinite(Number(v)) ? formatCurrency(Number(v)) : null);
  const count = (v) => (v != null && Number(v) >= 0 ? String(Number(v)) : null);
  const available = data && !data.error;
  set('weekly-income-summary', available ? money(data.total_income) : null);
  set('weekly-positions-count', available ? count(data.positions_count) : null);
  set('friday-date', available ? formatFridayDate(data.this_friday) : null);
  set('open-short-income-summary', available ? money(data.open_short_total_income) : null);
  set('open-short-contracts-count', available ? count(data.open_short_contracts_count) : null);
}

function renderRows(positions) {
  if (!_tableBody) return;
  if (!positions || positions.length === 0) {
    _tableBody.innerHTML = `<tr><td colspan="8" class="text-center text-muted py-4">No short options expiring this Friday.</td></tr>`;
    return;
  }
  _tableBody.innerHTML = positions.map(row).join('');
}

export async function renderWeeklyIncome() {
  resetElements();
  initElements();
  if (!_tableBody) return;

  _tableBody.innerHTML = `<tr><td colspan="8" class="text-center text-muted py-4">Loading short options expiring this Friday...</td></tr>`;
  if (_stateEl) _stateEl.innerHTML = '';

  try {
    const data = await fetchWeeklyOptionIncome();

    if (data && data.error) {
      if (isOpenDUnavailable(data)) {
        if (_stateEl) {
          StateModel.showError(STATE_CONTAINER_ID, 'OpenD unavailable — login required to view weekly income.');
        }
        renderRows([]);
        updateSummary([], true);
        updateAccountPremiumSummary(null);
        return;
      }
      if (_stateEl) {
        StateModel.showError(STATE_CONTAINER_ID, data.error);
      }
      renderRows([]);
      updateSummary([], true);
      updateAccountPremiumSummary(null);
      return;
    }

    const positions = data?.positions || [];
    renderRows(positions);
    updateSummary(positions);
    updateAccountPremiumSummary(data);

    if (_stateEl) _stateEl.innerHTML = '';
  } catch (error) {
    console.error('Error rendering weekly income:', error);
    renderRows([]);
    updateSummary([], true);
    updateAccountPremiumSummary(null);
    if (_stateEl) {
      StateModel.showError(STATE_CONTAINER_ID, 'Failed to load weekly income data.');
    }
  }
}
