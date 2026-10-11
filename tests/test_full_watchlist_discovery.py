"""Whole-union regressions through the real read-only adapter, cache, and scorer."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from api.services.recommendations import RecommendationEngine
from api.services.watchlist_manager import WatchlistManager
from core.connection_manager import MoomooConnection
from core.quote_cache import OptionChainCache, contract_windows
from core.run_model import recompute_effective_snapshot
from core.utils import market_now
from core.wheel_runner import WheelRunner
from db.database import OptionsDatabase


@pytest.fixture
def scan(tmp_path, monkeypatch):
    monkeypatch.setattr(MoomooConnection, "_instances", {})
    conn = MoomooConnection()
    conn.quote_ctx = MagicMock()
    conn.is_connected = lambda: True
    conn._rate_limiter.check_rate_limit = MagicMock()
    conn._option_chain_rate_limiter.check_rate_limit = MagicMock()
    conn._option_chain_rate_limiter._api_calls_count = 0
    now = market_now()
    option_rows = {}
    tickers = [f"TICK{index}" for index in range(69)]

    def get_chain(code, start, end, option_type):
        conn._option_chain_rate_limiter._api_calls_count += 1
        assert option_type == "ALL"
        price = 50 if code in {f"US.{ticker}" for ticker in tickers[:9]} else 150
        rows = []
        for dte in (14, 21):
            expiry = (now.date() + timedelta(days=dte)).isoformat()
            if not start <= expiry <= end:
                continue
            for right in ("CALL", "PUT"):
                option_code = f"{code}{expiry}{right}"
                rows.append(
                    {"code": option_code, "strike_price": price * 0.9, "strike_time": expiry, "option_type": right}
                )
                option_rows[option_code] = {
                    "code": option_code,
                    "option_strike_price": price * 0.9,
                    "option_expiry_date": expiry,
                    "option_type": right,
                    "bid_price": 1,
                    "ask_price": 1.05,
                    "last_price": 1,
                    "volume": 100,
                    "option_open_interest": 500,
                    "option_implied_volatility": 30,
                    "option_delta": -0.3,
                    "update_time": now.strftime("%Y-%m-%d %H:%M:%S"),
                }
        return 0, pd.DataFrame(rows)

    def snapshots(codes):
        return 0, pd.DataFrame(
            [
                option_rows[code]
                if code in option_rows
                else {
                    "code": code,
                    "last_price": 50 if code in {f"US.{ticker}" for ticker in tickers[:9]} else 150,
                    "update_time": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "stock_type": "STOCK",
                }
                for code in codes
            ]
        )

    conn.quote_ctx.get_option_chain.side_effect = get_chain
    conn.quote_ctx.get_market_snapshot.side_effect = snapshots
    db = OptionsDatabase(str(tmp_path / "scan.db"))
    config = {"wheel_preset": "aggressive"}
    manager = WatchlistManager(config, db)
    manager.get_scan_universe = MagicMock(
        return_value={
            "status": "ok",
            "group_name": "All",
            "tickers": tickers,
            "raw_codes": {},
            "unsupported": [],
        }
    )
    connection = MagicMock()
    connection._ensure_connection.return_value = conn
    portfolio = {
        "positions": {},
        "cash_available_for_csp": 1000,
        "account_value": 100000,
        "underlying_capital_exposure": {},
        "short_calls": {},
        "short_puts": {},
    }
    context = MagicMock()
    context.get_portfolio_context.return_value = portfolio
    iv = MagicMock()
    iv.get_iv_environment_score.return_value = (0, 0.5, "normal")
    iv.get_earnings_score_impact.return_value = (0, None)
    iv.get_earnings_info.return_value = {}
    engine = RecommendationEngine(connection, config, db, iv, context, MagicMock(), manager, MagicMock(), MagicMock())
    yield engine, conn, db, portfolio, tickers
    db.close()


def test_69_symbols_cold_warm_restart_and_review_only(scan):
    engine, conn, db, portfolio, tickers = scan
    progress = []
    with patch("api.services.recommendations.is_market_open", return_value=False):
        cold = engine.get_top_recommendations(progress_callback=lambda *args: progress.append(args))
        assert cold["success"], cold
        assert cold["scan_coverage"] == {"scanned": 69, "total": 69, "complete": True}
        assert cold["preflight"]["chain_calls"] == 69
        assert conn.quote_ctx.get_option_chain.call_count == 69
        assert cold["watchlist_csps"]["signals"]
        assert all(
            pick["research_only"] and not pick["copy_eligible"] and pick["max_contracts"] == 0
            for pick in cold["watchlist_csps"]["signals"]
        )
        assert progress[-1] == ("discover", 69, 69)
        warm = engine.get_top_recommendations()
        assert warm["scan_coverage"]["complete"]
        assert warm["preflight"]["chain_calls"] == 0
        assert conn.quote_ctx.get_option_chain.call_count == 69
        # Empty memory after a process restart still reads today's SQLite directory.
        conn._quote_cache = OptionChainCache()
        engine._yfinance_cache.clear()
        restarted = engine.get_top_recommendations()
        assert restarted["scan_coverage"]["complete"]
        assert restarted["preflight"]["chain_calls"] == 0
        assert conn.quote_ctx.get_option_chain.call_count == 69
    with patch("core.wheel_runner.is_market_open", return_value=False):
        snapshot = WheelRunner(db, MagicMock(), {})._build_snapshot("SIMULATE", "test", "", cold, portfolio, [])
    effective = recompute_effective_snapshot(snapshot.to_dict())
    assert effective["run"]["coverage_complete"]
    assert all(pick["eligibility"]["mode"] == "review_only" for pick in effective["csp_picks"])
    assert all(len(call.args[0]) <= 400 for call in conn.quote_ctx.get_market_snapshot.call_args_list)


def test_successful_empty_directory_counts_as_assessed(scan):
    engine, conn, db, portfolio, tickers = scan
    conn.quote_ctx.get_option_chain.side_effect = None
    conn.quote_ctx.get_option_chain.return_value = (0, pd.DataFrame())
    result = engine.get_top_recommendations()
    assert result["scan_coverage"]["complete"], result
    assert result["blocked_reason_counts"]["no_contracts_in_window"] == 69


def test_over_budget_remains_planning_without_discovery(scan):
    engine, conn, db, portfolio, tickers = scan
    engine._watchlist_provider._config_provider["scan_discovery_budget_sec"] = 1
    result = engine.get_top_recommendations()
    assert result["state"] == "planning"
    assert not result["scan_coverage"]["complete"]
    conn.quote_ctx.get_option_chain.assert_not_called()


def test_directory_date_and_window_boundaries():
    assert list(contract_windows("2026-10-01", "2026-10-31")) == [
        ("2026-10-01", "2026-10-30"),
        ("2026-10-31", "2026-10-31"),
    ]
    cache = OptionChainCache()
    cache.cache_contracts("US.AAPL", "2026-10-01", "2026-10-30", [])
    assert cache.get_contracts("US.AAPL", "2026-10-01", "2026-10-30") == []
    assert cache.get_contracts("US.AAPL", "2026-10-01", "2026-10-31") is None
    with patch("core.quote_cache.market_now", return_value=market_now() + timedelta(days=1)):
        assert cache.get_contracts("US.AAPL", "2026-10-01", "2026-10-30") is None


def test_quotes_batch_at_400_and_force_refresh_reuses_directory(scan):
    engine, conn, db, portfolio, tickers = scan
    start, end = engine._csp_discovery_window()
    contracts = conn.get_option_contracts("TICK0", start, end)
    conn.get_option_contracts("TICK0", start, end)
    assert conn.quote_ctx.get_option_chain.call_count == 1
    expiry = contracts[0]["expiration"]
    chain = conn.get_option_chain("TICK0", expiry, "P", force_refresh=True)
    assert chain["options"]
    assert conn.quote_ctx.get_option_chain.call_count == 1
    conn.quote_ctx.get_market_snapshot.reset_mock()
    conn.get_option_quotes([f"US.OPTION{index}" for index in range(801)])
    assert [len(call.args[0]) for call in conn.quote_ctx.get_market_snapshot.call_args_list] == [400, 400, 1]


def test_fitting_candidates_precede_review_only_but_keep_rank_key(scan):
    engine, conn, db, portfolio, tickers = scan
    portfolio["cash_available_for_csp"] = 5000
    result = engine.get_top_recommendations()
    picks = result["watchlist_csps"]["signals"]
    flags = [bool(pick["research_only"]) for pick in picks]
    assert flags == sorted(flags)
    assert False in flags and True in flags
    for market_open in (False, True):
        with patch("core.wheel_runner.is_market_open", return_value=market_open):
            snapshot = WheelRunner(db, MagicMock(), {})._build_snapshot("SIMULATE", "test", "", result, portfolio, [])
        effective = recompute_effective_snapshot(snapshot.to_dict())
        assert all(
            pick["eligibility"]["mode"] == "review_only" for pick in effective["csp_picks"] if pick.get("research_only")
        )
        if not market_open:
            assert all(
                pick["eligibility"]["mode"] == "staged"
                for pick in effective["csp_picks"]
                if not pick.get("research_only")
            )
    assert all(pick["chain_source"] == "broker" and pick["price_source"] == "broker" for pick in picks)


def _fail_chain(conn, failing_code, *, times):
    """Make contract discovery fail for one underlying on its first ``times`` calls."""
    real = conn.quote_ctx.get_option_chain.side_effect
    remaining = {"count": times}

    def chain(code, start, end, option_type):
        if code == failing_code and remaining["count"] > 0:
            remaining["count"] -= 1
            return -1, "request timeout"
        return real(code, start, end, option_type)

    conn.quote_ctx.get_option_chain.side_effect = chain


def test_transient_discovery_failure_is_retried_in_the_same_run(scan):
    engine, conn, db, portfolio, tickers = scan
    _fail_chain(conn, "US.TICK3", times=1)
    result = engine.get_top_recommendations()
    assert result["scan_coverage"] == {"scanned": 69, "total": 69, "complete": True}, result["blocked_reason_counts"]
    assert conn.quote_ctx.get_option_chain.call_count == 70
    assert "broker_data_unavailable" not in result["blocked_reason_counts"]


def test_persistent_discovery_failure_stays_partial_and_names_the_symbol(scan):
    engine, conn, db, portfolio, tickers = scan
    _fail_chain(conn, "US.TICK3", times=99)
    result = engine.get_top_recommendations()
    assert result["scan_coverage"] == {"scanned": 68, "total": 69, "complete": False}
    # Exactly one retry: 69 first calls plus one more for the failed symbol.
    assert conn.quote_ctx.get_option_chain.call_count == 70
    blocked = [row for row in result["blocked_signals"] if row["reason_code"] == "broker_data_unavailable"]
    assert [row["ticker"] for row in blocked] == ["TICK3"]
    assert "request timeout" in blocked[0]["reason_text"]
    with patch("core.wheel_runner.is_market_open", return_value=False):
        snapshot = WheelRunner(db, MagicMock(), {})._build_snapshot("SIMULATE", "test", "", result, portfolio, [])
    assert snapshot.run.partial_symbols == ("TICK3",)
    effective = recompute_effective_snapshot(snapshot.to_dict())
    assert effective["eligibility"]["coverage"]["truth"] == "partial"
    assert "TICK3" in effective["eligibility"]["coverage"]["reasons"][0]
    assert effective["csp_picks"]
    assert all(pick["eligibility"]["mode"] == "review_only" for pick in effective["csp_picks"])


def test_discovery_is_not_retried_when_every_symbol_fails(scan):
    engine, conn, db, portfolio, tickers = scan
    conn.quote_ctx.get_option_chain.side_effect = None
    conn.quote_ctx.get_option_chain.return_value = (-1, "request timeout")
    result = engine.get_top_recommendations()
    assert result["scan_coverage"] == {"scanned": 0, "total": 69, "complete": False}
    assert conn.quote_ctx.get_option_chain.call_count == 69
    assert result["blocked_reason_counts"]["broker_data_unavailable"] == 69


def test_missing_option_quote_is_requested_again(scan):
    engine, conn, db, portfolio, tickers = scan
    real = conn.quote_ctx.get_market_snapshot.side_effect
    dropped = []

    def snapshots(codes):
        ret, frame = real(codes)
        puts = [code for code in codes if code.endswith("PUT")]
        if puts and not dropped:
            dropped.append(puts[0])
            frame = frame[frame["code"] != puts[0]]
        return ret, frame

    conn.quote_ctx.get_market_snapshot.side_effect = snapshots
    result = engine.get_top_recommendations()
    assert dropped
    assert result["scan_coverage"] == {"scanned": 69, "total": 69, "complete": True}, result["blocked_reason_counts"]
