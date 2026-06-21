import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../frontend/static/js/dashboard/api.js', () => ({
  fetchWeeklyOptionIncome: vi.fn(),
  isOpenDUnavailable: vi.fn(),
}));

vi.mock('../../frontend/static/js/utils/state-model.js', () => ({
  default: {
    showEmpty: vi.fn(),
    showError: vi.fn(),
  },
}));

vi.mock('../../frontend/static/js/utils/formatters.js', () => ({
  escapeHtml: vi.fn((value) => String(value ?? '')),
  formatCurrency: vi.fn((v) => `$${v.toFixed(2)}`),
  formatPercent: vi.fn((v) => `${v.toFixed(1)}%`),
}));

import { fetchWeeklyOptionIncome, isOpenDUnavailable } from '../../frontend/static/js/dashboard/api.js';
import StateModel from '../../frontend/static/js/utils/state-model.js';

function setupDOM() {
  document.body.innerHTML = `
    <section class="app-section income-stage-section">
      <div id="weekly-income-state"></div>
      <div class="table-responsive">
        <table class="table table-hover">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Type</th>
              <th>Strike</th>
              <th>Expiration</th>
              <th>Avg Price</th>
              <th>Qty</th>
              <th>Total Income</th>
              <th>Notional</th>
            </tr>
          </thead>
          <tbody id="filled-orders-table">
            <tr>
              <td colspan="8" class="text-center text-muted py-4">Loading...</td>
            </tr>
          </tbody>
          <tfoot class="income-stage-summary">
            <tr id="weekly-earnings-summary">
              <td colspan="8">
                <div>
                  <span>Weekly income</span>
                  <span id="weekly-earnings-total">$0.00</span>
                </div>
                <div>
                  <span>Position count</span>
                  <span id="weekly-order-count">0</span>
                </div>
                <div>
                  <span>Avg premium</span>
                  <span id="weekly-average-premium">$0.00</span>
                </div>
                <div>
                  <span>PUT notional</span>
                  <span id="weekly-notional-value">$0.00</span>
                </div>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  `;
}

describe('weekly-income rendering', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupDOM();
  });

  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('renders positions in the table on success', async () => {
    fetchWeeklyOptionIncome.mockResolvedValue({
      positions: [
        { symbol: 'NVDA', option_type: 'P', strike: 850.0, expiration: '20260515', position: -2, avg_cost: 15.5, income: 155.0 },
        { symbol: 'AAPL', option_type: 'P', strike: 180.0, expiration: '20260515', position: -1, avg_cost: 8.2, income: 82.0 },
      ],
      total_income: 237.0,
      positions_count: 2,
      this_friday: '20260515',
    });
    isOpenDUnavailable.mockReturnValue(false);

    const { renderWeeklyIncome } = await import(
      '../../frontend/static/js/dashboard/weekly-income.js'
    );

    await renderWeeklyIncome();

    const tbody = document.getElementById('filled-orders-table');
    const rows = tbody.querySelectorAll('tr');
    expect(rows.length).toBe(2);

    expect(rows[0].textContent).toContain('NVDA');
    expect(rows[0].textContent).toContain('Put');
    expect(rows[0].textContent).toContain('$155.00');

    expect(rows[1].textContent).toContain('AAPL');
    expect(rows[1].textContent).toContain('$82.00');

    const totalEl = document.getElementById('weekly-earnings-total');
    expect(totalEl.textContent).toBe('$237.00');

    const countEl = document.getElementById('weekly-order-count');
    expect(countEl.textContent).toBe('2');
  });

  it('shows empty row when no positions', async () => {
    fetchWeeklyOptionIncome.mockResolvedValue({
      positions: [],
      total_income: 0,
      positions_count: 0,
      this_friday: '20260515',
    });
    isOpenDUnavailable.mockReturnValue(false);

    const { renderWeeklyIncome } = await import(
      '../../frontend/static/js/dashboard/weekly-income.js'
    );

    await renderWeeklyIncome();

    const tbody = document.getElementById('filled-orders-table');
    const rows = tbody.querySelectorAll('tr');
    expect(rows.length).toBe(1);
    expect(rows[0].textContent).toContain('No short options');

    const totalEl = document.getElementById('weekly-earnings-total');
    expect(totalEl.textContent).toBe('$0.00');
  });

  it('shows OpenD-unavailable state when OpenD is unavailable', async () => {
    fetchWeeklyOptionIncome.mockResolvedValue({
      positions: [],
      total_income: 0,
      positions_count: 0,
      error: 'OpenD unavailable',
      error_code: 'opend_unavailable',
    });
    isOpenDUnavailable.mockReturnValue(true);

    const { renderWeeklyIncome } = await import(
      '../../frontend/static/js/dashboard/weekly-income.js'
    );

    await renderWeeklyIncome();

    const errorCall = StateModel.showError.mock.calls.find(c => c[0] === 'weekly-income-state');
    expect(errorCall).toBeDefined();
    expect(errorCall[1]).toContain('OpenD unavailable');

    const tbody = document.getElementById('filled-orders-table');
    expect(tbody.querySelectorAll('tr').length).toBe(1);
    expect(tbody.textContent).toContain('No short options');
  });

  it('recognizes PUT option_type and calculates put notional', async () => {
    fetchWeeklyOptionIncome.mockResolvedValue({
      positions: [
        { symbol: 'AAPL', option_type: 'PUT', strike: 180.0, expiration: '20260515', position: -2, avg_cost: 8.0, income: 80.0 },
      ],
      total_income: 80.0,
      positions_count: 1,
      this_friday: '20260515',
    });
    isOpenDUnavailable.mockReturnValue(false);

    const { renderWeeklyIncome } = await import(
      '../../frontend/static/js/dashboard/weekly-income.js'
    );

    await renderWeeklyIncome();

    const tbody = document.getElementById('filled-orders-table');
    const rows = tbody.querySelectorAll('tr');
    expect(rows.length).toBe(1);
    expect(rows[0].textContent).toContain('AAPL');
    expect(rows[0].textContent).toContain('Put');

    // Notional = strike * abs(position) * 100 = 180 * 2 * 100 = 36000
    const notionalEl = document.getElementById('weekly-notional-value');
    expect(notionalEl.textContent).toContain('36000');
  });

  it('recognizes CALL option_type', async () => {
    fetchWeeklyOptionIncome.mockResolvedValue({
      positions: [
        { symbol: 'MSFT', option_type: 'CALL', strike: 400.0, expiration: '20260515', position: -1, avg_cost: 5.0, income: 50.0 },
      ],
      total_income: 50.0,
      positions_count: 1,
      this_friday: '20260515',
    });
    isOpenDUnavailable.mockReturnValue(false);

    const { renderWeeklyIncome } = await import(
      '../../frontend/static/js/dashboard/weekly-income.js'
    );

    await renderWeeklyIncome();

    const tbody = document.getElementById('filled-orders-table');
    const rows = tbody.querySelectorAll('tr');
    expect(rows.length).toBe(1);
    expect(rows[0].textContent).toContain('MSFT');
    expect(rows[0].textContent).toContain('Call');
  });
});
