"""Roll/hold/close diagnostics for actual option positions.

Lives in the api layer because it composes registered services (portfolio,
ivearnings) with portfolio scoring helpers. ``WheelRunner`` receives this as an
injected ``roll_diagnostics_provider`` callable, so ``core`` never imports
``api``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("api.services.roll_diagnostics")


def build_roll_decisions(portfolio_context, conn):
    """Build per-position roll diagnostics; never raises."""
    try:
        option_positions = []
        positions = portfolio_context.get("positions", {}) or {}
        for pos in positions.values():
            if str(pos.get("security_type", "") or "").upper() == "OPT":
                option_positions.append(pos)
        if not option_positions:
            return []
        from api import get_service
        from api.services.portfolio_scoring import build_portfolio_context, score_position

        ps = get_service("portfolio")
        ctx, _, _ = build_portfolio_context(option_positions, ps)
        iv_earnings = get_service("ivearnings")
        decisions = []
        for pos in option_positions:
            decision = score_position(pos, conn, ctx, iv_earnings)
            if decision is None:
                continue
            decisions.append(
                {
                    "ticker": decision.ticker,
                    "option_type": decision.option_type,
                    "strike": decision.strike,
                    "expiration": decision.expiration,
                    "dte": decision.dte,
                    "roll_pressure": decision.roll_pressure,
                    "profit_target_progress": decision.profit_target_progress,
                    "otm_pct": decision.otm_pct,
                    "extrinsic_remaining": decision.extrinsic_remaining,
                    "warnings": decision.warnings,
                    "wheel_decision": decision.to_dict(),
                }
            )
        return decisions
    except Exception as exc:
        logger.warning(f"Roll diagnostics unavailable: {exc}")
        return []
