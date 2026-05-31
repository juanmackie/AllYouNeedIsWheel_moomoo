"""
Wheel Risk Panel — surface concentration, cash reserved, earnings exposure,
ticker/sector concentration, and macro/VIX pressure for the dashboard.

All computations are stateless given a portfolio_context.
"""

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def compute_wheel_risk(portfolio_context: dict, config: dict | None = None) -> dict:
    """
    Compute all risk panel metrics from a portfolio context.

    Args:
        portfolio_context: dict from PortfolioContext.get_portfolio_context()
        config: optional app config for thresholds

    Returns:
        dict with risk metrics for the wheel risk panel
    """
    positions = portfolio_context.get("positions", {})
    short_calls: dict[str, int] = portfolio_context.get("short_calls", {})
    short_puts: dict[str, int] = portfolio_context.get("short_puts", {})
    cash_balance = float(portfolio_context.get("cash_balance", 0) or 0)
    account_value = float(portfolio_context.get("account_value", 0) or 0)
    cash_reserved = float(portfolio_context.get("cash_reserved_for_csp", 0) or 0)
    cash_available = float(portfolio_context.get("cash_available_for_csp", 0) or 0)
    broker_buying_power = float(portfolio_context.get("broker_buying_power", 0) or 0)
    vix_regime = portfolio_context.get("vix_regime", {}) or {}
    macro_regime = portfolio_context.get("macro_regime", {})

    if account_value <= 0:
        account_value = max(cash_balance, 1)

    # ── Concentration ──────────────────────────────────────────────────
    position_values = {}
    total_stock_value = 0.0
    for ticker, pos in positions.items():
        qty = float(pos.get("position", 0) or 0)
        price = float(pos.get("market_price", 0) or pos.get("avg_cost", 0) or 0)
        value = qty * price
        if value > 0:
            position_values[ticker] = value
            total_stock_value += value

    concentration = {}
    for ticker, val in sorted(position_values.items(), key=lambda x: x[1], reverse=True):
        pct = (val / account_value * 100) if account_value > 0 else 0
        concentration[ticker] = {
            "value": round(val, 2),
            "pct_of_account": round(pct, 1),
            "warning": pct > 20,
        }

    top_concentration = sum(
        v["pct_of_account"] for v in sorted(concentration.values(), key=lambda x: x["pct_of_account"], reverse=True)[:3]
    )

    # ── Cash / CSP exposure ────────────────────────────────────────────
    csp_exposure = {
        "cash_balance": round(cash_balance, 2),
        "account_value": round(account_value, 2),
        "cash_reserved_for_csp": round(cash_reserved, 2),
        "cash_available_for_csp": round(cash_available, 2),
        "broker_buying_power": round(broker_buying_power, 2),
        "csp_cash_ratio": round((cash_reserved / account_value * 100), 1) if account_value > 0 else 0,
        "free_cash_ratio": round((cash_available / account_value * 100), 1) if account_value > 0 else 0,
        "warning_cash_low": cash_available < account_value * 0.05,
        "warning_overallocated": (cash_reserved / account_value * 100) > 50 if account_value > 0 else False,
    }

    # ── Covered call exposure ──────────────────────────────────────────
    cc_exposure = []
    for ticker, contracts in short_calls.items():
        pos = positions.get(ticker, {})
        shares_owned = abs(float(pos.get("position", 0) or 0))
        capped_value = contracts * 100 * float(pos.get("market_price", pos.get("avg_cost", 0) or 0))
        total_position_value = position_values.get(ticker, 0)
        upside_capped_pct = (capped_value / total_position_value * 100) if total_position_value > 0 else 0
        cc_exposure.append({
            "ticker": ticker,
            "contracts": contracts,
            "shares_owned": int(shares_owned),
            "coverage_ratio": round((contracts * 100) / max(shares_owned, 1) * 100, 1),
            "upside_capped_value": round(capped_value, 2),
            "upside_capped_pct": round(upside_capped_pct, 1),
        })

    # ── Earnings exposure ──────────────────────────────────────────────
    earnings_exposure = portfolio_context.get("earnings_exposure", {
        "tickers_at_risk": [],
        "count": 0,
    })

    # ── VIX / Macro pressure ───────────────────────────────────────────
    vix_level = float(vix_regime.get("vix", 20.0) if isinstance(vix_regime, dict) else 20.0)
    vix_regime_name = str(vix_regime.get("regime", "normal") if isinstance(vix_regime, dict) else "normal")

    macro_pressure = {
        "vix_level": vix_level,
        "vix_regime": vix_regime_name,
        "vix_pressure": _classify_vix_pressure(vix_level),
        "total_short_options": sum(short_calls.values()) + sum(short_puts.values()),
    }

    if isinstance(macro_regime, dict):
        macro_pressure["macro_multiplier"] = macro_regime.get("macro_multiplier", 1.0)
        macro_pressure["credit_stress"] = macro_regime.get("credit_stress", "unknown")
        macro_pressure["rate_regime"] = macro_regime.get("rate_regime", "unknown")

    # ── Summary ────────────────────────────────────────────────────────
    warnings = []
    if csp_exposure.get("warning_overallocated"):
        warnings.append("More than 50% of account value reserved for CSPs")
    if csp_exposure.get("warning_cash_low"):
        warnings.append("Less than 5% of account value available as free cash")
    for t, c in concentration.items():
        if c.get("warning"):
            warnings.append(f"{t} concentration at {c['pct_of_account']:.0f}% of account")
    if macro_pressure.get("vix_pressure") == "high":
        warnings.append("VIX is elevated — reduce position sizes")
    if cc_exposure and any(c["coverage_ratio"] > 80 for c in cc_exposure):
        warnings.append("Covered calls cap >80% of shares — limited upside")
    if earnings_exposure["count"] > 0:
        warnings.append(f"{earnings_exposure['count']} open option(s) have upcoming earnings dates")

    return {
        "concentration": {
            "by_ticker": concentration,
            "top_3_concentration_pct": round(top_concentration, 1),
            "total_stock_value": round(total_stock_value, 2),
        },
        "csp_exposure": csp_exposure,
        "covered_call_exposure": cc_exposure,
        "earnings_exposure": earnings_exposure,
        "macro_pressure": macro_pressure,
        "warnings": warnings,
        "generated_at": datetime.now().isoformat(),
    }


def _classify_vix_pressure(vix: float) -> str:
    if vix < 12:
        return "low"
    if vix < 20:
        return "normal"
    if vix < 30:
        return "elevated"
    return "high"
