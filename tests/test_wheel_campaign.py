from core.wheel_campaign import build_campaign, build_campaigns


def _fill(fill_id, security_type, side, qty, price, captured_at, **extra):
    return {
        "fill_id": fill_id,
        "security_type": security_type,
        "ticker": "ABC",
        "side": side,
        "qty": qty,
        "price": price,
        "fees": 1.0,
        "captured_at": captured_at,
        **extra,
    }


def test_campaign_keeps_moomoo_basis_until_capability_is_measured():
    position = {"symbol": "US.ABC", "position": 100, "avg_cost": 70.0}
    fills = [
        _fill("s1", "STK", "BUY", 100, 70, "2026-09-01 10:00:00"),
        _fill(
            "o1", "OPT", "SELL", 1, 2.0, "2026-09-02 10:00:00", option_type="PUT", strike=70, expiration="2026-09-18"
        ),
        _fill("o2", "OPT", "BUY", 1, 0.5, "2026-09-10 10:00:00", option_type="PUT", strike=70, expiration="2026-09-18"),
    ]

    campaign = build_campaign(position, fills, fill_pull_ok=True, capability_measured=False)

    assert campaign["broker_avg_cost"] == 70.0
    assert campaign["adjusted_basis"] == 70.0
    assert campaign["basis_source"] == "moomoo_avg_cost"
    assert campaign["basis_confidence"] == "unverified"
    assert "broker_history_capability_unmeasured" in campaign["basis_reasons"]


def test_campaign_math_uses_closed_contract_credits_and_all_held_shares():
    position = {"symbol": "US.ABC", "position": 126, "avg_cost": 70.0}
    fills = [
        _fill("s1", "STK", "BUY", 126, 70, "2026-09-01 10:00:00"),
        _fill(
            "o1", "OPT", "SELL", 2, 2.0, "2026-09-02 10:00:00", option_type="PUT", strike=70, expiration="2026-09-18"
        ),
        _fill("o2", "OPT", "BUY", 2, 0.5, "2026-09-10 10:00:00", option_type="PUT", strike=70, expiration="2026-09-18"),
    ]

    campaign = build_campaign(position, fills, fill_pull_ok=True, capability_measured=True, open_option_positions=[])

    assert campaign["net_option_premium"] == 298.0
    assert campaign["adjusted_basis"] == round(70 - 298 / 126, 4)
    assert campaign["basis_source"] == "premium_adjusted"
    assert campaign["lot_anchor"] == "2026-09-01 10:00:00"


def test_stock_fill_quantity_mismatch_and_failed_pull_fall_back_to_broker_basis():
    position = {"symbol": "US.ABC", "position": 100, "avg_cost": 70.0}
    fills = [_fill("s1", "STK", "BUY", 50, 70, "2026-09-01 10:00:00")]

    mismatch = build_campaign(position, fills, fill_pull_ok=True, capability_measured=True, open_option_positions=[])
    failed = build_campaign(position, fills, fill_pull_ok=False, capability_measured=True, open_option_positions=[])
    assert mismatch["basis_source"] == "moomoo_avg_cost"
    assert "stock_fills_do_not_reconcile_position" in mismatch["basis_reasons"]
    assert failed["basis_source"] == "moomoo_avg_cost"
    assert "fill_pull_not_successful_this_refresh" in failed["basis_reasons"]


def test_assignment_pair_includes_put_credit_before_stock_lot_anchor_and_marks_complete():
    position = {"symbol": "US.ABC", "position": 100, "avg_cost": 70.0, "market_price": 60}
    fills = [
        _fill(
            "put-sell",
            "OPT",
            "SELL",
            1,
            2.0,
            "2026-09-01 10:00:00",
            option_type="PUT",
            strike=70,
            expiration="2026-09-18",
        ),
        _fill(
            "put-assign",
            "OPT",
            "BUY",
            1,
            0.0,
            "2026-09-18 16:00:00",
            option_type="PUT",
            strike=70,
            expiration="2026-09-18",
        ),
        _fill("stock-buy", "STK", "BUY", 100, 70.0, "2026-09-18 16:00:01"),
    ]

    campaign = build_campaign(
        position,
        fills,
        fill_pull_ok=True,
        capability_measured=True,
        open_option_positions=[],
        as_of="2026-10-11T12:00:00",
    )

    assert campaign["net_option_premium"] == 198.0
    assert campaign["basis_confidence"] == "complete"
    assert campaign["available_contracts"] == 1
    assert campaign["break_even"]["capital_tied_up"] == 6000.0
    assert campaign["break_even"]["weeks_to_no_loss_exit"] is None


def test_open_short_option_without_matching_sell_fill_blocks_adjusted_basis():
    position = {"symbol": "US.ABC", "position": 100, "avg_cost": 70.0}
    open_short = {
        "symbol": "US.ABC260918C75",
        "security_type": "OPT",
        "position": -1,
        "option_type": "CALL",
        "expiration": "2026-09-18",
        "strike": 75,
    }

    campaign = build_campaign(
        position,
        [],
        fill_pull_ok=True,
        capability_measured=True,
        open_option_positions=[open_short],
    )

    assert campaign["basis_source"] == "moomoo_avg_cost"
    assert "open_short_leg_missing_from_ledger" in campaign["basis_reasons"]


def test_previous_sold_share_lot_does_not_credit_the_current_campaign():
    position = {"symbol": "US.ABC", "position": 100, "avg_cost": 70.0}
    fills = []
    for label, day, price in [("old", "04", 3.0), ("current", "18", 2.0)]:
        expiry = f"2026-09-{day}"
        fills.extend(
            [
                _fill(
                    f"{label}-sell",
                    "OPT",
                    "SELL",
                    1,
                    price,
                    f"{expiry} 10:00:00",
                    option_type="PUT",
                    strike=70,
                    expiration=expiry,
                ),
                _fill(
                    f"{label}-assign",
                    "OPT",
                    "BUY",
                    1,
                    0,
                    f"{expiry} 16:00:00",
                    option_type="PUT",
                    strike=70,
                    expiration=expiry,
                ),
                _fill(f"{label}-shares", "STK", "BUY", 100, 70, f"{expiry} 16:00:01"),
            ]
        )
    fills.append(_fill("sold-old-shares", "STK", "SELL", 100, 70, "2026-09-10 10:00:00"))
    campaign = build_campaign(position, fills, fill_pull_ok=True, capability_measured=True, open_option_positions=[])
    assert campaign["net_option_premium"] == 198.0
    assert campaign["adjusted_basis"] == 68.02


def test_break_even_uses_market_capital_and_weekly_closed_call_income():
    position = {"symbol": "US.ABC", "position": 100, "avg_cost": 70.0, "market_price": 60.0}
    fills = [
        _fill("s1", "STK", "BUY", 100, 70, "2026-09-01 10:00:00"),
        _fill(
            "c1", "OPT", "SELL", 1, 1.0, "2026-09-02 10:00:00", option_type="CALL", strike=75, expiration="2026-09-18"
        ),
        _fill(
            "c2", "OPT", "BUY", 1, 0.5, "2026-09-09 10:00:00", option_type="CALL", strike=75, expiration="2026-09-18"
        ),
    ]

    campaign = build_campaign(
        position,
        fills,
        fill_pull_ok=True,
        capability_measured=True,
        open_option_positions=[],
        as_of="2026-09-15T10:00:00",
    )

    assert campaign["realized_premium_per_week"] == 24.0
    assert campaign["break_even"]["capital_tied_up"] == 6000.0
    assert campaign["break_even"]["weeks_to_no_loss_exit"] == 39.7


def test_build_campaigns_only_returns_stock_rows():
    positions = [
        {"symbol": "US.ABC", "security_type": "STK", "position": 100, "avg_cost": 70},
        {"symbol": "US.ABC260918P70", "security_type": "OPT", "position": -1, "avg_cost": 2},
    ]

    campaigns = build_campaigns(positions, [], fill_pull_ok=False, open_option_positions=[])

    assert len(campaigns) == 1
    assert campaigns[0]["ticker"] == "ABC"
