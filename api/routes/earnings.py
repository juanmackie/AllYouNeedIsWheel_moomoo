"""
Earnings Routes
Earnings lock and locked tickers endpoints.
"""

import logging
from flask import Blueprint, request, jsonify
from api.services.iv_earnings_service import IVEarningsService
from db.database import OptionsDatabase

logger = logging.getLogger(__name__)

bp = Blueprint('earnings', __name__, url_prefix='/api/earnings')


@bp.route('/locked-tickers')
def get_locked_tickers():
    """
    Get tickers currently locked due to upcoming earnings (within lock_days).
    
    Query Parameters:
        lock_days: Number of days to consider (default: 5)
        
    Returns:
        JSON with locked tickers and their earnings dates.
    """
    lock_days = request.args.get('lock_days', 5, type=int)
    
    try:
        db = OptionsDatabase()
        service = IVEarningsService(db)
        
        # Get all tickers with earnings within lock_days
        locked = db.get_pending_earnings(days_threshold=lock_days)
        
        if not locked:
            return jsonify({
                'success': True,
                'locked': [],
                'count': 0,
                'lock_days': lock_days,
            })
        
        # Format the response
        locked_list = []
        for item in locked:
            locked_list.append({
                'ticker': item.get('ticker'),
                'earnings_date': item.get('earnings_date'),
                'days_to_earnings': item.get('days_to_earnings'),
            })
        
        return jsonify({
            'success': True,
            'locked': locked_list,
            'count': len(locked_list),
            'lock_days': lock_days,
        })
        
    except Exception as e:
        logger.error(f"Error getting locked tickers: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@bp.route('/lock-status')
def get_lock_status():
    """
    Get the current earnings lock status and configuration.
    
    Returns:
        JSON with lock configuration and status.
    """
    try:
        # Get lock_days from config (default 5)
        from config import DEFAULT_CONNECTION_CONFIG
        lock_days = DEFAULT_CONNECTION_CONFIG.get('earnings_lock_days', 5)
        
        return jsonify({
            'success': True,
            'lock_days': lock_days,
            'enabled': True,  # Can be toggled via frontend
        })
        
    except Exception as e:
        logger.error(f"Error getting lock status: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
