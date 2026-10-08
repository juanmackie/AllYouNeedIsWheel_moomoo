"""Pure scan planning helpers must retain cash-fit and complete-universe facts."""

from core.presets import get_preset
from core.scan_plan import chain_budget, csp_affordability, partition_universe, select_chain_rows


def test_csp_affordability_matches_legacy_five_to_fifteen_percent_grid():
    preset = get_preset("balanced").to_screener_profile()

    for price in range(1, 1001, 7):
        for cash in range(0, 100_001, 1379):
            min_strike = price * (1 - preset["csp_max_otm_pct"] / 100)
            max_strike = price * (1 - preset["csp_min_otm_pct"] / 100)
            affordable_strike = min(max_strike, cash / 100)
            legacy = price > 0 and cash > 0 and affordable_strike >= min_strike
            assert csp_affordability(price, cash, cash, preset) is legacy


def test_csp_affordability_uses_smaller_cash_or_exposure_room():
    preset = get_preset("balanced")

    assert csp_affordability(100, 10_000, 9_000, preset)
    assert not csp_affordability(100, 10_000, 8_499, preset)
    assert not csp_affordability(100, 500, 20_000, preset)
    assert not csp_affordability(0, 10_000, 10_000, preset)


def test_partition_universe_keeps_unpriced_symbols_as_gaps_and_records_proven_cash_skips():
    preset = get_preset("balanced")

    scan, cash_skips, gaps = partition_universe(
        ["AAA", "BBB", "CCC"],
        {"AAA": 90.0, "BBB": 100.0},
        cash=10_000,
        exposure_room=8_499,
        preset=preset,
        fetched_at="2026-09-30T00:00:00Z",
    )

    assert scan == ["AAA"]
    assert cash_skips == [
        {
            "ticker": "BBB",
            "price": 100.0,
            "min_strike_needed": 85.0,
            "cash_at_scan": 8_499.0,
            "fetched_at": "2026-09-30T00:00:00Z",
        }
    ]
    assert gaps == [{"ticker": "CCC", "reason": "missing_price"}]


def test_partition_universe_does_not_call_missing_exposure_a_cash_skip():
    scan, cash_skips, gaps = partition_universe(
        ["AAA"], {"AAA": 100.0}, cash=10_000, exposure_room=None, preset=get_preset("balanced")
    )

    assert scan == []
    assert cash_skips == []
    assert gaps == [{"ticker": "AAA", "reason": "missing_exposure_room"}]


def test_partition_universe_accepts_per_ticker_exposure_room():
    scan, cash_skips, gaps = partition_universe(
        ["AAA", "BBB"],
        {"AAA": 100.0, "BBB": 100.0},
        cash=10_000,
        exposure_room={"AAA": 9_000, "BBB": 8_000},
        preset=get_preset("balanced"),
    )

    assert scan == ["AAA"]
    assert cash_skips[0]["ticker"] == "BBB"
    assert cash_skips[0]["cash_at_scan"] == 8_000
    assert gaps == []


def test_chain_budget_is_dte_span_in_thirty_day_windows():
    assert chain_budget(get_preset("balanced")) == {"span_days": 32, "calls_per_symbol": 2}
    assert chain_budget(get_preset("conservative")) == {"span_days": 25, "calls_per_symbol": 1}
    assert chain_budget(get_preset("aggressive")) == {"span_days": 29, "calls_per_symbol": 1}


def test_select_chain_rows_filters_puts_and_retains_all_qualifying_rows():
    preset = get_preset("balanced")
    rows = [
        {"expiration": "20261016", "dte": 16, "strike": 95.0, "option_type": "PUT"},
        {"expiration": "20261023", "dte": 23, "strike": 90.0, "option_type": "PUT"},
        {"expiration": "20261113", "dte": 37, "strike": 85.0, "option_type": "PUT"},
        {"expiration": "20261120", "dte": 51, "strike": 90.0, "option_type": "PUT"},
        {"expiration": "20261120", "dte": 51, "strike": 80.0, "option_type": "PUT"},
        {"expiration": "20261023", "dte": 23, "strike": 90.0, "option_type": "CALL"},
    ]

    selected = select_chain_rows(rows, 100.0, 10_000, 9_000, preset)

    assert selected == [rows[1], rows[2]]
