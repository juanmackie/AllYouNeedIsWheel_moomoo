"""
AutoTrader Core Module
"""

# Import tools and utilities
# Import logging configuration
from .logging_config import configure_logging, get_logger
from .utils import format_currency, format_percentage, get_closest_friday, get_next_monthly_expiration

__all__ = [
    # Connection
    "MoomooConnection",
    # Utils
    "get_closest_friday",
    "get_next_monthly_expiration",
    "format_currency",
    "format_percentage",
    # Logging
    "configure_logging",
    "get_logger",
]


def __getattr__(name):
    if name == "MoomooConnection":
        from .connection import MoomooConnection

        return MoomooConnection
    raise AttributeError(f"module 'core' has no attribute {name!r}")
