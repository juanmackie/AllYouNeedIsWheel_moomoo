"""
Display-unit normalisation for Greeks, IV, and related metrics.

Centralises formatting so every consumer renders 0.30 IV → "30.0%",
delta -0.25 → "-0.25 / 25%", etc.
"""


def format_iv_decimal(iv: float) -> str:
    return f"{iv * 100:.1f}%"


def format_iv_rank(rank: float) -> str:
    return f"{rank * 100:.0f}%"


def format_delta(delta: float) -> str:
    return f"{delta:.2f} / {abs(delta) * 100:.0f}%"


def format_currency(value: float) -> str:
    return f"${value:,.2f}"
