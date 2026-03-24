"""
Utility functions for the autotrader package
"""

import os
import glob
import logging
from datetime import datetime, timedelta, time as datetime_time
import math
import pytz

# Configure logger
logger = logging.getLogger('autotrader.utils')

def rotate_logs(logs_dir='logs', max_logs=5):
    """
    Rotate log files, keeping only the specified number of most recent logs.
    """
    log_files = glob.glob(os.path.join(logs_dir, 'trader_*.log'))
    if len(log_files) <= max_logs:
        return
    sorted_logs = sorted(log_files, key=os.path.getmtime, reverse=True)
    logs_to_delete = sorted_logs[max_logs:]
    for log_file in logs_to_delete:
        try:
            os.remove(log_file)
        except Exception as e:
            print(f"Error deleting log file {log_file}: {e}")

def rotate_reports(reports_dir='reports', max_reports=5):
    """
    Rotate HTML report files.
    """
    report_files = glob.glob(os.path.join(reports_dir, 'options_report_*.html'))
    if len(report_files) <= max_reports:
        return
    sorted_reports = sorted(report_files, key=os.path.getmtime, reverse=True)
    reports_to_delete = sorted_reports[max_reports:]
    for report_file in reports_to_delete:
        try:
            os.remove(report_file)
        except Exception as e:
            print(f"Error deleting report file {report_file}: {e}")

def setup_logging(logs_dir='logs', log_prefix='trader', log_level=logging.DEBUG):
    """
    Set up logging configuration
    """
    os.makedirs(logs_dir, exist_ok=True)
    rotate_logs(logs_dir=logs_dir, max_logs=5)
    
    log_file = os.path.join(logs_dir, f"{log_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    
    file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    
    file_handler.setFormatter(file_formatter)
    console_handler.setFormatter(console_formatter)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # Mute moomoo logs if necessary (similar to how IB logs were muted)
    # logging.getLogger('moomoo').setLevel(logging.WARNING)
    
    return logging.getLogger('autotrader')

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

def parse_date_string(date_str):
    return datetime.strptime(date_str, "%Y%m%d")

def format_date_string(date_obj):
    return date_obj.strftime("%Y%m%d")

def format_currency(value):
    if value is None or (isinstance(value, float) and math.isnan(value)): return "$0.00"
    return f"${value:.2f}"

def format_percentage(value):
    if value is None or (isinstance(value, float) and math.isnan(value)): return "0.00%"
    return f"{value:.2f}%"

def get_strikes_around_price(price, interval, num_strikes):
    nearest_strike_below = math.floor(price / interval) * interval
    strikes = []
    for i in range(num_strikes // 2, 0, -1): strikes.append(nearest_strike_below - (i * interval))
    strikes.append(nearest_strike_below)
    for i in range(1, num_strikes // 2 + 1): strikes.append(nearest_strike_below + (i * interval))
    return strikes

def is_market_hours(include_after_hours=False):
    eastern = pytz.timezone('US/Eastern')
    now = datetime.now(eastern)
    if now.weekday() >= 5: return False
    current_time = now.time()
    market_open = datetime_time(9, 30)
    market_close = datetime_time(16, 0)
    if market_open <= current_time <= market_close: return True
    if not include_after_hours: return False
    pre_market_open = datetime_time(4, 0)
    after_hours_close = datetime_time(20, 0)
    if pre_market_open <= current_time < market_open: return True
    if market_close < current_time <= after_hours_close: return True
    return False
