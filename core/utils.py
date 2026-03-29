"""
Utility functions for the autotrader package
"""

import os
import logging
from datetime import datetime, timedelta
import math

# Configure logger
logger = logging.getLogger('autotrader.utils')

def get_closest_friday():
    today = datetime.now().date()
    weekday = today.weekday()
    if weekday < 4: days_to_add = 4 - weekday
    elif weekday == 4: days_to_add = 0
    else: days_to_add = 4 + (7 - weekday)
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
        if month == 12: year += 1; month = 1
        else: month += 1
        first_day = datetime(year, month, 1).date()
        weekday = first_day.weekday()
        days_to_add = 4 - weekday if weekday < 4 else 4 + (7 - weekday)
        first_friday = first_day + timedelta(days=days_to_add)
        third_friday = first_friday + timedelta(days=14)
    return third_friday.strftime('%Y%m%d')

def format_currency(value):
    if value is None or (isinstance(value, float) and math.isnan(value)): return "$0.00"
    return f"${value:.2f}"

def format_percentage(value):
    if value is None or (isinstance(value, float) and math.isnan(value)): return "0.00%"
    return f"{value:.2f}%"
