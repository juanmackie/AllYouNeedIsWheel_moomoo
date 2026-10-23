"""Focused tests for core/account_pnl.py: the broker-truth account P&L model.

Synthetic tickers and values only. No broker, OpenD, or database access.
"""

import unittest

from core.account_pnl import build_account_pnl

AS_OF = "2026-10-23T09:00:00"


def _fill(ticker, security_type, side, qty, price, fees=0.0, option_type="", strike=None, expiration="", at=None):
    return {
        "ticker": ticker,
        "security_type": security_type,
        "side": side,
        "qty": qty,
        "price": price,
        "fees": fees,
        "option_type": option_type,
        "strike": strike,
        "expiration": expiration,
        "captured_at": at or "2026-03-02T10:00:00",
    }


def _stk(symbol, qty, market_price, avg_cost):
    return {
        "symbol": symbol,
        "security_type": "STK",
        "qty": qty,
        "market_price": market_price,
        "avg_cost": avg_cost,
    }


def _opt(symbol, qty, market_price, option_type, strike, expiration, underlying):
    return {
        "symbol": symbol,
        "security_type": "OPT",
        "qty": qty,
        "market_price": market_price,
        "avg_cost": 0.0,
        "strike": str(strike),
        "expiration": expiration,
        "option_type": option_type,
        "underlying": underlying,
    }


def _row(payload, ticker):
    return next(row for row in payload["underlyings"] if row["ticker"] == ticker)


class TestAssignmentAndCalledAway(unittest.TestCase):
    def test_assigned_then_called_away_below_basis_is_reconciled(self):
        fills = [
            _fill("AAA", "OPT", "SELL", 1, 7.05, fees=0.55, option_type="PUT", strike=187.5, expiration="20260320"),
            _fill("AAA", "OPT", "BUY", 1, 0.0, fees=0.0, option_type="PUT", strike=187.5, expiration="20260320"),
            _fill("AAA", "STK", "BUY", 100, 187.5, fees=0.0, at="2026-03-21T10:00:00"),
            _fill("AAA", "OPT", "SELL", 1, 1.10, fees=0.56, option_type="CALL", strike=142.0, expiration="20260417"),
            _fill("AAA", "OPT", "BUY", 1, 0.0, fees=0.0, option_type="CALL", strike=142.0, expiration="20260417"),
            _fill("AAA", "STK", "SELL", 100, 142.0, fees=0.29, at="2026-04-18T10:00:00"),
        ]

        payload = build_account_pnl(fills, [], positions_as_of=AS_OF)
        row = _row(payload, "AAA")

        self.assertEqual(row["status"], "reconciled")
        self.assertEqual(row["share_pnl"], -4550.00)
        self.assertEqual(row["option_pnl"], 815.00)
        self.assertEqual(row["fees"], 1.40)
        self.assertEqual(row["net_pnl"], -3736.40)
        self.assertEqual(row["notes"], [])


class TestOpenShortPutAtMark(unittest.TestCase):
    def test_open_short_put_is_valued_at_broker_mark(self):
        fills = [
            _fill("BBB", "OPT", "SELL", 1, 9.10, fees=0.56, option_type="PUT", strike=126.0, expiration="20261218"),
        ]
        positions = [_opt("BBB261218P126000", -1, 28.73, "PUT", 126.0, "20261218", "BBB")]

        payload = build_account_pnl(fills, positions, positions_as_of=AS_OF)
        row = _row(payload, "BBB")

        self.assertEqual(row["status"], "reconciled")
        self.assertEqual(row["option_pnl"], -1963.00)
        self.assertEqual(row["net_pnl"], -1963.56)
        self.assertEqual(row["open_contracts"], 1)


class TestExpiredShortPut(unittest.TestCase):
    def test_expired_worthless_put_keeps_premium_and_has_no_mark(self):
        # OpenD records the expiry of a short option as a zero-price BUY.
        fills = [
            _fill("CCC", "OPT", "SELL", 1, 1.20, fees=0.55, option_type="PUT", strike=59.0, expiration="20261016"),
            _fill("CCC", "OPT", "BUY", 1, 0.0, fees=0.0, option_type="PUT", strike=59.0, expiration="20261016"),
        ]

        payload = build_account_pnl(fills, [], positions_as_of=AS_OF)
        row = _row(payload, "CCC")

        self.assertEqual(row["status"], "reconciled")
        self.assertEqual(row["option_pnl"], 120.00)
        self.assertEqual(row["net_pnl"], 119.45)
        self.assertEqual(row["open_contracts"], 0)

    def test_missing_fills_on_expired_contracts_are_unknown_not_profit_or_loss(self):
        # A buy-to-close whose opening sell is older than the fill history must
        # not read as a long option that expired worthless, and an expired short
        # without its closing record must not read as kept premium.
        fills = [
            _fill("IRN", "OPT", "BUY", 2, 4.55, fees=1.06, option_type="PUT", strike=52.0, expiration="20260702"),
            _fill("KPT", "OPT", "SELL", 1, 1.20, fees=0.55, option_type="PUT", strike=59.0, expiration="20261016"),
        ]

        payload = build_account_pnl(fills, [], positions_as_of=AS_OF)

        for ticker in ("IRN", "KPT"):
            row = _row(payload, ticker)
            self.assertEqual(row["status"], "unreconciled")
            self.assertIsNone(row["net_pnl"])
        self.assertEqual(payload["totals"]["unreconciled_count"], 2)
        self.assertEqual(payload["totals"]["net_pnl"], 0.0)


class TestPartialHistory(unittest.TestCase):
    def test_shares_bought_before_fill_history_use_broker_average_cost(self):
        fills = [
            _fill("DDD", "OPT", "SELL", 1, 1.00, fees=0.0, option_type="CALL", strike=70.0, expiration="20260417"),
            _fill("DDD", "OPT", "BUY", 1, 0.10, fees=0.0, option_type="CALL", strike=70.0, expiration="20260417"),
        ]
        positions = [_stk("DDD", 126, 68.8, 69.194)]

        payload = build_account_pnl(fills, positions, positions_as_of=AS_OF)
        row = _row(payload, "DDD")

        self.assertEqual(row["status"], "partial_history")
        self.assertEqual(row["share_pnl"], -49.64)
        self.assertEqual(row["option_pnl"], 90.00)
        self.assertEqual(row["shares_held"], 126)
        self.assertTrue(any("126 shares were held before the fill history" in note for note in row["notes"]))

    def test_window_bought_shares_use_broker_cost_basis_for_share_pnl(self):
        # Reviewer repro: 60 shares bought in the window at 40, broker holds 100 at average cost 50.
        # The broker cost covers the old and new lots, so the window cash is not added again.
        fills = [_fill("HHH", "STK", "BUY", 60, 40.0, at="2026-03-02T10:00:00")]
        positions = [_stk("HHH", 100, 60.0, 50.0)]

        payload = build_account_pnl(fills, positions, positions_as_of=AS_OF)
        row = _row(payload, "HHH")

        self.assertEqual(row["status"], "partial_history")
        self.assertEqual(row["share_pnl"], 1000.00)
        self.assertEqual(row["net_pnl"], 1000.00)

    def test_old_shares_partly_sold_in_window_are_unreconciled(self):
        # 60 shares bought in the window, 20 sold in the window, 100 held. The sold
        # shares may have come from the older lots, so realized P&L is unknown.
        fills = [
            _fill("III", "STK", "BUY", 60, 40.0, at="2026-03-02T10:00:00"),
            _fill("III", "STK", "SELL", 20, 45.0, at="2026-04-02T10:00:00"),
        ]
        positions = [_stk("III", 100, 60.0, 50.0)]

        payload = build_account_pnl(fills, positions, positions_as_of=AS_OF)
        row = _row(payload, "III")

        self.assertEqual(row["status"], "unreconciled")
        self.assertIsNone(row["share_pnl"])
        self.assertIsNone(row["net_pnl"])
        self.assertIn(
            "shares older than the fill history were partly sold; realized share P&L is unknown", row["notes"]
        )
        self.assertEqual(payload["totals"]["unreconciled_count"], 1)


class TestMismatch(unittest.TestCase):
    def test_open_contract_missing_at_broker_is_unreconciled_and_excluded_from_totals(self):
        fills = [
            _fill("EEE", "OPT", "SELL", 1, 1.10, fees=0.0, option_type="PUT", strike=59.0, expiration="20261030"),
            _fill("FFF", "OPT", "SELL", 1, 0.50, fees=0.10, option_type="CALL", strike=10.0, expiration="20260417"),
            _fill("FFF", "OPT", "BUY", 1, 0.0, fees=0.0, option_type="CALL", strike=10.0, expiration="20260417"),
        ]

        payload = build_account_pnl(fills, [], positions_as_of=AS_OF)
        unreconciled = _row(payload, "EEE")
        reconciled = _row(payload, "FFF")

        self.assertEqual(unreconciled["status"], "unreconciled")
        self.assertIsNone(unreconciled["net_pnl"])
        self.assertIsNone(unreconciled["option_pnl"])
        self.assertIsNone(unreconciled["share_pnl"])
        self.assertIn("fills show -1 PUT 59.0 20261030, broker holds 0", unreconciled["notes"])
        self.assertEqual(payload["totals"]["unreconciled_count"], 1)
        self.assertEqual(payload["totals"]["net_pnl"], reconciled["net_pnl"])
        self.assertEqual(payload["totals"]["net_pnl"], 49.90)


class TestUnknownFee(unittest.TestCase):
    def test_unknown_fee_is_counted_and_known_fees_still_apply(self):
        fills = [
            _fill("GGG", "OPT", "SELL", 1, 0.30, fees=None, option_type="PUT", strike=20.0, expiration="20260320"),
            _fill("GGG", "OPT", "SELL", 1, 0.20, fees=0.65, option_type="PUT", strike=20.0, expiration="20260320"),
            _fill("GGG", "OPT", "BUY", 2, 0.0, fees=0.0, option_type="PUT", strike=20.0, expiration="20260320"),
        ]

        payload = build_account_pnl(fills, [], positions_as_of=AS_OF)
        row = _row(payload, "GGG")

        self.assertEqual(row["status"], "reconciled")
        self.assertEqual(row["fees_unknown"], 1)
        self.assertEqual(row["fees"], 0.65)
        self.assertEqual(row["net_pnl"], 49.35)
        self.assertIn("1 fill fee(s) not yet known; net excludes them", row["notes"])
        self.assertEqual(payload["totals"]["fees_unknown"], 1)


class TestCashFlowsAndNav(unittest.TestCase):
    def setUp(self):
        self.cash_flows = [
            {
                "clearing_date": "2026-05-04",
                "currency": "AUD",
                "flow_type": "Bank Transfer Deposits",
                "amount": 10000.0,
                "remark": "",
            },
            {"clearing_date": "2026-05-04", "currency": "AUD", "flow_type": "Currency Exchange", "amount": -6500.0},
            {"clearing_date": "2026-05-04", "currency": "USD", "flow_type": "Currency Exchange", "amount": 6500.0},
            {"clearing_date": "2026-06-30", "currency": "USD", "flow_type": "Cash Plus interest", "amount": 5.0},
            {"clearing_date": "2026-07-15", "currency": "USD", "flow_type": "Cash Dividend", "amount": 10.0},
            {"clearing_date": "2026-07-15", "currency": "USD", "flow_type": "Dividend Tax", "amount": -1.5},
        ]
        self.nav_history = [
            {"captured_at": "2026-05-01T16:00:00", "net_liquidation": 50000.0},
            {"captured_at": "2026-08-22T16:00:00", "net_liquidation": 59501.29},
            {"captured_at": "2026-10-23T16:00:00", "net_liquidation": 55986.75},
        ]
        self.coverage = {"earliest": "2026-03-01", "latest": "2026-10-23"}

    def test_income_capital_movements_and_nav_since_last_deposit(self):
        payload = build_account_pnl(
            [],
            [],
            cash_flows=self.cash_flows,
            nav_history=self.nav_history,
            positions_as_of=AS_OF,
            cash_flow_coverage=self.coverage,
        )

        self.assertEqual(payload["income"]["usd_total"], 13.50)
        self.assertNotIn("Currency Exchange", payload["income"]["by_currency"].get("USD", {}))
        self.assertEqual(payload["capital_movements"]["count"], 1)
        self.assertEqual(payload["capital_movements"]["last_date"], "2026-05-04")
        self.assertEqual(payload["capital_movements"]["by_currency"], {"AUD": 10000.0})

        nav = payload["nav_since_last_capital_movement"]
        self.assertEqual(nav["start_at"], "2026-08-22T16:00:00")
        self.assertEqual(nav["end_at"], "2026-10-23T16:00:00")
        self.assertEqual(nav["change"], -3514.54)
        self.assertEqual(nav["change_pct"], -5.91)
        self.assertTrue(nav["verified"])

    def test_unknown_cash_flow_types_are_unclassified_and_not_counted_as_income(self):
        cash_flows = self.cash_flows + [
            {"clearing_date": "2026-07-01", "currency": "USD", "flow_type": "Trade Settlement", "amount": -250.0},
        ]

        payload = build_account_pnl([], [], cash_flows=cash_flows, positions_as_of=AS_OF)

        self.assertEqual(payload["income"]["usd_total"], 13.50)
        self.assertEqual(payload["income"]["unclassified"], {"USD": {"Trade Settlement": -250.0}})
        self.assertNotIn("Trade Settlement", payload["income"]["by_currency"].get("USD", {}))
        self.assertEqual(payload["totals"]["net_pnl_with_income"], 13.50)

    def test_nav_is_null_after_an_undated_capital_movement(self):
        cash_flows = [
            {"clearing_date": "", "currency": "AUD", "flow_type": "Bank Transfer Deposits", "amount": 500.0},
        ]

        payload = build_account_pnl(
            [],
            [],
            cash_flows=cash_flows,
            nav_history=self.nav_history,
            positions_as_of=AS_OF,
            cash_flow_coverage=self.coverage,
        )

        self.assertEqual(payload["capital_movements"]["count"], 1)
        self.assertIsNone(payload["capital_movements"]["last_date"])
        self.assertIsNone(payload["nav_since_last_capital_movement"])

    def test_nav_is_unverified_without_cash_flow_coverage(self):
        payload = build_account_pnl(
            [],
            [],
            cash_flows=self.cash_flows,
            nav_history=self.nav_history,
            positions_as_of=AS_OF,
            cash_flow_coverage={},
        )

        self.assertFalse(payload["nav_since_last_capital_movement"]["verified"])


if __name__ == "__main__":
    unittest.main()
