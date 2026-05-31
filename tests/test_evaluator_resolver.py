"""
Tests for core/evaluator.py — signal outcome resolver and helpers.
"""

import unittest
from datetime import datetime
from unittest.mock import MagicMock
from core.evaluator import (
    _resolve_signal_outcome,
    _normalize_exp,
    _extract_underlying,
    is_valid_training_outcome,
    _compute_portfolio_hash_for_record,
    _feed_feedback,
)


class TestExtractUnderlying(unittest.TestCase):
    def test_bare_ticker(self):
        self.assertEqual(_extract_underlying('AAPL'), 'AAPL')

    def test_moomoo_stock_prefix(self):
        self.assertEqual(_extract_underlying('US.AAPL'), 'AAPL')

    def test_moomoo_option_symbol(self):
        self.assertEqual(_extract_underlying('US.AAPL20250516P00150000'), 'AAPL')

    def test_standard_option_symbol(self):
        self.assertEqual(_extract_underlying('AAPL250516C00150000'), 'AAPL')

    def test_empty_string(self):
        self.assertEqual(_extract_underlying(''), '')

    def test_none(self):
        self.assertEqual(_extract_underlying(None), '')

    def test_case_insensitivity(self):
        self.assertEqual(_extract_underlying('us.aapl20250516p00150000'), 'AAPL')

    def test_other_tickers(self):
        self.assertEqual(_extract_underlying('US.TSLA20250516C00200000'), 'TSLA')
        self.assertEqual(_extract_underlying('MSFT'), 'MSFT')
        self.assertEqual(_extract_underlying('US.SOFI'), 'SOFI')


class TestNormalizeExp(unittest.TestCase):
    def test_already_yyyymmdd(self):
        self.assertEqual(_normalize_exp('20250516'), '20250516')

    def test_hyphenated(self):
        self.assertEqual(_normalize_exp('2025-05-16'), '20250516')

    def test_slashed(self):
        self.assertEqual(_normalize_exp('2025/05/16'), '20250516')

    def test_empty(self):
        self.assertEqual(_normalize_exp(''), '')

    def test_none(self):
        self.assertEqual(_normalize_exp(None), '')

    def test_int_input(self):
        self.assertEqual(_normalize_exp(20250516), '20250516')
        self.assertEqual(_normalize_exp(20250516.0), '20250516')

    def test_datetime_input(self):
        d = datetime(2025, 5, 16)
        self.assertEqual(_normalize_exp(d), '20250516')


class TestIsValidTrainingOutcome(unittest.TestCase):
    def test_valid_outcomes(self):
        for outcome in ('expired_worthless', 'assigned', 'called_away',
                        'closed_profit', 'closed_loss', 'rolled_profit', 'rolled_loss'):
            self.assertTrue(is_valid_training_outcome(outcome))

    def test_invalid_outcomes(self):
        for outcome in ('unknown', 'ignored', 'still_open', 'manual_unlinked'):
            self.assertFalse(is_valid_training_outcome(outcome))


class TestResolveSignalOutcome(unittest.TestCase):
    def setUp(self):
        self.signal = {
            'ticker': 'AAPL',
            'option_type': 'PUT',
            'strike': 150.0,
            'expiration': '20250516',
            'premium_per_contract': 1.50,
        }

    def _mock_portfolio_service(self, opt_positions=None, stk_positions=None):
        svc = MagicMock()
        svc.get_positions.side_effect = lambda sec_type: {
            'OPT': opt_positions or [],
            'STK': stk_positions or [],
        }.get(sec_type, [])
        return svc

    def test_no_portfolio_service(self):
        outcome, ret = _resolve_signal_outcome(self.signal, None)
        self.assertEqual(outcome, 'unknown')
        self.assertEqual(ret, 0.0)

    def test_put_expired_worthless_no_stock(self):
        svc = self._mock_portfolio_service(opt_positions=[], stk_positions=[])
        outcome, ret = _resolve_signal_outcome(self.signal, svc)
        self.assertEqual(outcome, 'expired_worthless')
        self.assertEqual(ret, 1.50)

    def test_put_assigned_stock_appears(self):
        svc = self._mock_portfolio_service(
            opt_positions=[],
            stk_positions=[{'symbol': 'AAPL', 'position': 200, 'security_type': 'STK'}],
        )
        outcome, ret = _resolve_signal_outcome(self.signal, svc)
        self.assertEqual(outcome, 'assigned')
        self.assertEqual(ret, 1.50)

    def test_put_assigned_partial_under_100_shares_falls_to_expired(self):
        """Below 100 shares of stock is unusual for assignment but for safety falls to expired_worthless."""
        svc = self._mock_portfolio_service(
            opt_positions=[],
            stk_positions=[{'symbol': 'AAPL', 'position': 50, 'security_type': 'STK'}],
        )
        outcome, ret = _resolve_signal_outcome(self.signal, svc)
        self.assertEqual(outcome, 'expired_worthless')

    def test_call_called_away_no_stock_remaining(self):
        """CALL: no stock remaining means all shares were called away."""
        signal = {**self.signal, 'option_type': 'CALL'}
        svc = self._mock_portfolio_service(opt_positions=[], stk_positions=[])
        outcome, ret = _resolve_signal_outcome(signal, svc)
        self.assertEqual(outcome, 'called_away')
        self.assertEqual(ret, 1.50)

    def test_call_called_away_stock_qty_below_100(self):
        """CALL: stock qty < 100 means partial call-away."""
        signal = {**self.signal, 'option_type': 'CALL'}
        svc = self._mock_portfolio_service(
            opt_positions=[],
            stk_positions=[{'symbol': 'AAPL', 'position': 50, 'security_type': 'STK'}],
        )
        outcome, ret = _resolve_signal_outcome(signal, svc)
        self.assertEqual(outcome, 'called_away')

    def test_call_expired_worthless_stock_still_held(self):
        """CALL: stock still held with >= 100 shares means option expired worthless."""
        signal = {**self.signal, 'option_type': 'CALL'}
        svc = self._mock_portfolio_service(
            opt_positions=[],
            stk_positions=[{'symbol': 'AAPL', 'position': 300, 'security_type': 'STK'}],
        )
        outcome, ret = _resolve_signal_outcome(signal, svc)
        self.assertEqual(outcome, 'expired_worthless')

    def test_still_open_matching_option_position(self):
        svc = self._mock_portfolio_service(
            opt_positions=[{
                'symbol': 'AAPL',
                'option_type': 'PUT',
                'strike': 150.0,
                'expiration': '2025-05-16',
                'position': 1,
                'security_type': 'OPT',
            }],
            stk_positions=[],
        )
        outcome, ret = _resolve_signal_outcome(self.signal, svc)
        self.assertEqual(outcome, 'still_open')
        self.assertIsNone(ret)

    def test_expiration_mismatch_does_not_match(self):
        """Same ticker/type/strike but different expiration should NOT match."""
        svc = self._mock_portfolio_service(
            opt_positions=[{
                'symbol': 'AAPL',
                'option_type': 'PUT',
                'strike': 150.0,
                'expiration': '20250620',
                'position': 1,
                'security_type': 'OPT',
            }],
            stk_positions=[],
        )
        outcome, ret = _resolve_signal_outcome(self.signal, svc)
        self.assertNotEqual(outcome, 'still_open')
        self.assertEqual(outcome, 'expired_worthless')

    def test_expiration_with_different_format_still_matches(self):
        """Hyphenated broker expiration should still match YYYYMMDD signal."""
        svc = self._mock_portfolio_service(
            opt_positions=[{
                'symbol': 'AAPL',
                'option_type': 'PUT',
                'strike': 150.0,
                'expiration': '2025-05-16',
                'position': 1,
                'security_type': 'OPT',
            }],
            stk_positions=[],
        )
        outcome, ret = _resolve_signal_outcome(self.signal, svc)
        self.assertEqual(outcome, 'still_open')

    def test_position_qty_zero_returns_closed_profit(self):
        """Position exists but qty <= 0 means it was closed."""
        svc = self._mock_portfolio_service(
            opt_positions=[{
                'symbol': 'AAPL',
                'option_type': 'PUT',
                'strike': 150.0,
                'expiration': '2025-05-16',
                'position': 0,
                'security_type': 'OPT',
            }],
            stk_positions=[],
        )
        outcome, ret = _resolve_signal_outcome(self.signal, svc)
        self.assertEqual(outcome, 'closed_profit')

    def test_unknown_option_type_returns_closed_profit(self):
        signal = {**self.signal, 'option_type': 'UNKNOWN'}
        svc = self._mock_portfolio_service(opt_positions=[], stk_positions=[])
        outcome, ret = _resolve_signal_outcome(signal, svc)
        self.assertEqual(outcome, 'closed_profit')

    def test_call_expired_worthless_stock_qty_exactly_100(self):
        """CALL: exactly 100 shares held, option expired worthless."""
        signal = {**self.signal, 'option_type': 'CALL'}
        svc = self._mock_portfolio_service(
            opt_positions=[],
            stk_positions=[{'symbol': 'AAPL', 'position': 100, 'security_type': 'STK'}],
        )
        outcome, ret = _resolve_signal_outcome(signal, svc)
        self.assertEqual(outcome, 'expired_worthless')

    def test_call_called_away_stock_qty_zero(self):
        """CALL: stock position exists but with 0 shares → called away."""
        signal = {**self.signal, 'option_type': 'CALL'}
        svc = self._mock_portfolio_service(
            opt_positions=[],
            stk_positions=[{'symbol': 'AAPL', 'position': 0, 'security_type': 'STK'}],
        )
        outcome, ret = _resolve_signal_outcome(signal, svc)
        self.assertEqual(outcome, 'called_away')

    def test_still_open_with_full_option_symbol(self):
        """Broker returns full option symbol like US.AAPL20250516P00150000."""
        svc = self._mock_portfolio_service(
            opt_positions=[{
                'symbol': 'US.AAPL20250516P00150000',
                'option_type': 'PUT',
                'strike': 150.0,
                'expiration': '2025-05-16',
                'position': 1,
                'security_type': 'OPT',
            }],
            stk_positions=[],
        )
        outcome, ret = _resolve_signal_outcome(self.signal, svc)
        self.assertEqual(outcome, 'still_open')

    def test_stock_position_with_us_prefix(self):
        """Stock position symbol has US. prefix and PUT was assigned."""
        svc = self._mock_portfolio_service(
            opt_positions=[],
            stk_positions=[{'symbol': 'US.AAPL', 'position': 200, 'security_type': 'STK'}],
        )
        outcome, ret = _resolve_signal_outcome(self.signal, svc)
        self.assertEqual(outcome, 'assigned')

    def test_call_called_away_full_option_symbol(self):
        """Full option symbol matching + no stock → called away."""
        signal = {**self.signal, 'option_type': 'CALL'}
        svc = self._mock_portfolio_service(
            opt_positions=[{
                'symbol': 'US.AAPL20250620C00150000',
                'option_type': 'CALL',
                'strike': 150.0,
                'expiration': '2025-06-20',
                'position': 1,
                'security_type': 'OPT',
            }],
            stk_positions=[],
        )
        # Different expiration, so no match → no stock → called_away
        outcome, ret = _resolve_signal_outcome(signal, svc)
        self.assertEqual(outcome, 'called_away')


class TestComputePortfolioHash(unittest.TestCase):
    def test_returns_string(self):
        result = _compute_portfolio_hash_for_record({'positions': {}, 'cash_balance': 0})
        self.assertIsInstance(result, str)

    def test_different_positions_different_hash(self):
        ctx1 = {'positions': {'AAPL': {'position': 100}}, 'cash_balance': 50000}
        ctx2 = {'positions': {'AAPL': {'position': 200}}, 'cash_balance': 50000}
        self.assertNotEqual(
            _compute_portfolio_hash_for_record(ctx1),
            _compute_portfolio_hash_for_record(ctx2),
        )

    def test_empty_ctx_returns_hash(self):
        result = _compute_portfolio_hash_for_record({})
        self.assertIsInstance(result, str)
        self.assertNotEqual(result, 'unknown')


class TestFeedFeedback(unittest.TestCase):
    def test_does_not_call_without_feedback_enabled(self):
        repo = MagicMock()
        signal = {'recommendation_id': 'abc', 'ticker': 'AAPL', 'score': 50, 'score_details_json': '{}'}
        _feed_feedback(repo, signal, 'expired_worthless', 1.0, {'feedback_enabled': False})
        repo.save_feedback_event.assert_not_called()

    def test_does_not_call_with_invalid_outcome(self):
        repo = MagicMock()
        signal = {'recommendation_id': 'abc', 'ticker': 'AAPL', 'score': 50, 'score_details_json': '{}'}
        _feed_feedback(repo, signal, 'unknown', 0.0, {'feedback_enabled': True})
        repo.save_feedback_event.assert_not_called()

    def test_does_not_call_below_min_samples(self):
        repo = MagicMock()
        repo.get_valid_sample_count.return_value = 5
        signal = {'recommendation_id': 'abc', 'ticker': 'AAPL', 'score': 50, 'score_details_json': '{}'}
        _feed_feedback(repo, signal, 'expired_worthless', 1.0,
                       {'feedback_enabled': True, 'feedback_min_valid_samples': 30})
        repo.save_feedback_event.assert_not_called()
