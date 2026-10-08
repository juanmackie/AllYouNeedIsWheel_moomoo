from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from core.session_clock import SessionCalendar

ET = ZoneInfo("America/New_York")


def test_weekday_fallback_reports_its_source_and_regular_hours():
    calendar = SessionCalendar.weekday_fallback()

    assert calendar.source == "weekday_fallback"
    assert not calendar.is_open(datetime(2026, 9, 30, 9, 29, tzinfo=ET))
    assert calendar.is_open(datetime(2026, 9, 30, 9, 30, tzinfo=ET))
    assert calendar.is_open(datetime(2026, 9, 30, 16, 0, tzinfo=ET))
    assert not calendar.is_open(datetime(2026, 9, 30, 16, 1, tzinfo=ET))
    assert not calendar.is_open(datetime(2026, 10, 3, 12, 0, tzinfo=ET))


def test_trading_day_rows_cover_holidays_and_half_days():
    calendar = SessionCalendar.from_trading_days(
        [
            {"trade_date": "2026-11-27", "close_time": "13:00"},
        ]
    )

    assert calendar.source == "moomoo_request_trading_days"
    assert not calendar.is_trading_day(date(2026, 11, 26))
    assert calendar.is_trading_day(date(2026, 11, 27))
    assert not calendar.is_open(datetime(2026, 11, 27, 13, 1, tzinfo=ET))
    assert calendar.regular_close(date(2026, 11, 27)) == time(13, 0)


def test_last_regular_close_uses_the_latest_known_session():
    calendar = SessionCalendar.from_trading_days(["2026-09-28", "2026-09-29", "2026-09-30"])

    assert calendar.last_regular_close(datetime(2026, 10, 1, 8, 0, tzinfo=ET)) == datetime(
        2026, 9, 30, 16, 0, tzinfo=ET
    )


def test_next_regular_open_skips_holiday_and_weekend_using_provider_sessions():
    calendar = SessionCalendar.from_trading_days(["2026-11-27", "2026-11-30"])

    assert calendar.next_regular_open(datetime(2026, 11, 26, 12, 0, tzinfo=ET)) == datetime(
        2026, 11, 27, 9, 30, tzinfo=ET
    )
    assert calendar.next_regular_open(datetime(2026, 11, 27, 10, 0, tzinfo=ET)) == datetime(
        2026, 11, 30, 9, 30, tzinfo=ET
    )


def test_next_regular_open_labels_weekday_fallback_at_the_call_site():
    calendar = SessionCalendar.weekday_fallback()

    assert calendar.next_regular_open(datetime(2026, 9, 30, 8, 0, tzinfo=ET)) == datetime(2026, 9, 30, 9, 30, tzinfo=ET)
