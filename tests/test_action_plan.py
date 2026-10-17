from core.action_plan import build_next_actions


def test_actions_order_and_manual_close_ticket():
    rows = [
        {
            "ticker": "ZZZ",
            "option_type": "CALL",
            "strike": 20,
            "expiration": "20261023",
            "dte": 12,
            "exit_verdict": "HOLD",
        },
        {
            "ticker": "AAA",
            "option_type": "CALL",
            "strike": 10,
            "expiration": "20261016",
            "dte": 5,
            "exit_verdict": "CLOSE",
            "ask": 0.25,
            "quantity": 2,
        },
    ]
    actions = build_next_actions(roll_decisions=rows)
    assert actions[0]["verdict"] == "CLOSE"
    assert actions[0]["ticket"]["legs"][0]["executable_price"] == 0.25
    assert actions[0]["ticket"]["legs"][0]["quantity"] == 2
    assert actions == build_next_actions(roll_decisions=rows)


def test_cash_plan_preserves_allocated_quantity():
    plan = {
        "lines": [
            {
                "ticker": "ABC",
                "option_type": "PUT",
                "strike": 10,
                "expiration": "20261023",
                "bid": 0.5,
                "deployment_contracts": 3,
            }
        ],
        "total_cash": 3000,
    }
    actions = build_next_actions(deployment_plan=plan)
    assert actions[0]["verdict"] == "DEPLOY_CASH"
    assert actions[0]["ticket"]["legs"][0]["quantity"] == 3


def test_repair_holding_keeps_rejection_and_odd_lot_has_no_ticket():
    campaigns = [
        {"ticker": "AAA", "shares": 100, "adjusted_basis": 70, "market_price": 42},
        {"ticker": "BBB", "shares": 26, "adjusted_basis": 10, "market_price": 11},
    ]
    rejected = [
        {
            "ticker": "AAA",
            "signal_type": "covered_call",
            "reason_code": "repair_earnings_before_expiry",
            "reason_text": "earnings before expiry",
        }
    ]
    actions = build_next_actions(campaigns=campaigns, rejected=rejected)
    repair = next(a for a in actions if a["underlying"] == "AAA")
    assert repair["verdict"] == "WAIT"
    assert repair["reason_codes"] == ["repair_earnings_before_expiry"]
    assert all(a["ticket"] is None for a in actions)


def test_runner_publishes_cash_plan_and_actions_atomically():
    from core.wheel_runner import WheelRunner

    result = {
        "scan_coverage": {"total": 1, "scanned": 1},
        "watchlist_csps": {"signals": []},
        "deployment_plan": {
            "lines": [
                {
                    "ticker": "ABC",
                    "option_type": "PUT",
                    "strike": 10,
                    "expiration": "20261023",
                    "bid": 0.5,
                    "deployment_contracts": 3,
                }
            ]
        },
    }
    snapshot = WheelRunner(None, None, {})._build_snapshot("REAL", "opaque", "", result, {}, [])
    payload = snapshot.to_dict()
    assert payload["deployment_plan"] == result["deployment_plan"]
    assert payload["next_actions"][0]["ticket"]["legs"][0]["quantity"] == 3
    assert payload["run"]["schema_version"] == 3


def test_covered_call_action_carries_basis_source_from_call_or_wheel_decision():
    campaigns = [{"ticker": "AAA", "shares": 100, "adjusted_basis": 70, "market_price": 42}]
    direct = {"ticker": "AAA", "option_type": "CALL", "strike": 75, "if_called_pnl_vs_basis": 500.0}
    nested = {
        "ticker": "AAA",
        "option_type": "CALL",
        "strike": 75,
        "wheel_decision": {"if_called_pnl_vs_basis": 500.0, "basis_source": "moomoo_avg_cost"},
    }
    actions = build_next_actions(campaigns=campaigns, cc_decisions=[{**direct, "basis_source": "premium_adjusted"}])
    assert next(a for a in actions if a["underlying"] == "AAA")["basis_source"] == "premium_adjusted"

    actions = build_next_actions(campaigns=campaigns, cc_decisions=[nested])
    assert next(a for a in actions if a["underlying"] == "AAA")["basis_source"] == "moomoo_avg_cost"

    actions = build_next_actions(campaigns=campaigns, cc_decisions=[direct])
    assert next(a for a in actions if a["underlying"] == "AAA")["basis_source"] is None
