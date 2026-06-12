"""
Score regression tests.

Loads fixtures from tests/fixtures/ and asserts:
  - pass/fail matches expected
  - rank order is stable
  - warnings are present/absent as expected
  - data provenance fields exist
"""

import unittest
from datetime import datetime, timedelta
from core.wheel_decision import score_contract, WheelDecision
from tests.fixtures import (
    get_cc_healthy,
    get_cc_below_cost_basis,
    get_csp_healthy,
    get_csp_low_cash,
    get_low_iv_scenario,
    get_high_iv_scenario,
    get_wide_spread_scenario,
    get_earnings_today_scenario,
    get_missing_greeks_scenario,
    get_yfinance_fallback_scenario,
)


class TestScoreRegression(unittest.TestCase):
    """Golden-path regression tests for score_contract()."""

    # -- Covered Call scenarios ----------------------------

    def test_cc_healthy_passes(self):
        """Healthy CC: 200 shares, clean liquidity, good IV."""
        option, profile, portfolio, expected = get_cc_healthy()
        stock_price = 155.0
        result = score_contract(
            'AAPL', option, stock_price, profile, portfolio,
            iv_status_str='extreme_low', iv_rank=0.15
        )
        self.assertIsNotNone(result)
        self.assertIsInstance(result, WheelDecision)
        self.assertGreaterEqual(result.contract_score, expected['min_score'])
        self.assertEqual(result.option_type, 'CALL')
        # Should NOT warn about cost basis
        warning_text = ' '.join(result.warnings)
        self.assertNotIn('cost basis', warning_text.lower())

    def test_cc_below_cost_basis_warns(self):
        """CC below cost basis should warn."""
        option, profile, portfolio, expected = get_cc_below_cost_basis()
        stock_price = 150.0
        result = score_contract(
            'AAPL', option, stock_price, profile, portfolio,
            iv_status_str='extreme_low', iv_rank=0.15
        )
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result.contract_score, expected['min_score'])
        warning_text = ' '.join(result.warnings).lower()
        self.assertIn(expected['warning_contains'].lower(), warning_text)

    # -- Cash-Secured Put scenarios -----------------------

    def test_csp_healthy_passes(self):
        """Healthy CSP: enough cash, clean liquidity."""
        option, profile, portfolio, expected = get_csp_healthy()
        stock_price = 100.0
        result = score_contract(
            'AAPL', option, stock_price, profile, portfolio,
            iv_status_str='extreme_low', iv_rank=0.15
        )
        self.assertIsNotNone(result)
        self.assertIsInstance(result, WheelDecision)
        self.assertGreaterEqual(result.contract_score, expected['min_score'])
        self.assertEqual(result.option_type, 'PUT')
        # PUT return should use strike * 100, not stock_price * 100
        self.assertAlmostEqual(result.cash_required, expected['cash_required'], delta=1.0)

    def test_csp_low_cash_fails(self):
        """CSP with insufficient cash should return a decision with hard_blockers."""
        option, profile, portfolio, expected = get_csp_low_cash()
        stock_price = 550.0
        result = score_contract(
            'AAPL', option, stock_price, profile, portfolio,
            iv_status_str='extreme_low', iv_rank=0.15
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    # -- Environment scenarios ----------------------------

    def test_low_iv_suppresses_score(self):
        """Low IV should produce lower score and IV warning."""
        option, profile, portfolio, expected = get_low_iv_scenario()
        stock_price = 100.0
        result = score_contract(
            'AAPL', option, stock_price, profile, portfolio,
            iv_status_str='extreme_low', iv_rank=0.15
        )
        self.assertIsNotNone(result)
        self.assertLessEqual(result.contract_score, expected['min_score'])
        warning_text = ' '.join(result.warnings).lower()
        self.assertIn(expected['warning_contains'].lower(), warning_text)

    def test_high_iv_boosts_and_warns(self):
        """High IV should produce high score but warn about extreme IV."""
        option, profile, portfolio, expected = get_high_iv_scenario()
        stock_price = 100.0
        result = score_contract(
            'AAPL', option, stock_price, profile, portfolio,
            iv_status_str='extreme_high', iv_rank=0.60
        )
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result.contract_score, expected['min_score'])
        warning_text = ' '.join(result.warnings).lower()
        self.assertIn(expected['warning_contains'].lower(), warning_text)

    # -- Edge case scenarios ------------------------------

    def test_wide_spread_fails(self):
        """Wide spread (> max_spread_pct) should return a decision with hard_blockers."""
        option, profile, portfolio, expected = get_wide_spread_scenario()
        stock_price = 100.0
        result = score_contract(
            'AAPL', option, stock_price, profile, portfolio,
            iv_status_str='extreme_low', iv_rank=0.15
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_earnings_today_warns(self):
        """Earnings today should warn and reduce score."""
        option, profile, portfolio, expected = get_earnings_today_scenario()
        stock_price = 100.0
        # Add earnings_info to kwargs
        earnings_info = {
            'earnings_date': datetime.now().strftime('%Y-%m-%d'),
            'days_to_earnings': 0,
            'warning_level': 'today',
        }
        result = score_contract(
            'AAPL', option, stock_price, profile, portfolio,
            earnings_adjustment=-50,
            earnings_info=earnings_info,
        )
        self.assertIsNotNone(result)
        warning_text = ' '.join(result.warnings).lower()
        self.assertIn('earnings today', warning_text)

    def test_missing_greeks_computed(self):
        """Missing Greeks should be computed via Black-Scholes."""
        option, profile, portfolio, expected = get_missing_greeks_scenario()
        stock_price = 100.0
        result = score_contract(
            'AAPL', option, stock_price, profile, portfolio,
            iv_status_str='extreme_low', iv_rank=0.15
        )
        self.assertIsNotNone(result)
        # Delta should have been computed (non-zero)
        self.assertGreater(abs(result.delta), 0.001)

    def test_missing_iv_blocks_contract(self):
        """Missing IV should not receive inflated IV-adjusted and EV scores."""
        profile = _get_base_profile()
        portfolio = _get_csp_portfolio(cash=50000)
        option = _make_option(strike=95, bid=2.0, ask=2.10, oi=500, vol=200, delta=-0.25, iv=0)
        option['expiration'] = (datetime.now() + timedelta(days=37)).strftime('%Y%m%d')

        result = score_contract('AAPL', option, 100.0, profile, portfolio)

        self.assertTrue(result.hard_blockers)
        self.assertIn('missing_iv', result.blocked_reason_codes)

    def test_put_sizing_can_exceed_one_contract_under_risk_cap(self):
        """PUT max_contracts should reflect available buying power before 10% sizing cap."""
        profile = _get_base_profile()
        portfolio = _get_csp_portfolio(cash=100000)
        portfolio['broker_buying_power'] = 100000.0
        portfolio['account_value'] = 100000.0
        option = _make_option(strike=50, bid=1.0, ask=1.10, oi=500, vol=200, delta=-0.25, iv=0.30)
        option['expiration'] = (datetime.now() + timedelta(days=37)).strftime('%Y%m%d')

        result = score_contract('AAPL', option, 60.0, profile, portfolio)

        self.assertFalse(result.hard_blockers)
        self.assertEqual(result.max_contracts, 20)
        self.assertEqual(result.recommended_contracts, 2)

    def test_yfinance_fallback_warns(self):
        """yfinance fallback should warn about data source."""
        option, profile, portfolio, expected = get_yfinance_fallback_scenario()
        stock_price = 100.0
        result = score_contract(
            'AAPL', option, stock_price, profile, portfolio,
            iv_status_str='extreme_low', iv_rank=0.15
        )
        self.assertIsNotNone(result)
        warning_text = ' '.join(result.warnings).lower()
        self.assertIn('yfinance', warning_text)

    def test_unknown_iv_status_returns_neutral_scoring(self):
        """Unknown IV status (0 IV) should not crash and produce neutral score."""
        profile = _get_base_profile()
        portfolio = _get_csp_portfolio(cash=20000)
        option = _make_option(strike=95, bid=2.0, ask=2.10, oi=500, vol=200, delta=-0.25, iv=0.30)
        option['expiration'] = (datetime.now() + timedelta(days=37)).strftime('%Y%m%d')

        result = score_contract(
            'AAPL', option, 100.0, profile, portfolio,
            iv_env_adjustment=0, iv_status_str='unknown', iv_rank=0.5,
        )
        self.assertIsNotNone(result)
        self.assertGreater(result.contract_score, 0)
        self.assertFalse(result.hard_blockers)
        # iv_environment sub-score should reflect neutral position (0 adjustment)
        iv_env_score = result.score_details.get('iv_environment', 50)
        self.assertAlmostEqual(iv_env_score, 50.0, places=0)


class TestScoreRankOrder(unittest.TestCase):
    """Tests that ranking rules produce correct ordering."""

    def test_tight_spread_beats_high_yield_wide_spread(self):
        """Tight spread should outrank higher yield with poor liquidity."""
        future = (datetime.now() + timedelta(days=21)).strftime('%Y%m%d')
        profile = _get_base_profile()

        # Candidate A: tight spread, moderate yield
        opt_a = _make_option(strike=95, bid=2.0, ask=2.10, oi=500, vol=200, delta=-0.25, iv=0.30)
        opt_a['expiration'] = future

        # Candidate B: wider spread, higher yield
        opt_b = _make_option(strike=94, bid=3.0, ask=3.50, oi=100, vol=50, delta=-0.30, iv=0.35)
        opt_b['expiration'] = future

        portfolio = _get_csp_portfolio(cash=20000)

        result_a = score_contract('AAPL', opt_a, 100.0, profile, portfolio)
        result_b = score_contract('AAPL', opt_b, 100.0, profile, portfolio)

        self.assertIsNotNone(result_a)
        self.assertIsNotNone(result_b)
        # With tight spread and good liquidity, A should outrank B
        self.assertGreaterEqual(result_a.contract_score, result_b.contract_score)

    def test_earnings_today_not_top_ranked(self):
        """Earnings today should severely penalize score."""
        future = (datetime.now() + timedelta(days=21)).strftime('%Y%m%d')
        profile = _get_base_profile()

        # Normal candidate
        opt_normal = _make_option(strike=95, bid=2.0, ask=2.10, oi=500, vol=200, delta=-0.25, iv=0.30)
        opt_normal['expiration'] = future

        # Earnings today candidate (same metrics but earnings penalty)
        opt_earnings = _make_option(strike=95, bid=2.0, ask=2.10, oi=500, vol=200, delta=-0.25, iv=0.30)
        opt_earnings['expiration'] = future

        portfolio = _get_csp_portfolio(cash=20000)

        result_normal = score_contract('AAPL', opt_normal, 100.0, profile, portfolio)
        result_earnings = score_contract(
            'AAPL', opt_earnings, 100.0, profile, portfolio,
            earnings_adjustment=-50,
        )

        self.assertIsNotNone(result_normal)
        self.assertIsNotNone(result_earnings)
        self.assertGreater(result_normal.contract_score, result_earnings.contract_score)


class TestScoreDetailsPresent(unittest.TestCase):
    """Every scored contract must have complete score_details."""

    def test_score_details_all_keys_present(self):
        """score_details should have all expected keys."""
        option, profile, portfolio, _ = get_csp_healthy()
        result = score_contract('AAPL', option, 100.0, profile, portfolio)
        self.assertIsNotNone(result)
        details = result.score_details
        expected_keys = [
            'annualized', 'buffer', 'liquidity', 'delta_fit',
            'otm_fit', 'capital_fit', 'iv_adjusted', 'theta_delta',
            'expected_value', 'capital_efficiency', 'iv_environment',
        ]
        for key in expected_keys:
            self.assertIn(key, details, f"Missing key in score_details: {key}")

    def test_rationale_not_empty(self):
        """Rationale should explain the score."""
        option, profile, portfolio, _ = get_csp_healthy()
        result = score_contract('AAPL', option, 100.0, profile, portfolio)
        self.assertIsNotNone(result)
        self.assertGreater(len(result.rationale), 0)
        for line in result.rationale:
            self.assertIsInstance(line, str)
            self.assertGreater(len(line), 5)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_base_profile():
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


def _get_csp_portfolio(cash=10000):
    return {
        'positions': {},
        'cash_balance': float(cash),
        'account_value': 50000.0,
        'short_calls': {},
        'short_puts': {},
    }


def _make_option(strike, bid, ask, oi, vol, delta, iv, option_type='PUT'):
    return {
        'strike': strike,
        'option_type': option_type,
        'bid': bid,
        'ask': ask,
        'last': (bid + ask) / 2,
        'delta': delta,
        'gamma': 0.04,
        'theta': -0.06,
        'vega': 0.15,
        'implied_volatility': iv,
        'open_interest': oi,
        'volume': vol,
    }


if __name__ == '__main__':
    unittest.main()
