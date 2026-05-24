import { describe, it, expect } from 'vitest';
import {
  calculateMidPrice,
  calculateTargetStrike,
  roundStrikeToNearestHalf,
  parseExpirationDate,
  formatDateToAPIfmt,
  formatExpirationDisplay,
  addDaysToDate,
  findClosestStrike,
  isValidStrike,
  calculatePercentDifference,
  formatPercentage,
  getBadgeColor,
} from '../../frontend/static/js/rollover/rollover-calculator.js';

describe('rollover-calculator', () => {
  describe('calculateMidPrice', () => {
    it('averages bid and ask when both are positive', () => {
      expect(calculateMidPrice(2.0, 3.0)).toBe(2.5);
    });

    it('returns bid when only bid is positive', () => {
      expect(calculateMidPrice(2.0, 0)).toBe(2.0);
    });

    it('returns ask when only ask is positive', () => {
      expect(calculateMidPrice(0, 3.0)).toBe(3.0);
    });

    it('returns 0 when both are zero', () => {
      expect(calculateMidPrice(0, 0)).toBe(0);
    });
  });

  describe('calculateTargetStrike', () => {
    it('computes put strike below stock price', () => {
      expect(calculateTargetStrike(100, 10, 'PUT')).toBe(90);
    });

    it('computes call strike above stock price', () => {
      expect(calculateTargetStrike(100, 10, 'CALL')).toBeCloseTo(110);
    });

    it('handles "C" shorthand for call', () => {
      expect(calculateTargetStrike(100, 5, 'C')).toBe(105);
    });
  });

  describe('roundStrikeToNearestHalf', () => {
    it('rounds to nearest 0.5', () => {
      expect(roundStrikeToNearestHalf(95.3)).toBe(95.5);
      expect(roundStrikeToNearestHalf(95.1)).toBe(95.0);
    });

    it('preserves exact half values', () => {
      expect(roundStrikeToNearestHalf(95.5)).toBe(95.5);
    });
  });

  describe('parseExpirationDate', () => {
    it('parses YYYYMMDD format', () => {
      const d = parseExpirationDate('20260524');
      expect(d).not.toBeNull();
      expect(d.getFullYear()).toBe(2026);
      expect(d.getMonth()).toBe(4);
      expect(d.getDate()).toBe(24);
    });

    it('parses YYMMDD format', () => {
      const d = parseExpirationDate('260524');
      expect(d).not.toBeNull();
    });

    it('returns null for null input', () => {
      expect(parseExpirationDate(null)).toBeNull();
    });
  });

  describe('formatDateToAPIfmt', () => {
    it('formats date as YYYYMMDD', () => {
      expect(formatDateToAPIfmt(new Date(2026, 4, 24))).toBe('20260524');
    });
  });

  describe('formatExpirationDisplay', () => {
    it('formats 8-digit date with hyphens', () => {
      expect(formatExpirationDisplay('20260524')).toBe('2026-05-24');
    });

    it('returns "-" for empty input', () => {
      expect(formatExpirationDisplay('')).toBe('-');
    });
  });

  describe('addDaysToDate', () => {
    it('adds days to a date', () => {
      const d = addDaysToDate(new Date(2026, 4, 24), 5);
      expect(d.getDate()).toBe(29);
    });
  });

  describe('findClosestStrike', () => {
    it('finds the closest strike to target', () => {
      expect(findClosestStrike([90, 95, 100], 97)).toBe(95);
    });

    it('returns null for empty array', () => {
      expect(findClosestStrike([], 100)).toBeNull();
    });
  });

  describe('isValidStrike', () => {
    it('returns true for numeric strings', () => {
      expect(isValidStrike('95.5')).toBe(true);
    });

    it('returns false for non-numeric strings', () => {
      expect(isValidStrike('abc')).toBe(false);
    });
  });

  describe('calculatePercentDifference', () => {
    it('computes put OTM percentage', () => {
      const r = calculatePercentDifference(100, 90, 'PUT');
      expect(r.difference).toBe(10);
      expect(r.percentDifference).toBeCloseTo(11.11, 1);
    });
  });

  describe('formatPercentage', () => {
    it('formats numeric value', () => {
      const result = formatPercentage(8.5, false);
      expect(result).toContain('8.50%');
    });

    it('returns 0.00% for null', () => {
      expect(formatPercentage(null, false)).toBe('0.00%');
    });
  });

  describe('getBadgeColor', () => {
    it('returns success for executed', () => {
      expect(getBadgeColor('executed')).toBe('success');
    });

    it('returns danger for cancelled', () => {
      expect(getBadgeColor('cancelled')).toBe('danger');
    });

    it('returns warning for processing', () => {
      expect(getBadgeColor('processing')).toBe('warning');
    });

    it('returns secondary for unknown', () => {
      expect(getBadgeColor('unknown')).toBe('secondary');
    });
  });
});
