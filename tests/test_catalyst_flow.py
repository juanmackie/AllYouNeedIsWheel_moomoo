from datetime import datetime, timedelta
from unittest.mock import patch

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


# ---------------------------------------------------------------------------
# Social context integration
# ---------------------------------------------------------------------------

def _make_catalyst_service(*, apewisdom_enabled=True, max_boost_tickers=2):
    from api.services.catalyst_flow_service import CatalystFlowService

    CatalystFlowService._shared_cache = {}

    return CatalystFlowService(
        config_provider={
            "db_path": ":memory:",
            "catalyst_flow": {
                "min_volume": 100,
                "min_premium_notional": 100_000,
                "min_fresh_volume_ratio": 2,
                "max_expirations": 1,
                "max_dte": 90,
                "max_scan_tickers": 2,
                "apewisdom": {
                    "enabled": apewisdom_enabled,
                    "min_mentions": 1,
                    "max_boost_tickers": max_boost_tickers,
                    "exclude_tickers": [],
                },
            },
            "evaluator": {"enabled": False},
        }
    )


def _fake_signal(ticker: str, score: float) -> list[dict]:
    return [{
        "ticker": ticker,
        "side": "CALL",
        "score": score,
        "cluster_expirations": ["20260619"],
        "is_hedged": False,
        "rationale": [],
        "blockers": [],
    }]


def _aw_payload(*tickers: str) -> dict:
    results = []
    for idx, ticker in enumerate(tickers, 1):
        results.append({
            "ticker": ticker,
            "mentions": str(100 - idx * 10),
            "mentions_24h_ago": "10",
            "rank": str(idx),
            "rank_24h_ago": str(idx + 10),
            "upvotes": "25",
        })
    return {"results": results}


def test_get_signals_reuses_apewisdom_cache_across_calls():
    svc = _make_catalyst_service()
    svc.connection = type("Conn", (), {"is_connected": lambda self: True})()

    def fake_scan(conn, ticker, config):
        return _fake_signal(ticker, 30 if ticker == "AAPL" else 28)

    with patch("api.services.apewisdom_service.ApeWisdomService.fetch_all_stocks", return_value=_aw_payload("AAPL", "TSLA")) as fetch_mock, patch.object(svc, "_scan_ticker", side_effect=fake_scan):
        first = svc.get_signals(tickers=["AAPL"], limit=2)
        second = svc.get_signals(tickers=["AAPL"], limit=2)

    assert fetch_mock.call_count == 1
    assert svc._apewisdom_service is not None
    assert first["apewisdom"]["boost_tickers_applied"] == ["TSLA"]
    assert second["apewisdom"]["boost_tickers_applied"] == ["TSLA"]


def test_watchlist_ticker_receives_social_context():
    svc = _make_catalyst_service()
    svc.connection = type("Conn", (), {"is_connected": lambda self: True})()

    with patch("api.services.apewisdom_service.ApeWisdomService.fetch_all_stocks", return_value=_aw_payload("AAPL")), patch.object(svc, "_scan_ticker", return_value=_fake_signal("AAPL", 40)):
        result = svc.get_signals(tickers=["AAPL"], limit=1)

    assert result["signals"][0]["social"]["source"] == "apewisdom"
    assert result["signals"][0]["social"]["mentions"] == 90
    assert result["apewisdom"]["boost_tickers_applied"] == []


def test_social_boost_resorts_signals_after_augmentation():
    svc = _make_catalyst_service(max_boost_tickers=1)
    svc.connection = type("Conn", (), {"is_connected": lambda self: True})()

    def fake_scan(conn, ticker, config):
        return _fake_signal(ticker, 30 if ticker == "AAPL" else 31)

    with patch("api.services.apewisdom_service.ApeWisdomService.fetch_all_stocks", return_value=_aw_payload("AAPL")), patch.object(svc, "_scan_ticker", side_effect=fake_scan):
        result = svc.get_signals(tickers=["AAPL", "MSFT"], limit=2)

    assert [signal["ticker"] for signal in result["signals"]] == ["AAPL", "MSFT"]
    assert result["signals"][0]["score"] == 31.5
    assert result["signals"][0]["social"]["source"] == "apewisdom"


def test_get_signals_falls_back_to_watchlist_when_apewisdom_disabled():
    svc = _make_catalyst_service(apewisdom_enabled=False)
    svc.connection = type("Conn", (), {"is_connected": lambda self: True})()

    with patch.object(svc, "_scan_ticker", return_value=_fake_signal("AAPL", 35)), patch("api.services.apewisdom_service.ApeWisdomService.fetch_all_stocks") as fetch_mock:
        result = svc.get_signals(tickers=["AAPL"], limit=1)

    assert result["signals"][0]["ticker"] == "AAPL"
    assert result["apewisdom"]["enabled"] is False
    fetch_mock.assert_not_called()
