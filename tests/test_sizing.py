"""Tests for core/sizing.py — portfolio-aware exposure arithmetic."""

import unittest

from core.sizing import existing_short_exposure_by_underlying


class TestExistingShortExposure(unittest.TestCase):
    def test_empty_and_missing(self):
        self.assertEqual(existing_short_exposure_by_underlying(None), {})
        self.assertEqual(existing_short_exposure_by_underlying({}), {})
        self.assertEqual(existing_short_exposure_by_underlying({"short_puts": None}), {})

    def test_combines_puts_and_calls_per_underlying(self):
        ctx = {
            "short_puts": {"TSLA260904P00300000": 2, "AAPL": 1},
            "short_calls": {"TSLA261016C00320000": 1},
        }
        exposure = existing_short_exposure_by_underlying(ctx)
        self.assertEqual(exposure["TSLA"], 3)
        self.assertEqual(exposure["AAPL"], 1)

    def test_negative_counts_clamped(self):
        ctx = {"short_puts": {"SPY": -3}}
        self.assertEqual(existing_short_exposure_by_underlying(ctx)["SPY"], 0)


if __name__ == "__main__":
    unittest.main()
