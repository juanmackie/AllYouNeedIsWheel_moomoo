from unittest.mock import MagicMock, patch


def test_ledger_route_returns_recent_entries_and_stats():
    from api import create_app

    app = create_app({"TESTING": True})
    app.config["database"] = MagicMock()
    ledger = MagicMock()
    ledger.get_recent.return_value = [{"id": 7, "scan_type": "recommendations"}]
    ledger.get_stats.return_value = {"total_scans": 1}

    with patch("api.routes.ledger.ScanLedger", return_value=ledger):
        response = app.test_client().get("/api/ledger/scans")

    assert response.status_code == 200
    assert response.get_json()["entries"] == [{"id": 7, "scan_type": "recommendations"}]
    assert response.get_json()["stats"] == {"total_scans": 1}
