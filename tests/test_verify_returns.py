"""Pure checks for tools/verify_returns.py: broker-side recomputation of returns.

Synthetic rows only. No OpenD, broker, or network. One test per behavior.
"""

from datetime import date

import pytest

from tools.verify_returns import (
    capital_flow_amount,
    classify_code,
    compare_fills,
    compare_order_fees,
    compare_pnl,
    deal_cash_flow,
    history_windows,
    independent_pnl,
    modified_dietz,
    same_scope_totals,
)


def _deal(deal_id, order_id, side, qty, price, kind="OPT", underlying="SOXL"):
    return {
        "deal_id": deal_id,
        "order_id": order_id,
        "side": side,
        "qty": qty,
        "price": price,
        "kind": kind,
        "underlying": underlying,
    }


def _fill(fill_id, order_id, side, qty, price, fees, security_type="OPT", ticker="SOXL"):
    return {
        "fill_id": fill_id,
        "order_id": order_id,
        "side": side,
        "qty": qty,
        "price": price,
        "fees": fees,
        "security_type": security_type,
        "ticker": ticker,
    }


def test_missing_deal_on_each_side_is_reported():
    broker = [_deal("D1", "O1", "SELL", 1.0, 2.0), _deal("D2", "O2", "BUY", 100.0, 10.0, "STK", "AAPL")]
    db = [_fill("D1", "O1", "SELL", 1.0, 2.0, 0.65), _fill("D3", "O3", "BUY", 1.0, 0.5, 0.65)]

    result = compare_fills(db, broker)

    assert result["missing_in_db"] == ["D2"]
    assert result["missing_in_broker"] == ["D3"]
    assert result["field_mismatches"] == []
    assert result["matched"] == 1


def test_price_difference_above_tolerance_is_reported_and_rounding_noise_is_not():
    broker = [_deal("D1", "O1", "SELL", 1.0, 2.0), _deal("D2", "O2", "SELL", 1.0, 2.0)]
    db = [_fill("D1", "O1", "SELL", 1.0, 2.0 + 1e-9, 0.65), _fill("D2", "O2", "SELL", 1.0, 2.0001, 0.65)]

    result = compare_fills(db, broker)

    assert result["field_mismatches"] == [{"deal_id": "D2", "field": "price", "db": 2.0001, "broker": 2.0}]
    assert result["matched"] == 1


def test_null_db_fee_is_unknown_not_zero():
    db = [_fill("D1", "O1", "SELL", 1.0, 2.0, None)]

    result = compare_order_fees(db, {"O1": 1.30})

    assert result["mismatches"] == []
    assert result["matched"] == []
    assert result["unknown_db_fee_orders"] == ["O1"]
    assert result["null_db_fee_fills"] == 1


def test_db_fee_sum_off_by_more_than_one_cent_is_a_mismatch():
    db = [_fill("D1", "O1", "SELL", 1.0, 2.0, 0.65), _fill("D2", "O1", "SELL", 1.0, 2.0, 0.65)]

    result = compare_order_fees(db, {"O1": 1.30 + 0.02})

    assert [m["order_id"] for m in result["mismatches"]] == ["O1"]
    assert result["matched"] == []


def test_option_cash_uses_contract_multiplier_and_stock_does_not():
    assert deal_cash_flow("SELL", 2, 1.5, "OPT") == pytest.approx(300.0)
    assert deal_cash_flow("SELL", 100, 10.0, "STK") == pytest.approx(1000.0)
    assert deal_cash_flow("BUY", 1, 0.5, "OPT") == pytest.approx(-50.0)


def test_sell_then_buy_to_close_nets_to_the_premium_kept_less_fees():
    deals = [_deal("D1", "O1", "SELL", 1.0, 2.00), _deal("D2", "O2", "BUY", 1.0, 0.50)]

    result = independent_pnl(deals, fee_by_order={"O1": 0.65, "O2": 0.65}, positions=[])
    row = result["by_underlying"]["SOXL"]

    assert row["option_cash"] == pytest.approx(150.0)
    assert row["fees"] == pytest.approx(1.30)
    assert row["net"] == pytest.approx(148.70)


def test_modified_dietz_with_mid_window_deposit_is_below_plain_nav_change():
    start, end = date(2026, 10, 1), date(2026, 10, 11)

    result = modified_dietz(100.0, 120.0, [(date(2026, 10, 6), 10.0)], start, end)

    # Deposit weight = (end - day) / days = 5 / 10. Denominator = 100 + 5.
    assert result["return"] == pytest.approx(10.0 / 105.0)
    assert result["plain_change"] == pytest.approx(0.20)
    assert result["return"] < result["plain_change"]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("US.SOXL261030P126000", ("OPT", "SOXL")),
        ("US.AAPL", ("STK", "AAPL")),
    ],
)
def test_broker_code_maps_to_security_type_and_underlying(code, expected):
    assert classify_code(code) == expected


def test_reconciled_app_row_off_by_more_than_five_cents_is_a_mismatch_with_components():
    deals = [_deal("D1", "O1", "SELL", 1.0, 2.00), _deal("D2", "O2", "BUY", 1.0, 0.50)]
    indep = independent_pnl(deals, fee_by_order={"O1": 0.65, "O2": 0.65}, positions=[])
    app_rows = [
        {
            "ticker": "SOXL",
            "status": "reconciled",
            "net_pnl": 148.80,
            "option_pnl": 150.10,
            "share_pnl": 0.0,
            "fees": 1.30,
            "fees_unknown": 0,
            "notes": [],
        }
    ]

    result = compare_pnl(app_rows, indep["by_underlying"])

    assert len(result["mismatches"]) == 1
    mismatch = result["mismatches"][0]
    assert mismatch["ticker"] == "SOXL"
    assert mismatch["diff"] == pytest.approx(0.10)
    assert mismatch["app"]["option_pnl"] == pytest.approx(150.10)
    assert mismatch["independent"]["fees"] == pytest.approx(1.30)


def test_unreconciled_app_row_is_listed_and_is_not_a_mismatch():
    deals = [_deal("D1", "O1", "SELL", 1.0, 2.00)]
    indep = independent_pnl(deals, fee_by_order={"O1": 0.65}, positions=[])
    app_rows = [
        {
            "ticker": "SOXL",
            "status": "unreconciled",
            "net_pnl": None,
            "option_pnl": None,
            "share_pnl": None,
            "fees": 0.0,
            "fees_unknown": 0,
            "notes": ["fills show 1 contract, broker holds 0"],
        }
    ]

    result = compare_pnl(app_rows, indep["by_underlying"])

    assert result["mismatches"] == []
    assert result["unreconciled_app"] == ["SOXL"]


def test_history_windows_span_at_most_ninety_days_and_overlap_by_one_day():
    windows = history_windows(date(2025, 1, 1), date(2025, 12, 31))

    assert windows[0][0] == date(2025, 1, 1)
    assert windows[-1][1] == date(2025, 12, 31)
    for window_start, window_end in windows:
        assert (window_end - window_start).days <= 89
    for (_, previous_end), (next_start, _) in zip(windows, windows[1:]):
        assert next_start == previous_end


@pytest.mark.parametrize(
    ("direction", "amount", "expected"),
    [("IN", 500.0, 500.0), ("OUT", 500.0, -500.0), ("OUT", -500.0, -500.0), ("", 500.0, None)],
)
def test_capital_flow_sign_comes_from_direction_not_amount_sign(direction, amount, expected):
    assert capital_flow_amount(direction, amount) == expected


def test_same_scope_totals_exclude_partial_history_and_unreconciled_rows():
    app_rows = [
        {"ticker": "AAA", "status": "reconciled", "net_pnl": -100.0},
        {"ticker": "OLD", "status": "partial_history", "net_pnl": 50.0},
        {"ticker": "GAP", "status": "unreconciled", "net_pnl": None},
    ]
    indep_rows = {"AAA": {"net": -98.0}, "OLD": {"net": 900.0}, "GAP": {"net": 10.0}}
    totals = same_scope_totals(app_rows, indep_rows)
    assert totals["app_same_scope_net_sum"] == -100.0
    assert totals["independent_same_scope_net_sum"] == -98.0
    assert totals["same_scope_diff"] == -2.0
    assert totals["excluded_partial_history"] == ["OLD"]
    assert totals["excluded_unreconciled"] == ["GAP"]
