"""The Step-0 capability gate must never mistake missing broker data for a go."""

from unittest.mock import Mock

import pandas as pd
import pytest

from tools.probe_option_chain_cost import measure, probe


def _connection(contract_count=400):
    conn = Mock()
    conn._format_symbol.side_effect = lambda symbol: f"US.{symbol}"
    conn._option_chain_rate_limiter.get_stats.return_value = {"api_calls_count": 2}
    conn.quote_ctx.get_option_chain.return_value = (
        0,
        pd.DataFrame(
            [
                {
                    "code": f"US.TEST{index}",
                    "strike_time": "2026-10-16" if index % 2 else "2026-10-23",
                    "option_type": "CALL" if index % 2 else "PUT",
                }
                for index in range(contract_count)
            ]
        ),
    )
    conn.get_market_snapshot.return_value = (
        0,
        pd.DataFrame(
            [
                {
                    "code": f"US.TEST{index}",
                    "bid_price": 1.0,
                    "ask_price": 1.1,
                    "option_delta": -0.3,
                    "option_implied_volatility": 40.0,
                    "update_time": "2026-10-02 16:00:00",
                }
                for index in range(contract_count)
            ]
        ),
    )
    return conn


def test_ranged_discovery_and_exactly_400_quotes():
    conn = _connection()
    report = measure(conn, ["SOXL", "SOXS"], "2026-10-09", "2026-11-06")
    assert report["decision"] == "go"
    assert report["snapshot"]["usable_rows"] == 400
    assert conn._option_chain_rate_limiter.check_rate_limit.call_count == 2
    assert conn._acquire_option_chain_gate.call_count == conn._option_chain_gate.release.call_count == 2
    assert conn.quote_ctx.get_option_chain.call_args_list[0].kwargs == {
        "code": "US.SOXL",
        "start": "2026-10-09",
        "end": "2026-11-06",
        "option_type": "ALL",
    }
    conn.get_market_snapshot.assert_called_once()
    assert len(conn.get_market_snapshot.call_args.args[0]) == 400


@pytest.mark.parametrize(
    "field", ["bid_price", "ask_price", "option_delta", "option_implied_volatility", "update_time"]
)
def test_missing_required_quote_field_is_no_go(field):
    conn = _connection()
    conn.get_market_snapshot.return_value[1][field] = None
    assert measure(conn, ["SOXL", "SOXS"], "2026-10-09", "2026-11-06")["decision"] == "no-go"


def test_small_sample_is_unmeasured_not_go():
    conn = _connection(399)
    assert measure(conn, ["SOXL", "SOXS"], "2026-10-09", "2026-11-06")["decision"] == "blocked"
    conn.get_market_snapshot.assert_not_called()


def test_rate_limit_failure_adapts_and_releases_gate():
    conn = _connection()
    conn.quote_ctx.get_option_chain.side_effect = [
        (-1, "frequency limit"),
        conn.quote_ctx.get_option_chain.return_value,
    ]
    assert measure(conn, ["SOXL", "SOXS"], "2026-10-09", "2026-11-06")["decision"] == "no-go"
    conn._option_chain_rate_limiter.record_rate_limit.assert_called_once()
    assert conn._option_chain_gate.release.call_count == 2


def test_snapshot_failure_is_no_go():
    conn = _connection()
    conn.get_market_snapshot.return_value = (-1, "query failed")
    assert measure(conn, ["SOXL", "SOXS"], "2026-10-09", "2026-11-06")["decision"] == "no-go"


def test_unreachable_opend_blocks_without_sdk_connection(monkeypatch):
    tcp = Mock(side_effect=ConnectionRefusedError())
    monkeypatch.setattr("tools.probe_option_chain_cost.socket.create_connection", tcp)
    assert probe()["decision"] == "blocked"
