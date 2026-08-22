"""Versioned wheel risk presets.

Replaces growth-mode objectives and granular UI overrides. A preset is an
immutable, versioned set of strategy thresholds. Presets may change strategy
parameters but never weaken provenance, freshness, coverage, read-only, or
cash/share constraints (those are enforced outside presets).

The selected preset is persisted in SQLite (`settings` table) and defaults
to ``balanced``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

DEFAULT_PRESET_KEY = "balanced"


@dataclass(frozen=True)
class WheelPreset:
    key: str
    version: int
    label: str
    description: str
    # CSP qualification
    csp_target_delta: float
    csp_delta_tolerance: float
    csp_min_dte: int
    csp_max_dte: int
    csp_preferred_dte: int
    csp_min_otm_pct: float
    csp_max_otm_pct: float
    # Covered-call strike
    call_default_otm_pct: float
    # Liquidity / premium floors
    min_csp_buying_power: float
    min_mid_price: float
    max_spread_pct: float
    min_premium_per_contract: float
    min_open_interest: int
    # Sizing cap (per CSP, % of buying power)
    max_buying_power_pct_per_csp: float
    # Long-horizon growth objective this preset is tuned for (e.g., 10x capital).
    target_account_multiple: float

    def to_dict(self) -> dict:
        return asdict(self)

    def to_screener_profile(self) -> dict:
        """Shape consumed by watchlist/screening code."""
        return {
            "csp_target_delta": self.csp_target_delta,
            "csp_delta_tolerance": self.csp_delta_tolerance,
            "csp_min_dte": self.csp_min_dte,
            "csp_max_dte": self.csp_max_dte,
            "csp_preferred_dte": self.csp_preferred_dte,
            "csp_default_otm_pct": self.csp_min_otm_pct,
            "call_default_otm_pct": self.call_default_otm_pct,
            "csp_min_otm_pct": self.csp_min_otm_pct,
            "csp_max_otm_pct": self.csp_max_otm_pct,
            "min_csp_buying_power": self.min_csp_buying_power,
            "min_mid_price": self.min_mid_price,
            "max_spread_pct": self.max_spread_pct,
            "min_premium_per_contract": self.min_premium_per_contract,
            "min_open_interest": self.min_open_interest,
            "max_buying_power_pct_per_csp": self.max_buying_power_pct_per_csp,
            "target_account_multiple": self.target_account_multiple,
            "max_watchlist_tickers": 25,
            "require_cash_fit": True,
        }


WHEEL_PRESETS: dict[str, WheelPreset] = {
    "conservative": WheelPreset(
        key="conservative",
        version=3,
        label="Conservative",
        description="Stricter liquidity, smaller allocations, farther OTM strikes.",
        target_account_multiple=5.0,
        csp_target_delta=0.25,
        csp_delta_tolerance=0.08,
        csp_min_dte=35,
        csp_max_dte=45,
        csp_preferred_dte=40,
        csp_min_otm_pct=7.0,
        csp_max_otm_pct=15.0,
        call_default_otm_pct=8.0,
        min_csp_buying_power=7500.0,
        min_mid_price=0.10,
        max_spread_pct=45.0,
        min_premium_per_contract=15.0,
        min_open_interest=25,
        max_buying_power_pct_per_csp=50.0,
    ),
    "balanced": WheelPreset(
        key="balanced",
        version=3,
        label="Balanced",
        description="Moderate DTE/delta/liquidity and position-size limits (default).",
        target_account_multiple=10.0,
        csp_target_delta=0.30,
        csp_delta_tolerance=0.12,
        csp_min_dte=30,
        csp_max_dte=45,
        csp_preferred_dte=37,
        csp_min_otm_pct=5.0,
        csp_max_otm_pct=15.0,
        call_default_otm_pct=10.0,
        min_csp_buying_power=5000.0,
        min_mid_price=0.05,
        max_spread_pct=60.0,
        min_premium_per_contract=10.0,
        min_open_interest=10,
        max_buying_power_pct_per_csp=80.0,
    ),
    "aggressive": WheelPreset(
        key="aggressive",
        version=3,
        label="Aggressive",
        description="Shorter DTE, broader deltas, and larger allocations.",
        target_account_multiple=10.0,
        csp_target_delta=0.35,
        csp_delta_tolerance=0.15,
        csp_min_dte=21,
        csp_max_dte=45,
        csp_preferred_dte=30,
        csp_min_otm_pct=3.0,
        csp_max_otm_pct=15.0,
        call_default_otm_pct=12.0,
        min_csp_buying_power=3000.0,
        min_mid_price=0.03,
        max_spread_pct=70.0,
        min_premium_per_contract=5.0,
        min_open_interest=5,
        max_buying_power_pct_per_csp=90.0,
    ),
}


def get_preset(key: str | None) -> WheelPreset:
    """Return the preset for key, falling back to Balanced for unknown keys."""
    if key and key in WHEEL_PRESETS:
        return WHEEL_PRESETS[key]
    return WHEEL_PRESETS[DEFAULT_PRESET_KEY]


def all_presets() -> dict[str, dict]:
    return {key: preset.to_dict() for key, preset in WHEEL_PRESETS.items()}
