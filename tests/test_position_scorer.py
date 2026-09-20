"""Tests for core/position_scorer.py — the open-position → exit-playbook bridge.

The pure rules live in tests/test_exit_playbook.py. These tests prove the wiring:
that entry credit, earnings timing, and the ex-dividend day count actually reach
`evaluate_exit` from a scored position (the failure mode that would leave a rule
implemented but never firing).
"""

import unittest

from core.exit_playbook import VERDICT_CLOSE, VERDICT_HOLD, VERDICT_TAKE_PROFIT
from core.position_scorer import score_existing_position

PORTFOLIO_CONTEXT = {
    "positions": {},
    "cash_balance": 10000.0,
    "available_cash": 10000.0,
    "cash_available_for_csp": 10000.0,
    "account_value": 50000.0,
}


def _position(option_type, strike, stock_price, dte, bid, ask, avg_cost, delta):
    return {
        "option_type": option_type,
        "strike": strike,
        "expiration": "20260115",
        "dte": dte,
        "bid": bid,
        "ask": ask,
        "last": (bid + ask) / 2,
        "delta": delta,
        "theta": -0.05,
        "implied_volatility": 0.35,
        "avg_cost": avg_cost,
    }


def _score(position, stock_price, earnings_info=None):
    return score_existing_position(
        ticker="AAPL",
        position_data=position,
        current_stock_price=stock_price,
        portfolio_context=PORTFOLIO_CONTEXT,
        earnings_info=earnings_info,
    )


class TestExitBridge(unittest.TestCase):
    def _mild_itm_call(self, dte):
        """A call barely ITM with a mark near the entry credit.

        Deliberately avoids the other rules so each test isolates the
        ex-dividend wiring: |delta| 0.55 < 0.65, -0.95% ITM << 15% deep,
        captured -5% (no profit take, no loss stop), and the roll rule needs a
        non-ITM strike.
        """
        return _position("CALL", 104.0, 105.0, dte, 1.00, 1.10, 1.00, 0.55)

    def test_itm_call_with_ex_dividend_before_expiry_closes(self):
        decision = _score(
            self._mild_itm_call(20),
            105.0,
            earnings_info={"days_to_ex_dividend": 8, "days_to_earnings": None},
        )
        self.assertEqual(decision.exit_verdict, VERDICT_CLOSE)
        self.assertTrue(any("Ex-dividend" in r for r in decision.exit_reasons))

    def test_ex_dividend_after_expiry_does_not_close(self):
        decision = _score(self._mild_itm_call(10), 105.0, earnings_info={"days_to_ex_dividend": 25})
        self.assertNotEqual(decision.exit_verdict, VERDICT_CLOSE)
        self.assertFalse(any("Ex-dividend" in r for r in decision.exit_reasons))

    def test_missing_ex_dividend_key_is_silent(self):
        decision = _score(self._mild_itm_call(20), 105.0, earnings_info={})
        self.assertEqual(decision.exit_verdict, VERDICT_HOLD)
        self.assertFalse(any("Ex-dividend" in r for r in decision.exit_reasons))

    def test_double_entry_credit_triggers_loss_stop(self):
        # Entry credit 1.00, current mark 2.00 -> -100% captured -> 2x stop.
        decision = _score(_position("PUT", 100.0, 95.0, 40, 1.95, 2.05, 1.00, -0.50), 95.0)
        self.assertEqual(decision.exit_verdict, VERDICT_CLOSE)
        self.assertTrue(any("Loss stop" in r for r in decision.exit_reasons))

    def test_loss_short_of_stop_holds(self):
        decision = _score(_position("PUT", 100.0, 95.0, 40, 1.85, 1.95, 1.00, -0.45), 95.0)
        self.assertEqual(decision.exit_verdict, VERDICT_HOLD)

    def test_unknown_entry_credit_cannot_trigger_stop_or_profit_take(self):
        decision = _score(_position("PUT", 100.0, 95.0, 40, 1.95, 2.05, 0.0, -0.50), 95.0)
        self.assertNotEqual(decision.exit_verdict, VERDICT_CLOSE)
        self.assertNotEqual(decision.exit_verdict, VERDICT_TAKE_PROFIT)


if __name__ == "__main__":
    unittest.main()
