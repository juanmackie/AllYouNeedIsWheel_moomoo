"""Replay model contracts: P&L, unresolved handling, selection rules, walk-forward, verdict.

Synthetic data only. No broker, network, or options.db access.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from tools.replay_signals import (
    BASE_SLIPPAGE,
    SLIPPAGE_LEVELS,
    Trade,
    bootstrap_ci,
    build_cohorts,
    build_series,
    calibrate_fees,
    cohort_trades,
    entry_session,
    make_trade,
    pool_contracts,
    select_published,
    select_rules,
    settle_contract,
    to_contract,
    verdict,
    walk_forward,
)

NY = ZoneInfo("America/New_York")
FEES = {"option_fee_per_contract": 0.65, "stock_sale_fee": 1.0}
ENTRY = date(2026, 10, 23)
EXPIRY = date(2026, 11, 6)


def _raw(ticker="AAA", strike=50.0, bid=1.0, ask=1.1, expiration="20261106", **extra):
    raw = {
        "ticker": ticker,
        "option_type": "PUT",
        "strike": strike,
        "expiration": expiration,
        "bid": bid,
        "ask": ask,
        "dte": 14,
        "delta": -0.2,
        "event_tier": "event_safe",
        "held_position": False,
        "hard_blockers": [],
        "capital_velocity_per_day": 0.005,
        "wheel_decision": {"expected_value": None},
    }
    raw.update(extra)
    return raw


def _bars(*rows):
    """Rows of (iso_day, open, close, adj_close_or_None)."""
    return build_series([{"date": d, "open": o, "close": c, "adj_close": a} for d, o, c, a in rows])


def _otm_series():
    return _bars(("2026-10-23", 100, 100, None), ("2026-11-06", 100, 101, None))


def _itm_series():
    return _bars(("2026-10-23", 50, 50, None), ("2026-11-06", 46, 45, None))


def _trade(cohort, expiry, pnl, ticker="AAA"):
    return Trade(
        cohort=cohort,
        ticker=ticker,
        expiration=expiry,
        entry=expiry,
        strike=50.0,
        capital=5000.0,
        days=10,
        pnl={s: pnl for s in SLIPPAGE_LEVELS},
        assigned=False,
        stock_pnl=0.0,
        spy_pnl=0.0,
    )


def test_otm_expiry_keeps_credit_less_entry_fee():
    stock = _otm_series()
    contract = to_contract(_raw(strike=50.0, bid=1.0, ask=1.1))
    settlement = settle_contract(contract, stock, stock, ENTRY, EXPIRY)
    trade = make_trade(0, contract, settlement, FEES)
    assert trade.assigned is False
    assert trade.pnl[0.0] == pytest.approx(100 * 1.0 - 0.65)


def test_itm_expiry_charges_intrinsic_loss_and_stock_sale_fee():
    stock = _itm_series()
    contract = to_contract(_raw(strike=50.0, bid=1.0, ask=1.0))
    settlement = settle_contract(contract, stock, stock, ENTRY, EXPIRY)
    trade = make_trade(0, contract, settlement, FEES)
    assert trade.assigned is True
    assert trade.pnl[0.0] == pytest.approx(100 - 0.65 - 100 * 5 - 1.0)


def test_slippage_moves_entry_credit_toward_worse_fill():
    stock = _otm_series()
    contract = to_contract(_raw(strike=50.0, bid=1.00, ask=1.20))
    settlement = settle_contract(contract, stock, stock, ENTRY, EXPIRY)
    trade = make_trade(0, contract, settlement, FEES)
    assert trade.pnl[0.0] - trade.pnl[0.5] == pytest.approx(10.0)
    assert trade.pnl[BASE_SLIPPAGE] == pytest.approx(100 * 0.95 - 0.65)


def test_unresolved_contracts_are_excluded_and_counted_never_zeroed():
    spy = _bars(("2026-10-23", 400, 400, None), ("2026-11-06", 400, 401, None))
    ccc = _bars(("2026-10-23", 10, 10, 10.0), ("2026-11-06", 10, 10, 5.0))
    selections = {
        "R1_current": [
            to_contract(_raw("AAA")),
            to_contract(_raw("NOPX")),
            to_contract(_raw("CCC")),
            to_contract(_raw("AAA", expiration="20261218")),
        ]
    }
    prices = {"AAA": _otm_series(), "CCC": ccc, "SPY": spy}
    trades, unresolved = cohort_trades(0, ENTRY, selections, prices, FEES, EXPIRY)
    assert [t.ticker for t in trades["R1_current"]] == ["AAA"]
    assert dict(unresolved["R1_current"]) == {"no_price": 1, "corporate_action": 1, "not_expired": 1}


def test_same_session_snapshots_keep_only_the_last_published():
    trading = [date(2026, 10, 19), date(2026, 10, 20)]
    early = {
        "snapshot_id": "early",
        "published_at": datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc),
        "signals": [],
        "candidates": [_raw()],
    }
    late = {**early, "snapshot_id": "late", "published_at": datetime(2026, 10, 19, 13, 20, tzinfo=timezone.utc)}
    cohorts, _stats = build_cohorts([late, early], trading)
    assert len(cohorts) == 1
    assert cohorts[0].snapshot_id == "late"
    assert cohorts[0].entry == date(2026, 10, 19)


@pytest.mark.parametrize(
    ("decision", "source", "expected"),
    [
        (datetime(2026, 10, 19, 9, 0, tzinfo=NY), "published", date(2026, 10, 19)),
        (datetime(2026, 10, 19, 10, 0, tzinfo=NY), "published", date(2026, 10, 20)),
        (datetime(2026, 10, 22, 15, 52, 30, tzinfo=NY), "quote", date(2026, 10, 23)),
    ],
)
def test_entry_session_is_next_trading_date_after_the_decision(decision, source, expected):
    trading = [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 23)]
    assert entry_session(decision, source, trading) == expected


def test_pool_excludes_held_blocked_crossed_and_call_candidates():
    raws = [
        _raw("AAA"),
        _raw("HELD", held_position=True),
        _raw("BLK", hard_blockers=["stale quote"]),
        _raw("ZERO", bid=0.0),
        _raw("CROSS", bid=1.2, ask=1.0),
        _raw("CALL", option_type="CALL"),
    ]
    assert [c.ticker for c in pool_contracts(raws)] == ["AAA"]


def test_top_three_picks_are_distinct_tickers():
    raws = [
        _raw("AAA", strike=50.0, capital_velocity_per_day=0.009),
        _raw("AAA", strike=49.0, capital_velocity_per_day=0.008),
        _raw("BBB", capital_velocity_per_day=0.007),
        _raw("CCC", capital_velocity_per_day=0.006),
        _raw("DDD", capital_velocity_per_day=0.005),
    ]
    rules = select_rules(pool_contracts(raws), leveraged=())
    assert [c.ticker for c in rules["R1_current"]] == ["AAA", "BBB", "CCC"]
    assert rules["R1_current"][0].strike == 50.0


def test_tail_gate_removes_leveraged_etf_from_r1_but_not_r2():
    raws = [_raw("SOXL", capital_velocity_per_day=0.02), _raw("AAA", capital_velocity_per_day=0.01)]
    rules = select_rules(pool_contracts(raws), leveraged=("SOXL",))
    assert [c.ticker for c in rules["R1_current"]] == ["AAA"]
    assert [c.ticker for c in rules["R2_no_tail_gate"]] == ["SOXL", "AAA"]
    assert "SOXL" in [c.ticker for c in rules["POOL_ALL"]]
    assert "SOXL" not in [c.ticker for c in rules["POOL"]]


def test_low_delta_rule_skips_unknown_delta_and_high_delta():
    raws = [
        _raw("AAA", delta=None, capital_velocity_per_day=0.03),
        _raw("BBB", delta=-0.35, capital_velocity_per_day=0.02),
        _raw("CCC", delta=-0.18, capital_velocity_per_day=0.01),
    ]
    rules = select_rules(pool_contracts(raws), leveraged=())
    assert [c.ticker for c in rules["R3_low_delta"]] == ["CCC"]


def test_model_ev_ranks_expected_value_per_dollar_of_capital():
    raws = [
        _raw("BIG", strike=100.0, dte=10, capital_velocity_per_day=0.02, wheel_decision={"expected_value": 40.0}),
        _raw("SMALL", strike=20.0, dte=10, capital_velocity_per_day=0.01, wheel_decision={"expected_value": 10.0}),
        _raw("NONE", strike=30.0, dte=10, capital_velocity_per_day=0.03),
    ]
    rules = select_rules(pool_contracts(raws), leveraged=())
    assert [c.ticker for c in rules["R4_model_ev"]] == ["SMALL", "BIG"]
    assert [c.ticker for c in rules["R1_current"]] == ["NONE", "BIG", "SMALL"]


def test_published_signals_use_pool_quote_and_drop_unpriced_entries():
    pool = pool_contracts([_raw("AAA", bid=1.0, ask=1.1)])
    signals = [
        {
            "signal_type": "csp",
            "ticker": "AAA",
            "option_type": "PUT",
            "strike": 50.0,
            "expiration": "20261106",
            "bid": 0.9,
            "ask": 1.0,
        },
        {
            "signal_type": "csp",
            "ticker": "NEW",
            "option_type": "PUT",
            "strike": 20.0,
            "expiration": "20261106",
            "bid": 0.5,
            "ask": 0.6,
        },
        {
            "signal_type": "csp",
            "ticker": "NOQ",
            "option_type": "PUT",
            "strike": 20.0,
            "expiration": "20261106",
            "bid": None,
            "ask": None,
        },
        {
            "signal_type": "covered_call",
            "ticker": "AAA",
            "option_type": "CALL",
            "strike": 55.0,
            "expiration": "20261106",
            "bid": 0.4,
            "ask": 0.5,
        },
    ]
    picks, dropped = select_published(signals, pool)
    assert [(c.ticker, c.bid) for c in picks] == [("AAA", 1.0), ("NEW", 0.5)]
    assert dropped == 1


def test_fee_calibration_ignores_null_fees_and_zero_price_fills():
    rows = [
        {"security_type": "OPT", "side": "BUY", "qty": 2, "price": 1.0, "fees": 1.3},
        {"security_type": "OPT", "side": "SELL", "qty": 1, "price": 0.0, "fees": 9.9},
        {"security_type": "OPT", "side": "SELL", "qty": 1, "price": 1.0, "fees": None},
        {"security_type": "STK", "side": "SELL", "qty": 100, "price": 50.0, "fees": 2.0},
        {"security_type": "STK", "side": "SELL", "qty": 100, "price": 50.0, "fees": None},
    ]
    fees = calibrate_fees(rows)
    assert fees["option_fee_per_contract"] == pytest.approx(0.65)
    assert fees["stock_sale_fee"] == pytest.approx(2.0)
    assert fees["option_fill_samples"] == 1
    assert fees["stock_sell_samples"] == 1
    assert fees["calibrated"] is True


def test_fee_fallback_is_flagged_uncalibrated_without_data():
    fees = calibrate_fees([])
    assert fees["option_fee_per_contract"] == 0.65
    assert fees["stock_sale_fee"] == 1.0
    assert fees["calibrated"] is False


def test_walk_forward_purges_training_trades_expiring_on_or_after_test_entry():
    entries = [date(2026, 10, 5), date(2026, 10, 12), date(2026, 10, 19)]
    trades = {
        # Look-ahead bait: a large win for R1 that expires after the test entry.
        "R1_current": [
            _trade(0, date(2026, 10, 9), -50.0),
            _trade(1, date(2026, 10, 26), 5000.0),
            _trade(2, date(2026, 10, 30), 0.0),
        ],
        "R2_no_tail_gate": [
            _trade(0, date(2026, 10, 9), 100.0),
            _trade(1, date(2026, 10, 16), 100.0),
            _trade(2, date(2026, 10, 30), 100.0),
        ],
        "R3_low_delta": [],
        "R4_model_ev": [],
    }
    result = walk_forward(entries, trades)
    fold = result["folds"][2]
    assert fold["status"] == "ok"
    assert fold["chosen_rule"] == "R2_no_tail_gate"
    assert fold["training_trades"]["R1_current"] == 1


def test_walk_forward_skips_fold_without_two_training_cohorts():
    entries = [date(2026, 10, 5), date(2026, 10, 12)]
    trades = {
        "R1_current": [_trade(0, date(2026, 10, 9), 100.0)],
        "R2_no_tail_gate": [],
        "R3_low_delta": [],
        "R4_model_ev": [],
    }
    result = walk_forward(entries, trades)
    assert result["folds"][1]["status"] == "skipped_insufficient_training"


def test_verdict_is_inconclusive_below_eight_oos_cohorts_even_when_positive():
    result = verdict(oos_cohorts=5, ci=[0.001, 0.002], diff_pool=0.001, diff_spy=0.001)
    assert result["verdict"] == "inconclusive"
    assert any("8" in reason for reason in result["reasons"])


def test_edge_requires_every_predeclared_condition_and_negative_needs_upper_bound_below_zero():
    assert verdict(8, [0.001, 0.002], 0.001, 0.001)["verdict"] == "edge_supported"
    assert verdict(8, [0.001, 0.002], 0.001, -0.001)["verdict"] == "inconclusive"
    assert verdict(8, [-0.002, -0.001], -0.001, -0.001)["verdict"] == "negative"


def test_bootstrap_interval_is_deterministic_and_bounded_by_cohort_rates():
    pairs = [(10.0, 100.0), (-5.0, 100.0), (8.0, 120.0), (3.0, 90.0), (6.0, 80.0)]
    rates = [excess / capday for excess, capday in pairs]
    first = bootstrap_ci(pairs)
    assert first == bootstrap_ci(pairs)
    assert min(rates) <= first[0] <= first[1] <= max(rates)
    assert bootstrap_ci([]) is None
