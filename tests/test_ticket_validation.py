from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from api.routes.run import evaluate_copy_check


def _inputs(kind="close"):
    now_et = datetime(2026, 10, 11, 12, tzinfo=ZoneInfo("America/New_York"))
    view = {
        "run": {
            "run_id": "one",
            "coverage_total": 1,
            "coverage_scanned": 1,
            "quote_fetched_at": {"ABC": "2026-10-09T20:00:00+00:00"},
        },
        "roll_candidates": [
            {
                "ticker": "ABC",
                "option_type": "CALL",
                "expiration": "20261030",
                "strike": 12,
                "bid": 0.5,
                "copy_eligible": True,
                "recommended_contracts": 1,
            }
        ],
    }
    requested = {
        "kind": kind,
        "run_id": "one",
        "ticker": "ABC",
        "option_type": "CALL",
        "expiration": "20261016",
        "strike": 10,
        "quantity": 1,
    }
    positions = {
        "positions": {
            "ABC": {"symbol": "US.ABC", "position": 100, "avg_cost": 10, "security_type": "STK"},
            "ABC261016C10000": {
                "symbol": "US.ABC261016C10000",
                "option_type": "CALL",
                "strike": 10,
                "expiration": "20261016",
                "position": -1,
                "security_type": "OPT",
            },
        }
    }

    def fetch(**args):
        return {
            "source": "broker",
            "option": {
                "bid": 0.5 if args["strike"] == 12 else 0.1,
                "ask": 0.6 if args["strike"] == 12 else 0.2,
                "update_time": "2026-10-09 16:00:00",
            },
        }

    return view, requested, now_et, positions, fetch


def test_close_revalidates_actual_short_position_and_price():
    view, request, now, positions, fetch = _inputs()
    result = evaluate_copy_check(
        view,
        request,
        now_et=now,
        now_utc=now.astimezone(timezone.utc),
        fetch_live_chain=fetch,
        fetch_live_positions=lambda: positions,
    )
    assert result["mode"] == "staged"
    assert result["contract"]["ask"] == 0.2
    request["quantity"] = 2
    assert (
        evaluate_copy_check(view, request, now_et=now, fetch_live_chain=fetch, fetch_live_positions=lambda: positions)[
            "mode"
        ]
        == "review_only"
    )


def test_roll_checks_both_legs_and_rejects_debit():
    view, request, now, positions, fetch = _inputs("roll")
    request.update(expiration="20261030", strike=12, close_expiration="20261016", close_strike=10)
    result = evaluate_copy_check(
        view, request, now_et=now, fetch_live_chain=fetch, fetch_live_positions=lambda: positions
    )
    assert result["mode"] == "staged"
    assert result["net_credit"] == 0.3

    def debit(**args):
        evidence = fetch(**args)
        evidence["option"]["ask"] = 0.8
        return evidence

    assert (
        evaluate_copy_check(view, request, now_et=now, fetch_live_chain=debit, fetch_live_positions=lambda: positions)[
            "mode"
        ]
        == "review_only"
    )


def test_roll_rejects_repair_when_fresh_delta_is_above_owner_limit():
    view, request, now, positions, fetch = _inputs("roll")
    request.update(expiration="20261030", strike=12, close_expiration="20261016", close_strike=10)
    view["preset"] = {"repair_call_max_delta": 0.2}
    positions["positions"]["ABC"]["avg_cost"] = 20
    view["roll_candidates"][0].update(
        wheel_decision={"broker_cost_basis": 20}, earnings_date="20261115", security_type="stock"
    )

    def threatened(**args):
        quote = fetch(**args)
        quote["option"]["delta"] = 0.45
        return quote

    result = evaluate_copy_check(
        view, request, now_et=now, fetch_live_chain=threatened, fetch_live_positions=lambda: positions
    )
    assert result["mode"] == "review_only"
    assert "repair" in result["reasons"][0].lower()


def test_open_repair_rechecks_delta_and_free_shares():
    view, request, now, positions, fetch = _inputs("open")
    target = dict(
        view.pop("roll_candidates")[0],
        covered_call_intent="repair",
        earnings_date="20261115",
        event_tier="event_safe",
    )
    view["cc_decisions"] = [target]
    request.update(expiration="20261030", strike=12)
    positions["positions"]["ABC"]["avg_cost"] = 20
    positions["positions"].pop("ABC261016C10000")

    def quote(**args):
        evidence = fetch(**args)
        evidence["option"]["delta"] = 0.15
        return evidence

    def check():
        return evaluate_copy_check(
            view, request, now_et=now, fetch_live_chain=quote, fetch_live_positions=lambda: positions
        )

    assert check()["mode"] == "staged"
    positions["positions"]["ABC"]["position"] = 26
    assert check()["mode"] == "review_only"
    positions["positions"]["ABC"]["position"] = 100

    def threatened(**args):
        evidence = quote(**args)
        evidence["option"]["delta"] = 0.45
        return evidence

    assert (
        evaluate_copy_check(
            view, request, now_et=now, fetch_live_chain=threatened, fetch_live_positions=lambda: positions
        )["mode"]
        == "review_only"
    )


def test_repair_call_blocks_without_scoring_time_event_safe_tier():
    from api.services.ticket_validation import call_quote_blocker

    target = {
        "ticker": "ABC",
        "option_type": "CALL",
        "expiration": "20261030",
        "strike": 12,
        "earnings_date": "20261115",
        "security_type": "stock",
    }
    quote = {"bid": 0.5, "delta": 0.1}
    preset = {"repair_call_max_delta": 0.2}

    assert (
        call_quote_blocker(target, quote, 20, preset) == "repair call earnings date is unknown, stale, or before expiry"
    )
    assert call_quote_blocker(dict(target, event_tier="event_safe"), quote, 20, preset) is None
    nested = dict(target, wheel_decision={"event_tier": "event_safe"})
    assert call_quote_blocker(nested, quote, 20, preset) is None
