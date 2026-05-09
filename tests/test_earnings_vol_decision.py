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
    })

    assert signal.signal == "GREEN"
    assert signal.label == "Qualified"
    assert signal.term_structure_ratio == 1.5
    assert signal.iv_rv_ratio == 1.895
    assert not signal.blockers


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
