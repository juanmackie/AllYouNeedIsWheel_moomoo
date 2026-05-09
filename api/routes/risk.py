"""
Risk Sizing Routes
Exposes ATR-based position sizing via REST API.
"""

import logging
from flask import Blueprint, request, jsonify
from api.routes.utils import error_response, success_response
from api.services.risk_sizing_service import get_risk_sizing_service

logger = logging.getLogger(__name__)

bp = Blueprint('risk', __name__, url_prefix='/api/risk')


@bp.route('/sizing')
def get_sizing():
    """
    Get ATR-based position sizing for a ticker.

    Query Parameters:
        ticker: Stock symbol (e.g., AAPL)
        account_value: Total account value (default: 45000)
        risk_pct: Risk percentage (default: 0.01 = 1%)
        atr_period: ATR period (default: 14)

    Returns:
        JSON with sizing breakdown.
    """
    ticker = request.args.get('ticker', '').strip().upper()
    if not ticker:
        return error_response('Missing required parameter: ticker', status_code=400)

    try:
        account_value = float(request.args.get('account_value', 45000))
        risk_pct = float(request.args.get('risk_pct', 0.01))
        atr_period = int(request.args.get('atr_period', 14))

        service = get_risk_sizing_service()
        result = service.calculate_position_size(
            ticker=ticker,
            account_value=account_value,
            risk_pct=risk_pct,
            atr_period=atr_period
        )

        return success_response({
            'data': result,
        })

    except Exception as e:
        logger.error(f"Error calculating position size for {ticker}: {e}")
        return error_response(str(e))


@bp.route('/sizing/batch', methods=['POST'])
def get_batch_sizing():
    """
    Get ATR-based position sizing for multiple tickers.

    POST Body:
        { "tickers": ["AAPL", "MSFT"], "account_value": 45000, "risk_pct": 0.01 }

    Returns:
        JSON with sizing for each ticker.
    """
    try:
        data = request.get_json()
        tickers = data.get('tickers', [])
        account_value = float(data.get('account_value', 45000))
        risk_pct = float(data.get('risk_pct', 0.01))

        if not tickers:
            return error_response('No tickers provided', status_code=400)

        service = get_risk_sizing_service()
        results = {}

        for ticker in tickers:
            try:
                result = service.calculate_position_size(
                    ticker=ticker.strip().upper(),
                    account_value=account_value,
                    risk_pct=risk_pct
                )
                results[ticker] = result
            except Exception as e:
                logger.error(f"Error calculating size for {ticker}: {e}")
                results[ticker] = {'error': str(e)}

        return success_response({
            'data': results,
        })

    except Exception as e:
        logger.error(f"Error in batch sizing: {e}")
        return error_response(str(e))


@bp.route('/sizing/cache/clear', methods=['POST'])
def clear_sizing_cache():
    """Clear the risk sizing cache."""
    try:
        service = get_risk_sizing_service()
        service.clear_cache()
        return success_response({
            'message': 'Risk sizing cache cleared'
        })
    except Exception as e:
        logger.error(f"Error clearing sizing cache: {e}")
        return error_response(str(e))
