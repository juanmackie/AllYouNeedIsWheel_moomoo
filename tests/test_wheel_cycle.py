from datetime import date

from core.wheel_cycle import adjusted_basis, classify_fills, fold_cycles, gate_basis


def _opt_fill(fill_id, side, option_type="PUT", price=0, timestamp="2026-09-30T15:59:59-04:00", qty=1):
    return {
        "fill_id": fill_id,
        "ticker": "SYN",
        "security_type": "OPT",
        "contract_key": "SYN 20261016 P10.0",
        "option_type": option_type,
        "strike": 10.0,
        "expiration": "20261016",
        "side": side,
        "qty": qty,
        "price": price,
        "fees": 0.55,
        "captured_at": timestamp,
    }


def _stock_fill(fill_id, side, qty=100, price=10.0, timestamp="2026-09-30T16:00:01-04:00"):
    return {
        "fill_id": fill_id,
        "ticker": "SYN",
        "security_type": "STK",
        "side": side,
        "qty": qty,
        "price": price,
        "fees": 0.0,
        "captured_at": timestamp,
    }


def test_assignment_zero_buy_pairs_with_same_underlying_stock_buy_within_five_seconds():
    events = classify_fills(
        [
            _opt_fill("opt-assign", "BUY"),
            _stock_fill("stock-assign", "BUY", timestamp="2026-09-30T16:00:02-04:00"),
        ]
    )

    assert len(events) == 1
    assert events[0]["event_type"] == "assigned_put"
    assert events[0]["shares"] == 100
    assert events[0]["gross_basis_per_share"] == 10.0
    assert events[0]["share_fill_price"] == 10.0
    assert events[0]["share_fill_fees"] == 0.0


def test_zero_price_option_buy_without_close_stock_pair_is_unclassified():
    events = classify_fills([_opt_fill("opt-unmatched", "BUY")])

    assert len(events) == 1
    assert events[0]["event_type"] == "unclassified_zero_price_option_fill"


def test_called_away_mirror_is_fixture_classified_as_provisional():
    events = classify_fills(
        [
            _opt_fill("call-assign", "BUY", option_type="CALL"),
            _stock_fill("stock-called", "SELL", timestamp="2026-09-30T16:00:03-04:00"),
        ]
    )

    assert events[0]["event_type"] == "called_away"
    assert events[0]["provisional"] is True
    assert events[0]["share_fill_price"] == 10.0


def test_cycle_combines_net_option_credits_and_proven_share_pnl_only_after_called_away():
    events = [
        {
            "event_type": "open_put",
            "ticker": "SYN",
            "timestamp": "2026-01-01T10:00:00-05:00",
            "contracts": 1,
            "premium_net": 105.0,
            "fees_known": True,
        },
        {
            "event_type": "assigned_put",
            "ticker": "SYN",
            "timestamp": "2026-01-20T16:00:00-05:00",
            "contracts": 1,
            "shares": 100,
            "gross_basis_per_share": 10.0,
            "share_fill_price": 10.1,
            "share_fill_fees": 0.5,
            "option_exercise_fees": 0.1,
            "fees_known": True,
        },
        {
            "event_type": "open_call",
            "ticker": "SYN",
            "timestamp": "2026-01-21T10:00:00-05:00",
            "contracts": 1,
            "premium_net": 55.0,
            "fees_known": True,
        },
        {
            "event_type": "called_away",
            "ticker": "SYN",
            "timestamp": "2026-02-20T16:00:00-05:00",
            "contracts": 1,
            "shares": 100,
            "share_fill_price": 11.5,
            "share_fill_fees": 0.5,
            "option_exercise_fees": 0.1,
            "fees_known": True,
            "provisional": False,
        },
    ]

    cycle = fold_cycles(events)[0]

    assert cycle["state"] == "closed"
    assert cycle["cycle_outcome"] == {
        "status": "measured",
        "net_pnl": 298.8,
        "pnl_scope": "wheel_cycle",
        "option_leg_net_pnl": 159.8,
        "share_leg_net_pnl": 139.0,
    }


def test_open_or_provisional_cycles_do_not_report_combined_pnl():
    open_cycle = fold_cycles(
        [
            {
                "event_type": "open_put",
                "ticker": "SYN",
                "timestamp": "2026-01-01T10:00:00-05:00",
                "contracts": 1,
                "premium_net": 105.0,
                "fees_known": True,
            },
            {
                "event_type": "assigned_put",
                "ticker": "SYN",
                "timestamp": "2026-01-20T16:00:00-05:00",
                "contracts": 1,
                "shares": 100,
                "gross_basis_per_share": 10.0,
                "share_fill_price": 10.0,
                "share_fill_fees": 0.0,
                "option_exercise_fees": 0.0,
                "fees_known": True,
            },
        ]
    )[0]
    provisional_cycle = fold_cycles(
        [
            {
                "event_type": "open_put",
                "ticker": "SYN",
                "timestamp": "2026-01-01T10:00:00-05:00",
                "contracts": 1,
                "premium_net": 105.0,
                "fees_known": True,
            },
            {
                "event_type": "assigned_put",
                "ticker": "SYN",
                "timestamp": "2026-01-20T16:00:00-05:00",
                "contracts": 1,
                "shares": 100,
                "gross_basis_per_share": 10.0,
                "share_fill_price": 10.0,
                "share_fill_fees": 0.0,
                "option_exercise_fees": 0.0,
                "fees_known": True,
            },
            {
                "event_type": "open_call",
                "ticker": "SYN",
                "timestamp": "2026-01-21T10:00:00-05:00",
                "contracts": 1,
                "premium_net": 55.0,
                "fees_known": True,
            },
            {
                "event_type": "called_away",
                "ticker": "SYN",
                "timestamp": "2026-02-20T16:00:00-05:00",
                "contracts": 1,
                "shares": 100,
                "share_fill_price": 11.5,
                "share_fill_fees": 0.0,
                "option_exercise_fees": 0.0,
                "fees_known": True,
                "provisional": True,
            },
        ]
    )[0]
    review_cycle = fold_cycles(
        [
            {
                "event_type": "open_call",
                "ticker": "SYN",
                "timestamp": "2026-01-01T10:00:00-05:00",
                "contracts": 1,
                "premium_net": 55.0,
                "fees_known": True,
            },
            {
                "event_type": "close_option",
                "ticker": "SYN",
                "timestamp": "2026-01-02T10:00:00-05:00",
                "contracts": 1,
                "option_type": "CALL",
                "premium_net": -20.0,
                "fees_known": True,
            },
        ]
    )[0]
    assert open_cycle["cycle_outcome"]["status"] == "open"
    assert open_cycle["cycle_outcome"]["net_pnl"] is None
    assert provisional_cycle["cycle_outcome"]["status"] == "unknown"
    assert provisional_cycle["cycle_outcome"]["reason"] == "called_away_pair_provisional"
    assert review_cycle["cycle_outcome"]["status"] == "unknown"


def test_buyback_option_pnl_includes_debit_and_fee():
    close = _opt_fill("close", "BUY", price=0.25)
    event = classify_fills([close])[0]

    assert event["event_type"] == "close_option"
    assert event["premium_net"] == -25.55


def test_buying_back_covered_call_keeps_assigned_share_cycle_open():
    events = [
        {
            "event_type": "open_put",
            "ticker": "SYN",
            "timestamp": "2026-01-01T10:00:00-05:00",
            "contracts": 1,
            "premium_net": 105.0,
            "fees_known": True,
        },
        {
            "event_type": "assigned_put",
            "ticker": "SYN",
            "timestamp": "2026-01-20T16:00:00-05:00",
            "contracts": 1,
            "shares": 100,
            "gross_basis_per_share": 10.0,
            "fees_known": True,
        },
        {
            "event_type": "open_call",
            "ticker": "SYN",
            "timestamp": "2026-01-21T10:00:00-05:00",
            "contracts": 1,
            "premium_net": 55.0,
            "fees_known": True,
        },
        {
            "event_type": "close_option",
            "ticker": "SYN",
            "timestamp": "2026-01-25T10:00:00-05:00",
            "premium_net": -25.55,
            "fees_known": True,
        },
    ]

    cycle = fold_cycles(events)[0]

    assert cycle["state"] == "assigned_uncovered"
    assert cycle["closed_at"] is None
    assert cycle["cycle_outcome"]["status"] == "open"
    assert cycle["cycle_outcome"]["net_pnl"] is None


def test_expiry_absent_from_current_snapshot_is_unverified_until_complete_fills():
    events = classify_fills(
        [],
        previous_short_contracts=[{"ticker": "SYN", "option_type": "PUT", "expiration": "20260925"}],
        current_short_contracts=[],
        today=date(2026, 9, 30),
    )

    assert events[0]["event_type"] == "expired_unverified"


def test_folded_basis_excludes_open_call_credit_and_gate_falls_back_safely():
    events = [
        {
            "event_type": "open_put",
            "ticker": "SYN",
            "timestamp": "2026-09-01T10:00:00-04:00",
            "shares": 0,
            "gross_basis_per_share": None,
            "premium_net": 90.0,
            "fees_known": True,
        },
        {
            "event_type": "assigned_put",
            "ticker": "SYN",
            "timestamp": "2026-09-30T16:00:00-04:00",
            "shares": 100,
            "gross_basis_per_share": 10.0,
            "fees_known": True,
        },
        {
            "event_type": "open_call",
            "ticker": "SYN",
            "timestamp": "2026-10-01T10:00:00-04:00",
            "premium_net": 40.0,
            "fees_known": True,
        },
    ]
    cycles = fold_cycles(events)
    cycles = fold_cycles(
        events,
        evidence={
            "window_start": "2026-09-01T00:00:00-04:00",
            "ledger_shares": 100,
            "broker_shares": 100,
            "no_unclassified_zero_price_fills": True,
            "eligible_cycle_ids": [cycles[0]["cycle_id"]],
        },
    )

    cycle = cycles[0]
    basis, reasons = adjusted_basis(cycle)
    assert basis == 9.1
    assert not reasons
    assert gate_basis({**cycle, "completeness": "proven"}, 12.0) == (9.1, "ledger_adjusted")
    assert gate_basis({**cycle, "completeness": "partial"}, 12.0) == (12.0, "moomoo_avg_cost")
    assert gate_basis({**cycle, "completeness": "proven"}, None) == (9.1, "ledger_adjusted")
    assert gate_basis({"completeness": "partial"}, None) == (None, "missing")
