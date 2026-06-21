import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../frontend/static/js/dashboard/options-table-state.js', () => ({
  state: {
    tickersData: {},
    portfolioSummary: null,
    customTickers: new Set(),
    watchlistTickers: new Set(),
  },
}));

import {
  calculatePremium,
  calculateOTMPercentage,
  calculateRecommendedPutQuantity,
  getPremiumPerContract,
} from '../../frontend/static/js/dashboard/options-table-calc.js';

describe('options-table-calc', () => {
  describe('calculatePremium', () => {
    it('averages bid and ask when both are positive', () => {
      expect(calculatePremium(2.0, 3.0, 0)).toBe(2.5);
    });

    it('returns bid when only bid is positive', () => {
      expect(calculatePremium(2.0, 0, 0)).toBe(2.0);
    });

    it('returns ask when only ask is positive', () => {
      expect(calculatePremium(0, 3.0, 0)).toBe(3.0);
    });

    it('returns last when only last is positive', () => {
      expect(calculatePremium(0, 0, 2.5)).toBe(2.5);
    });

    it('returns 0.05 fallback when all are zero', () => {
      expect(calculatePremium(0, 0, 0)).toBe(0.05);
    });
  });

  describe('getPremiumPerContract', () => {
    it('uses authoritative premium_per_contract when present', () => {
      expect(getPremiumPerContract({ premium_per_contract: 280.5, mid_price: 2.1 })).toBe(280.5);
    });

    it('uses mid_price when premium_per_contract is unavailable', () => {
      expect(getPremiumPerContract({ mid_price: 2.805 })).toBe(280.5);
    });

    it('returns null for one-sided quotes without vetted premium', () => {
      expect(getPremiumPerContract({ bid: 0, ask: 2.8, last: 2.7 })).toBeNull();
    });
  });

  describe('calculateOTMPercentage', () => {
    it('computes OTM percentage for put', () => {
      expect(calculateOTMPercentage(95, 100)).toBe(-5);
    });

    it('returns 0 for missing values', () => {
      expect(calculateOTMPercentage(0, 100)).toBe(0);
      expect(calculateOTMPercentage(95, 0)).toBe(0);
    });
  });

  describe('calculateRecommendedPutQuantity', () => {
    beforeEach(() => {
      const { state } = require('../../frontend/static/js/dashboard/options-table-state.js');
      state.portfolioSummary = { cash_balance: 50000 };
    });

    it('returns default 1 when no portfolio summary', () => {
      const { state } = require('../../frontend/static/js/dashboard/options-table-state.js');
      state.portfolioSummary = null;
      const r = calculateRecommendedPutQuantity(100, 95, 'AAPL');
      expect(r.quantity).toBe(1);
    });

    it('computes quantity from cash balance', () => {
      const { state } = require('../../frontend/static/js/dashboard/options-table-state.js');
      state.portfolioSummary = { cash_balance: 100000 };
      const r = calculateRecommendedPutQuantity(100, 95, 'AAPL');
      expect(r.quantity).toBeGreaterThanOrEqual(1);
    });
  });
});
