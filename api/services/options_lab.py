"""
Options Lab — payoff/scenario analysis for a selected contract.

Provides:
  - Breakeven price
  - Assignment cost
  - Expected move buffer
  - IV crush sensitivity
  - Max contracts (cash/shares constrained)
  - Stress loss (worst-case drawdown)
  - Roll comparison (current vs hypothetical roll)
"""

import math
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def compute_options_lab(contract: dict, portfolio_context: dict) -> dict:
    """
    Compute all Options Lab metrics for a single contract.

    Args:
        contract: dict with strike, expiration, bid, ask, last, delta, gamma,
                  theta, vega, implied_volatility, option_type, dte, stock_price,
                  open_interest, volume, mid_price
        portfolio_context: dict with positions, cash_balance, account_value, etc.

    Returns:
        dict with all scenario metrics
    """
    option_type = str(contract.get("option_type", "")).upper()
    strike = float(contract.get("strike", 0) or 0)
    expiration = str(contract.get("expiration", "") or "")
    stock_price = float(contract.get("stock_price", 0) or 0)
    mid_price = float(contract.get("mid_price", 0) or 0)
    bid = float(contract.get("bid", 0) or 0)
    ask = float(contract.get("ask", 0) or 0)
    iv = float(contract.get("implied_volatility", 0) or 0)
    delta = float(contract.get("delta", 0) or 0)
    gamma = float(contract.get("gamma", 0) or 0)
    theta = float(contract.get("theta", 0) or 0)
    vega = float(contract.get("vega", 0) or 0)
    dte = int(contract.get("dte", 0) or 0)
    premium = mid_price * 100

    result = {
        "breakeven": _compute_breakeven(stock_price, strike, mid_price, option_type),
        "assignment_cost": _compute_assignment_cost(strike, option_type),
        "expected_move_buffer": _compute_expected_move_buffer(stock_price, iv, dte, strike, option_type),
        "max_loss": _compute_max_loss(strike, mid_price, option_type),
        "max_profit": _compute_max_profit(strike, mid_price, stock_price, option_type),
        "iv_crush_sensitivity": _compute_iv_crush_sensitivity(stock_price, strike, iv, dte, vega, option_type),
        "stress_loss": _compute_stress_loss(stock_price, strike, premium, delta, option_type),
        "max_contracts": _compute_max_contracts(strike, premium, option_type, portfolio_context),
        "cash_required": _compute_cash_required(strike, option_type),
        "return_if_unchanged": _compute_return_if_unchanged(premium, strike, option_type, dte),
        "return_if_assigned": _compute_return_if_assigned(stock_price, strike, mid_price, option_type),
        "pop": _compute_pop(delta, option_type),
        "greeks": {
            "delta": round(delta, 4),
            "gamma": round(gamma, 4),
            "theta": round(theta, 4),
            "vega": round(vega, 4),
        },
        "parameters": {
            "stock_price": stock_price,
            "strike": strike,
            "mid_price": mid_price,
            "premium": round(premium, 2),
            "iv": round(iv, 4),
            "dte": dte,
            "expiration": expiration,
            "option_type": option_type,
        },
    }

    return result


def _compute_breakeven(stock_price: float, strike: float, mid_price: float, option_type: str) -> float:
    if option_type == "CALL":
        return round(strike + mid_price, 2)
    return round(strike - mid_price, 2)


def _compute_assignment_cost(strike: float, option_type: str) -> float:
    if option_type == "PUT":
        return round(strike * 100, 2)
    return 0.0


def _compute_expected_move_buffer(stock_price: float, iv: float, dte: int, strike: float, option_type: str) -> float:
    if stock_price <= 0 or iv <= 0 or dte <= 0:
        return 0.0
    iv_norm = iv / 100.0 if iv > 3.0 else iv
    expected_move = stock_price * iv_norm * math.sqrt(dte / 365.0)
    expected_move_pct = (expected_move / stock_price) * 100.0
    if option_type == "PUT":
        otm_pct = max(0, (stock_price - strike) / stock_price) * 100.0
    else:
        otm_pct = max(0, (strike - stock_price) / stock_price) * 100.0
    return round(otm_pct - expected_move_pct, 2)


def _compute_max_loss(strike: float, mid_price: float, option_type: str) -> float:
    if option_type == "PUT":
        return round((strike - mid_price) * 100, 2)
    return round(mid_price * 100, 2)


def _compute_max_profit(strike: float, mid_price: float, stock_price: float, option_type: str) -> float:
    if option_type == "PUT":
        return round(mid_price * 100, 2)
    upside = max(0, strike - stock_price) if option_type == "CALL" else 0
    return round((mid_price + upside) * 100, 2)


def _compute_iv_crush_sensitivity(stock_price: float, strike: float, iv: float, dte: int, vega: float, option_type: str) -> dict:
    if stock_price <= 0 or strike <= 0 or dte <= 0:
        return {"crush_1pt": 0.0, "crush_2pt": 0.0, "new_iv": 0.0, "new_premium": 0.0}
    if iv <= 0:
        return {"crush_1pt": 0.0, "crush_2pt": 0.0, "new_iv": 0.0, "new_premium": 0.0}
    vega_abs = abs(vega)
    crush_1pt = round(vega_abs * 100, 2)
    crush_2pt = round(vega_abs * 200, 2)
    new_iv_1 = round(iv - 0.01, 4)
    new_iv_2 = round(iv - 0.02, 4)
    return {
        "crush_1pt": crush_1pt,
        "crush_2pt": crush_2pt,
        "new_iv_1pt": max(new_iv_1, 0.0),
        "new_iv_2pt": max(new_iv_2, 0.0),
        "premium_loss_1pt": round(crush_1pt, 2),
        "premium_loss_2pt": round(crush_2pt, 2),
    }


def _compute_stress_loss(stock_price: float, strike: float, premium: float, delta: float, option_type: str) -> float:
    if option_type == "PUT":
        return round((strike - stock_price * 0.8) * 100 - premium, 2)
    return round(premium, 2)


def _compute_max_contracts(strike: float, premium: float, option_type: str, portfolio_context: dict) -> dict:
    account_value = float(portfolio_context.get("account_value", 0) or 0)
    cash_balance = float(portfolio_context.get("cash_balance", 0) or 0)
    available_cash = float(portfolio_context.get("available_cash", cash_balance) or cash_balance)
    broker_buying_power = float(portfolio_context.get("broker_buying_power", available_cash) or available_cash)

    if option_type == "PUT":
        cash_req = strike * 100
        if cash_req <= 0:
            return {"by_cash": 0, "by_risk": 0, "recommended": 0}
        by_cash = int(broker_buying_power // cash_req) if cash_req > 0 else 0
        by_risk = max(1, int((account_value * 0.10) // cash_req)) if cash_req > 0 else 1
        return {
            "by_cash": max(by_cash, 0),
            "by_risk": max(by_risk, 1),
            "recommended": max(min(by_cash, by_risk), 0),
        }
    return {"by_cash": 0, "by_risk": 0, "recommended": 0}


def _compute_cash_required(strike: float, option_type: str) -> float:
    if option_type == "PUT":
        return round(strike * 100, 2)
    return 0.0


def _compute_return_if_unchanged(premium: float, strike: float, option_type: str, dte: int) -> dict:
    if option_type == "PUT" and strike > 0 and dte > 0:
        annualized = (premium / (strike * 100)) * (365 / dte) * 100
        return {"return_pct": round((premium / (strike * 100)) * 100, 2), "annualized_pct": round(annualized, 2)}
    return {"return_pct": 0.0, "annualized_pct": 0.0}


def _compute_return_if_assigned(stock_price: float, strike: float, mid_price: float, option_type: str) -> dict:
    if option_type == "PUT" and stock_price > 0:
        cost_basis = strike - mid_price
        if_put = (mid_price / (strike * 100)) * 100
        return {"cost_basis": round(cost_basis, 2), "return_pct": round(if_put, 2)}
    if option_type == "CALL" and stock_price > 0:
        if_called = (((strike - stock_price) + mid_price) / stock_price) * 100
        return {"strike_gain_pct": round(((strike - stock_price) / stock_price) * 100, 2), "total_return_pct": round(if_called, 2)}
    return {}


def _compute_pop(delta: float, option_type: str) -> float:
    if option_type == "PUT":
        return round((1 - abs(delta)) * 100, 1)
    return round(abs(delta) * 100, 1)


def compute_roll_comparison(current_contract: dict, roll_contract: dict) -> dict:
    """
    Compare a roll from current_contract to roll_contract.
    """
    current = compute_options_lab(current_contract, {})
    rolled = compute_options_lab(roll_contract, {})

    current_premium = current.get("parameters", {}).get("premium", 0)
    rolled_premium = rolled.get("parameters", {}).get("premium", 0)
    current_dte = current.get("parameters", {}).get("dte", 0)
    rolled_dte = rolled.get("parameters", {}).get("dte", 0)

    premium_diff = round(rolled_premium - current_premium, 2)
    dte_diff = rolled_dte - current_dte

    return {
        "current": current,
        "roll_target": rolled,
        "premium_difference": premium_diff,
        "dte_difference": dte_diff,
        "net_credit": premium_diff > 0,
        "net_debit": premium_diff < 0,
        "recommendation": (
            "Roll forward for additional premium"
            if premium_diff > 0 else
            "Roll is a net debit — consider waiting"
            if premium_diff < -50 else
            "Premium-neutral roll — primarily for DTE extension"
        ),
    }
