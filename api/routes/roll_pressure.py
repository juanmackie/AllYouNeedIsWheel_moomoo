"""
Roll-pressure analysis route.

Extracted from api/routes/portfolio.py (F008).
"""

from flask import Blueprint, jsonify
from datetime import datetime
import traceback

from api.routes.utils import error_response, success_response
from api.services.portfolio_scoring import build_portfolio_context, score_position
from core.ticker_utils import earnings_underlying_ticker

from core.logging_config import get_logger
logger = get_logger('api.routes.roll_pressure', 'api')

bp = Blueprint('roll_pressure', __name__, url_prefix='/api/portfolio')


def _get_portfolio_service():
    import api
    return api.get_service('portfolio')


def _ensure_opend_available():
    from flask import current_app
    from core.connection import probe_opend_status
    connection_config = current_app.config.get('connection_config', {})
    status = probe_opend_status(
        host=connection_config.get('host', '127.0.0.1'),
        port=connection_config.get('port', 11111)
    )
    if status.get('status') == 'connected':
        return None
    error_code = (
        'opend_login_required' if status.get('status') == 'login_required'
        else 'opend_unavailable'
    )
    return error_response(
        status.get('message', 'OpenD is unavailable.'),
        status_code=503,
        error_code=error_code,
        opend_status=status
    )


@bp.route('/roll-pressure', methods=['GET'])
def get_roll_pressure():
    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response

        positions = _get_portfolio_service().get_positions('OPT')
        if positions is None:
            return error_response('Failed to load positions', status_code=500)

        option_positions = (
            positions if isinstance(positions, list)
            else positions.get('positions', [])
        )
        if not option_positions:
            return success_response({
                'positions': [], 'count': 0,
                'generated_at': datetime.now().isoformat()
            })

        import api
        ps = _get_portfolio_service()
        portfolio_context, _, _ = build_portfolio_context(option_positions, ps)
        conn = ps._ensure_connection()
        iv_earnings_service = api.get_service('ivearnings')

        scored_positions = []
        for pos in option_positions:
            decision = score_position(pos, conn, portfolio_context, iv_earnings_service)
            if decision is None:
                continue

            raw_symbol = pos.get('symbol') or decision.ticker
            option_code = decision.ticker or raw_symbol
            underlying = earnings_underlying_ticker(raw_symbol or option_code)

            scored_positions.append({
                'ticker': decision.ticker,
                'symbol': raw_symbol,
                'underlying': underlying,
                'position': pos.get('position', 0),
                'option_type': decision.option_type,
                'strike': decision.strike,
                'expiration': decision.expiration,
                'dte': decision.dte,
                'stock_price': decision.stock_price,
                'bid': decision.bid,
                'ask': decision.ask,
                'mid_price': decision.mid_price,
                'implied_volatility': decision.implied_volatility,
                'delta': decision.delta,
                'roll_pressure': decision.roll_pressure,
                'extrinsic_remaining': decision.extrinsic_remaining,
                'profit_target_progress': decision.profit_target_progress,
                'otm_pct': decision.otm_pct,
                'size_fit': decision.size_fit,
                'expected_move_buffer': decision.expected_move_buffer,
                'warnings': decision.warnings,
                'wheel_decision': decision.to_dict(),
            })

        scored_positions.sort(key=lambda x: x['roll_pressure'], reverse=True)

        return success_response({
            'positions': scored_positions,
            'count': len(scored_positions),
            'generated_at': datetime.now().isoformat()
        })

    except Exception as e:
        logger.error(f"Error getting roll pressure: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
