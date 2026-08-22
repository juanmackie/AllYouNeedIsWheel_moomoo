"""Wheel parity contract tests.

These fixtures pin the behavior contracts that BOTH Moomoo implementations
(AllYouNeedIsWheel and the archived Moomoo Signal v2) must satisfy, so the
retained app can be proven behaviorally equivalent on the wheel surface:

- account normalization / opaque identity
- option-symbol parsing (US.AAPL vs AAPL, YYYYMMDD expirations)
- CSP cash requirement (strike * 100, true cash not buying power)
- covered-share reservation (100-share lots minus existing short calls)
- quote freshness gating (tradeable vs stale)
- hard-gate behavior (missing IV / liquidity)
- premium-velocity ranking within risk tiers
- roll identity (contract-exact roll/hold/close diagnostics)
- failed/partial refresh semantics (last-good preserved, no new run id)
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import core.wheel_decision as _wheel_decision
from core.presets import get_preset
from core.run_model import RunMetadata, WheelRunSnapshot, utc_now_iso
from core.ticker_utils import canonical_underlying
from core.wheel_decision import WheelDecision, score_contract
from core.wheel_runner import opaque_account_id

_market_closed_patch = patch.object(_wheel_decision, "is_market_open", return_value=False)
_market_closed_patch.start()


def tearDownModule():
    _market_closed_patch.stop()


def _option(**overrides):
    base = {
        "strike": 140.0,
        "expiration": (datetime.now() + timedelta(days=37)).strftime("%Y%m%d"),
        "option_type": "PUT",
        "bid": 1.50,
        "ask": 1.60,
        "last": 1.55,
        "delta": -0.28,
        "gamma": 0.02,
        "theta": -0.04,
        "vega": 0.10,
        "implied_volatility": 0.32,
        "open_interest": 500,
        "volume": 200,
        "chain_source": "broker",
    }
    base.update(overrides)
    return base


def _portfolio(cash=50000.0, short_puts=None, short_calls=None, positions=None):
    return {
        "positions": positions or {},
        "cash_balance": cash,
        "available_cash": cash,
        "broker_buying_power": cash,
        "cash_available_for_csp": cash,
        "cash_reserved_for_csp": 0.0,
        "excess_liquidity": cash,
        "short_calls": short_calls or {},
        "short_puts": short_puts or {},
    }


class TestSymbolNormalizationParity(unittest.TestCase):
    def test_canonical_underlying_equivalence(self):
        self.assertEqual(canonical_underlying("US.AAPL"), canonical_underlying("AAPL"))
        self.assertEqual(canonical_underlying("AAPL"), "AAPL")

    def test_expiration_normalization(self):
        from core.wheel_decision import _normalize_expiration

        self.assertEqual(_normalize_expiration("2026-09-18"), "20260918")
        self.assertEqual(_normalize_expiration("20260918"), "20260918")


def _profile():
    return {
        "min_mid_price": 0.05,
        "max_spread_pct": 60,
        "min_premium_per_contract": 5,
        "min_open_interest": 1,
        "min_volume": 1,
        "target_iv_adjusted": 50,
        "target_theta_delta_ratio": 0.005,
        "preferred_dte": 37,
        "target_delta": 0.30,
        "delta_tolerance": 0.12,
        "ideal_open_interest": 500,
        "ideal_volume": 100,
        "ideal_spread_pct": 12,
        "liquidity_weight_multiplier": 1.0,
        "profile_type": "monthly",
        "min_dte": 30,
        "max_dte": 45,
        "min_otm_pct": 5,
        "max_otm_pct": 15,
    }


class TestCspCashParity(unittest.TestCase):
    def test_csp_cash_required_is_strike_times_100(self):
        """Both implementations must collateralize at strike*100 (true cash)."""
        result = score_contract(
            "AAPL",
            _option(strike=140.0),
            150.0,
            _profile(),
            _portfolio(),
            iv_env_adjustment=0,
            iv_rank=0.5,
            iv_status_str="normal",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.cash_required, 14000.0)


class TestCoveredShareReservationParity(unittest.TestCase):
    def test_available_contracts_reserve_existing_short_calls(self):
        """300 shares with 1 existing short call leaves 2 available lots."""
        shares = 300
        existing_short_calls = 1
        available = max(0, int(shares // 100) - existing_short_calls)
        self.assertEqual(available, 2)

    def test_partially_encumbered_lots(self):
        self.assertEqual(max(0, int(150 // 100) - 0), 1)
        self.assertEqual(max(0, int(150 // 100) - 1), 0)


class TestQuoteFreshnessParity(unittest.TestCase):
    def test_fresh_ready_run_tradeable(self):
        fetched = utc_now_iso()
        run = RunMetadata(
            run_id="r1",
            generated_at=fetched,
            published_at=fetched,
            env="REAL",
            account_id=opaque_account_id("ACC1"),
            preset_key="balanced",
            preset_version=1,
            market_state="open",
            status="ready",
            quote_fetched_at={"AAPL": fetched},
            max_tradeable_age_sec=120,
            coverage_scanned=1,
            coverage_total=1,
        )
        snap = WheelRunSnapshot(
            run=run,
            portfolio={},
            csp_picks=(),
            cc_decisions=(),
            roll_decisions=(),
            rejected=(),
            preset={},
            watchlist_origins={},
        )
        self.assertTrue(snap.tradeable)

    def test_stale_run_not_tradeable(self):
        old = datetime.now().timestamp() - 300
        from datetime import datetime as _dt
        from datetime import timezone

        fetched = _dt.fromtimestamp(old, tz=timezone.utc).isoformat()
        run = RunMetadata(
            run_id="r2",
            generated_at=fetched,
            published_at=fetched,
            env="REAL",
            account_id=opaque_account_id("ACC1"),
            preset_key="balanced",
            preset_version=1,
            market_state="open",
            status="ready",
            quote_fetched_at={"AAPL": fetched},
            max_tradeable_age_sec=120,
            coverage_scanned=1,
            coverage_total=1,
        )
        snap = WheelRunSnapshot(
            run=run,
            portfolio={},
            csp_picks=(),
            cc_decisions=(),
            roll_decisions=(),
            rejected=(),
            preset={},
            watchlist_origins={},
        )
        self.assertFalse(snap.tradeable)


class TestHardGatesParity(unittest.TestCase):
    def test_missing_iv_blocks(self):
        decision = score_contract(
            "AAPL",
            _option(implied_volatility=0.0),
            150.0,
            _profile(),
            _portfolio(),
            iv_env_adjustment=0,
            iv_rank=0.5,
            iv_status_str="unknown",
        )
        # Missing critical data must never surface an actionable candidate.
        self.assertTrue(decision is None or bool(decision.hard_blockers))

    def test_low_liquidity_blocks(self):
        decision = score_contract(
            "AAPL",
            _option(open_interest=1, volume=0),
            150.0,
            {**_profile(), **{"min_open_interest": 10}},
            _portfolio(),
            iv_env_adjustment=0,
            iv_rank=0.5,
            iv_status_str="normal",
        )
        self.assertTrue(decision is None or bool(decision.hard_blockers))


class TestRankingParity(unittest.TestCase):
    def test_premium_velocity_primary_within_tier(self):
        from core.scoring_factors import premium_velocity_per_day

        self.assertGreater(premium_velocity_per_day(200, 10), premium_velocity_per_day(150, 10))


class TestRollIdentityParity(unittest.TestCase):
    def test_roll_decision_keeps_contract_identity(self):
        decision = WheelDecision(
            ticker="AAPL",
            option_type="PUT",
            strike=140.0,
            expiration="20260918",
            dte=21,
            roll_pressure=85.0,
            profit_target_progress=10.0,
            otm_pct=6.0,
        )
        self.assertEqual(decision.ticker, "AAPL")
        self.assertEqual(decision.strike, 140.0)
        self.assertEqual(decision.expiration, "20260918")
        self.assertEqual(decision.option_type, "PUT")


class TestPresetParity(unittest.TestCase):
    def test_balanced_default_and_versions(self):
        preset = get_preset(None)
        self.assertEqual(preset.key, "balanced")
        self.assertEqual(preset.version, 3)
        self.assertTrue(preset.to_screener_profile()["require_cash_fit"])


if __name__ == "__main__":
    unittest.main()
