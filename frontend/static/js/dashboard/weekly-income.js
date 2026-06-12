import { formatCurrency } from '../utils/formatters.js';
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
    <td><strong>${position.symbol || '—'}</strong></td>
    <td><span class="${typeClass} fw-semibold">${typeLabel}</span></td>
    <td>${strike}</td>
    <td class="small">${expiration}</td>
    <td>${avgCost}</td>
    <td class="text-center">${qty}</td>
    <td class="fw-semibold">${income}</td>
    <td class="text-muted small">${notional}</td>
  </tr>`;
}

function updateSummary(positions) {
  if (!_summaryEls) return;
  const totalIncome = positions.reduce((s, p) => s + (p.income || 0), 0);
  const totalQty = positions.reduce((s, p) => s + Math.abs(p.position || 0), 0);
  const putNotional = positions
    .filter(p => ['P', 'PUT'].includes(p.option_type))
    .reduce((s, p) => s + (p.strike || 0) * 100 * Math.abs(p.position || 0), 0);
  const avgPremium = totalQty > 0 ? totalIncome / totalQty : 0;

  if (_summaryEls.total) _summaryEls.total.textContent = formatCurrency(totalIncome);
  if (_summaryEls.count) _summaryEls.count.textContent = positions.length;
  if (_summaryEls.avgPremium) _summaryEls.avgPremium.textContent = formatCurrency(avgPremium);
  if (_summaryEls.notional) _summaryEls.notional.textContent = formatCurrency(putNotional);
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
        updateSummary([]);
        return;
      }
      if (_stateEl) {
        StateModel.showError(STATE_CONTAINER_ID, data.error);
      }
      renderRows([]);
      updateSummary([]);
      return;
    }

    const positions = data?.positions || [];
    renderRows(positions);
    updateSummary(positions);

    if (_stateEl) _stateEl.innerHTML = '';
  } catch (error) {
    console.error('Error rendering weekly income:', error);
    renderRows([]);
    updateSummary([]);
    if (_stateEl) {
      StateModel.showError(STATE_CONTAINER_ID, 'Failed to load weekly income data.');
    }
  }
}
