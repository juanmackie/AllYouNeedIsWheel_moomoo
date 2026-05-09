"""
Shared position-scoring logic for portfolio route endpoints.

Extracted from api/routes/portfolio.py to avoid duplication between
the roll-pressure and alerts endpoints.
"""

from datetime import datetime

from core.logging_config import get_logger
logger = get_logger('api.services.portfolio_scoring', 'api')


def build_portfolio_context(option_positions, portfolio_service):
    try:
        summary = portfolio_service.get_portfolio_summary()
        cash_balance = float(summary.get('available_cash', 0) or 0)
        account_value = float(summary.get('account_value', 0) or 0)
    except Exception:
        cash_balance = 0
        account_value = 0

    positions_map = {}
    for pos in option_positions:
        ticker = str(pos.get('symbol', '') or '').replace('US.', '')
        positions_map[ticker] = pos

    portfolio_context = {
        'positions': positions_map,
        'cash_balance': cash_balance,
        'account_value': account_value,
        'short_calls': {},
        'short_puts': {},
        'vix_regime': {'regime': 'normal', 'vix': 20.0},
    }

    for pos in option_positions:
        ticker = str(pos.get('symbol', '') or '').replace('US.', '')
        pos_qty = int(pos.get('position', 0) or 0)
        opt_type = str(pos.get('option_type', '') or '').upper()
        if pos_qty < 0:
            if opt_type == 'CALL':
                portfolio_context['short_calls'][ticker] = abs(pos_qty)
            elif opt_type == 'PUT':
                portfolio_context['short_puts'][ticker] = abs(pos_qty)

    return portfolio_context, cash_balance, account_value


def score_position(pos, conn, portfolio_context, iv_earnings_service):
    from core.wheel_decision import score_existing_position
    from api.services.macro_regime_service import get_macro_service

    ticker = str(pos.get('symbol', '') or '').replace('US.', '')
    option_type = str(pos.get('option_type', '') or '').upper()
    strike = float(pos.get('strike', 0) or 0)
    expiration = str(pos.get('expiration', '') or '')
    position_qty = int(pos.get('position', 0) or 0)

    if position_qty >= 0:
        return None

    try:
        current_price = conn.get_stock_price(ticker)
        if current_price is None or current_price <= 0:
            return None
    except Exception:
        return None

    try:
        exp_date = datetime.strptime(expiration, '%Y%m%d').date()
        dte = (exp_date - datetime.now().date()).days
    except (ValueError, TypeError):
        dte = 0

    bid = float(pos.get('bid', 0) or 0)
    ask = float(pos.get('ask', 0) or 0)
    mid_price = (
        (bid + ask) / 2 if bid > 0 and ask > 0
        else float(pos.get('market_price', 0) or 0)
    )

    pos_data = {
        'option_type': option_type,
        'strike': strike,
        'expiration': expiration,
        'dte': dte,
        'bid': bid,
        'ask': ask,
        'last': mid_price,
        'delta': float(pos.get('delta', 0) or 0),
        'theta': float(pos.get('theta', 0) or 0),
        'implied_volatility': float(pos.get('implied_volatility', 0) or 0),
    }

    iv = float(pos.get('implied_volatility', 0) or 0)
    if iv > 0:
        iv_earnings_service.record_iv_data(
            ticker, iv, current_price, option_type, expiration, dte
        )
    iv_env_adj, iv_rank, iv_status = (
        iv_earnings_service.get_iv_environment_score(
            ticker, iv if iv > 0 else 0.20
        )
    )
    earnings_adj, _ = iv_earnings_service.get_earnings_score_impact(ticker)
    macro = get_macro_service().get_macro_regime()

    decision = score_existing_position(
        ticker=ticker,
        position_data=pos_data,
        current_stock_price=current_price,
        portfolio_context=portfolio_context,
        iv_env_adjustment=iv_env_adj,
        iv_rank=iv_rank,
        iv_status_str=iv_status,
        earnings_adjustment=earnings_adj,
        macro_regime=macro,
    )

    return decision
