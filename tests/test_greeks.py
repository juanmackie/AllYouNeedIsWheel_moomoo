"""Tests for core/greeks.py — S05: stdlib NormalDist replaces SciPy norm.

Verifies compute_bs_greeks agrees with an independent Black-Scholes reference
computed directly from statistics.NormalDist, over representative and deep
tail (far OTM/ITM) cases, and that the resulting values stay within sane
Black-Scholes bounds.
"""

import math
import unittest
from statistics import NormalDist

from core.greeks import _STANDARD_NORMAL, compute_bs_greeks

_RISK_FREE = 0.05


def _reference_greeks(S, K, T, sigma, opt_type, r=_RISK_FREE):
    """Independent Black-Scholes reference using stats.NormalDist directly."""
    sqrt_T = math.sqrt(max(T, 1 / 365))
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    n = NormalDist()
    nd1 = n.pdf(d1)
    cdf = n.cdf
    if opt_type == "CALL":
        delta = cdf(d1)
        theta_year = -(S * nd1 * sigma) / (2.0 * sqrt_T) - r * K * math.exp(-r * T) * cdf(d2)
    else:
        delta = cdf(d1) - 1.0
        theta_year = -(S * nd1 * sigma) / (2.0 * sqrt_T) + r * K * math.exp(-r * T) * cdf(-d2)
    gamma = nd1 / (S * sigma * sqrt_T)
    vega = S * nd1 * sqrt_T / 100.0
    return delta, gamma, theta_year / 365.0, vega


class TestGreeksParity(unittest.TestCase):
    # Representative plus deep tail (far OTM / far ITM) moneyness cases.
    CASES = [
        # (S, K, T_years, iv, type)
        (100.0, 100.0, 0.25, 0.30, "CALL"),  # ATM
        (100.0, 100.0, 0.25, 0.30, "PUT"),  # ATM put
        (100.0, 80.0, 0.5, 0.25, "CALL"),  # ITM call
        (100.0, 125.0, 0.05, 0.50, "CALL"),  # OTM short-dated
        (100.0, 60.0, 1.0, 0.20, "PUT"),  # deep ITM put
        (100.0, 200.0, 1.0, 0.20, "PUT"),  # deep OTM put
    ]

    def test_matches_independent_normaldist_reference(self):
        for S, K, T, iv, opt_type in self.CASES:
            got = compute_bs_greeks(S, K, T, iv, opt_type)
            want = _reference_greeks(S, K, T, iv, opt_type)
            for g, w in zip(got, want):
                self.assertAlmostEqual(g, w, places=9)

    def test_bs_bounds_hold(self):
        # ATM call ~0.5-0.6 delta (drift pushes it above 0.5), ATM put ~ -(0.4-0.5),
        # gamma positive, vega positive.
        d, g, _, v = compute_bs_greeks(100.0, 100.0, 0.25, 0.30, "CALL")
        self.assertGreater(d, 0.5)
        self.assertLess(d, 0.6)
        self.assertGreater(g, 0)
        self.assertGreater(v, 0)
        d_put, _, _, _ = compute_bs_greeks(100.0, 100.0, 0.25, 0.30, "PUT")
        self.assertLess(d_put, -0.4)
        self.assertGreater(d_put, -0.5)

    def test_deep_otm_itm_limits(self):
        # Deep ITM call -> delta ~1; deep OTM call -> delta ~0.
        itm, *_ = compute_bs_greeks(100.0, 30.0, 1.0, 0.20, "CALL")
        self.assertGreater(itm, 0.999)
        otm, *_ = compute_bs_greeks(100.0, 300.0, 0.05, 0.20, "CALL")
        self.assertLess(otm, 1e-6)

    def test_standard_normal_defaults_to_std_normal(self):
        # _STANDARD_NORMAL must be a unit normal (mu=0, sigma=1).
        self.assertAlmostEqual(_STANDARD_NORMAL.cdf(0.0), 0.5)
        self.assertAlmostEqual(_STANDARD_NORMAL.pdf(0.0), 1.0 / math.sqrt(2 * math.pi))

    def test_nonpositive_input_returns_zeros(self):
        self.assertEqual(compute_bs_greeks(0.0, 100.0, 0.25, 0.3, "CALL"), (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(compute_bs_greeks(100.0, 100.0, 0.25, 0.0, "CALL"), (0.0, 0.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
