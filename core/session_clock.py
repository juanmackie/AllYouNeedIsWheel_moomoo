"""Pure US market-session calendar helpers.

OpenD trading-day rows can be supplied when available. Without them the
calendar uses weekday-only regular hours and reports that fallback explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

MARKET_TIMEZONE = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _as_time(value: Any) -> time | None:
    if isinstance(value, time):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return time.fromisoformat(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class SessionCalendar:
    """Trading sessions, optional early closes, and the evidence source."""

    trading_days: frozenset[date] | None = None
    early_closes: dict[date, time] = field(default_factory=dict)
    source: str = "weekday_fallback"

    @classmethod
    def weekday_fallback(cls) -> SessionCalendar:
        return cls()

    @classmethod
    def from_trading_days(cls, rows: Iterable[Any] | None) -> SessionCalendar:
        sessions: set[date] = set()
        early_closes: dict[date, time] = {}
        for row in rows or []:
            if isinstance(row, dict):
                session_date = next(
                    (_as_date(row.get(key)) for key in ("trade_date", "trading_day", "date") if row.get(key)),
                    None,
                )
                close = next(
                    (_as_time(row.get(key)) for key in ("close_time", "market_close", "close") if row.get(key)),
                    None,
                )
            else:
                session_date = _as_date(row)
                close = None
            if session_date is None:
                continue
            sessions.add(session_date)
            if close is not None and close < REGULAR_CLOSE:
                early_closes[session_date] = close
        if not sessions:
            return cls.weekday_fallback()
        return cls(frozenset(sessions), early_closes, "moomoo_request_trading_days")

    def is_trading_day(self, day: date) -> bool:
        if self.trading_days is not None:
            return day in self.trading_days
        return day.weekday() < 5

    def regular_open(self, day: date) -> time:
        return REGULAR_OPEN

    def regular_close(self, day: date) -> time:
        return self.early_closes.get(day, REGULAR_CLOSE)

    def is_open(self, now: datetime) -> bool:
        market_now = now.replace(tzinfo=MARKET_TIMEZONE) if now.tzinfo is None else now.astimezone(MARKET_TIMEZONE)
        day = market_now.date()
        if not self.is_trading_day(day):
            return False
        return self.regular_open(day) <= market_now.time().replace(tzinfo=None) <= self.regular_close(day)

    def last_regular_close(self, now: datetime) -> datetime | None:
        market_now = now.replace(tzinfo=MARKET_TIMEZONE) if now.tzinfo is None else now.astimezone(MARKET_TIMEZONE)
        start = market_now.date()
        for days_back in range(0, 370):
            day = start - timedelta(days=days_back)
            if not self.is_trading_day(day):
                continue
            close = datetime.combine(day, self.regular_close(day), tzinfo=MARKET_TIMEZONE)
            if close <= market_now:
                return close
        return None

    def next_regular_open(self, now: datetime) -> datetime | None:
        """Return the next scheduled US regular-session open after ``now``."""
        market_now = now.replace(tzinfo=MARKET_TIMEZONE) if now.tzinfo is None else now.astimezone(MARKET_TIMEZONE)
        for days_ahead in range(0, 371):
            day = market_now.date() + timedelta(days=days_ahead)
            if not self.is_trading_day(day):
                continue
            opening = datetime.combine(day, self.regular_open(day), tzinfo=MARKET_TIMEZONE)
            if opening > market_now:
                return opening
        return None
