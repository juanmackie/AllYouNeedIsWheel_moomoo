"""
AutoTrader Core Module
"""

# Import tools and utilities
from .utils import (
    get_closest_friday, 
    get_next_monthly_expiration,
    format_currency,
    format_percentage
)

# Import logging configuration
from .logging_config import configure_logging, get_logger

# Import connection classes
from .connection import MoomooConnection

__all__ = [
    # Connection
    'MoomooConnection',
    
    # Utils
    'get_closest_friday',
    'get_next_monthly_expiration',
    'format_currency',
    'format_percentage',
    
    # Logging
    'configure_logging',
    'get_logger'
]
