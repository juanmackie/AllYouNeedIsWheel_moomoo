"""
Tests for core/wheel_decision.py - Unified Wheel Decision Engine
"""

import unittest
from datetime import datetime, timedelta
from core.wheel_decision import (
    WheelDecision,
    _clamp,
    _score_proximity,
    _score_positive_metric,
    _calculate_mid_price,
    _compute_shared_subscores,
    _compute_roll_pressure,
    _compute_profit_target_progress,
    _compute_size_fit,
    _compute_expected_move_buffer,
    score_contract,
    score_existing_position,
)


class TestWheelDecisionDataclass(unittest.TestCase):
    """Test the WheelDecision dataclass"""
    
    def test_default_creation(self):
        """Test creating WheelDecision with default values"""
        decision = WheelDecision()
        self.assertEqual(decision.ticker, "")
        self.assertEqual(decision.option_type, "")
        self.assertEqual(decision.strike, 0.0)
        self.assertEqual(decision.contract_score, 0.0)
        self.assertEqual(decision.warnings, [])
        self.assertEqual(decision.rationale, [])
        self.assertEqual(decision.score_details, {})
    
    def test_custom_creation(self):
        """Test creating WheelDecision with custom values"""
        decision = WheelDecision(
            ticker="AAPL",
            option_type="PUT",
            strike=150.0,
            contract_score=85.5
        )
        self.assertEqual(decision.ticker, "AAPL")
        self.assertEqual(decision.option_type, "PUT")
        self.assertEqual(decision.strike, 150.0)
        self.assertEqual(decision.contract_score, 85.5)


class TestHelperFunctions(unittest.TestCase):
    """Test pure helper functions"""
    
    def test_clamp_within_range(self):
        """Test _clamp when value is within range"""
        self.assertEqual(_clamp(0.5), 0.5)
        self.assertEqual(_clamp(0.0), 0.0)
        self.assertEqual(_clamp(1.0), 1.0)
    
    def test_score_proximity_exact_match(self):
        """Test _score_proximity when value equals target"""
        self.assertEqual(_score_proximity(10.0, 10.0, 5.0), 1.0)
    
    def test_score_positive_metric_below_ideal(self):
        """Test _score_positive_metric when value is below ideal"""
        self.assertEqual(_score_positive_metric(50.0, 100.0), 0.5)
    
    def test_calculate_mid_price_both_valid(self):
        """Test _calculate_mid_price with both bid and ask"""
        self.assertEqual(_calculate_mid_price(5.0, 7.0), 6.0)


class TestComputeRollPressure(unittest.TestCase):
    """Test _compute_roll_pressure function"""
    
    def test_high_dte_low_pressure(self):
        """Test roll pressure with high DTE (low pressure)"""
        decision = WheelDecision(
            option_type="PUT",
            dte=30,
            strike=95.0,
            stock_price=100.0,
            mid_price=2.0,
            extrinsic_remaining=1.50
        )
        pressure = _compute_roll_pressure(decision)
        self.assertGreaterEqual(pressure, 0.0)
        self.assertLessEqual(pressure, 100.0)
    
    def test_low_dte_high_pressure(self):
        """Test roll pressure with low DTE (high pressure)"""
        decision = WheelDecision(
            option_type="PUT",
            dte=3,
            strike=95.0,
            stock_price=100.0,
            mid_price=0.50,
            extrinsic_remaining=0.25
        )
        pressure = _compute_roll_pressure(decision)
        self.assertGreater(pressure, 50.0)
    
    def test_negative_dte_pressure(self):
        """Test roll pressure with negative DTE"""
        decision = WheelDecision(
            option_type="PUT",
            dte=-5,
            strike=95.0,
            stock_price=100.0,
        )
        pressure = _compute_roll_pressure(decision)
        # Negative DTE should result in high pressure
        self.assertGreater(pressure, 50.0)
        self.assertLessEqual(pressure, 100.0)


class TestComputeProfitTargetProgress(unittest.TestCase):
    """Test _compute_profit_target_progress function"""
    
    def test_full_progress(self):
        """Test profit progress with low DTE (high progress)"""
        decision = WheelDecision(dte=5, premium_per_contract=2.0)
        progress = _compute_profit_target_progress(decision)
        self.assertGreater(progress, 50.0)
        self.assertLessEqual(progress, 100.0)
    
    def test_expired(self):
        """Test profit progress with expired option"""
        decision = WheelDecision(dte=0, premium_per_contract=2.0)
        progress = _compute_profit_target_progress(decision)
        self.assertEqual(progress, 100.0)


class TestScoreContract(unittest.TestCase):
    """Test score_contract function"""
    
    def setUp(self):
        """Set up test fixtures"""
        future_date = (datetime.now() + timedelta(days=21)).strftime('%Y%m%d')
        self.base_option = {
            'strike': 95.0,
            'expiration': future_date,
            'option_type': 'PUT',
            'bid': 2.0,
            'ask': 2.50,
            'last': 2.25,
            'delta': -0.20,
            'gamma': 0.05,
            'theta': -0.05,
            'vega': 0.15,
            'implied_volatility': 0.30,
            'open_interest': 500,
            'volume': 100,
        }
        self.base_profile = {
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
        self.base_portfolio = {
            'positions': {},
            'cash_balance': 10000.0,
            'account_value': 50000.0,
            'short_puts': {},
        }
    
    def test_valid_put_contract(self):
        """Test scoring a valid PUT contract"""
        result = score_contract(
            ticker="AAPL",
            option=self.base_option,
            stock_price=100.0,
            profile=self.base_profile,
            portfolio_context=self.base_portfolio,
        )
        self.assertIsNotNone(result)
        self.assertIsInstance(result, WheelDecision)
        self.assertEqual(result.ticker, "AAPL")
        self.assertEqual(result.option_type, "PUT")
        self.assertGreater(result.contract_score, 0)


class TestHelperFunctionsEdgeCases(unittest.TestCase):
    """Edge-case tests for pure helper functions (boundary values, zero-division guards)"""

    # -- _clamp edge cases ------------------------------------------------

    def test_clamp_below_min(self):
        """_clamp returns minimum when value is below range"""
        self.assertEqual(_clamp(-0.5), 0.0)
        self.assertEqual(_clamp(-5.0, 1.0, 10.0), 1.0)

    def test_clamp_above_max(self):
        """_clamp returns maximum when value is above range"""
        self.assertEqual(_clamp(1.5), 1.0)
        self.assertEqual(_clamp(50.0, 0.0, 10.0), 10.0)

    def test_clamp_exact_bounds(self):
        """_clamp returns value when at exact bounds"""
        self.assertEqual(_clamp(0.0), 0.0)
        self.assertEqual(_clamp(1.0), 1.0)

    def test_clamp_degenerate_range(self):
        """_clamp handles min > max (degenerate range)"""
        self.assertEqual(_clamp(5.0, 10.0, 1.0), 10.0)  # clamped to min
        self.assertEqual(_clamp(0.0, 10.0, 1.0), 10.0)  # clamped to min

    # -- _score_proximity edge cases --------------------------------------

    def test_score_proximity_zero_tolerance(self):
        """_score_proximity returns 0 when tolerance is 0"""
        self.assertEqual(_score_proximity(10.0, 10.0, 0.0), 0.0)
        self.assertEqual(_score_proximity(5.0, 10.0, 0.0), 0.0)

    def test_score_proximity_exact_match(self):
        """_score_proximity returns 1.0 on exact match"""
        self.assertEqual(_score_proximity(10.0, 10.0, 5.0), 1.0)

    def test_score_proximity_far_outside(self):
        """_score_proximity returns 0 when value far outside tolerance"""
        self.assertEqual(_score_proximity(100.0, 10.0, 5.0), 0.0)

    def test_score_proximity_half_tolerance(self):
        """_score_proximity returns 0.5 at half tolerance"""
        self.assertEqual(_score_proximity(12.5, 10.0, 5.0), 0.5)

    def test_score_proximity_negative_tolerance(self):
        """_score_proximity returns 0 for negative tolerance"""
        self.assertEqual(_score_proximity(10.0, 10.0, -5.0), 0.0)

    # -- _score_positive_metric edge cases --------------------------------

    def test_score_positive_metric_zero_ideal(self):
        """_score_positive_metric returns 0 when ideal_value is 0"""
        self.assertEqual(_score_positive_metric(50.0, 0.0), 0.0)

    def test_score_positive_metric_zero_value(self):
        """_score_positive_metric returns 0 when value is 0"""
        self.assertEqual(_score_positive_metric(0.0, 100.0), 0.0)

    def test_score_positive_metric_exceeds_ideal(self):
        """_score_positive_metric caps at 1.0 when value exceeds ideal"""
        self.assertEqual(_score_positive_metric(200.0, 100.0), 1.0)

    def test_score_positive_metric_negative_values(self):
        """_score_positive_metric handles negative values gracefully"""
        result = _score_positive_metric(-50.0, 100.0)
        self.assertEqual(result, 0.0)  # negative / positive = 0 via clamp

    # -- _calculate_mid_price edge cases ----------------------------------

    def test_calculate_mid_price_bid_only(self):
        """_calculate_mid_price falls back to bid when ask is 0"""
        self.assertEqual(_calculate_mid_price(5.0, 0.0, 0.0), 5.0)

    def test_calculate_mid_price_ask_only(self):
        """_calculate_mid_price falls back to ask when bid is 0"""
        self.assertEqual(_calculate_mid_price(0.0, 7.0, 0.0), 7.0)

    def test_calculate_mid_price_last_only(self):
        """_calculate_mid_price falls back to last when bid/ask are 0"""
        self.assertEqual(_calculate_mid_price(0.0, 0.0, 3.0), 3.0)

    def test_calculate_mid_price_all_zero(self):
        """_calculate_mid_price returns 0 when all prices are 0"""
        self.assertEqual(_calculate_mid_price(0.0, 0.0, 0.0), 0.0)

    def test_calculate_mid_price_negative_values(self):
        """_calculate_mid_price treats negative values as 0"""
        mid = _calculate_mid_price(-1.0, -2.0, 0.0)
        self.assertEqual(mid, 0.0)

    def test_calculate_mid_price_prefers_bid_ask(self):
        """_calculate_mid_price uses bid/ask average even when last is available"""
        self.assertEqual(_calculate_mid_price(5.0, 7.0, 10.0), 6.0)

    # -- _compute_roll_pressure edge cases --------------------------------

    def test_roll_pressure_zero_strike_and_price(self):
        """_compute_roll_pressure handles zero strike and stock_price"""
        d = WheelDecision(option_type='PUT', strike=0, stock_price=0, extrinsic_remaining=0)
        pressure = _compute_roll_pressure(d)
        self.assertGreaterEqual(pressure, 0.0)
        self.assertLessEqual(pressure, 100.0)

    def test_roll_pressure_zero_extrinsic(self):
        """_compute_roll_pressure handles zero extrinsic remaining"""
        d = WheelDecision(option_type='PUT', dte=10, strike=95.0, stock_price=100.0,
                          extrinsic_remaining=0)
        pressure = _compute_roll_pressure(d)
        self.assertGreater(pressure, 0)

    def test_roll_pressure_itm_call(self):
        """_compute_roll_pressure for ITM CALL (strike < stock_price)"""
        d = WheelDecision(option_type='CALL', dte=10, strike=90.0, stock_price=100.0,
                          extrinsic_remaining=0.50)
        pressure = _compute_roll_pressure(d)
        self.assertGreater(pressure, 50.0)

    def test_roll_pressure_itm_put(self):
        """_compute_roll_pressure for ITM PUT (stock_price < strike)"""
        d = WheelDecision(option_type='PUT', dte=10, strike=110.0, stock_price=100.0,
                          extrinsic_remaining=0.50)
        pressure = _compute_roll_pressure(d)
        self.assertGreater(pressure, 50.0)

    def test_roll_pressure_far_otm(self):
        """_compute_roll_pressure for far OTM position (low pressure)"""
        d = WheelDecision(option_type='PUT', dte=45, strike=80.0, stock_price=100.0,
                          extrinsic_remaining=1.50)
        pressure = _compute_roll_pressure(d)
        self.assertGreaterEqual(pressure, 0.0)
        self.assertLess(pressure, 30.0)

    def test_roll_pressure_negative_dte(self):
        """_compute_roll_pressure with negative DTE (expired)"""
        d = WheelDecision(option_type='PUT', dte=-5, strike=95.0, stock_price=100.0,
                          extrinsic_remaining=0.0)
        pressure = _compute_roll_pressure(d)
        self.assertGreaterEqual(pressure, 0.0)
        self.assertLessEqual(pressure, 100.0)

    # -- _compute_profit_target_progress edge cases ------------------------

    def test_profit_progress_zero_premium(self):
        """_compute_profit_target_progress returns 0 when premium is 0"""
        self.assertEqual(_compute_profit_target_progress(
            WheelDecision(premium_per_contract=0, dte=10)), 0.0)

    def test_profit_progress_zero_dte(self):
        """_compute_profit_target_progress returns 100 when dte is 0"""
        self.assertEqual(_compute_profit_target_progress(
            WheelDecision(premium_per_contract=2, dte=0)), 100.0)

    def test_profit_progress_negative_dte(self):
        """_compute_profit_target_progress returns 100 when dte is negative"""
        self.assertEqual(_compute_profit_target_progress(
            WheelDecision(premium_per_contract=2, dte=-5)), 100.0)

    def test_profit_progress_high_dte(self):
        """_compute_profit_target_progress near 0 for high DTE"""
        self.assertEqual(_compute_profit_target_progress(
            WheelDecision(premium_per_contract=2, dte=30)), 0.0)

    def test_profit_progress_partial(self):
        """_compute_profit_target_progress partial progress at 15 DTE"""
        progress = _compute_profit_target_progress(
            WheelDecision(premium_per_contract=2, dte=15))
        self.assertAlmostEqual(progress, 50.0, places=1)

    # -- _compute_size_fit edge cases --------------------------------------

    def test_size_fit_call_no_shares(self):
        """_compute_size_fit returns 0 for CALL with no shares owned"""
        d = WheelDecision(option_type='CALL', ticker='AAPL', max_contracts=1)
        ctx = {'positions': {'AAPL': {'position': 0}}, 'cash_balance': 10000.0}
        self.assertEqual(_compute_size_fit(d, ctx), 0.0)

    def test_size_fit_call_zero_max_contracts(self):
        """_compute_size_fit returns 0 for CALL when max_contracts is 0"""
        d = WheelDecision(option_type='CALL', ticker='AAPL', max_contracts=0)
        ctx = {'positions': {'AAPL': {'position': 200}}, 'cash_balance': 10000.0}
        self.assertEqual(_compute_size_fit(d, ctx), 0.0)

    def test_size_fit_put_no_cash(self):
        """_compute_size_fit returns 0 for PUT when cash_balance is 0"""
        d = WheelDecision(option_type='PUT', ticker='AAPL', cash_required=10000.0)
        ctx = {'cash_balance': 0.0}
        self.assertEqual(_compute_size_fit(d, ctx), 0.0)

    def test_size_fit_put_zero_cash_required(self):
        """_compute_size_fit returns 50 for PUT when cash_required is 0"""
        d = WheelDecision(option_type='PUT', ticker='AAPL', cash_required=0.0)
        ctx = {'cash_balance': 10000.0}
        self.assertEqual(_compute_size_fit(d, ctx), 50.0)

    def test_size_fit_put_partial(self):
        """_compute_size_fit returns proportional fit for PUT"""
        d = WheelDecision(option_type='PUT', ticker='AAPL', cash_required=20000.0)
        ctx = {'cash_balance': 10000.0}
        self.assertEqual(_compute_size_fit(d, ctx), 50.0)

    def test_size_fit_call_full(self):
        """_compute_size_fit returns 100 for CALL with enough shares"""
        d = WheelDecision(option_type='CALL', ticker='AAPL', max_contracts=2)
        ctx = {'positions': {'AAPL': {'position': 200}}, 'cash_balance': 10000.0}
        self.assertEqual(_compute_size_fit(d, ctx), 100.0)

    # -- _compute_expected_move_buffer edge cases --------------------------

    def test_expected_move_zero_stock_price(self):
        """_compute_expected_move_buffer returns 0 when stock_price is 0"""
        d = WheelDecision(stock_price=0, implied_volatility=0.3, dte=21, otm_pct=5.0)
        self.assertEqual(_compute_expected_move_buffer(d), 0.0)

    def test_expected_move_zero_iv(self):
        """_compute_expected_move_buffer returns 0 when IV is 0"""
        d = WheelDecision(stock_price=100, implied_volatility=0, dte=21, otm_pct=5.0)
        self.assertEqual(_compute_expected_move_buffer(d), 0.0)

    def test_expected_move_zero_dte(self):
        """_compute_expected_move_buffer returns 0 when DTE is 0"""
        d = WheelDecision(stock_price=100, implied_volatility=0.3, dte=0, otm_pct=5.0)
        self.assertEqual(_compute_expected_move_buffer(d), 0.0)

    def test_expected_move_positive_buffer(self):
        """_compute_expected_move_buffer positive when OTM > expected move"""
        d = WheelDecision(stock_price=100, implied_volatility=0.2, dte=7, otm_pct=10.0)
        buf = _compute_expected_move_buffer(d)
        self.assertGreater(buf, 0.0)

    def test_expected_move_negative_buffer(self):
        """_compute_expected_move_buffer negative when OTM < expected move"""
        d = WheelDecision(stock_price=100, implied_volatility=0.5, dte=30, otm_pct=2.0)
        buf = _compute_expected_move_buffer(d)
        self.assertLess(buf, 0.0)

    # -- _compute_shared_subscores edge cases ------------------------------

    def test_shared_subscores_zero_delta(self):
        """_compute_shared_subscores handles zero delta (no division by zero)"""
        d = WheelDecision(delta=0, stock_price=100, theta=-0.05, spread_pct=20,
                          open_interest=500, volume=100, iv_adjusted_return=10,
                          premium_per_contract=2.0, expected_value=1.0,
                          dte=21, otm_pct=10)
        profile = {'ideal_open_interest': 500, 'ideal_volume': 100, 'ideal_spread_pct': 12,
                   'target_delta': 0.20, 'delta_tolerance': 0.15, 'preferred_dte': 21,
                   'target_theta_delta_ratio': 0.005, 'liquidity_weight_multiplier': 1.0,
                   'target_iv_adjusted': 50}
        _compute_shared_subscores(d, profile)
        self.assertEqual(d._theta_delta_ratio, 0.0)
        self.assertEqual(d.tdr_score, 0.0)

    def test_shared_subscores_zero_stock_price(self):
        """_compute_shared_subscores handles zero stock_price"""
        d = WheelDecision(delta=-0.2, stock_price=0, theta=-0.05, spread_pct=20,
                          open_interest=500, volume=100, iv_adjusted_return=10,
                          premium_per_contract=2.0, expected_value=1.0,
                          dte=21, otm_pct=10)
        profile = {'ideal_open_interest': 500, 'ideal_volume': 100, 'ideal_spread_pct': 12,
                   'target_delta': 0.20, 'delta_tolerance': 0.15, 'preferred_dte': 21,
                   'target_theta_delta_ratio': 0.005, 'liquidity_weight_multiplier': 1.0,
                   'target_iv_adjusted': 50}
        _compute_shared_subscores(d, profile)
        self.assertEqual(d._theta_delta_ratio, 0.0)

    def test_shared_subscores_zero_theta(self):
        """_compute_shared_subscores handles zero theta"""
        d = WheelDecision(delta=-0.2, stock_price=100, theta=0, spread_pct=20,
                          open_interest=500, volume=100, iv_adjusted_return=10,
                          premium_per_contract=2.0, expected_value=1.0,
                          dte=21, otm_pct=10)
        profile = {'ideal_open_interest': 500, 'ideal_volume': 100, 'ideal_spread_pct': 12,
                   'target_delta': 0.20, 'delta_tolerance': 0.15, 'preferred_dte': 21,
                   'target_theta_delta_ratio': 0.005, 'liquidity_weight_multiplier': 1.0,
                   'target_iv_adjusted': 50}
        _compute_shared_subscores(d, profile)
        self.assertEqual(d._theta_delta_ratio, 0.0)

    def test_shared_subscores_zero_iv_adjusted_return(self):
        """_compute_shared_subscores handles zero iv_adjusted_return"""
        d = WheelDecision(delta=-0.2, stock_price=100, theta=-0.05, spread_pct=20,
                          open_interest=500, volume=100, iv_adjusted_return=0,
                          premium_per_contract=2.0, expected_value=1.0,
                          dte=21, otm_pct=10)
        profile = {'ideal_open_interest': 500, 'ideal_volume': 100, 'ideal_spread_pct': 12,
                   'target_delta': 0.20, 'delta_tolerance': 0.15, 'preferred_dte': 21,
                   'target_theta_delta_ratio': 0.005, 'liquidity_weight_multiplier': 1.0,
                   'target_iv_adjusted': 50}
        _compute_shared_subscores(d, profile)
        self.assertEqual(d.iv_adjusted_score, 0.0)

    def test_shared_subscores_requires_required_keys(self):
        """_compute_shared_subscores raises KeyError when required profile keys missing"""
        d = WheelDecision(delta=-0.2, stock_price=100, theta=-0.05, spread_pct=20,
                          open_interest=500, volume=100, iv_adjusted_return=10,
                          premium_per_contract=2.0, expected_value=1.0,
                          dte=21, otm_pct=10)
        with self.assertRaises(KeyError):
            _compute_shared_subscores(d, {})  # Empty profile = missing required keys

    def test_shared_subscores_missing_optional_keys(self):
        """_compute_shared_subscores handles missing optional profile keys (used via .get())"""
        d = WheelDecision(delta=-0.2, stock_price=100, theta=-0.05, spread_pct=20,
                          open_interest=500, volume=100, iv_adjusted_return=10,
                          premium_per_contract=2.0, expected_value=1.0,
                          dte=21, otm_pct=10)
        # Required keys present, optional keys (liquidity_weight_multiplier,
        # target_iv_adjusted, target_theta_delta_ratio) missing
        profile = {
            'ideal_open_interest': 500,
            'ideal_volume': 100,
            'ideal_spread_pct': 12,
            'target_delta': 0.20,
            'delta_tolerance': 0.15,
            'preferred_dte': 21,
        }
        _compute_shared_subscores(d, profile)
        self.assertIsInstance(d.oi_score, (int, float))
        self.assertIsInstance(d.volume_score, (int, float))
        self.assertIsInstance(d.spread_score, (int, float))
        # target_theta_delta_ratio defaults to 0.005, so tdr_score = 0.0025/0.005*100 = 50
        self.assertEqual(d.tdr_score, 50.0)


class TestScoreContractEdgeCases(unittest.TestCase):
    """Edge-case tests for score_contract function"""

    def setUp(self):
        future_date = (datetime.now() + timedelta(days=21)).strftime('%Y%m%d')
        self.base_option = {
            'strike': 95.0,
            'expiration': future_date,
            'option_type': 'PUT',
            'bid': 2.0,
            'ask': 2.50,
            'last': 2.25,
            'delta': -0.20,
            'gamma': 0.05,
            'theta': -0.05,
            'vega': 0.15,
            'implied_volatility': 0.30,
            'open_interest': 500,
            'volume': 100,
        }
        self.base_profile = {
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
        self.base_portfolio = {
            'positions': {},
            'cash_balance': 10000.0,
            'account_value': 50000.0,
            'short_puts': {},
        }

    def test_score_contract_zero_strike(self):
        """score_contract returns WheelDecision with hard_blockers for zero strike"""
        opt = dict(self.base_option, strike=0)
        result = score_contract('AAPL', opt, 100.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_score_contract_empty_expiration(self):
        """score_contract returns WheelDecision with hard_blockers for empty expiration"""
        opt = dict(self.base_option, expiration='')
        result = score_contract('AAPL', opt, 100.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_score_contract_invalid_expiration(self):
        """score_contract returns WheelDecision with hard_blockers for invalid expiration format"""
        opt = dict(self.base_option, expiration='invalid')
        result = score_contract('AAPL', opt, 100.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_score_contract_expired_option(self):
        """score_contract returns WheelDecision with hard_blockers for expired option (dte <= 0)"""
        past_date = (datetime.now() - timedelta(days=5)).strftime('%Y%m%d')
        opt = dict(self.base_option, expiration=past_date)
        result = score_contract('AAPL', opt, 100.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_score_contract_below_min_mid_price(self):
        """score_contract returns WheelDecision with hard_blockers when mid_price below min_mid_price"""
        opt = dict(self.base_option, bid=0.01, ask=0.02)
        result = score_contract('AAPL', opt, 100.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_score_contract_spread_too_wide(self):
        """score_contract returns WheelDecision with hard_blockers when spread exceeds max_spread_pct"""
        opt = dict(self.base_option, bid=0.5, ask=2.5)  # spread = 200%
        result = score_contract('AAPL', opt, 100.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_score_contract_low_oi_and_volume(self):
        """score_contract returns WheelDecision with hard_blockers when OI and volume both below minimum"""
        opt = dict(self.base_option, open_interest=0, volume=0)
        result = score_contract('AAPL', opt, 100.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_score_contract_min_premium_too_low(self):
        """score_contract returns WheelDecision with hard_blockers when premium per contract < minimum"""
        opt = dict(self.base_option, bid=0.04, ask=0.06)
        result = score_contract('AAPL', opt, 100.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)  # premium = 0.05*100 = 5 < 10

    def test_score_contract_low_oi_and_volume(self):
        """score_contract returns WheelDecision with hard_blockers when OI and volume both below minimum"""
        opt = dict(self.base_option, open_interest=0, volume=0)
        result = score_contract('AAPL', opt, 100.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_score_contract_put_atm(self):
        """score_contract returns WheelDecision with hard_blockers for PUT when strike >= stock_price"""
        opt = dict(self.base_option, strike=100.0)
        result = score_contract('AAPL', opt, 100.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_score_contract_put_insufficient_cash(self):
        """score_contract returns WheelDecision with hard_blockers for PUT when cash required exceeds available"""
        opt = dict(self.base_option, strike=500.0, expiration=(
            datetime.now() + timedelta(days=21)).strftime('%Y%m%d'))
        portfolio = dict(self.base_portfolio, cash_balance=100.0)
        result = score_contract('AAPL', opt, 550.0, self.base_profile, portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_score_contract_call_itm(self):
        """score_contract returns WheelDecision with hard_blockers for CALL when strike <= stock_price"""
        opt = dict(self.base_option, option_type='CALL', strike=95.0)
        portfolio = dict(self.base_portfolio,
                         positions={'AAPL': {'position': 200}})
        result = score_contract('AAPL', opt, 100.0, self.base_profile, portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_score_contract_call_no_shares(self):
        """score_contract returns WheelDecision with hard_blockers for CALL when no shares owned"""
        opt = dict(self.base_option, option_type='CALL', strike=110.0,
                   expiration=(datetime.now() + timedelta(days=21)).strftime('%Y%m%d'))
        result = score_contract('AAPL', opt, 100.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_score_contract_unknown_option_type(self):
        """score_contract returns WheelDecision with hard_blockers for unknown option type"""
        opt = dict(self.base_option, option_type='CALL_PUT')
        result = score_contract('AAPL', opt, 100.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_score_contract_zero_stock_price(self):
        """score_contract returns WheelDecision with hard_blockers for zero stock price"""
        opt = dict(self.base_option)
        result = score_contract('AAPL', opt, 0.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_score_contract_zero_strike(self):
        """score_contract returns WheelDecision with hard_blockers for zero strike"""
        opt = dict(self.base_option, strike=0)
        result = score_contract('AAPL', opt, 100.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)

    def test_score_contract_all_zero_prices(self):
        """score_contract handles all zero bid/ask/last gracefully"""
        opt = dict(self.base_option, bid=0, ask=0, last=0)
        result = score_contract('AAPL', opt, 100.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)  # mid_price=0 < min_mid_price

    def test_score_contract_call_successful(self):
        """score_contract returns valid WheelDecision for valid CALL"""
        future_date = (datetime.now() + timedelta(days=21)).strftime('%Y%m%d')
        opt = dict(self.base_option, option_type='CALL', strike=110.0,
                   expiration=future_date, delta=0.20)
        portfolio = dict(self.base_portfolio,
                         positions={'AAPL': {'position': 200, 'avg_cost': 105.0}})
        result = score_contract('AAPL', opt, 100.0, self.base_profile, portfolio)
        self.assertIsNotNone(result)
        self.assertEqual(result.option_type, 'CALL')
        self.assertGreater(result.contract_score, 0)

    def test_score_contract_missing_optional_fields(self):
        """score_contract handles missing gamma/theta/vega fields"""
        future_date = (datetime.now() + timedelta(days=21)).strftime('%Y%m%d')
        opt = {'strike': 95.0, 'expiration': future_date, 'option_type': 'PUT',
               'bid': 2.0, 'ask': 2.50, 'delta': -0.20, 'open_interest': 500, 'volume': 100}
        result = score_contract('AAPL', opt, 100.0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertEqual(result.gamma, 0.0)
        self.assertEqual(result.theta, 0.0)
        self.assertEqual(result.vega, 0.0)

    def test_score_contract_zero_stock_price(self):
        """score_contract returns WheelDecision with hard_blockers when stock_price is 0"""
        result = score_contract('AAPL', self.base_option, 0, self.base_profile, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertTrue(result.hard_blockers)


class TestScoreExistingPositionEdgeCases(unittest.TestCase):
    """Edge-case tests for score_existing_position function"""

    def setUp(self):
        self.future_date = (datetime.now() + timedelta(days=14)).strftime('%Y%m%d')
        self.base_position = {
            'option_type': 'PUT',
            'strike': 95.0,
            'expiration': self.future_date,
            'dte': 14,
            'bid': 1.0,
            'ask': 1.50,
            'last': 1.25,
            'delta': -0.15,
            'theta': -0.03,
            'implied_volatility': 0.25,
        }
        self.base_portfolio = {
            'positions': {'AAPL': {'position': -1}},
            'cash_balance': 10000.0,
            'short_puts': {'AAPL': 1},
        }

    def test_existing_position_zero_stock_price(self):
        """score_existing_position handles zero stock_price without error"""
        result = score_existing_position(
            'AAPL', self.base_position, 0, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertEqual(result.otm_pct, 0.0)
        self.assertEqual(result.expected_move_buffer, 0.0)

    def test_existing_position_empty_portfolio(self):
        """score_existing_position handles empty portfolio_context"""
        result = score_existing_position(
            'AAPL', self.base_position, 100.0, {})
        self.assertIsNotNone(result)
        self.assertEqual(result.vix_regime, 'normal')

    def test_existing_position_missing_market_data(self):
        """score_existing_position handles missing bid/ask/last"""
        pos = dict(self.base_position, bid=0, ask=0, last=0)
        result = score_existing_position(
            'AAPL', pos, 100.0, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertEqual(result.mid_price, 0.0)
        self.assertEqual(result.premium_per_contract, 0.0)

    def test_existing_position_atm_put(self):
        """score_existing_position with ATM PUT (strike ~= stock_price)"""
        result = score_existing_position(
            'AAPL', self.base_position, 95.0, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.otm_pct, 0.0, places=1)
        self.assertIn('Approaching strike', ' '.join(result.warnings))

    def test_existing_position_itm_put(self):
        """score_existing_position with ITM PUT (stock_price < strike)"""
        result = score_existing_position(
            'AAPL', self.base_position, 90.0, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertLess(result.otm_pct, 0)
        warning_text = ' '.join(result.warnings)
        self.assertIn('Strike crossed', warning_text)

    def test_existing_position_high_roll_pressure(self):
        """score_existing_position warns on high roll pressure"""
        pos = dict(self.base_position, dte=3)
        result = score_existing_position(
            'AAPL', pos, 100.0, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertGreater(result.roll_pressure, 50)
        self.assertIn('Only 3 DTE remaining', ' '.join(result.warnings))

    def test_existing_position_call_type(self):
        """score_existing_position handles CALL type"""
        pos = dict(self.base_position, option_type='CALL', strike=105.0)
        result = score_existing_position(
            'AAPL', pos, 100.0, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertEqual(result.option_type, 'CALL')
        self.assertGreater(result.otm_pct, 0)

    def test_existing_position_itm_call(self):
        """score_existing_position warns when CALL is ITM"""
        pos = dict(self.base_position, option_type='CALL', strike=95.0)
        result = score_existing_position(
            'AAPL', pos, 100.0, self.base_portfolio)
        self.assertIsNotNone(result)
        self.assertLess(result.otm_pct, 0)

    def test_existing_position_custom_iv_env(self):
        """score_existing_position accepts iv and earnings overrides"""
        result = score_existing_position(
            'AAPL', self.base_position, 100.0, self.base_portfolio,
            iv_env_adjustment=10.0, iv_rank=0.8, iv_status_str='high',
            earnings_adjustment=-5.0,
            earnings_info={'warning_level': 'soon', 'days_to_earnings': 3})
        self.assertIsNotNone(result)
        self.assertEqual(result.iv_rank, 80.0)
        self.assertEqual(result.iv_status, 'high')

    def test_existing_position_macro_regime(self):
        """score_existing_position accepts macro_regime override"""
        mr = {'macro_multiplier': 0.85, 'rate_regime': 'tightening',
              'credit_stress': 'elevated', 'summary': 'Macro headwinds', 'advice': 'Reduce risk'}
        result = score_existing_position(
            'AAPL', self.base_position, 100.0, self.base_portfolio,
            macro_regime=mr)
        self.assertIsNotNone(result)


class TestScoreExistingPosition(unittest.TestCase):
    """Test score_existing_position function"""
    
    def test_valid_position(self):
        """Test scoring a valid existing position"""
        future_date = (datetime.now() + timedelta(days=14)).strftime('%Y%m%d')
        position_data = {
            'option_type': 'PUT',
            'strike': 95.0,
            'expiration': future_date,
            'dte': 14,
            'bid': 1.0,
            'ask': 1.50,
            'last': 1.25,
            'delta': -0.15,
            'theta': -0.03,
            'implied_volatility': 0.25,
        }
        portfolio = {
            'positions': {'AAPL': {'position': -1}},
            'cash_balance': 10000.0,
            'short_puts': {'AAPL': 1},
        }
        result = score_existing_position(
            ticker="AAPL",
            position_data=position_data,
            current_stock_price=100.0,
            portfolio_context=portfolio,
        )
        self.assertIsNotNone(result)
        self.assertIsInstance(result, WheelDecision)
        self.assertEqual(result.ticker, "AAPL")
        self.assertEqual(result.option_type, "PUT")


if __name__ == '__main__':
    unittest.main()
