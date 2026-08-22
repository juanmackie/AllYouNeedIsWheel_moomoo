"""
Utility functions for the autotrader package
"""

import logging
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Configure logger
logger = logging.getLogger("autotrader.utils")


def get_closest_friday():
    today = datetime.now().date()
    weekday = today.weekday()
    if weekday < 4:
        days_to_add = 4 - weekday
    elif weekday == 4:
        days_to_add = 0
    else:
        days_to_add = 4 + (7 - weekday)
    return today + timedelta(days=days_to_add)


def get_next_monthly_expiration():
    today = datetime.now().date()
    year, month = today.year, today.month
    first_day = datetime(year, month, 1).date()
    weekday = first_day.weekday()
    days_to_add = 4 - weekday if weekday < 4 else 4 + (7 - weekday)
    first_friday = first_day + timedelta(days=days_to_add)
    third_friday = first_friday + timedelta(days=14)

    if third_friday < today:
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
        first_day = datetime(year, month, 1).date()
        weekday = first_day.weekday()
        days_to_add = 4 - weekday if weekday < 4 else 4 + (7 - weekday)
        first_friday = first_day + timedelta(days=days_to_add)
        third_friday = first_friday + timedelta(days=14)
    return third_friday.strftime("%Y%m%d")


MARKET_TIMEZONE = ZoneInfo("America/New_York")


_last_market_status = None


def is_market_open() -> bool:
    """Return True if US equity markets are currently open (9:30 AM - 4:00 PM ET, Mon-Fri)."""
    global _last_market_status
    now = datetime.now(MARKET_TIMEZONE)
    if now.weekday() >= 5:
        result = False
    else:
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        result = market_open <= now <= market_close
    if _last_market_status is None or _last_market_status != result:
        logger.info("Market %s at %s ET", "OPEN" if result else "CLOSED", now.strftime("%H:%M"))
        _last_market_status = result
    return result


def market_now():
    """Return current datetime in US market timezone."""
    return datetime.now(MARKET_TIMEZONE)


def entry_window_advice(now: datetime | None = None) -> dict:
    """Intraday entry-quality guidance for placing option tickets.

    Pure function of an ET clock (defaults to now). Deterministic so tests
    pass fixed datetimes. Returns quality tier + a short actionable message:

        closed  — outside RTH; stage tickets for the open instead
        poor    — first 15 minutes (spread blowout at the bell)
        caution — last 30 minutes (MOC flows / pinning risk)
        fair    — midday lull (thinner volume, wider fills possible)
        good    — mid-session (deepest liquidity, tightest spreads)
    """
    now = market_now() if now is None else now
    weekday = now.weekday()
    if weekday >= 5:
        return {
            "quality": "closed",
            "message": "Weekend — markets closed. Stage limit tickets for the next US session.",
        }

    open_dt = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_dt = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if now < open_dt:
        return {
            "quality": "closed",
            "message": "Pre-market — stage limit tickets now, place after 9:35 AM ET when spreads settle.",
        }
    if now > close_dt:
        return {
            "quality": "closed",
            "message": "After hours — review signals and stage tickets for tomorrow's open.",
        }

    minutes_since_open = (now - open_dt).total_seconds() / 60.0
    minutes_to_close = (close_dt - now).total_seconds() / 60.0
    if minutes_since_open <= 15:
        return {
            "quality": "poor",
            "message": "First 15 minutes — opening volatility blows out spreads; wait until ~9:45 AM ET.",
        }
    if minutes_to_close <= 30:
        return {
            "quality": "caution",
            "message": "Final 30 minutes — MOC flows and pinning risk; prefer rolling entries to tomorrow.",
        }
    if now.hour in (11, 13) or (now.hour == 12 and now.minute <= 59):
        return {
            "quality": "fair",
            "message": "Midday lull — volume thinner and fills can slip; use midpoint limits.",
        }
    return {
        "quality": "good",
        "message": "Mid-session — deepest liquidity and tightest spreads; good window to enter.",
    }


def format_currency(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "$0.00"
    return f"${value:.2f}"


def format_percentage(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "0.00%"
    return f"{value:.2f}%"
