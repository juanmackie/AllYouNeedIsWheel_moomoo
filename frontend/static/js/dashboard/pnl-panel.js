/**
 * P&L panel — broker-truth account P&L by underlying (read-only).
 *
 * Data source: GET /api/options/analytics/pnl (local SQLite read, no OpenD
 * gate). Net P&L is broker cash from every fill plus held positions at the
 * broker mark, less known fees. Deposits and withdrawals are not profit and
 * are excluded from the totals.
 *
 * API-fed text is inserted through textContent; markup is built with DOM nodes.
 */
import { formatCurrency } from '../utils/formatters.js';

const STATE_ID = 'pnl-state';
const TOTALS_ID = 'pnl-totals';
const ROWS_ID = 'pnl-rows';
const FOOTNOTE_ID = 'pnl-footnote';
const ENDPOINT = '/api/options/analytics/pnl';
const LOAD_ERROR = 'Could not load P&L. Retry later; your records are unchanged.';
const EMPTY_TEXT = 'No broker fills yet. Run a refresh to pull fills from OpenD.';
const STATUS_LABELS = {
  reconciled: 'Reconciled',
  partial_history: 'Partial history',
  unreconciled: 'Unknown',
};

/** Finite number, or NaN for null, undefined, '' and non-numeric values (never 0). */
function num(value) {
  if (value == null || value === '') return NaN;
  return Number(value);
}

/** Signed currency with explicit +/−, for P&L values. */
function signedMoney(value) {
  const n = num(value);
  if (!Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  if (abs < 0.005) return '$0.00';
  return `${n > 0 ? '+' : '−'}${formatCurrency(abs)}`;
}

function pnlClass(value) {
  const n = num(value);
  if (!Number.isFinite(n) || n === 0) return '';
  // The theme's signal colour is the same red as losses, so gains keep the
  // default ink and only their "+" sign; losses alone are coloured.
  return n < 0 ? 'ft-td-down' : '';
}

function plainCurrency(value) {
  const n = num(value);
  return Number.isFinite(n) ? formatCurrency(n) : '—';
}

function count(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

/** Known fees, with a marker when some fees are not yet known (net excludes them). */
function feesText(fees, feesUnknown) {
  const pending = count(feesUnknown);
  return `${plainCurrency(fees)}${pending > 0 ? ` (+${pending} pending)` : ''}`;
}

/** Unique unclassified cash-flow types, in API order, across currencies. */
function unclassifiedTypes(income) {
  const types = new Set();
  for (const flows of Object.values((income && income.unclassified) || {})) {
    for (const flow of Object.keys(flows || {})) types.add(flow);
  }
  return [...types];
}

function metricCell(label, value, extraClass = '') {
  const wrapper = document.createElement('div');
  wrapper.className = 'summary-metric-cell';
  const title = document.createElement('p');
  title.className = 'summary-metric-label';
  title.textContent = label;
  const metric = document.createElement('h3');
  metric.className = `summary-metric-value ${extraClass}`.trim();
  metric.textContent = value;
  wrapper.append(title, metric);
  return wrapper;
}

/** NAV change since the last deposit or withdrawal, or an em dash when absent. */
function navText(nav) {
  if (!nav) return { text: '—', className: '' };
  const change = num(nav.change);
  if (!Number.isFinite(change)) return { text: '—', className: '' };
  const pct = num(nav.change_pct);
  let pctText = '';
  if (Number.isFinite(pct)) {
    const sign = pct > 0 ? '+' : pct < 0 ? '−' : '';
    pctText = ` (${sign}${Math.abs(pct).toFixed(2)}%)`;
  }
  const unverified = nav.verified === true ? '' : ' · unverified';
  return { text: `${signedMoney(change)}${pctText}${unverified}`, className: pnlClass(change) };
}

function renderTotals(payload) {
  const el = document.getElementById(TOTALS_ID);
  if (!el) return;
  el.replaceChildren();
  const totals = payload.totals || {};
  const income = payload.income || {};
  const nav = navText(payload.nav_since_last_capital_movement);
  const netWithIncome = num(totals.net_pnl_with_income);
  const metrics = [
    ['Net P&L', signedMoney(totals.net_pnl), pnlClass(totals.net_pnl)],
    ['Options', signedMoney(totals.option_pnl), pnlClass(totals.option_pnl)],
    ['Shares', signedMoney(totals.share_pnl), pnlClass(totals.share_pnl)],
    ['Fees', feesText(totals.fees, totals.fees_unknown)],
    ['Interest & dividends (USD)', signedMoney(income.usd_total), pnlClass(income.usd_total)],
    ['Net incl. income', signedMoney(netWithIncome), pnlClass(netWithIncome)],
    ['NAV since last deposit', nav.text, nav.className],
    ['Unreconciled', String(count(totals.unreconciled_count))],
  ];
  for (const [label, value, extraClass = ''] of metrics) el.appendChild(metricCell(label, value, extraClass));
}

function cell(text, className = '') {
  const td = document.createElement('td');
  td.className = className;
  td.textContent = text;
  return td;
}

function underlyingRow(row) {
  const tr = document.createElement('tr');
  const net = num(row.net_pnl);
  tr.append(
    cell(String(row.ticker || '—'), 'ft-td-bold'),
    cell(STATUS_LABELS[row.status] || 'Unknown'),
    cell(signedMoney(row.option_pnl), 'ft-td-right'),
    cell(signedMoney(row.share_pnl), 'ft-td-right'),
    cell(feesText(row.fees, row.fees_unknown), 'ft-td-right'),
    cell(signedMoney(net), `ft-td-right ${pnlClass(net)}`.trim()),
  );
  const notes = Array.isArray(row.notes) ? row.notes : [];
  if (notes.length) tr.setAttribute('title', notes.join('; '));
  return tr;
}

function renderRows(underlyings) {
  const body = document.getElementById(ROWS_ID);
  if (!body) return;
  body.replaceChildren(...underlyings.map(underlyingRow));
}

function renderFootnote(payload) {
  const el = document.getElementById(FOOTNOTE_ID);
  if (!el) return;
  let text = `Positions as of ${payload.as_of || 'unknown'}. Fill history from ${payload.history_from || 'unknown'}.`;
  const deposits = count(payload.capital_movements && payload.capital_movements.count);
  if (deposits > 0) text += ` Deposits/withdrawals excluded: ${deposits}.`;
  const unclassified = unclassifiedTypes(payload.income);
  if (unclassified.length > 0) {
    text += ` Unclassified cash-flow types not counted: ${unclassified.join(', ')}.`;
  }
  el.textContent = text;
}

function clearData() {
  document.getElementById(TOTALS_ID)?.replaceChildren();
  document.getElementById(ROWS_ID)?.replaceChildren();
  const footnote = document.getElementById(FOOTNOTE_ID);
  if (footnote) footnote.textContent = '';
}

function setState(text) {
  const el = document.getElementById(STATE_ID);
  if (el) el.textContent = text;
}

async function fetchPnl() {
  try {
    const resp = await fetch(ENDPOINT);
    if (!resp.ok) {
      console.error('P&L panel: bad status', resp.status);
      return null;
    }
    return await resp.json();
  } catch (err) {
    console.error('P&L panel: fetch failed:', err);
    return null;
  }
}

/** Load the account P&L read model and render totals, underlyings, and the footnote. */
export async function renderPnlPanel() {
  if (!document.getElementById(STATE_ID)) return; // panel not in DOM

  setState('Loading…');
  const payload = await fetchPnl();
  if (!document.getElementById(STATE_ID)) return;

  if (!payload || payload.success === false || !Array.isArray(payload.underlyings)) {
    setState(LOAD_ERROR);
    clearData();
    return;
  }

  if (payload.underlyings.length === 0) {
    setState(EMPTY_TEXT);
    clearData();
    return;
  }

  const unreconciled = count(payload.totals && payload.totals.unreconciled_count);
  setState(
    unreconciled > 0
      ? `${unreconciled} underlying(s) cannot be reconciled yet; their P&L is unknown and excluded from totals.`
      : '',
  );
  renderTotals(payload);
  renderRows(payload.underlyings);
  renderFootnote(payload);
}
