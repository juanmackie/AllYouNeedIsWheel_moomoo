"""Tests for core/position_diff.py — pure trade-event inference.

Covers: entry detection, exit detection, roll pairing (same underlying+type),
assignment detection (short put gone + share growth), partial buyback,
first-run baseline, and no-change idempotency.
"""

import unittest

from core.position_diff import infer_trade_events


def _opt(symbol, expiration, opt_type, strike, qty=-1, market_price=1.5):
    return {
        "symbol": symbol,
        "security_type": "OPT",
        "qty": qty,
        "strike": str(strike),
        "expiration": expiration,
        "option_type": opt_type,
        "market_price": market_price,
        "avg_cost": 0.0,
        "market_val": market_price * 100 * abs(qty),
        "contract_key": f"{symbol} {expiration} {opt_type[:1]}{strike}",
    }


def _stk(symbol, qty):
    return {"symbol": symbol, "security_type": "STK", "qty": qty}


def _snap(positions, captured_at="2026-08-22T15:00:00+00:00"):
    return {"captured_at": captured_at, "positions": positions}


class TestInferTradeEvents(unittest.TestCase):
    def test_first_run_baseline_no_events(self):
        self.assertEqual(infer_trade_events(None, _snap([_opt("AAPL", "20260918", "PUT", 200)])), [])
        self.assertEqual(infer_trade_events(_snap([]), None), [])

    def test_no_change_no_events(self):
        snap = _snap([_opt("AAPL", "20260918", "PUT", 200), _stk("AAPL", 100)])
        self.assertEqual(infer_trade_events(snap, snap), [])

    def test_new_short_put_is_entry(self):
        prev = _snap([])
        curr = _snap([_opt("TSLA", "20260904", "PUT", 300)])
        events = infer_trade_events(prev, curr)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["event_type"], "entry")
        self.assertEqual(event["ticker"], "TSLA")
        self.assertEqual(event["option_type"], "PUT")
        self.assertEqual(event["strike"], 300.0)
        self.assertEqual(event["expiration"], "20260904")
        self.assertEqual(event["details"]["source"], "position_diff")

    def test_vanished_short_put_is_exit(self):
        prev = _snap([_opt("TSLA", "20260904", "PUT", 300)])
        curr = _snap([_stk("TSLA", 0) or {}])
        events = infer_trade_events(prev, curr)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "exit")
        self.assertEqual(events[0]["ticker"], "TSLA")

    def test_same_underlying_move_is_roll(self):
        prev = _snap([_opt("TSLA", "20260904", "PUT", 300), _opt("TSLA", "20261002", "PUT", 280)])
        curr = _snap([_opt("TSLA", "20261002", "PUT", 280), _opt("TSLA", "20261016", "PUT", 290)])
        events = infer_trade_events(prev, curr)
        rolls = [e for e in events if e["event_type"] == "roll"]
        self.assertEqual(len(rolls), 1)
        roll = rolls[0]
        self.assertEqual(roll["from_strike"], 300.0)
        self.assertEqual(roll["from_expiration"], "20260904")
        self.assertEqual(roll["strike"], 290.0)
        self.assertEqual(roll["expiration"], "20261016")
        # No duplicate exit/entry for the paired legs.
        self.assertEqual(len(events), 1)

    def test_different_types_not_rolled(self):
        prev = _snap([_opt("AAPL", "20260918", "CALL", 250)])
        curr = _snap([_opt("AAPL", "20261016", "PUT", 230)])
        types = sorted(e["event_type"] for e in infer_trade_events(prev, curr))
        self.assertEqual(types, ["entry", "exit"])

    def test_short_put_assignment(self):
        prev = _snap([_opt("INTC", "20260821", "PUT", 30), _stk("INTC", 0)])
        curr = _snap([_stk("INTC", 100)])
        events = infer_trade_events(prev, curr)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "assignment")
        self.assertEqual(events[0]["ticker"], "INTC")

    def test_partial_buyback_is_exit_with_counts(self):
        prev = _snap([_opt("NVDA", "20260918", "PUT", 180, qty=-2)])
        curr = _snap([_opt("NVDA", "20260918", "PUT", 180, qty=-1)])
        events = infer_trade_events(prev, curr)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["event_type"], "exit")
        self.assertEqual(event["details"]["contracts_closed"], 1)
        self.assertEqual(event["details"]["remaining_contracts"], -1)

    def test_long_options_ignored(self):
        prev = _snap([_opt("SPY", "20260904", "CALL", 600, qty=2)])
        curr = _snap([])
        self.assertEqual(infer_trade_events(prev, curr), [])


if __name__ == "__main__":
    unittest.main()
