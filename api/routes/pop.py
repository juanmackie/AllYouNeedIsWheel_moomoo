"""
Probability of Profit API Routes
"""

import logging
from flask import Blueprint, request, jsonify
from api.services.pop_service import get_pop, calculate_pop_delta

logger = logging.getLogger(__name__)

bp = Blueprint('pop', __name__, url_prefix='/api/pop')


@bp.route('/estimate')
def estimate_pop():
    """
    Get Probability of Profit estimate.

    Query Parameters:
        ticker: Stock symbol
        strike: Option strike price
        expiration: Expiration (YYYYMMDD)
        type: 'CALL' or 'PUT'
        delta: Option delta
        iv: Implied volatility
        dte: Days to expiration
        method: 'delta' or 'monte_carlo'

    Returns:
        JSON with PoP estimate.
    """
    ticker = request.args.get('ticker', '').strip().upper()
    strike = request.args.get('strike', type=float)
    expiration = request.args.get('expiration', '').strip()
    option_type = request.args.get('type', '').strip().upper()
    delta = request.args.get('delta', type=float)
    iv = request.args.get('iv', type=float)
    dte = request.args.get('dte', type=int)
    method = request.args.get('method', 'delta').strip().lower()

    if not ticker or not strike or not expiration or not option_type:
        return jsonify({'success': False, 'error': 'Missing required parameters'}), 400

    try:
        result = get_pop(ticker, strike, expiration, option_type, delta, iv, dte, method)
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        logger.error(f"Error estimating PoP for {ticker}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
