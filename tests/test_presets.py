"""Versioned preset contract tests.

Presets are immutable, versioned strategy thresholds. They may change
strategy parameters but must never weaken provenance, freshness, coverage,
read-only, or cash/share constraints (those live outside presets).
"""

import unittest

from core.presets import DEFAULT_PRESET_KEY, WHEEL_PRESETS, all_presets, get_preset


class TestPresetContract(unittest.TestCase):
    def test_three_presets_exist_and_balanced_is_default(self):
        self.assertEqual(set(WHEEL_PRESETS.keys()), {"conservative", "balanced", "aggressive"})
        self.assertEqual(DEFAULT_PRESET_KEY, "balanced")

    def test_presets_are_frozen_and_versioned(self):
        for key, preset in WHEEL_PRESETS.items():
            self.assertEqual(preset.key, key)
            self.assertGreaterEqual(preset.version, 1)
            with self.assertRaises(Exception):
                preset.csp_min_dte = 1  # frozen dataclass

    def test_risk_ordering_is_monotonic(self):
        """Conservative <= Balanced <= Aggressive on the risk axes."""
        cons = WHEEL_PRESETS["conservative"]
        bal = WHEEL_PRESETS["balanced"]
        agg = WHEEL_PRESETS["aggressive"]

        self.assertLessEqual(cons.csp_target_delta, bal.csp_target_delta)
        self.assertLessEqual(bal.csp_target_delta, agg.csp_target_delta)
        self.assertLessEqual(cons.max_buying_power_pct_per_csp, bal.max_buying_power_pct_per_csp)
        self.assertLessEqual(bal.max_buying_power_pct_per_csp, agg.max_buying_power_pct_per_csp)
        self.assertGreaterEqual(cons.min_open_interest, bal.min_open_interest)
        self.assertGreaterEqual(bal.min_open_interest, agg.min_open_interest)
        self.assertGreaterEqual(cons.min_mid_price, bal.min_mid_price)
        self.assertGreaterEqual(bal.min_mid_price, agg.min_mid_price)

    def test_unknown_key_falls_back_to_balanced(self):
        self.assertIs(get_preset("quantum"), get_preset(DEFAULT_PRESET_KEY))
        self.assertIs(get_preset(None), get_preset(DEFAULT_PRESET_KEY))

    def test_screener_profile_shape_is_complete(self):
        """The profile is the only threshold source: every reader key is present."""
        profile = get_preset("balanced").to_screener_profile()
        for key in (
            "csp_target_delta",
            "csp_delta_tolerance",
            "csp_min_dte",
            "csp_max_dte",
            "csp_preferred_dte",
            "csp_min_otm_pct",
            "csp_max_otm_pct",
            "call_default_otm_pct",
            "call_target_delta",
            "call_delta_tolerance",
            "min_csp_buying_power",
            "min_mid_price",
            "max_spread_pct",
            "min_premium_per_contract",
            "min_open_interest",
            "min_volume",
            "ideal_open_interest",
            "ideal_volume",
            "ideal_spread_pct",
            "max_expirations",
            "max_buying_power_pct_per_csp",
            "target_account_multiple",
            "require_cash_fit",
            # Neutral keys readers subscript directly (no missing-key path).
            "target_delta",
            "delta_tolerance",
            "default_otm_pct",
        ):
            self.assertIn(key, profile, f"missing {key}")
        self.assertTrue(profile["require_cash_fit"])
        self.assertEqual(profile["target_delta"], profile["csp_target_delta"])

    def test_presets_do_not_weaken_safety_constraints(self):
        """No preset may disable cash-fit or drop the buying-power cap."""
        for preset in WHEEL_PRESETS.values():
            self.assertTrue(preset.to_screener_profile()["require_cash_fit"])
            self.assertGreater(preset.max_buying_power_pct_per_csp, 0)
            self.assertLessEqual(preset.max_buying_power_pct_per_csp, 100)

    def test_all_presets_serialize(self):
        data = all_presets()
        self.assertEqual(len(data), 3)
        for key, payload in data.items():
            self.assertEqual(payload["key"], key)


if __name__ == "__main__":
    unittest.main()
