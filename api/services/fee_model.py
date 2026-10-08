"""Account-scoped measured option fee estimates for exit diagnostics."""

from __future__ import annotations

import logging
from datetime import timedelta

from core.measured_fee_model import estimate_us_option_fees
from core.utils import market_now

logger = logging.getLogger("api.services.fee_model")

FEE_LOOKBACK_DAYS = 90


def get_measured_option_fee_model(database, env: str, account_id: str, lookback_days: int = FEE_LOOKBACK_DAYS) -> dict:
    """Read recent account-scoped fills and estimate median fees per order.

    The service never queries a prospective fee schedule. Missing or failed
    fill reads return an unknown model so callers can suppress net-credit
    comparisons rather than assuming zero fees.
    """
    days = max(1, int(lookback_days))
    start = (market_now().date() - timedelta(days=days)).isoformat()
    try:
        fills = database.get_fills(
            env=env,
            account_id=account_id,
            security_type="OPT",
            start=start,
            limit=10000,
        )
    except Exception as exc:
        logger.warning("Measured option fee history unavailable: %s", type(exc).__name__)
        fills = []
    return estimate_us_option_fees(fills if isinstance(fills, list) else [], lookback_days=days)
