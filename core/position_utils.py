"""
Centralized utilities for parsing Moomoo position data.
Eliminates duplicated symbol stripping and quantity parsing across the codebase.
"""


def parse_moomoo_symbol(raw_symbol: str) -> str:
    """
    Strip the 'US.' prefix from Moomoo symbols to get the canonical ticker.

    Args:
        raw_symbol: The raw symbol from Moomoo (e.g., 'US.AAPL' or 'AAPL')

    Returns:
        The canonical ticker symbol (e.g., 'AAPL')
    """
    if not raw_symbol:
        return ""
    return str(raw_symbol).replace("US.", "").strip()


def parse_position_qty(qty_value) -> int:
    """
    Safely parse a position quantity to an integer.

    Args:
        qty_value: The quantity value (can be string, float, int, or None)

    Returns:
        The quantity as an integer (0 if invalid)
    """
    if qty_value is None:
        return 0
    try:
        return int(float(qty_value))
    except (TypeError, ValueError):
        return 0
