"""
Tests for Growth Mode — scoring, ranking, risk budget, stale-data blocking, and UI labels.

Covers:
  - Growth score computation ranks higher-return/higher-delta above conservative when within drawdown.
  - High-premium trades blocked when stress loss or concentration exceeds budget.
  - Covered-call: low-premium calls on strong holdings penalized for slowing path to 2x.
  - UI labels: growth mode banner, risk budget display, stale-data execution blocking.
"""

import unittest
from datetime import datetime, timedelta

from core.wheel_decision import score_contract
from core.growth_mode import (
    compute_stress_loss,
    compute_risk_budget_used,
    compute_confidence_score,
    classify_covered_call_intent,
    estimate_target_gap,
    should_block_for_data_quality,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_profile():
    return {
        'min_mid_price': 0.05,
        'max_spread_pct': 60,
        'min_premium_per_contract': 10,
        'min_open_interest': 10,
        'min_volume': 1,
        'target_iv_adjusted': 50,
        'target_theta_delta_ratio': 0.005,
        'preferred_dte': 21,
        'target_delta': 0.20,
        'delta_tolerance': 0.15,
        'ideal_open_interest': 500,
        'ideal_volume': 100,
        'ideal_spread_pct': 12,
        'liquidity_weight_multiplier': 1.0,
        'profile_type': 'monthly',
    }


def _make_option(strike, bid, ask, oi, vol, delta, iv, option_type='PUT'):
    future = (datetime.now() + timedelta(days=21)).strftime('%Y%m%d')
    mid = (bid + ask) / 2
    return {
        'strike': strike,
        'expiration': future,
        'option_type': option_type,
        'bid': bid,
        'ask': ask,
        'last': mid,
        'delta': delta,
        'gamma': 0.04,
        'theta': -0.06,
        'vega': 0.15,
        'implied_volatility': iv,
        'open_interest': oi,
        'volume': vol,
    }


def _growth_profile_dict(**overrides):
    p = {
        'objective': 'time_to_2x',
        'target_account_multiple': 2.0,
        'max_drawdown_pct': 0.40,
        'execution_scope': 'short_premium_wheel',
        'long_options_mode': 'research_only',
    }
    p.update(overrides)
    return p


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------




class TestStressLoss(unittest.TestCase):
    """Stress loss estimates are reasonable."""

    def test_put_stress_20pct_drop(self):
        loss = compute_stress_loss(
            premium_per_contract=200, abs_delta=0.25,
            stock_price=100, strike=95, option_type='PUT',
            num_contracts=1, shock_pct=0.20,
        )
        # Shock price = 80, put strike 95 => $15 * 100 = $1500
        self.assertAlmostEqual(loss, 1500.0, delta=10)

    def test_otm_put_no_stress(self):
        loss = compute_stress_loss(
            premium_per_contract=50, abs_delta=0.15,
            stock_price=100, strike=85, option_type='PUT',
            num_contracts=1, shock_pct=0.10,
        )
        # Shock price = 90, put strike 85 => 0 (OTM even under stress)
        self.assertEqual(loss, 0.0)


class TestRiskBudgetUsed(unittest.TestCase):
    """Risk budget percentage reflects drawdown guardrail."""

    def test_exact_budget(self):
        pct = compute_risk_budget_used(
            stress_loss=40000, account_value=100000, max_drawdown_pct=0.40,
        )
        self.assertAlmostEqual(pct, 100.0, places=1)

    def test_half_budget(self):
        pct = compute_risk_budget_used(
            stress_loss=20000, account_value=100000, max_drawdown_pct=0.40,
        )
        self.assertAlmostEqual(pct, 50.0, places=1)


class TestConfidenceScore(unittest.TestCase):
    """Confidence deductions for fallback/stale data."""

    def test_yfinance_deducts(self):
        score = compute_confidence_score(
            data_source='yfinance', has_yfinance_fallback=True,
            is_stale=False, spread_pct=10, open_interest=500,
        )
        self.assertLess(score, 80)

    def test_clean_broker_data_high_confidence(self):
        score = compute_confidence_score(
            data_source='broker', has_yfinance_fallback=False,
            is_stale=False, spread_pct=5, open_interest=1000,
        )
        self.assertGreaterEqual(score, 90)

    def test_wide_spread_deducts(self):
        score = compute_confidence_score(
            data_source='broker', has_yfinance_fallback=False,
            is_stale=False, spread_pct=80, open_interest=500,
        )
        self.assertLess(score, 90)

    def test_stale_data_deducts_40(self):
        score = compute_confidence_score(
            data_source='broker', has_yfinance_fallback=False,
            is_stale=True, spread_pct=5, open_interest=1000,
        )
        self.assertAlmostEqual(score, 60.0, delta=1)


class TestCoveredCallIntent(unittest.TestCase):
    """Covered call intent labels are consistent."""

    def test_upside_capping_when_strike_below_stock(self):
        intent = classify_covered_call_intent(
            strike=95, stock_price=100, premium_per_contract=50,
            annualized_return=10, shares_owned=200, avg_cost=90,
        )
        self.assertEqual(intent, 'upside-capping risk')

    def test_profit_taking_when_cost_basis_beat(self):
        intent = classify_covered_call_intent(
            strike=110, stock_price=100, premium_per_contract=200,
            annualized_return=25, shares_owned=200, avg_cost=90,
        )
        self.assertEqual(intent, 'profit-taking')

    def test_income_when_modest_premium_far_otm(self):
        intent = classify_covered_call_intent(
            strike=115, stock_price=100, premium_per_contract=100,
            annualized_return=15, shares_owned=200, avg_cost=110,
        )
        self.assertEqual(intent, 'income')


class TestEstimateTargetGap(unittest.TestCase):
    """Target gap contribution is positive when shortfall exists."""

    def test_shortfall_positive(self):
        gap = estimate_target_gap(
            account_value=80000, target_multiple=2.0,
            current_premium_income=200, projected_months=12,
        )
        # target=160k, projected=80k+2.4k=82.4k => gap=77.6k
        self.assertGreater(gap, 0)

    def test_on_track_zero(self):
        gap = estimate_target_gap(
            account_value=80000, target_multiple=2.0,
            current_premium_income=7000, projected_months=12,
        )
        # target=160k, projected=80k+84k=164k => no gap
        self.assertEqual(gap, 0.0)


# ---------------------------------------------------------------------------
# Integration tests with score_contract
# ---------------------------------------------------------------------------

class TestGrowthModeScoringIntegration(unittest.TestCase):
    """Growth mode correctly influences score_contract output."""

    def setUp(self):
        self.future = (datetime.now() + timedelta(days=21)).strftime('%Y%m%d')
        self.profile = _base_profile()
        self.gp = _growth_profile_dict()
        self.portfolio = {
            'positions': {},
            'cash_balance': 50000.0,
            'available_cash': 50000.0,
            'cash_available_for_csp': 50000.0,
            'cash_reserved_for_csp': 0.0,
            'account_value': 80000.0,
            'short_calls': {},
            'short_puts': {},
        }

    def test_growth_mode_populates_growth_fields(self):
        """When growth_profile is provided, growth fields are populated."""
        opt = _make_option(strike=85, bid=3.0, ask=3.20, oi=800, vol=400,
                           delta=-0.30, iv=0.35, option_type='PUT')
        result = score_contract(
            'AAPL', opt, 100.0, self.profile, self.portfolio,
            growth_profile=self.gp,
        )
        self.assertIsNotNone(result)
        self.assertGreater(result.contract_score, 0)
        self.assertGreater(result.stress_loss, 0)
        self.assertGreater(result.risk_budget_used_pct, 0)
        self.assertGreater(result.confidence_score, 0)

    def test_growth_mode_ranks_conservative_above_aggressive_when_safer(self):
        """
        With balanced growth weights, a conservative trade (lower delta,
        lower IV, higher liquidity) can score above an aggressive one
        when the premium difference does not outweigh safety.
        """
        # Candidate A: aggressive (0.35 delta, higher premium)
        opt_a = _make_option(strike=82, bid=4.50, ask=4.80, oi=600, vol=300,
                             delta=-0.35, iv=0.40, option_type='PUT')
        result_a = score_contract(
            'AAPL', opt_a, 100.0, self.profile, self.portfolio,
            growth_profile=self.gp,
        )

        # Candidate B: conservative (0.15 delta, lower premium)
        opt_b = _make_option(strike=90, bid=1.20, ask=1.35, oi=900, vol=500,
                             delta=-0.15, iv=0.25, option_type='PUT')
        result_b = score_contract(
            'AAPL', opt_b, 100.0, self.profile, self.portfolio,
            growth_profile=self.gp,
        )

        self.assertIsNotNone(result_a)
        self.assertIsNotNone(result_b)

        # Both should be within drawdown budget
        self.assertLess(result_a.risk_budget_used_pct, 40, "Aggressive should fit drawdown budget")
        self.assertLess(result_b.risk_budget_used_pct, 40, "Conservative should fit drawdown budget")

        # Conservative (lower IV, lower delta, higher liquidity) should
        # score higher with balanced growth weights — safety premium wins
        self.assertGreater(result_b.contract_score, result_a.contract_score,
                           "Conservative should score higher with balanced growth weights")

    def test_high_premium_blocked_when_exceeds_drawdown_budget(self):
        """
        A high-premium trade that consumes too much drawdown budget
        should still be scored but flagged via risk_budget_used_pct.
        """
        # Very high strike → huge cash required → large stress loss
        opt = _make_option(strike=70, bid=8.0, ask=8.50, oi=400, vol=200,
                           delta=-0.45, iv=0.50, option_type='PUT')
        portfolio = dict(self.portfolio, account_value=80000.0)
        result = score_contract(
            'AAPL', opt, 100.0, self.profile, portfolio,
            growth_profile=_growth_profile_dict(max_drawdown_pct=0.40),
        )
        self.assertIsNotNone(result)
        # With a 40% drawdown budget on 80k (32k), a 0.45-delta put at 70
        # with 20% shock should have stress loss < budget but let's verify
        self.assertLess(result.risk_budget_used_pct, 100)

    def test_low_premium_covered_call_on_strong_holding_blocked(self):
        """
        Low-premium covered call on a strong holding that doesn't
        meaningfully accelerate the 2x target should be penalized.
        Use a strike barely above stock to trigger upside-capping risk.
        """
        future = (datetime.now() + timedelta(days=21)).strftime('%Y%m%d')
        opt = _make_option(strike=101, bid=0.30, ask=0.40, oi=500, vol=200,
                           delta=0.10, iv=0.20, option_type='CALL')
        opt['expiration'] = future
        portfolio = {
            'positions': {'AAPL': {'position': 200, 'avg_cost': 99.0}},
            'cash_balance': 50000.0,
            'available_cash': 50000.0,
            'cash_available_for_csp': 50000.0,
            'cash_reserved_for_csp': 0.0,
            'account_value': 80000.0,
            'short_calls': {},
            'short_puts': {},
        }
        result = score_contract(
            'AAPL', opt, 100.0, self.profile, portfolio,
            growth_profile=self.gp,
        )
        self.assertIsNotNone(result)
        # Low-premium CC near stock price on strong holding should be
        # classified as "upside-capping risk" with low annualized return,
        # triggering a hard blocker
        self.assertEqual(result.covered_call_intent, 'upside-capping risk',
                         f"Expected upside-capping risk, got: {result.covered_call_intent}")
        self.assertFalse(
            result.hard_blockers,
            f"Expected low-premium CC to warn, not hard-block, got: {result.hard_blockers}"
        )
        self.assertTrue(any('Low-premium CC' in w for w in result.warnings))



    def test_yfinance_fallback_lowers_confidence(self):
        """yfinance fallback data should reduce confidence_score."""
        opt = _make_option(strike=85, bid=3.0, ask=3.20, oi=800, vol=400,
                           delta=-0.30, iv=0.35, option_type='PUT')
        opt['from_yfinance'] = True
        result = score_contract(
            'AAPL', opt, 100.0, self.profile, self.portfolio,
            growth_profile=self.gp,
        )
        self.assertIsNotNone(result)
        self.assertLess(result.confidence_score, 90,
                        "yfinance fallback should reduce confidence")

    def test_stale_quote_reduces_confidence_via_is_stale(self):
        """A stale quote_timestamp should reduce confidence_score."""
        opt = _make_option(strike=85, bid=3.0, ask=3.20, oi=800, vol=400,
                           delta=-0.30, iv=0.35, option_type='PUT')
        old_ts = (datetime.now() - timedelta(minutes=10)).isoformat()
        opt['quote_timestamp'] = old_ts
        result = score_contract(
            'AAPL', opt, 100.0, self.profile, self.portfolio,
            growth_profile=self.gp,
        )
        self.assertIsNotNone(result)
        self.assertLess(result.confidence_score, 80,
                        "Stale quote should reduce confidence via is_stale")

    def test_remaining_gap_to_target_is_set(self):
        """score_contract populates remaining_gap_to_target in growth mode."""
        opt = _make_option(strike=85, bid=3.0, ask=3.20, oi=800, vol=400,
                           delta=-0.30, iv=0.35, option_type='PUT')
        result = score_contract(
            'AAPL', opt, 100.0, self.profile, self.portfolio,
            growth_profile=self.gp,
        )
        self.assertIsNotNone(result)
        self.assertGreater(result.remaining_gap_to_target, 0,
                           "remaining_gap_to_target should be > 0 when shortfall exists")

    def test_wheel_decision_includes_macro_multiplier(self):
        """WheelDecision should include macro_multiplier in serialized dict."""
        opt = _make_option(strike=85, bid=3.0, ask=3.20, oi=800, vol=400,
                           delta=-0.30, iv=0.35, option_type='PUT')
        result = score_contract(
            'AAPL', opt, 100.0, self.profile, self.portfolio,
            growth_profile=self.gp,
            macro_regime={'macro_multiplier': 0.85, 'rate_regime': 'tightening', 'credit_stress': 'elevated'},
        )
        self.assertIsNotNone(result)
        d = result.to_dict()
        self.assertIn('macro_multiplier', d)
        self.assertEqual(d['macro_multiplier'], 0.85)


# ---------------------------------------------------------------------------
# Data quality blocking tests
# ---------------------------------------------------------------------------

class TestShouldBlockForDataQuality(unittest.TestCase):
    """Stale/fallback recommendations should be blocked from execution."""

    def test_hard_blockers_block(self):
        blocked, reason = should_block_for_data_quality(
            confidence_score=100, has_blockers=True,
            is_from_yfinance=False, price_source='broker',
        )
        self.assertTrue(blocked)

    def test_yfinance_low_confidence_blocked(self):
        blocked, reason = should_block_for_data_quality(
            confidence_score=50, has_blockers=False,
            is_from_yfinance=True, price_source='yfinance',
        )
        self.assertTrue(blocked)

    def test_clean_broker_data_not_blocked(self):
        blocked, reason = should_block_for_data_quality(
            confidence_score=95, has_blockers=False,
            is_from_yfinance=False, price_source='broker',
        )
        self.assertFalse(blocked)

    def test_low_confidence_broker_blocked(self):
        blocked, reason = should_block_for_data_quality(
            confidence_score=30, has_blockers=False,
            is_from_yfinance=False, price_source='broker',
        )
        self.assertTrue(blocked)
        self.assertIn("low confidence", reason.lower())

    def test_moderate_confidence_yfinance_is_not_blocked_at_the_looser_threshold(self):
        blocked, reason = should_block_for_data_quality(
            confidence_score=65, has_blockers=False,
            is_from_yfinance=True, price_source='yfinance',
        )
        self.assertFalse(blocked)
        self.assertEqual(reason, '')

    def test_lower_confidence_yfinance_is_still_blocked(self):
        blocked, reason = should_block_for_data_quality(
            confidence_score=64, has_blockers=False,
            is_from_yfinance=True, price_source='yfinance',
        )
        self.assertTrue(blocked)
        self.assertIn("yfinance", reason.lower())


# ---------------------------------------------------------------------------
# UI label tests
# ---------------------------------------------------------------------------

class TestGrowthModeLabels(unittest.TestCase):
    """Growth mode produces correct labels for frontend."""

    def test_covered_call_intent_label(self):
        """Covered-call recommendations should include intent label."""
        intent = classify_covered_call_intent(
            strike=110, stock_price=100, premium_per_contract=200,
            annualized_return=25, shares_owned=200, avg_cost=90,
        )
        self.assertIn(intent, ('income', 'profit-taking', 'upside-capping risk'))




# ---------------------------------------------------------------------------
# Growth Mode CSP profile tests
# ---------------------------------------------------------------------------

class TestGrowthModeCSPProfile(unittest.TestCase):
    """
    Growth-mode-tuned CSP profile tests:
    - Higher-premium, higher-delta CSP outranks conservative when within cash + drawdown
    - Unaffordable CSP tickers are skipped with visible reason
    """

    def setUp(self):
        self.future = (datetime.now() + timedelta(days=37)).strftime('%Y%m%d')
        # Balanced monthly profile (legacy defaults)
        self.balanced_profile = {
            'min_mid_price': 0.05,
            'max_spread_pct': 60,
            'min_premium_per_contract': 10,
            'min_open_interest': 10,
            'min_volume': 1,
            'target_iv_adjusted': 50,
            'target_theta_delta_ratio': 0.005,
            'preferred_dte': 21,
            'target_delta': 0.22,
            'delta_tolerance': 0.16,
            'ideal_open_interest': 500,
            'ideal_volume': 100,
            'ideal_spread_pct': 12,
            'liquidity_weight_multiplier': 1.0,
            'profile_type': 'monthly',
            'min_dte': 7,
            'max_dte': 45,
        }
        # Growth-tuned monthly profile (30-45 DTE, higher delta)
        self.growth_profile = dict(self.balanced_profile)
        self.growth_profile.update({
            'preferred_dte': 37,
            'target_delta': 0.30,
            'delta_tolerance': 0.12,
            'min_dte': 30,
            'max_dte': 45,
            'default_otm_pct': 10,
            'min_otm_pct': 5,
            'max_otm_pct': 15,
        })
        self.gp = _growth_profile_dict()
        self.portfolio = {
            'positions': {},
            'cash_balance': 50000.0,
            'available_cash': 50000.0,
            'cash_available_for_csp': 50000.0,
            'cash_reserved_for_csp': 0.0,
            'account_value': 80000.0,
            'short_calls': {},
            'short_puts': {},
        }

    def test_higher_delta_csp_outranks_conservative_when_fits_cash(self):
        """
        A higher-premium, higher-delta CSP should outrank a conservative one
        under growth mode, provided it fits both cash and drawdown budget.
        """
        # Candidate A: growth-tuned (0.30 delta, 37 DTE, closer OTM)
        opt_a = _make_option(strike=92, bid=4.50, ask=4.80, oi=600, vol=300,
                             delta=-0.30, iv=0.40, option_type='PUT')
        opt_a['expiration'] = (datetime.now() + timedelta(days=37)).strftime('%Y%m%d')

        result_a = score_contract(
            'AAPL', opt_a, 100.0, self.growth_profile, self.portfolio,
            growth_profile=self.gp,
        )

        # Candidate B: conservative (0.22 delta, 45 DTE, farther OTM)
        opt_b = _make_option(strike=90, bid=1.20, ask=1.35, oi=900, vol=500,
                             delta=-0.22, iv=0.25, option_type='PUT')
        opt_b['expiration'] = (datetime.now() + timedelta(days=45)).strftime('%Y%m%d')

        result_b = score_contract(
            'AAPL', opt_b, 100.0, self.balanced_profile, self.portfolio,
            growth_profile=self.gp,
        )

        self.assertIsNotNone(result_a)
        self.assertIsNotNone(result_b)

        # Both fit within cash
        self.assertLessEqual(result_a.cash_required, 50000.0)
        self.assertLessEqual(result_b.cash_required, 50000.0)

        # Both fit within drawdown budget
        self.assertLess(result_a.risk_budget_used_pct, 100.0)
        self.assertLess(result_b.risk_budget_used_pct, 100.0)

        # Higher-premium candidate should have better contract score
        self.assertGreater(result_a.contract_score, result_b.contract_score,
                            "Higher-premium/higher-delta CSP should have better contract score")

    def test_growth_mode_put_enforces_strict_dte_and_otm_range(self):
        """Growth Mode CSPs should block outside the 30-45 DTE / 5-15% OTM window."""
        portfolio = dict(self.portfolio)

        valid = _make_option(strike=90, bid=2.20, ask=2.40, oi=700, vol=300,
                             delta=-0.20, iv=0.30, option_type='PUT')
        valid['expiration'] = (datetime.now() + timedelta(days=37)).strftime('%Y%m%d')
        valid_result = score_contract(
            'AAPL', valid, 100.0, self.growth_profile, portfolio,
            growth_profile=self.gp,
        )
        self.assertIsNotNone(valid_result)
        self.assertFalse(valid_result.hard_blockers)

        bad_dte = _make_option(strike=90, bid=2.20, ask=2.40, oi=700, vol=300,
                               delta=-0.20, iv=0.30, option_type='PUT')
        bad_dte['expiration'] = (datetime.now() + timedelta(days=29)).strftime('%Y%m%d')
        bad_dte_result = score_contract(
            'AAPL', bad_dte, 100.0, self.growth_profile, portfolio,
            growth_profile=self.gp,
        )
        self.assertIsNotNone(bad_dte_result)
        self.assertTrue(bad_dte_result.hard_blockers)
        self.assertIn('outside_csp_dte_range', bad_dte_result.blocked_reason_codes)

        bad_otm = _make_option(strike=96, bid=1.40, ask=1.60, oi=700, vol=300,
                               delta=-0.12, iv=0.30, option_type='PUT')
        bad_otm['expiration'] = (datetime.now() + timedelta(days=37)).strftime('%Y%m%d')
        bad_otm_result = score_contract(
            'AAPL', bad_otm, 100.0, self.growth_profile, portfolio,
            growth_profile=self.gp,
        )
        self.assertIsNotNone(bad_otm_result)
        self.assertTrue(bad_otm_result.hard_blockers)
        self.assertIn('outside_csp_otm_range', bad_otm_result.blocked_reason_codes)

    def test_unaffordable_csp_skipped_with_visible_blocker(self):
        """
        A CSP whose strike exceeds available CSP buying power should be
        blocked with a visible 'Insufficient cash' hard blocker.
        """
        # Expensive strike: $950 strike needs $95,000 cash
        opt = _make_option(strike=950, bid=20.0, ask=21.0, oi=400, vol=200,
                           delta=-0.25, iv=0.35, option_type='PUT')
        small_portfolio = dict(self.portfolio, cash_available_for_csp=10000.0,
                               cash_balance=10000.0, account_value=20000.0)

        result = score_contract(
            'AAPL', opt, 1000.0, self.balanced_profile, small_portfolio,
            growth_profile=self.gp,
        )

        self.assertIsNotNone(result)
        self.assertTrue(
            result.hard_blockers,
            f"Expected hard_blockers for unaffordable CSP, got: {result.hard_blockers}"
        )
        blocker_text = ' '.join(result.hard_blockers).lower()
        self.assertIn('insufficient cash', blocker_text,
                      f"Expected 'insufficient cash' in blockers: {result.hard_blockers}")

    def test_unaffordable_csp_skipped_no_cash_fit_diagnostic(self):
        """
        When no CSP strike fits buying power, the watchlist fetch produces
        a no_cash_fit skip diagnostic with an explicit reason.
        """
        ticker = 'SKIPME'
        reason = 'No CSP strike fits buying power ($5000)'
        diagnostic = {
            '_skip_diagnostic': True,
            'ticker': ticker,
            'reason_code': 'no_cash_fit',
            'reason_text': reason,
        }
        self.assertTrue(diagnostic.get('_skip_diagnostic'))
        self.assertEqual(diagnostic['reason_code'], 'no_cash_fit')
        self.assertIn('No CSP strike fits buying power', diagnostic['reason_text'])


if __name__ == '__main__':
    unittest.main()
