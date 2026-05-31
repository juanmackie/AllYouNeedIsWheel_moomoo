"""
Earnings Routes
Earnings lock and locked tickers endpoints.
"""

import logging
from flask import Blueprint, request
from api.routes.utils import error_response, success_response
from api import get_service

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
        service = get_service('ivearnings')
        db = service.db
        
        # Get all tickers with earnings within lock_days
        locked = db.get_pending_earnings(days_threshold=lock_days)
        
        if not locked:
            return success_response({
                'locked': [],
                'count': 0,
                'lock_days': lock_days,
            })
        
        from datetime import datetime
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        locked_list = []
        for item in locked:
            earnings_date = item.get('earnings_date')
            days_to = None
            if earnings_date:
                try:
                    ed = datetime.strptime(earnings_date[:10], '%Y-%m-%d')
                    days_to = (ed - today).days
                except Exception:
                    pass
            locked_list.append({
                'ticker': item.get('ticker'),
                'earnings_date': earnings_date,
                'days_to_earnings': days_to,
                'time_of_day': item.get('time_of_day'),
                'fiscal_date_ending': item.get('fiscal_date_ending'),
                'estimate': item.get('estimate'),
                'currency': item.get('currency'),
                'earnings_source': item.get('earnings_source'),
            })
        
        return success_response({
            'locked': locked_list,
            'count': len(locked_list),
            'lock_days': lock_days,
        })
        
    except Exception as e:
        logger.error(f"Error getting locked tickers: {e}")
        return error_response(str(e))


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
        
        return success_response({
            'lock_days': lock_days,
            'enabled': True,
        })
        
    except Exception as e:
        logger.error(f"Error getting lock status: {e}")
        return error_response(str(e))


@bp.route('/vol-signals')
def get_earnings_vol_signals():
    """
    Get read-only earnings volatility signals for the configured watchlist.

    These signals are educational/research labels only. They do not stage or
    execute trades.
    """
    limit = request.args.get('limit', 8, type=int)
    refresh = request.args.get('refresh', 'false').lower() == 'true'
    tickers_param = request.args.get('tickers', '')

    try:
        tickers = None
        if tickers_param:
            tickers = [ticker.strip().upper() for ticker in tickers_param.split(',') if ticker.strip()]

        service = get_service('earnings_vol')
        return success_response(service.get_signals(tickers=tickers, limit=limit, refresh=refresh))

    except Exception as e:
        logger.error(f"Error getting earnings vol signals: {e}")
        return error_response(str(e))
