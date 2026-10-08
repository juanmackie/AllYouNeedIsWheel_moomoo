from api.services.wheel_ledger import WheelLedgerService
from db.database import OptionsDatabase


def _cycle(cycle_id, env="SIMULATE", account_id="acct-one", state="assigned_uncovered"):
    return {
        "cycle_id": cycle_id,
        "env": env,
        "account_id": account_id,
        "ticker": "XYZ",
        "state": state,
        "origin": "cash_secured_put",
        "opened_at": "2026-01-01T12:00:00+00:00",
        "closed_at": None,
        "shares": 100,
        "gross_basis_per_share": 50,
        "credits_net": 1.2,
        "open_credit": None,
        "fees_known": True,
        "completeness": "partial",
        "reasons": ["fill_history_does_not_cover_cycle_origin"],
        "adjusted_basis_per_share": 48.8,
        "events": [],
        "updated_at": "2026-01-02T00:00:00+00:00",
    }


def test_wheel_cycles_upsert_and_scope_by_environment_and_account(tmp_path):
    db = OptionsDatabase(tmp_path / "wheel.sqlite")
    try:
        assert db.save_wheel_cycles([_cycle("c1")]) == 1
        assert db.save_wheel_cycles([_cycle("c1", state="cc_open")]) == 1
        db.save_wheel_cycles([_cycle("c2", account_id="acct-two")])
        db.save_wheel_cycles([_cycle("c3", env="REAL")])

        rows = db.get_wheel_cycles(env="SIMULATE", account_id="acct-one")
        assert [row["cycle_id"] for row in rows] == ["c1"]
        assert rows[0]["state"] == "cc_open"
        assert rows[0]["reasons"] == ["fill_history_does_not_cover_cycle_origin"]
        assert rows[0]["events"] == []
        assert db.get_wheel_cycles(env="REAL", account_id="acct-one")[0]["cycle_id"] == "c3"
    finally:
        db.close()


def test_recompute_proves_basis_only_with_window_and_matching_single_inventory(tmp_path):
    db = OptionsDatabase(tmp_path / "wheel-ledger.sqlite")
    identity = {"env": "SIMULATE", "account_id": "acct-one"}
    captured = "2026-09-10T15:00:00-04:00"
    db.save_fills(
        [
            {
                **identity,
                "fill_id": "open-put",
                "captured_at": "2026-09-01T10:00:00-04:00",
                "ticker": "XYZ",
                "security_type": "OPT",
                "option_type": "PUT",
                "strike": 50,
                "expiration": "20260918",
                "side": "SELL",
                "qty": 1,
                "price": 1,
                "fees": 0,
            },
            {
                **identity,
                "fill_id": "assigned-option",
                "captured_at": captured,
                "ticker": "XYZ",
                "security_type": "OPT",
                "option_type": "PUT",
                "strike": 50,
                "expiration": "20260918",
                "side": "BUY",
                "qty": 1,
                "price": 0,
                "fees": 0,
            },
            {
                **identity,
                "fill_id": "assigned-stock",
                "captured_at": "2026-09-10T15:00:01-04:00",
                "ticker": "XYZ",
                "security_type": "STK",
                "side": "BUY",
                "qty": 100,
                "price": 50,
                "fees": 0,
            },
        ]
    )
    service = WheelLedgerService(db)
    try:
        db.set_setting("fill_history_window_start:SIMULATE:acct-one", "2026-08-01T00:00:00-04:00")
        result = service.recompute("SIMULATE", "acct-one", {"XYZ": {"position": 100}})
        assert result["ok"] is True
        row = db.get_wheel_cycles("SIMULATE", "acct-one", "XYZ")[0]
        assert row["completeness"] == "proven"
        basis = service.basis_for_position("XYZ", {"avg_cost": 55}, "SIMULATE", "acct-one")
        assert basis["gate_basis"] == 49.0
        assert basis["gate_basis_source"] == "ledger_adjusted"

        # Multiple active inventory cycles make ledger inventory ambiguous.
        second = _cycle("other-cycle", state="cc_open")
        db.save_wheel_cycles([second])
        basis = service.basis_for_position("XYZ", {"avg_cost": 55}, "SIMULATE", "acct-one")
        assert basis["gate_basis"] == 55
        assert basis["gate_basis_source"] == "moomoo_avg_cost"
    finally:
        db.close()
