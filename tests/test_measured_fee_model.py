import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from api.services.fee_model import get_measured_option_fee_model
from core.measured_fee_model import estimate_us_option_fees, measured_fee_note


def _fill(order_id, side, qty, fees, code="US.AAPL261016C00185000", security_type="OPT"):
    return {
        "order_id": order_id,
        "side": side,
        "qty": qty,
        "fees": fees,
        "security_type": security_type,
        "raw_json": {"code": code},
    }


class TestMeasuredOptionFeeModel(unittest.TestCase):
    def test_uses_side_specific_median_of_complete_orders(self):
        fills = [
            _fill("buy-1", "BUY", 1, 0.50),
            _fill("buy-1", "BUY", 1, 0.50),
            _fill("buy-2", "BUY", 2, 1.20),
            _fill("buy-incomplete", "BUY", 1, None),
            _fill("sell-1", "SELL", 1, 0.55),
            _fill("sell-2", "SELL", 2, 1.20),
            _fill("foreign", "SELL", 1, 4.0, code="HK.AAPL261016C185"),
            _fill("stock", "SELL", 1, 0.01, security_type="STK"),
        ]

        model = estimate_us_option_fees(fills)

        self.assertEqual(model["BUY"]["fee_per_contract"], 0.55)
        self.assertEqual(model["BUY"]["sample_orders"], 2)
        self.assertEqual(model["SELL"]["fee_per_contract"], 0.575)
        self.assertEqual(model["SELL"]["sample_orders"], 2)
        self.assertEqual(model["source"], "measured_from_fills")
        self.assertIn("measured from your fills", measured_fee_note(model).lower())

    def test_missing_history_remains_unknown_instead_of_zero(self):
        model = estimate_us_option_fees([_fill("unknown", "BUY", 1, None)])

        self.assertIsNone(model["BUY"]["fee_per_contract"])
        self.assertIsNone(model["SELL"]["fee_per_contract"])
        self.assertEqual(model["source"], "unknown")
        self.assertIn("not treated as zero", measured_fee_note(model))

    @patch("api.services.fee_model.market_now")
    def test_service_reads_only_account_scoped_recent_option_fills(self, market_now):
        market_now.return_value = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
        db = MagicMock()
        db.get_fills.return_value = [
            _fill("buy-1", "BUY", 1, 0.50),
            _fill("sell-1", "SELL", 1, 0.55),
        ]

        model = get_measured_option_fee_model(db, "REAL", "opaque-account")

        db.get_fills.assert_called_once_with(
            env="REAL",
            account_id="opaque-account",
            security_type="OPT",
            start="2026-07-02",
            limit=10000,
        )
        self.assertEqual(model["BUY"]["fee_per_contract"], 0.5)
        self.assertEqual(model["SELL"]["fee_per_contract"], 0.55)


if __name__ == "__main__":
    unittest.main()
