from core.goal_pace import goal_pace


def _snapshot(day, nav):
    return {"captured_at": f"2026-08-{day:02d}T16:00:00-04:00", "net_liquidation": nav}


def test_goal_pace_neutralizes_deposits_and_requires_sixty_days():
    snapshots = [_snapshot(22, 10000), _snapshot(30, 12000)]
    flows = [
        {
            "clearing_date": "2026-08-26",
            "flow_type": "Deposit",
            "direction": "IN",
            "amount": 2000,
        }
    ]
    short = goal_pace(
        snapshots,
        flows,
        baseline_nav=10000,
        baseline_date="2026-08-22",
        as_of="2026-08-30",
    )
    assert short["goal_index"] == 1.0
    assert short["realized_cagr"] is None
    assert short["on_track"] is None


def test_goal_pace_reports_required_gap_and_does_not_mark_any_positive_return_on_track():
    result = goal_pace(
        [_snapshot(22, 10000), {"captured_at": "2026-10-21T16:00:00-04:00", "net_liquidation": 10100}],
        [],
        baseline_nav=10000,
        baseline_date="2026-08-22",
        target_multiple=5,
        horizon_end="2029-08-22",
        as_of="2026-10-21",
    )
    assert result["observation_ready"] is True
    assert result["required_cagr"] > result["realized_cagr"]
    assert result["gap_pp"] < 0
    assert result["on_track"] is False
    assert result["years_at_current_pace"] is not None


def test_goal_pace_withholds_return_metrics_until_cash_flow_history_is_confirmed_complete():
    result = goal_pace(
        [_snapshot(22, 10000), {"captured_at": "2026-10-21T16:00:00-04:00", "net_liquidation": 10100}],
        [],
        baseline_nav=10000,
        baseline_date="2026-08-22",
        horizon_end="2029-08-22",
        as_of="2026-10-21",
        flows_complete=False,
    )

    assert result["flows_complete"] is False
    assert result["confidence_state"] == "flows_incomplete"
    assert result["current_nav"] == 10100
    assert result["target_nav"] == 50000
    assert result["goal_index"] is None
    assert result["progress_pct"] is None
    assert result["required_cagr"] is None
    assert result["realized_cagr"] is None
    assert result["on_track"] is None


def test_incomplete_flows_remain_visible_when_goal_baseline_is_unavailable():
    result = goal_pace(
        [_snapshot(30, 10000)],
        [],
        baseline_nav=None,
        baseline_date="2026-08-22",
        as_of="2026-08-30",
        flows_complete=False,
    )

    assert result["confidence_state"] == "flows_incomplete"
    assert result["flows_complete"] is False
    assert result["goal_index"] is None
