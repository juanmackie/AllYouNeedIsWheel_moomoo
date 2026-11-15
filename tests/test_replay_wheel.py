"""Synthetic captured-signal wheel paths; no broker, credentials, or network."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from tools.replay_signals import build_series
from tools.replay_wheel import replay_wheel

NY = ZoneInfo("America/New_York")
FEES = {"option_fee_per_contract": 0.65, "stock_sale_fee": 1.0}


def signal(kind="PUT", strike=50, expiration="2026-10-09", **extra):
    return {
        "ticker": "AAA",
        "option_type": kind,
        "strike": strike,
        "expiration": expiration,
        "bid": 1.0,
        "ask": 1.2,
        "price_source": "moomoo",
        **extra,
    }


def snapshot(day, signals, account="one", hour=8):
    for raw in signals:
        raw.setdefault("quote_update_time", f"{day} 07:00:00")
    return {
        "snapshot_id": f"{day}-{hour}-{account}",
        "env": "REAL",
        "account_id": account,
        "published_at": datetime.fromisoformat(f"{day}T{hour:02}:00:00").replace(tzinfo=NY),
        "signals": signals,
    }


def prices(last="2026-10-23", close=55):
    rows = [
        ("2026-10-05", 51),
        ("2026-10-09", 45),
        ("2026-10-12", 46),
        ("2026-10-16", 49),
        ("2026-10-19", 50),
        (last, close),
    ]

    def series(spy=False):
        return build_series([{"date": d, "open": 100 if spy else p, "close": 100 if spy else p} for d, p in rows])

    return {"AAA": series(), "SPY": series(True)}


def run(snaps, data=None, end="2026-10-23"):
    return replay_wheel(snaps, data or prices(), FEES, date.fromisoformat(end))


def cycle():
    return [
        snapshot("2026-10-05", [signal()]),
        snapshot("2026-10-12", [signal("CALL", 52, "2026-10-16")]),
        snapshot("2026-10-19", [signal("CALL", 53, "2026-10-23")]),
    ]


def test_assignment_repeated_calls_and_callaway_keep_share_cash_and_fees_once():
    result = run(cycle())
    path = result["paths"][0]
    assert path["status"] == "called_away"
    assert [leg["option_type"] for leg in path["legs"]] == ["PUT", "CALL", "CALL"]
    # Three credits, three option fees, share purchase and sale fees, $300 share gain.
    assert path["pnl_by_slippage"]["0.0"] == pytest.approx(300 - 1.95 - 2 + 300)
    assert path["pnl_by_slippage"]["0.5"] == pytest.approx(270 - 1.95 - 2 + 300)
    assert path["capital_days"] == 5000 * 18
    assert path["spy_pnl"] == -1
    assert result["status_counts"] == {"called_away": 1}
    assert result["simulated"] is True


def test_unassigned_put_ends_without_using_later_calls():
    snaps = cycle()
    snaps[0]["signals"][0]["strike"] = 40
    path = run(snaps)["paths"][0]
    assert path["status"] == "put_expired"
    assert len(path["legs"]) == 1
    assert path["pnl_by_slippage"]["0.0"] == pytest.approx(99.35)


@pytest.mark.parametrize("change", ["other_account", "before_assignment", "blocked", "external", "below_basis"])
def test_unusable_calls_leave_shares_open_with_a_mark_instead_of_fabricated_premium(change):
    call = snapshot("2026-10-12", [signal("CALL", 52, "2026-10-23")])
    if change == "other_account":
        call["account_id"] = "two"
    elif change == "before_assignment":
        call["published_at"] = datetime(2026, 10, 8, 8, tzinfo=NY)
    elif change == "blocked":
        call["signals"][0]["hard_blockers"] = ["earnings"]
    elif change == "external":
        call["signals"][0]["price_source"] = "yfinance"
    else:
        call["signals"][0]["strike"] = 49
    path = run([snapshot("2026-10-05", [signal()]), call])["paths"][0]
    assert path["status"] == "shares_open"
    assert len(path["legs"]) == 1
    # Open share value is included; no hypothetical liquidation fee.
    assert path["pnl_by_slippage"]["0.0"] == pytest.approx(100 - 0.65 - 1 + 500)
    assert path["complete"] is False


def test_unexpired_call_has_unknown_pnl_without_a_captured_option_mark():
    snaps = cycle()[:2]
    snaps[1]["signals"][0]["expiration"] = "2026-11-06"
    path = run(snaps)["paths"][0]
    assert path["status"] == "call_open"
    assert path["pnl_by_slippage"] is None
    assert path["reason"] == "option_mark_unavailable"


@pytest.mark.parametrize("change,reason", [("missing", "missing_candle"), ("split", "corporate_action")])
def test_missing_settlement_or_corporate_action_never_becomes_a_zero_return(change, reason):
    data = prices()
    if change == "missing":
        data["AAA"].bars.pop(date(2026, 10, 9))
    else:
        data["AAA"] = build_series(
            [
                {"date": "2026-10-05", "open": 51, "close": 51, "adj_close": 51},
                {"date": "2026-10-09", "open": 45, "close": 45, "adj_close": 22.5},
            ]
        )
    path = run([snapshot("2026-10-05", [signal()])], data)["paths"][0]
    assert path["status"] == "unresolved"
    assert path["reason"] == reason
    assert path["pnl_by_slippage"] is None


def test_snapshot_published_after_open_cannot_trade_that_mornings_open():
    snap = snapshot("2026-10-05", [signal()], hour=10)
    path = run([snap])["paths"][0]
    assert path["entry"] == "2026-10-09"
    assert path["reason"] == "expires_on_or_before_entry"


def test_latest_snapshot_before_open_wins_and_duplicate_contract_is_counted_once():
    early = snapshot("2026-10-05", [signal(bid=9)], hour=7)
    late = snapshot("2026-10-05", [signal(), signal()], hour=8)
    result = run([late, early])
    assert len(result["paths"]) == 1
    assert result["paths"][0]["legs"][0]["bid"] == 1


def test_future_and_stale_quotes_are_rejected_and_reported():
    snap = snapshot(
        "2026-10-05",
        [signal(quote_update_time="2026-10-06 16:00:00"), signal(strike=49, quote_update_time="2026-09-01 16:00:00")],
    )
    result = run([snap])
    assert result["paths"] == []
    assert result["skipped_signals"] == {"future_quote": 1, "stale_quote": 1}


def test_empty_evidence_is_reported_without_claiming_an_edge():
    result = run([])
    assert result["paths"] == []
    assert result["status_counts"] == {}
    assert "verdict" not in result


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("quote_update_time", "", "invalid_quote_time"),
        ("bid", float("nan"), "invalid_quote"),
        ("chain_source", "yfinance", "non_broker_quote"),
    ],
)
def test_incomplete_or_invalid_quote_evidence_is_counted(field, value, reason):
    result = run([snapshot("2026-10-05", [signal(**{field: value})])])
    assert result["paths"] == []
    assert result["skipped_signals"] == {reason: 1}


def test_unscoped_history_is_not_joined_across_accounts():
    result = run([snapshot("2026-10-05", [signal()], account="")])
    assert result["paths"] == []
    assert result["skipped_signals"] == {"unscoped_snapshot": 1}


def test_missing_spy_expiry_bar_cannot_settle_a_put_on_the_wrong_session():
    data = prices()
    data["SPY"] = build_series(
        [
            {"date": "2026-10-05", "open": 100, "close": 100},
            {"date": "2026-10-08", "open": 100, "close": 100},
            {"date": "2026-10-12", "open": 100, "close": 100},
        ]
    )
    data["AAA"] = build_series(
        [
            {"date": "2026-10-05", "open": 51, "close": 51},
            {"date": "2026-10-08", "open": 51, "close": 51},
            {"date": "2026-10-09", "open": 45, "close": 45},
            {"date": "2026-10-12", "open": 45, "close": 45},
        ]
    )
    path = run([snapshot("2026-10-05", [signal()])], data)["paths"][0]
    assert path["status"] == "unresolved"
    assert path["reason"] == "missing_candle"
    assert path["pnl_by_slippage"] is None


def test_held_put_is_not_opened_as_a_new_cash_secured_path():
    result = run([snapshot("2026-10-05", [signal(held_position=True)])])
    assert result["paths"] == []
    assert result["skipped_signals"] == {"held_position": 1}


def test_weekend_expiry_uses_the_common_friday_session_and_compact_expiration():
    snaps = cycle()
    snaps[0]["signals"][0]["expiration"] = "20261010"
    path = run(snaps)["paths"][0]
    assert path["status"] == "called_away"
    assert path["pnl_by_slippage"]["0.0"] == pytest.approx(596.05)


@pytest.mark.parametrize("unplaced_symbol", [False, True])
def test_cli_uses_readonly_database_and_cached_prices_and_writes_labeled_reports(
    tmp_path, monkeypatch, unplaced_symbol
):
    import json
    import sqlite3

    from tools import replay_signals

    db = tmp_path / "synthetic.db"
    out = tmp_path / "replay"
    out.mkdir()
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE run_metadata (run_id, published_at, status, snapshot_json, env, account_id)")
        conn.execute(
            "CREATE TABLE option_fills (security_type, side, qty, price, fees, ticker, option_type, "
            "strike, expiration, captured_at, env, account_id)"
        )
        # Another account's expensive fills must not calibrate this path's fees.
        conn.execute(
            "INSERT INTO option_fills VALUES ('OPT', 'SELL', 1, 1, 999, 'AAA', 'PUT', "
            "50, '2026-10-09', '2026-10-05', 'REAL', 'other')"
        )
        for snap in cycle():
            conn.execute(
                "INSERT INTO run_metadata VALUES (?, ?, ?, ?, ?, ?)",
                (
                    snap["snapshot_id"],
                    snap["published_at"].isoformat(),
                    "planning",
                    json.dumps({"signals": snap["signals"], "csp_picks": []}),
                    "REAL",
                    "one",
                ),
            )
        if unplaced_symbol:
            # This snapshot has no legacy CSP entry session (its candidate has
            # a future quote), but has a valid publication-time wheel signal.
            snap = snapshot("2026-10-12", [signal(ticker="BBB", expiration="2026-10-16")], account="two")
            conn.execute(
                "INSERT INTO run_metadata VALUES (?, ?, ?, ?, ?, ?)",
                (
                    snap["snapshot_id"],
                    snap["published_at"].isoformat(),
                    "planning",
                    json.dumps(
                        {
                            "signals": snap["signals"],
                            "csp_picks": [signal(ticker="BBB", quote_update_time="2030-01-01 16:00:00")],
                        }
                    ),
                    "REAL",
                    "two",
                ),
            )
    cache = {
        symbol: {
            "start": "2026-01-01",
            "end": "2026-12-31",
            "rows": [{"date": d.isoformat(), "open": bar.open, "close": bar.close} for d, bar in series.bars.items()],
        }
        for symbol, series in prices().items()
    }
    if unplaced_symbol:
        cache["BBB"] = cache["AAA"]
    (out / "prices.json").write_text(json.dumps(cache), encoding="utf-8")
    monkeypatch.setattr(replay_signals, "Config", lambda: {})
    monkeypatch.setattr(replay_signals, "_connect", lambda: pytest.fail("offline replay connected to broker"))
    before = db.read_bytes()
    assert replay_signals.main(["--db", str(db), "--out-dir", str(out), "--no-fetch"]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    expected = {"called_away": 1, "shares_open": 1} if unplaced_symbol else {"called_away": 1}
    assert report["wheel_lifecycles"]["status_counts"] == expected
    assert report["wheel_lifecycles"]["paths"][0]["pnl_by_slippage"]["0.0"] == pytest.approx(596.05)
    assert report["wheel_lifecycles"]["paths"][0]["fees"]["calibrated"] is False
    assert report["verdict"]["verdict"] == "inconclusive"
    markdown = (out / "report.md").read_text(encoding="utf-8")
    assert "Wheel lifecycles (SIMULATED; measurement only)" in markdown
    assert "called_away" in markdown
    assert "not actual fills or a portfolio backtest" in markdown
    assert db.read_bytes() == before
