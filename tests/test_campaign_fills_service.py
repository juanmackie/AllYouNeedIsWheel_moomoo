import logging
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from api.services.fills_service import FillsService, normalize_deal_row, refresh_fill_evidence
from core.wheel_runner import opaque_account_id
from db.database import OptionsDatabase
from db.sqlite_pool import close_connection_pool


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


OPAQUE = opaque_account_id("BROKER-ACC-42")
AAPL_PUT_CODE = "US.AAPL260220P00070000"


@pytest.fixture
def database(tmp_path):
    db_path = str(tmp_path / "fills.db")
    db = OptionsDatabase(db_path)
    yield db
    db.close()
    close_connection_pool(db_path)


def _deal(deal_id, order_id, side="SELL", code=AAPL_PUT_CODE):
    return {
        "code": code,
        "deal_id": deal_id,
        "order_id": order_id,
        "qty": 1,
        "price": 1.5,
        "trd_side": side,
        "create_time": "2026-10-01 10:00:00",
        "status": "OK",
    }


def _broker(deals=None, history_error=None):
    connection = Mock()
    connection.resolve_portfolio_identity.return_value = ("REAL", OPAQUE)
    if history_error is not None:
        connection.get_history_deals.side_effect = history_error
    else:
        connection.get_history_deals.return_value = deals or []
    connection.get_order_fees.side_effect = lambda order_ids: [
        {"order_id": oid, "fee_amount": 0.65, "fee_details": []} for oid in order_ids
    ]
    return connection


def test_refresh_fill_evidence_persists_pulled_deals(database):
    connection = _broker(deals=[_deal("D1", "O1"), _deal("D2", "O2", side="BUY")])

    result = refresh_fill_evidence(connection, database)

    assert result["ok"] is True
    assert result["reason"] == ""
    assert result["ingested"] == 2
    assert result["fees_complete"] is True
    fills = database.get_fills(env="REAL", account_id=OPAQUE)
    assert sorted(fill["fill_id"] for fill in fills) == ["D1", "D2"]
    assert all(fill["fees"] is not None for fill in fills)


@pytest.mark.parametrize(
    "connection_factory, database_factory",
    [
        (lambda: None, lambda db: db),
        (lambda: _broker(), lambda db: None),
    ],
)
def test_refresh_fill_evidence_without_connection_or_database_reports_unavailable(
    database, connection_factory, database_factory
):
    result = refresh_fill_evidence(connection_factory(), database_factory(database))

    assert result == {"ok": False, "reason": "broker_connection_unavailable"}


def test_refresh_fill_evidence_history_failure_returns_failure_without_raising(database):
    connection = _broker(history_error=RuntimeError("private broker details"))

    result = refresh_fill_evidence(connection, database)

    assert result["ok"] is False
    assert result["reason"] == "history_query_failed"
    assert "private broker details" not in str(result)
    assert database.get_fills(env="REAL", account_id=OPAQUE) == []


def test_refresh_fill_evidence_unexpected_exception_is_logged_by_type_only(database, monkeypatch, caplog):
    def _raise(self, days=None):
        raise RuntimeError("private opend detail")

    monkeypatch.setattr(FillsService, "ingest_history_fills", _raise)

    with caplog.at_level(logging.WARNING, logger="api.services.fills"):
        result = refresh_fill_evidence(_broker(), database)

    assert result == {"ok": False, "reason": "fill_pull_failed"}
    assert "RuntimeError" in caplog.text
    assert "private opend detail" not in caplog.text


def test_fee_backfill_processes_forty_orders_per_call_and_reports_incomplete(database):
    rows = [normalize_deal_row(_deal(f"D{i:02d}", f"O{i:02d}"), "REAL", OPAQUE) for i in range(45)]
    database.save_fills(rows)
    connection = _broker()
    service = FillsService(connection, database)

    updated, complete = service._backfill_fees("REAL", OPAQUE, "2000-01-01", rows)

    assert complete is False
    batches = [call.args[0] for call in connection.get_order_fees.call_args_list]
    assert [len(batch) for batch in batches] == [20, 20]
    assert sorted(oid for batch in batches for oid in batch) == [f"O{i:02d}" for i in range(40)]
    assert updated == 40
    still_missing = database.get_fills(env="REAL", account_id=OPAQUE, order_id="O44")
    assert still_missing[0]["fees"] is None


def test_wheel_runner_fill_provider_pulls_fills_through_app_connection(tmp_path):
    from api import _SERVICE_EXT_KEY, create_app, get_service

    db_path = str(tmp_path / "wiring.db")
    database = OptionsDatabase(db_path)
    try:
        app = create_app(
            {"TESTING": True, "connection_config": {"host": "127.0.0.1", "port": 11111}, "database": database}
        )
        connection = _broker(deals=[_deal("D1", "O1")])
        helper = Mock()
        options = Mock()
        options._ensure_connection.return_value = connection
        options.portfolio_context_helper = helper
        with app.app_context():
            store = app.extensions.setdefault(_SERVICE_EXT_KEY, {})
            store["options"] = options
            store["ivearnings"] = Mock()
            runner = get_service("wheel_runner")
            result = runner._fills_ingest_provider()

        assert result["ok"] is True
        assert result["ingested"] == 1
        helper.set_campaign_refresh_evidence.assert_called_once_with(result)
        assert [fill["fill_id"] for fill in database.get_fills(env="REAL", account_id=OPAQUE)] == ["D1"]
    finally:
        database.close()
        close_connection_pool(db_path)
