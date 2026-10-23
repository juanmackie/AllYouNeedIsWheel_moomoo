import logging
from datetime import date, datetime, timedelta
from unittest.mock import Mock

import pytest

from api.services.fills_service import FillsService, history_windows, normalize_deal_row, refresh_fill_evidence
from core.wheel_runner import opaque_account_id
from db.database import OptionsDatabase
from db.sqlite_pool import close_connection_pool


def test_refresh_fill_pull_is_incremental_after_backfill():
    connection = Mock()
    connection.resolve_portfolio_identity.return_value = ("REAL", "opaque-account")
    connection.get_history_deals.return_value = []
    database = Mock()
    database.get_history_fill_coverage.return_value = {
        "status": "success",
        "last_success_at": (datetime.now() - timedelta(days=10)).isoformat(),
        "first_covered_date": "2025-01-01",
    }
    database.get_order_ids_missing_fees.return_value = []
    database.save_fills.return_value = 0
    database.get_fills.return_value = []

    result = FillsService(connection, database).ingest_history_fills(history_start="2025-01-01")

    assert result["ok"] is True
    assert result["query_days"] == 12
    connection.get_history_deals.assert_called_once()
    start = connection.get_history_deals.call_args.kwargs["start"]
    assert (datetime.now().date() - datetime.fromisoformat(start).date()).days == 12
    written = database.set_history_fill_coverage.call_args.args[2]
    assert written["status"] == "success"
    assert written["first_covered_date"] == "2025-01-01"


def test_first_pull_backfills_history_in_overlapping_90_day_windows():
    today = date.today()
    history_start = (today - timedelta(days=200)).isoformat()
    connection = Mock()
    connection.resolve_portfolio_identity.return_value = ("REAL", "opaque-account")
    connection.get_history_deals.return_value = []
    database = Mock()
    database.get_history_fill_coverage.return_value = {}
    database.get_order_ids_missing_fees.return_value = []
    database.save_fills.return_value = 0

    result = FillsService(connection, database).ingest_history_fills(history_start=history_start)

    assert result["ok"] is True
    assert result["windows"] == 3
    windows = [(call.kwargs["start"], call.kwargs["end"]) for call in connection.get_history_deals.call_args_list]
    assert len(windows) == 3
    assert windows[0][0] == history_start
    for start, end in windows[:-1]:
        assert (date.fromisoformat(end) - date.fromisoformat(start)).days + 1 <= 90
    for (_, previous_end), (next_start, _) in zip(windows, windows[1:]):
        assert date.fromisoformat(next_start) == date.fromisoformat(previous_end)
    # The last window is open-ended so OpenD includes today's deals.
    assert windows[-1][1] == ""
    assert date.fromisoformat(windows[-1][0]) <= today
    written = database.set_history_fill_coverage.call_args.args[2]
    assert written["status"] == "success"
    assert written["first_covered_date"] == history_start


def test_history_windows_split_inclusive_spans_at_ninety_days():
    start = date(2026, 1, 1)
    assert history_windows(date(2026, 1, 2), date(2026, 1, 1)) == []
    assert history_windows(start, start + timedelta(days=89)) == [("2026-01-01", "2026-03-31")]
    # The next window starts on the previous window's end date (one-day overlap).
    assert history_windows(start, start + timedelta(days=90)) == [
        ("2026-01-01", "2026-03-31"),
        ("2026-03-31", "2026-04-01"),
    ]
    assert history_windows(start, start + timedelta(days=178)) == [
        ("2026-01-01", "2026-03-31"),
        ("2026-03-31", "2026-06-28"),
    ]


def test_history_windows_single_day_and_last_window_end_at_range_end():
    start = date(2026, 1, 1)
    assert history_windows(start, start) == [("2026-01-01", "2026-01-01")]
    windows = history_windows(start, start + timedelta(days=200))
    assert windows[-1][1] == (start + timedelta(days=200)).isoformat()
    assert len(windows) == 3


def test_backfill_window_failure_saves_nothing_and_keeps_coverage_start():
    today = date.today()
    connection = Mock()
    connection.resolve_portfolio_identity.return_value = ("REAL", "opaque-account")
    connection.get_history_deals.side_effect = [[{"deal_id": "D1"}], None]
    database = Mock()
    covered = (today - timedelta(days=150)).isoformat()
    database.get_history_fill_coverage.return_value = {"first_covered_date": covered}

    result = FillsService(connection, database).ingest_history_fills(
        history_start=(today - timedelta(days=200)).isoformat()
    )

    assert result == {"ok": False, "reason": "history_query_failed", "ingested": 0}
    assert connection.get_history_deals.call_count == 2
    database.save_fills.assert_not_called()
    written = database.set_history_fill_coverage.call_args.args[2]
    assert written["status"] == "failed"
    assert written["first_covered_date"] == covered


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
    connection.get_cash_flow.return_value = []
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


def test_backfill_counts_a_deal_repeated_across_overlapping_windows_once(database):
    # The fake broker returns the same deals for every backfill window, like a
    # boundary day that two overlapping windows both contain.
    connection = _broker(deals=[_deal("D1", "O1"), _deal("D2", "O2", side="BUY")])

    result = refresh_fill_evidence(connection, database, history_start="2025-01-01")

    assert connection.get_history_deals.call_count > 1
    assert result["seen"] == 2
    assert result["ingested"] == 2


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
    def _raise(self, days=None, history_start=None):
        raise RuntimeError("private opend detail")

    monkeypatch.setattr(FillsService, "ingest_history_fills", _raise)

    with caplog.at_level(logging.WARNING, logger="api.services.fills"):
        result = refresh_fill_evidence(_broker(), database)

    assert result["ok"] is False
    assert result["reason"] == "fill_pull_failed"
    assert "RuntimeError" in caplog.text
    assert "private opend detail" not in caplog.text


def test_fee_backfill_processes_forty_orders_per_call_and_reports_incomplete(database):
    rows = [normalize_deal_row(_deal(f"D{i:02d}", f"O{i:02d}"), "REAL", OPAQUE) for i in range(45)]
    database.save_fills(rows)
    connection = _broker()
    service = FillsService(connection, database)

    updated, complete = service._backfill_fees("REAL", OPAQUE, rows)

    assert complete is False
    batches = [call.args[0] for call in connection.get_order_fees.call_args_list]
    assert [len(batch) for batch in batches] == [20, 20]
    assert sorted(oid for batch in batches for oid in batch) == [f"O{i:02d}" for i in range(40)]
    assert updated == 40
    still_missing = database.get_fills(env="REAL", account_id=OPAQUE, order_id="O44")
    assert still_missing[0]["fees"] is None


def test_fee_backfill_queries_only_orders_whose_stored_fills_lack_fees(database):
    # 45 orders already stored with known fees; the history window still
    # returns them as fee-less normalized rows. Only the 5 new orders may be
    # queried, so the 40-order budget is never spent on paid orders.
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    paid_deals = [{**_deal(f"OLD{i:02d}", f"OLD{i:02d}"), "create_time": now_text} for i in range(45)]
    new_deals = [{**_deal(f"NEW{i}", f"NEW{i}"), "create_time": now_text} for i in range(5)]
    paid_rows = [normalize_deal_row(deal, "REAL", OPAQUE) for deal in paid_deals]
    for row in paid_rows:
        row["fees"] = 0.65
    database.save_fills(paid_rows)
    connection = _broker(deals=[*paid_deals, *new_deals])

    result = FillsService(connection, database).ingest_history_fills(days=90)

    assert result["ok"] is True
    assert result["ingested"] == 5
    assert result["fees_complete"] is True
    queried = sorted(oid for call in connection.get_order_fees.call_args_list for oid in call.args[0])
    assert queried == [f"NEW{i}" for i in range(5)]
    fills = database.get_fills(env="REAL", account_id=OPAQUE)
    assert len(fills) == 50
    assert all(fill["fees"] is not None for fill in fills)
    assert {fill["fees"] for fill in fills if fill["order_id"].startswith("OLD")} == {0.65}


def test_fee_backfill_retries_unpaid_orders_regardless_of_age(database):
    # Three fee-less fills stored from an earlier pull, 200 days old.
    old_text = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")
    old_rows = [
        normalize_deal_row({**_deal(f"OLD{i}", f"OLD{i}"), "create_time": old_text}, "REAL", OPAQUE)
        for i in range(1, 4)
    ]
    database.save_fills(old_rows)
    connection = _broker(deals=[])
    connection.get_order_fees.side_effect = lambda order_ids: [
        {"order_id": oid, "fee_amount": 0.65, "fee_details": []} for oid in order_ids if oid.startswith("NEW")
    ]
    # First pull: the broker omits fees for the old orders, so they stay fee-less.
    first = FillsService(connection, database).ingest_history_fills(days=90)
    assert first["fees_complete"] is False
    assert len(database.get_order_ids_missing_fees(env="REAL", account_id=OPAQUE)) == 3

    # Second pull: a 30-day window that never reaches the old fills. One new
    # order arrives, and the broker now returns every fee.
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_deal = {**_deal("NEW1", "NEW1"), "create_time": now_text}
    connection.get_history_deals.return_value = [new_deal]
    connection.get_order_fees.side_effect = lambda order_ids: [
        {"order_id": oid, "fee_amount": 0.65, "fee_details": []} for oid in order_ids
    ]
    connection.get_order_fees.reset_mock()

    second = FillsService(connection, database).ingest_history_fills(days=30)

    assert second["fees_complete"] is True
    queried = sorted(oid for call in connection.get_order_fees.call_args_list for oid in call.args[0])
    assert queried == ["NEW1", "OLD1", "OLD2", "OLD3"]
    assert all(fill["fees"] is not None for fill in database.get_fills(env="REAL", account_id=OPAQUE))


WEDNESDAY = date(2026, 10, 21)


def _cash_flow_broker(responder=None):
    connection = _broker()
    connection.get_cash_flow.side_effect = responder or (lambda clearing_date: [])
    return connection


def test_cash_flow_first_run_queries_recent_weekdays_descending(database):
    connection = _cash_flow_broker()

    result = FillsService(connection, database).refresh_cash_flows(
        history_start="2026-10-01", max_days=5, today=WEDNESDAY
    )

    queried = [call.args[0] for call in connection.get_cash_flow.call_args_list]
    assert queried == ["2026-10-21", "2026-10-20", "2026-10-19", "2026-10-16", "2026-10-15"]
    assert result == {
        "ok": True,
        "queried": 5,
        "ingested": 0,
        "earliest": "2026-10-15",
        "latest": "2026-10-21",
        "complete": False,
    }
    coverage = database.get_cash_flow_coverage("REAL", OPAQUE)
    assert coverage["status"] == "success"
    assert coverage["earliest"] == "2026-10-15"
    assert coverage["latest"] == "2026-10-21"


def test_cash_flow_second_run_continues_backward_and_repulls_forward(database):
    FillsService(_cash_flow_broker(), database).refresh_cash_flows(
        history_start="2026-10-01", max_days=5, today=WEDNESDAY
    )
    connection = _cash_flow_broker()

    result = FillsService(connection, database).refresh_cash_flows(
        history_start="2026-10-01", max_days=5, today=WEDNESDAY
    )

    queried = [call.args[0] for call in connection.get_cash_flow.call_args_list]
    assert queried == ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-14", "2026-10-13"]
    assert result["ok"] is True
    assert result["earliest"] == "2026-10-13"
    assert result["latest"] == "2026-10-21"


def test_cash_flow_failure_stops_pass_and_keeps_last_contiguous_success(database):
    def _responder(clearing_date):
        return None if clearing_date == "2026-10-19" else []

    result = FillsService(_cash_flow_broker(_responder), database).refresh_cash_flows(
        history_start="2026-10-01", max_days=5, today=WEDNESDAY
    )

    assert result["ok"] is False
    assert result["queried"] == 3
    assert result["earliest"] == "2026-10-20"
    assert result["latest"] == "2026-10-21"
    coverage = database.get_cash_flow_coverage("REAL", OPAQUE)
    assert coverage["status"] == "partial"
    assert coverage["reason"] == "cash_flow_query_failed"
    assert coverage["earliest"] == "2026-10-20"


def test_cash_flow_reaching_history_start_reports_complete(database):
    connection = _cash_flow_broker()

    result = FillsService(connection, database).refresh_cash_flows(
        history_start="2026-10-19", max_days=10, today=WEDNESDAY
    )

    assert result["ok"] is True
    assert result["earliest"] == "2026-10-19"
    assert result["complete"] is True


def test_cash_flow_forward_failure_skips_the_backward_pass(database):
    database.set_cash_flow_coverage(
        "REAL",
        OPAQUE,
        {
            "status": "success",
            "last_attempt_at": "2026-10-20T09:00:00",
            "earliest": "2026-10-19",
            "latest": "2026-10-20",
            "reason": "",
        },
    )
    connection = _cash_flow_broker(lambda clearing_date: None)

    result = FillsService(connection, database).refresh_cash_flows(
        history_start="2026-10-01", max_days=5, today=WEDNESDAY
    )

    queried = [call.args[0] for call in connection.get_cash_flow.call_args_list]
    assert queried == ["2026-10-19"]
    assert result["ok"] is False
    assert result["queried"] == 1
    coverage = database.get_cash_flow_coverage("REAL", OPAQUE)
    assert coverage["status"] == "partial"
    assert coverage["earliest"] == "2026-10-19"


def test_ingest_cash_flows_reports_failed_dates(database):
    row = {
        "cashflow_id": "CF1",
        "clearing_date": "2026-10-21",
        "currency": "USD",
        "cashflow_type": "DEPOSIT",
        "cashflow_direction": "IN",
        "cashflow_amount": 100.0,
        "cashflow_remark": "",
    }
    connection = _cash_flow_broker(lambda clearing_date: None if clearing_date == "2026-10-20" else [row])

    result = FillsService(connection, database).ingest_cash_flows(["2026-10-20", "2026-10-21"])

    assert result["ok"] is False
    assert result["failed_dates"] == 1
    assert result["seen"] == 1
    assert result["ingested"] == 1


def test_refresh_fill_evidence_reports_cash_flow_slice(database):
    result = refresh_fill_evidence(_broker(), database, history_start="2026-01-01")

    assert result["ok"] is True
    assert result["cash_flows"]["ok"] is True
    assert "complete" in result["cash_flows"]


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
