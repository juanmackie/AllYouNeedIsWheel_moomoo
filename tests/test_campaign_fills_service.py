from datetime import datetime, timedelta
from unittest.mock import Mock

from api.services.fills_service import FillsService


def test_refresh_fill_pull_uses_incremental_window_capped_at_90_days():
    connection = Mock()
    connection.resolve_portfolio_identity.return_value = ("REAL", "opaque-account")
    connection.get_history_deals.return_value = []
    database = Mock()
    database.get_history_fill_coverage.return_value = {
        "status": "success",
        "last_success_at": (datetime.now() - timedelta(days=10)).isoformat(),
        "first_covered_date": "2026-07-01",
    }
    database.get_order_ids_missing_fees.return_value = []
    database.save_fills.return_value = 0
    database.get_fills.return_value = []

    result = FillsService(connection, database).ingest_history_fills()

    assert result["ok"] is True
    assert result["query_days"] == 12
    connection.get_history_deals.assert_called_once()
    start = connection.get_history_deals.call_args.kwargs["start"]
    assert (datetime.now().date() - datetime.fromisoformat(start).date()).days == 12
    written = database.set_history_fill_coverage.call_args.args[2]
    assert written["status"] == "success"
    assert written["first_covered_date"] == "2026-07-01"


def test_history_failure_records_failed_coverage_without_raw_broker_error():
    connection = Mock()
    connection.resolve_portfolio_identity.return_value = ("REAL", "opaque-account")
    connection.get_history_deals.return_value = None
    connection.last_error = "private broker details"
    database = Mock()
    database.get_history_fill_coverage.return_value = {}
    database.get_fills.return_value = []

    result = FillsService(connection, database).ingest_history_fills(days=140)

    assert result == {"ok": False, "reason": "history_query_failed", "ingested": 0}
    assert "private broker details" not in str(result)
    written = database.set_history_fill_coverage.call_args.args[2]
    assert written["status"] == "failed"
    assert written["reason"] == "history_query_failed"
    assert result.get("query_days", 90) <= 90
