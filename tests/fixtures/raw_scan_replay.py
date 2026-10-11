"""Synthetic Moomoo option-chain and quote evidence for a scanner replay."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def build_raw_scan_replay():
    """Return one fixed-time, two-symbol scan; no broker or network state."""
    now_utc = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
    fresh_fetch = now_utc.isoformat()
    quote_update_time = "2026-09-28 09:59:30"
    contracts = [
        {"code": "US.AAA-20261019-P90", "strike": 90.0, "expiration": "2026-10-19", "option_type": "PUT"},
        {"code": "US.BBB-20261019-P45", "strike": 45.0, "expiration": "2026-10-19", "option_type": "PUT"},
    ]
    quotes = {
        "US.AAA-20261019-P90": {
            "strike": 90.0,
            "expiration": "20261019",
            "option_type": "PUT",
            "bid": 1.50,
            "ask": 1.60,
            "last": 1.55,
            "delta": -0.30,
            "implied_volatility": 0.30,
            "open_interest": 500,
            "volume": 100,
            "update_time": quote_update_time,
            "quote_fetched_at_utc": fresh_fetch,
        },
        "US.BBB-20261019-P45": {
            "strike": 45.0,
            "expiration": "20261019",
            "option_type": "PUT",
            "bid": 1.00,
            "ask": 1.10,
            "last": 1.05,
            "delta": -0.30,
            "implied_volatility": 0.30,
            "open_interest": 500,
            "volume": 100,
            "update_time": quote_update_time,
            "quote_fetched_at_utc": fresh_fetch,
        },
    }
    prices = {
        "US.AAA": {"last_price": 100.0, "update_time": quote_update_time},
        "US.BBB": {"last_price": 50.0, "update_time": quote_update_time},
    }

    return {
        "now_utc": now_utc,
        "now_et": now_utc.astimezone(ZoneInfo("America/New_York")),
        "watchlist": ["AAA", "BBB"],
        "portfolio": {
            "positions": {"QQQ": {"position": 50, "market_price": 400.0, "avg_cost": 390.0}},
            "cash_balance": 25_000.0,
            "available_cash": 25_000.0,
            "broker_buying_power": 25_000.0,
            "broker_buying_power_source": "available_cash",
            "account_value": 100_000.0,
            "underlying_capital_exposure": {"AAA": 0.0, "BBB": 0.0, "QQQ": 20_000.0},
            "cash_available_for_csp": 20_000.0,
            "cash_reserved_for_csp": 0.0,
            "excess_liquidity": 20_000.0,
            "short_calls": {},
            "short_puts": {},
        },
        "contracts": contracts,
        "quotes": quotes,
        "prices": prices,
        "preset_key": "balanced",
        "quote_update_time": quote_update_time,
    }


def build_preset_flow_replay():
    """Return broker evidence at each v7 preset's inclusive CSP boundary."""
    scenario = build_raw_scan_replay()
    scenario["watchlist"] = ["COMMON", "BALANCED", "AGGRESSIVE"]
    scenario["preset_key"] = "balanced"
    scenario["contracts"] = []
    scenario["quotes"] = {}
    scenario["prices"] = {}
    scenario["portfolio"]["underlying_capital_exposure"] = {ticker: 0.0 for ticker in scenario["watchlist"]}

    contract_specs = (
        # Conservative's inclusive minimum DTE/OTM and upper delta boundary.
        ("COMMON", 21, 7.0, -0.33, 0.15, 0.16, 25, 1),
        # Balanced's inclusive minimum DTE/OTM and upper delta boundary.
        ("BALANCED", 14, 5.0, -0.42, 0.10, 0.15, 10, 1),
        # Aggressive's inclusive DTE/OTM/delta, premium, OI, and spread edges.
        ("AGGRESSIVE", 7, 3.0, -0.50, 0.05, 0.10, 5, 1),
    )
    stock_price = 100.0
    for ticker, dte, otm_pct, delta, bid, ask, open_interest, volume in contract_specs:
        strike = round(stock_price * (1 - otm_pct / 100), 2)
        expiration_date = scenario["now_et"].date() + timedelta(days=dte)
        code = f"US.{ticker}-{expiration_date:%Y%m%d}-P{int(strike * 100):05d}"
        scenario["contracts"].append(
            {
                "code": code,
                "strike": strike,
                "expiration": expiration_date.isoformat(),
                "option_type": "PUT",
            }
        )
        scenario["quotes"][code] = {
            "strike": strike,
            "expiration": expiration_date.strftime("%Y%m%d"),
            "option_type": "PUT",
            "bid": bid,
            "ask": ask,
            "last": (bid + ask) / 2,
            "delta": delta,
            "implied_volatility": 0.30,
            "open_interest": open_interest,
            "volume": volume,
            "update_time": scenario["quote_update_time"],
            "quote_fetched_at_utc": scenario["now_utc"].isoformat(),
        }
        scenario["prices"][f"US.{ticker}"] = {
            "last_price": stock_price,
            "update_time": scenario["quote_update_time"],
        }

    return scenario
