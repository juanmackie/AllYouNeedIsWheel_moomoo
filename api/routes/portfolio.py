"""
Portfolio API routes
"""

from flask import Blueprint, request, jsonify, current_app
from api.services.portfolio_service import PortfolioService
from core.connection import probe_opend_status

bp = Blueprint('portfolio', __name__, url_prefix='/api/portfolio')
portfolio_service = PortfolioService()


def _is_real_account_unavailable(message):
    if not message:
        return False

    return (
        'requested REAL account' in message
        or 'No available real accounts' in message
        or 'Nonexisting acc_id' in message
    )


def _service_unavailable_response(message, fallback_message):
    error_message = message or fallback_message
    payload = {'error': error_message}

    if _is_real_account_unavailable(error_message):
        payload['error_code'] = 'real_account_unavailable'
        payload['opend_status'] = {
            'status': 'real_account_unavailable',
            'message': error_message
        }

    return jsonify(payload), 503


def _ensure_opend_available():
    connection_config = current_app.config.get('connection_config', {})
    status = probe_opend_status(
        host=connection_config.get('host', '127.0.0.1'),
        port=connection_config.get('port', 11111)
    )
    if status.get('status') == 'connected':
        return None

    error_code = 'opend_login_required' if status.get('status') == 'login_required' else 'opend_unavailable'
    return jsonify({
        'error': status.get('message', 'OpenD is unavailable.'),
        'error_code': error_code,
        'opend_status': status
    }), 503

@bp.route('/', methods=['GET'])
def get_portfolio():
    """
    Get the current portfolio information
    """
    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response

        results = portfolio_service.get_portfolio_summary()
        if results is None:
            return _service_unavailable_response(
                portfolio_service.last_error,
                'Failed to load portfolio summary'
            )
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/positions', methods=['GET'])
def get_positions():
    """
    Get the current portfolio positions
    
    Query Parameters:
        type: Filter by position type (STK, OPT). If not provided, returns all positions.
    """
    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response

        # Get the position_type from query parameters
        position_type = request.args.get('type')
        # Validate position_type
        if position_type and position_type not in ['STK', 'OPT']:
            return jsonify({'error': 'Invalid position type. Supported types: STK, OPT'}), 400
            
        results = portfolio_service.get_positions(position_type)
        if results is None:
            return _service_unavailable_response(
                portfolio_service.last_error,
                'Failed to load positions'
            )
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/weekly-income', methods=['GET'])
def get_weekly_income():
    """
    Get weekly option income from short options expiring this Friday.
    
    Returns:
        A JSON response containing weekly option income data:
        {
            "positions": [
                {
                    "symbol": "NVDA",
                    "option_type": "P", 
                    "strike": 850.0,
                    "expiration": "20240510",
                    "position": 10,
                    "avg_cost": 15.5,
                    "current_price": 15.5,
                    "income": 155.0
                },
                ...
            ],
            "total_income": 155.0,
            "positions_count": 1,
            "this_friday": "20240510"
        }
        
        Error response:
        {
            "error": "Error message",
            "positions": [],
            "total_income": 0,
            "positions_count": 0
        }
    """
    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response

        results = portfolio_service.get_weekly_option_income()
        
        if 'error' in results:
            payload = {
                'error': results['error'],
                'positions': [],
                'total_income': 0,
                'positions_count': 0
            }
            if _is_real_account_unavailable(results['error']):
                payload['error_code'] = 'real_account_unavailable'
                payload['opend_status'] = {
                    'status': 'real_account_unavailable',
                    'message': results['error']
                }
                return jsonify(payload), 503
            return jsonify(payload), 500
        
        return jsonify(results), 200
    except Exception as e:
        return jsonify({
            'error': str(e),
            'positions': [],
            'total_income': 0,
            'positions_count': 0
        }), 500
