"""
Tests for core/ticker_utils.py — canonical_underlying helper
"""

import unittest

from core.ticker_utils import canonical_underlying


class TestCanonicalUnderlying(unittest.TestCase):
    """canonical_underlying strips exchange prefixes and normalizes separators."""

    def test_bare_ticker_passes_through(self):
        self.assertEqual(canonical_underlying('AAPL'), 'AAPL')

    def test_strips_us_prefix(self):
        self.assertEqual(canonical_underlying('US.AAPL'), 'AAPL')

    def test_strips_hk_prefix(self):
        self.assertEqual(canonical_underlying('HK.0700'), '0700')

    def test_strips_sz_prefix(self):
        self.assertEqual(canonical_underlying('SZ.000001'), '000001')

    def test_unknown_prefix_kept_as_is(self):
        self.assertEqual(canonical_underlying('XX.YYY'), 'XX-YYY')

    def test_colon_separator_stripped(self):
        self.assertEqual(canonical_underlying('US:UBER'), 'UBER')

    def test_strips_prefix_after_colon(self):
        self.assertEqual(canonical_underlying('BATS:AAPL'), 'AAPL')

    def test_dot_replaced_with_dash(self):
        self.assertEqual(canonical_underlying('BRK.B'), 'BRK-B')

    def test_slash_replaced_with_dash(self):
        self.assertEqual(canonical_underlying('BK/NG'), 'BK-NG')

    def test_dollar_sign_replaced_with_dash(self):
        self.assertEqual(canonical_underlying('ABC$DEF'), 'ABC-DEF')

    def test_us_prefix_with_sub_ticker(self):
        self.assertEqual(canonical_underlying('US.BRK.B'), 'BRK-B')

    def test_returns_empty_string_for_none(self):
        self.assertEqual(canonical_underlying(None), '')

    def test_returns_empty_string_for_empty_string(self):
        self.assertEqual(canonical_underlying(''), '')

    def test_returns_empty_string_for_blank_string(self):
        self.assertEqual(canonical_underlying('   '), '   ')

    def test_case_preserved(self):
        self.assertEqual(canonical_underlying('US.XPEV'), 'XPEV')

    def test_moomoo_style_us_prefix(self):
        self.assertEqual(canonical_underlying('US.UBER'), 'UBER')


if __name__ == '__main__':
    unittest.main()
