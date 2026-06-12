from datetime import datetime, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

from core.catalyst_flow_decision import classify_catalyst_flow

_overlay_map_patch = patch('api.services.catalyst_flow_service.fetch_signal_overlay_map', return_value={})
_overlay_map_patch.start()


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

    # Social context is attached but no longer boosts score, so original order preserved
    assert [signal["ticker"] for signal in result["signals"]] == ["MSFT", "AAPL"]
    aapl_sig = next(s for s in result["signals"] if s["ticker"] == "AAPL")
    assert aapl_sig["social"]["source"] == "apewisdom"


def test_get_signals_falls_back_to_watchlist_when_apewisdom_disabled():
    svc = _make_catalyst_service(apewisdom_enabled=False)
    svc.connection = type("Conn", (), {"is_connected": lambda self: True})()

    with patch.object(svc, "_scan_ticker", return_value=_fake_signal("AAPL", 35)), patch("api.services.apewisdom_service.ApeWisdomService.fetch_all_stocks") as fetch_mock:
        result = svc.get_signals(tickers=["AAPL"], limit=1)

    assert result["signals"][0]["ticker"] == "AAPL"
    assert result["apewisdom"]["enabled"] is False
    fetch_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Action bucket classification
# ---------------------------------------------------------------------------

def _make_signal_with_sides(*, ticker, sides_and_scores):
    """Build a list of signal dicts with specified sides and scores."""
    signals = []
    for side, score in sides_and_scores:
        signals.append({
            "ticker": ticker,
            "side": side,
            "score": score,
            "cluster_expirations": ["20260619"],
            "is_hedged": False,
            "rationale": ["$1.5M premium notional"],
            "blockers": [],
            "action_bucket": "CALL_RESEARCH" if side == "CALL" else "PUT_RESEARCH",
            "action_label": "Call Research" if side == "CALL" else "Put Research",
            "action_reason": "Fresh flow detected",
        })
    return signals


def test_same_ticker_call_and_put_becomes_conflict_watch():
    svc = _make_catalyst_service()
    svc.connection = type("Conn", (), {"is_connected": lambda self: True})()

    call_sig = _make_signal_with_sides(ticker="AVGO", sides_and_scores=[("CALL", 60)])[0]
    put_sig = _make_signal_with_sides(ticker="AVGO", sides_and_scores=[("PUT", 55)])[0]

    def fake_scan(conn, ticker, config):
        if ticker == "AVGO":
            return [call_sig, put_sig]
        return []

    with patch.object(svc, "_scan_ticker", side_effect=fake_scan):
        result = svc.get_signals(tickers=["AVGO"], limit=6)

    for sig in result["signals"]:
        assert sig["action_bucket"] == "CONFLICT_WATCH"
        assert "conflicting signals" in sig["action_reason"].lower()


def test_extreme_otm_becomes_speculative_only():
    from core.catalyst_flow_decision import classify_catalyst_flow

    signals = classify_catalyst_flow(
        ticker="SPCE",
        stock_price=10.0,
        option_list=[
            {
                "strike": 30.5,
                "option_type": "CALL",
                "option_volume": 2000,
                "option_open_interest": 50,
                "bid_price": 0.05,
                "ask_price": 0.10,
                "expiration": "20260718",
            }
        ],
        config={
            "min_volume": 100,
            "min_premium_notional": 10_000,
            "min_fresh_volume_ratio": 2,
            "max_expirations": 3,
        },
    )

    assert signals
    assert signals[0].action_bucket == "SPECULATIVE_ONLY"
    assert "OTM" in signals[0].action_reason
    assert "lottery flow" in signals[0].action_reason


def test_social_context_does_not_upgrade_actionability():
    svc = _make_catalyst_service()
    svc.connection = type("Conn", (), {"is_connected": lambda self: True})()

    low_signal = _make_signal_with_sides(ticker="GME", sides_and_scores=[("CALL", 25)])[0]
    low_signal["actionable"] = False
    low_signal["research_only"] = True

    with patch("api.services.apewisdom_service.ApeWisdomService.fetch_all_stocks",
               return_value=_aw_payload("GME")), \
         patch.object(svc, "_scan_ticker", return_value=[low_signal]):
        result = svc.get_signals(tickers=["GME"], limit=1)

    sig = result["signals"][0]
    social_rationale = [r for r in sig.get("rationale", []) if "social momentum" in r.lower()]
    assert social_rationale, "Social context should be in rationale"
    assert sig["actionable"] is False
    assert sig["research_only"] is True


def test_call_research_bucket_assigned_for_clean_call_flow():
    from core.catalyst_flow_decision import classify_catalyst_flow

    signals = classify_catalyst_flow(
        ticker="AAPL",
        stock_price=200.0,
        option_list=[
            {
                "strike": 210.0,
                "option_type": "CALL",
                "option_volume": 1500,
                "option_open_interest": 50,
                "bid_price": 4.00,
                "ask_price": 4.50,
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
    assert signals[0].action_bucket == "CALL_RESEARCH"
    assert signals[0].actionable is False
    assert signals[0].research_only is True


def test_put_research_bucket_assigned_for_clean_put_flow():
    from core.catalyst_flow_decision import classify_catalyst_flow

    signals = classify_catalyst_flow(
        ticker="TSLA",
        stock_price=300.0,
        option_list=[
            {
                "strike": 280.0,
                "option_type": "PUT",
                "option_volume": 2000,
                "option_open_interest": 80,
                "bid_price": 8.00,
                "ask_price": 8.50,
                "expiration": "20260620",
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
    assert signals[0].action_bucket == "PUT_RESEARCH"
    assert signals[0].actionable is False
    assert signals[0].research_only is True


# ---------------------------------------------------------------------------
# Canonical ticker grouping
# ---------------------------------------------------------------------------

def test_conflict_detection_groups_by_canonical_ticker():
    svc = _make_catalyst_service()
    svc.connection = type("Conn", (), {"is_connected": lambda self: True})()

    call_sig = _make_signal_with_sides(ticker="US.AVGO", sides_and_scores=[("CALL", 60)])[0]
    put_sig = _make_signal_with_sides(ticker="AVGO", sides_and_scores=[("PUT", 55)])[0]

    def fake_scan(conn, ticker, config):
        if ticker == "US.AVGO":
            return [call_sig]
        if ticker == "AVGO":
            return [put_sig]
        return []

    with patch.object(svc, "_scan_ticker", side_effect=fake_scan):
        result = svc.get_signals(tickers=["US.AVGO", "AVGO"], limit=6)

    for sig in result["signals"]:
        assert sig["action_bucket"] == "CONFLICT_WATCH"


# ---------------------------------------------------------------------------
# Dominance logic
# ---------------------------------------------------------------------------

def test_dominant_side_keeps_research_bucket():
    """When one side's top score is >= 1.5x the other, dominant side keeps its bucket."""
    svc = _make_catalyst_service()
    svc.connection = type("Conn", (), {"is_connected": lambda self: True})()

    strong_call = _make_signal_with_sides(ticker="AAPL", sides_and_scores=[("CALL", 80)])[0]
    weak_put = _make_signal_with_sides(ticker="AAPL", sides_and_scores=[("PUT", 30)])[0]

    def fake_scan(conn, ticker, config):
        return [strong_call, weak_put]

    with patch.object(svc, "_scan_ticker", side_effect=fake_scan):
        result = svc.get_signals(tickers=["AAPL"], limit=6)

    call_sig = next(s for s in result["signals"] if s["side"] == "CALL")
    put_sig = next(s for s in result["signals"] if s["side"] == "PUT")
    assert call_sig["action_bucket"] == "CALL_RESEARCH"
    assert put_sig["action_bucket"] == "WATCH"
    assert "dominates" in put_sig["action_reason"].lower()


def test_balanced_flow_becomes_conflict():
    """When scores are close, both meaningful sides become CONFLICT_WATCH."""
    svc = _make_catalyst_service()
    svc.connection = type("Conn", (), {"is_connected": lambda self: True})()

    call_sig = _make_signal_with_sides(ticker="AAPL", sides_and_scores=[("CALL", 60)])[0]
    put_sig = _make_signal_with_sides(ticker="AAPL", sides_and_scores=[("PUT", 55)])[0]

    def fake_scan(conn, ticker, config):
        return [call_sig, put_sig]

    with patch.object(svc, "_scan_ticker", side_effect=fake_scan):
        result = svc.get_signals(tickers=["AAPL"], limit=6)

    for sig in result["signals"]:
        assert sig["action_bucket"] == "CONFLICT_WATCH"


# ---------------------------------------------------------------------------
# Warning suppression
# ---------------------------------------------------------------------------

def test_get_ticker_warnings_suppresses_conflict_signals():
    from api.services.catalyst_flow_service import CatalystFlowService

    CatalystFlowService._shared_cache = {
        "AVGO": {
            "ts": __import__("time").time(),
            "signals": [
                {
                    "side": "CALL",
                    "is_hedged": False,
                    "action_bucket": "CONFLICT_WATCH",
                },
                {
                    "side": "PUT",
                    "is_hedged": False,
                    "action_bucket": "CONFLICT_WATCH",
                },
            ],
        }
    }
    svc = CatalystFlowService(config_provider={"catalyst_flow": {}})
    warnings = svc.get_ticker_warnings("AVGO")
    assert warnings == []


def test_get_ticker_warnings_suppresses_speculative_signals():
    from api.services.catalyst_flow_service import CatalystFlowService

    CatalystFlowService._shared_cache = {
        "SPCE": {
            "ts": __import__("time").time(),
            "signals": [
                {
                    "side": "CALL",
                    "is_hedged": False,
                    "action_bucket": "SPECULATIVE_ONLY",
                },
            ],
        }
    }
    svc = CatalystFlowService(config_provider={"catalyst_flow": {}})
    warnings = svc.get_ticker_warnings("SPCE")
    assert warnings == []


def test_get_ticker_warnings_allows_clean_research_signals():
    from api.services.catalyst_flow_service import CatalystFlowService

    CatalystFlowService._shared_cache = {
        "AAPL": {
            "ts": __import__("time").time(),
            "signals": [
                {
                    "side": "CALL",
                    "is_hedged": False,
                    "action_bucket": "CALL_RESEARCH",
                },
            ],
        }
    }
    svc = CatalystFlowService(config_provider={"catalyst_flow": {}})
    warnings = svc.get_ticker_warnings("AAPL")
    assert len(warnings) == 1
    assert "covered calls" in warnings[0]


# ---------------------------------------------------------------------------
# Load-bearing fields
# ---------------------------------------------------------------------------

def test_signal_includes_volume_oi_bid_ask_spread():
    from core.catalyst_flow_decision import classify_catalyst_flow

    signals = classify_catalyst_flow(
        ticker="AAPL",
        stock_price=200.0,
        option_list=[
            {
                "strike": 210.0,
                "option_type": "CALL",
                "option_volume": 1500,
                "option_open_interest": 200,
                "bid_price": 4.00,
                "ask_price": 4.50,
                "expiration": "20260619",
                "implied_volatility": 0.35,
                "delta": 0.42,
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
    sig = signals[0]
    assert sig.volume == 1500
    assert sig.open_interest == 200
    assert sig.bid == 4.00
    assert sig.ask == 4.50
    assert sig.spread == 0.50
    assert sig.implied_volatility == 0.35
    assert sig.delta == 0.42
    assert sig.expiry == "20260619"
