from datetime import datetime, timedelta

import pandas as pd
import pytest

from core.catalyst_flow_decision import classify_catalyst_flow


def test_classifier_accepts_common_quote_alias_fields():
    signals = classify_catalyst_flow(
        ticker="AAPL",
        stock_price=100.0,
        option_list=[
            {
                "strike": 110.0,
                "option_type": "CALL",
                "option_volume": 300,
                "option_open_interest": 20,
                "bid_price": 5.00,
                "ask_price": 5.20,
                "expiration": "20260619",
            }
        ],
        config={
            "min_volume": 100,
            "min_premium_notional": 100_000,
            "min_fresh_volume_ratio": 2,
            "max_expirations": 3,
        },
    )

    assert signals
    assert signals[0].side == "CALL"
    assert signals[0].premium_notional > 100_000
    assert signals[0].fresh_volume_ratio == 15.0


def test_service_uses_requested_chain_side_when_snapshot_side_is_ambiguous():
    moomoo = pytest.importorskip("moomoo")
    from api.services.catalyst_flow_service import CatalystFlowService

    expiration = (datetime.now() + timedelta(days=30)).strftime("%Y%m%d")

    class FakeConnection:
        def get_stock_price(self, ticker):
            return 100.0

        def get_option_expiration_dates(self, ticker):
            return moomoo.RET_OK, pd.DataFrame({"expiration_date": [expiration]})

        def get_option_chain(self, ticker, exp_str, right, target_strike=None):
            if right != "C":
                return {"options": []}
            return {
                "options": [
                    {
                        "strike": 110.0,
                        "option_type": "PUT",
                        "expiration": exp_str,
                        "volume": 300,
                        "open_interest": 20,
                        "bid": 5.00,
                        "ask": 5.20,
                    }
                ]
            }

    svc = CatalystFlowService(
        config_provider={
            "db_path": ":memory:",
            "catalyst_flow": {
                "min_volume": 100,
                "min_premium_notional": 100_000,
                "min_fresh_volume_ratio": 2,
                "max_expirations": 1,
                "max_dte": 90,
            },
            "evaluator": {"enabled": False},
        }
    )

    signals = svc._scan_ticker(
        FakeConnection(),
        "AAPL",
        svc.config["catalyst_flow"],
    )

    assert signals
    assert signals[0]["side"] == "CALL"
