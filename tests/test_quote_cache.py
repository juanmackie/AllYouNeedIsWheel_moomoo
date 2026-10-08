from datetime import datetime
from zoneinfo import ZoneInfo

from core.quote_cache import OptionChainCache
from core.session_clock import SessionCalendar

ET = ZoneInfo("America/New_York")


class FrozenDateTime(datetime):
    frozen = datetime(2026, 9, 30, 17, 0, tzinfo=ET)

    @classmethod
    def now(cls, tz=None):
        return cls.frozen if tz else cls.frozen.replace(tzinfo=None)


def test_after_hours_reuses_only_quotes_captured_after_regular_close(monkeypatch):
    monkeypatch.setattr(FrozenDateTime, "frozen", datetime(2026, 9, 30, 17, 0, tzinfo=ET))
    calendar = SessionCalendar.from_trading_days(["2026-09-30", "2026-10-01"])
    cache = OptionChainCache(
        session_calendar=calendar,
        clock=lambda: FrozenDateTime.frozen,
        timestamp_clock=lambda: FrozenDateTime.frozen.timestamp(),
    )
    cached = {"options": [{"bid": 1.0}]}
    cache._chain_cache["US.AAPL_20261016_P"] = (cached, datetime(2026, 9, 30, 16, 1, tzinfo=ET).timestamp())

    assert cache.get_option_chain("US.AAPL", "20261016", "P") is cached

    cache._chain_cache["US.MSFT_20261016_P"] = (
        cached,
        datetime(2026, 9, 30, 15, 59, tzinfo=ET).timestamp(),
    )
    assert cache.get_option_chain("US.MSFT", "20261016", "P") is None


def test_range_cache_reuses_after_close_and_invalidates_during_open(monkeypatch):
    monkeypatch.setattr(FrozenDateTime, "frozen", datetime(2026, 9, 30, 17, 0, tzinfo=ET))
    calendar = SessionCalendar.from_trading_days(["2026-09-30", "2026-10-01"])
    cache = OptionChainCache(
        session_calendar=calendar,
        clock=lambda: FrozenDateTime.frozen,
        timestamp_clock=lambda: FrozenDateTime.frozen.timestamp(),
    )
    band = {"min_strike": 10.0, "max_strike": 20.0}
    payload = {"options": []}
    cache.cache_option_chain_range("US.AAPL", "P", "2026-10-14", "2026-10-30", band, payload)
    key = ("US.AAPL", "P", "2026-10-14", "2026-10-30", 10.0, 20.0)
    cache._range_chain_cache[key] = (payload, datetime(2026, 9, 30, 16, 1, tzinfo=ET).timestamp())

    assert cache.get_option_chain_range("US.AAPL", "P", "2026-10-14", "2026-10-30", band) is payload

    monkeypatch.setattr(FrozenDateTime, "frozen", datetime(2026, 10, 1, 10, 0, tzinfo=ET))
    cache._range_chain_cache[key] = (payload, datetime(2026, 10, 1, 9, 0, tzinfo=ET).timestamp())
    assert cache.get_option_chain_range("US.AAPL", "P", "2026-10-14", "2026-10-30", band) is None
    cache._range_chain_cache[key] = (payload, datetime(2026, 10, 1, 11, 0, tzinfo=ET).timestamp())
    assert cache.get_option_chain_range("US.AAPL", "P", "2026-10-14", "2026-10-30", band) is None
