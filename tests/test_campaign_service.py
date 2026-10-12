from unittest.mock import Mock

from api.services.campaign_service import CampaignService


def test_campaign_service_uses_scoped_fills_and_keeps_broker_basis_when_gate_is_off():
    db = Mock()
    db.get_fills.return_value = []
    db.get_history_fill_coverage.return_value = {"status": "success"}
    service = CampaignService(db)
    positions = [{"symbol": "US.ABC", "security_type": "STK", "position": 100, "avg_cost": 70.0}]

    enriched = service.enrich_positions(positions, "REAL", "opaque-account", open_option_positions=[])

    db.get_fills.assert_called_once_with(env="REAL", account_id="opaque-account", limit=50000)
    assert enriched[0]["avg_cost"] == 70.0
    assert enriched[0]["adjusted_basis"] == 70.0
    assert enriched[0]["basis_source"] == "moomoo_avg_cost"
    assert enriched[0]["basis_confidence"] == "unverified"
    assert positions[0] == {"symbol": "US.ABC", "security_type": "STK", "position": 100, "avg_cost": 70.0}


def test_campaign_service_requires_current_refresh_success():
    db = Mock()
    db.get_fills.return_value = []
    db.get_history_fill_coverage.return_value = {"status": "success"}
    service = CampaignService(db, capability=True)
    service.set_refresh_evidence({"ok": True})
    good = service.get_campaigns(
        [{"symbol": "US.ABC", "security_type": "STK", "position": 100, "avg_cost": 70}],
        "REAL",
        "acct",
        open_option_positions=[],
    )
    service.set_refresh_evidence({"ok": False, "reason": "query timeout"})
    failed = service.get_campaigns(
        [{"symbol": "US.ABC", "security_type": "STK", "position": 100, "avg_cost": 70}],
        "REAL",
        "acct",
        open_option_positions=[],
    )

    assert good[0]["basis_source"] == "moomoo_avg_cost"
    assert "no_fills_in_ledger" in good[0]["basis_reasons"]
    assert failed[0]["basis_source"] == "moomoo_avg_cost"
    assert "fill_pull_not_successful_this_refresh" in failed[0]["basis_reasons"]
