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

});
