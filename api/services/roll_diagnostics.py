"""Roll/hold/close diagnostics for actual option positions.

Lives in the api layer because it composes the portfolio scoring helper with an
IV/earnings service supplied by the app factory. ``WheelRunner`` receives this
as an injected ``roll_diagnostics_provider`` callable, so ``core`` never imports
``api`` and the service never resolves the composition root itself.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("api.services.roll_diagnostics")


def build_roll_decisions(portfolio_context, conn, fresh_candidates=None, *, iv_earnings):
    """Build per-position diagnostics, naming only fresh, copy-eligible targets.

    ``iv_earnings`` is injected by the app factory so this service never imports
    the composition root (``api``) or resolves services itself.
    """
    try:
        option_positions = []
        positions = portfolio_context.get("positions", {}) or {}
        for pos in positions.values():
            if str(pos.get("security_type", "") or "").upper() == "OPT":
                option_positions.append(pos)
        if not option_positions:
            return []
        from api.services.portfolio_scoring import score_position

        ctx = dict(portfolio_context)
        profile = ctx.get("exit_profile") or {}
        ctx.update(profile)
        ctx["exit_roll_dte"] = profile.get("roll_dte", 21)
        decisions = []
        for pos in option_positions:
            decision = score_position(pos, conn, ctx, iv_earnings, fresh_candidates)
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
                    "exit_verdict": decision.exit_verdict,
                    "exit_reasons": decision.exit_reasons,
                    "roll_target": decision.roll_target,
                    "warnings": decision.warnings,
                    "bid": decision.bid,
                    "ask": decision.ask,
                    "quantity": abs(float(pos.get("position", 0) or 0)),
                    "exit_reason_codes": getattr(decision, "exit_reason_codes", []),
                    "wheel_decision": decision.to_dict(),
                }
            )
        return decisions
    except Exception as exc:
        logger.warning(f"Roll diagnostics unavailable: {exc}")
        return []
