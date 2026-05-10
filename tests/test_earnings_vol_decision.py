from core.earnings_vol_decision import classify_earnings_vol_signal


def test_classifies_clean_earnings_vol_setup_as_qualified():
    signal = classify_earnings_vol_signal({
        "ticker": "AMZN",
        "earnings_date": "2026-05-12",
        "days_to_earnings": 2,
        "front_iv": 0.72,
        "back_iv": 0.48,
        "rv30": 0.38,
        "avg_volume_30d": 8_000_000,
        "spread_pct": 4.0,
        "open_interest": 2_000,
        "option_volume": 500,
        "front_expiration": "2026-05-15",
        "back_expiration": "2026-06-05",
        "atm_strike": 185.0,
        "estimated_calendar_debit": 2.50,
        "max_risk_per_contract": 2.50,
    })

    assert signal.signal == "GREEN"
    assert signal.label == "Qualified"
    assert signal.term_structure_ratio == 1.5
    assert signal.iv_rv_ratio == 1.895
    assert not signal.blockers
    assert signal.structure == "ATM calendar"
    assert signal.atm_strike == 185.0
    assert signal.front_expiration == "2026-05-15"
    assert signal.back_expiration == "2026-06-05"
    assert signal.estimated_calendar_debit == 2.50
    assert signal.entry_plan == "Enter while front IV premium is positive"
    assert signal.exit_plan == "Close after earnings IV crush"
    assert signal.profit_target == "Target 20-40% where liquidity allows"
    assert signal.invalidation


def test_blocks_signal_when_front_iv_is_not_elevated():
    signal = classify_earnings_vol_signal({
        "ticker": "MSFT",
        "earnings_date": "2026-05-12",
        "days_to_earnings": 2,
        "front_iv": 0.32,
        "back_iv": 0.36,
        "rv30": 0.30,
        "avg_volume_30d": 5_000_000,
        "spread_pct": 3.0,
        "open_interest": 1_500,
        "option_volume": 300,
        "front_expiration": "2026-05-15",
        "back_expiration": "2026-06-05",
        "atm_strike": 300.0,
        "estimated_calendar_debit": 3.20,
    })

    assert signal.signal == "AVOID"
    assert "Front IV is not elevated over back IV" in signal.blockers


def test_blocks_signal_when_earnings_date_is_missing():
    signal = classify_earnings_vol_signal({
        "ticker": "NVDA",
        "front_iv": 0.75,
        "back_iv": 0.50,
        "rv30": 0.45,
        "avg_volume_30d": 20_000_000,
        "spread_pct": 5.0,
        "open_interest": 3_000,
        "option_volume": 1_000,
    })

    assert signal.signal == "AVOID"
    assert "No confirmed earnings date" in signal.blockers


def test_far_out_earnings_does_not_return_green():
    signal = classify_earnings_vol_signal({
        "ticker": "AMD",
        "earnings_date": "2026-08-15",
        "days_to_earnings": 85,
        "front_iv": 0.80,
        "back_iv": 0.45,
        "rv30": 0.40,
        "avg_volume_30d": 15_000_000,
        "spread_pct": 3.0,
        "open_interest": 5_000,
        "option_volume": 1_200,
        "front_expiration": "2026-08-20",
        "back_expiration": "2026-09-20",
        "atm_strike": 140.0,
        "estimated_calendar_debit": 4.20,
    })

    assert signal.signal == "WATCH"
    assert signal.label == "Watch"
    assert signal.days_to_earnings == 85
    assert any("too early" in n.lower() for n in signal.notes)


def test_qualified_signal_survives_serialization():
    signal = classify_earnings_vol_signal({
        "ticker": "GOOGL",
        "earnings_date": "2026-05-14",
        "days_to_earnings": 4,
        "front_iv": 0.68,
        "back_iv": 0.47,
        "rv30": 0.36,
        "avg_volume_30d": 6_000_000,
        "spread_pct": 5.0,
        "open_interest": 2_500,
        "option_volume": 800,
        "front_expiration": "2026-05-16",
        "back_expiration": "2026-06-20",
        "atm_strike": 170.0,
        "estimated_calendar_debit": 1.85,
        "max_risk_per_contract": 1.85,
    })

    d = signal.to_dict()
    assert d["signal"] == "GREEN"
    assert d["atm_strike"] == 170.0
    assert d["front_expiration"] == "2026-05-16"
    assert d["back_expiration"] == "2026-06-20"
    assert d["estimated_calendar_debit"] == 1.85
    assert d["structure"] == "ATM calendar"
    assert d["entry_plan"]
    assert d["exit_plan"]
    assert d["profit_target"]
    assert d["invalidation"]


def test_missing_trade_plan_fields_fall_back_to_watch():
    signal = classify_earnings_vol_signal({
        "ticker": "INTC",
        "earnings_date": "2026-05-12",
        "days_to_earnings": 2,
        "front_iv": 0.70,
        "back_iv": 0.50,
        "rv30": 0.38,
        "avg_volume_30d": 10_000_000,
        "spread_pct": 4.0,
        "open_interest": 3_000,
        "option_volume": 500,
    })

    assert signal.signal == "WATCH"
    assert signal.label == "Watch"
    assert "Incomplete trade plan fields" in signal.blockers
    assert signal.entry_plan == "Waiting on strike, expirations, or debit"
    assert signal.structure == "ATM calendar"
