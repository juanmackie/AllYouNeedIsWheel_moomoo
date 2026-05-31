"""
Wheel Scan Ledger API routes — inspectable record of every scan.
"""

import logging
from flask import Blueprint, jsonify, current_app
from api.routes.utils import success_response, error_response
from core.scan_ledger import ScanLedger

logger = logging.getLogger(__name__)

bp = Blueprint('ledger', __name__, url_prefix='/api/ledger')


@bp.route('/scans')
def get_recent_scans():
    """Get recent scan ledger entries."""
    try:
        db = current_app.config.get('database')
        if not db:
            return error_response('Database not available', status_code=503)
        ledger = ScanLedger(db)
        entries = ledger.get_recent(limit=50)
        stats = ledger.get_stats()
        return success_response({
            'entries': entries,
            'count': len(entries),
            'stats': stats,
        })
    except Exception as e:
        logger.error(f"Error fetching ledger scans: {e}")
        return error_response(str(e))


@bp.route('/scans/<int:entry_id>')
def get_scan(entry_id: int):
    """Get a single scan ledger entry by ID."""
    try:
        db = current_app.config.get('database')
        if not db:
            return error_response('Database not available', status_code=503)
        ledger = ScanLedger(db)
        entry = ledger.get_by_id(entry_id)
        if not entry:
            return error_response('Scan entry not found', status_code=404)
        return success_response({'entry': entry})
    except Exception as e:
        logger.error(f"Error fetching scan entry {entry_id}: {e}")
        return error_response(str(e))


@bp.route('/scans/stats')
def get_scan_stats():
    """Get aggregate statistics for all scans."""
    try:
        db = current_app.config.get('database')
        if not db:
            return error_response('Database not available', status_code=503)
        ledger = ScanLedger(db)
        stats = ledger.get_stats()
        return success_response({'stats': stats})
    except Exception as e:
        logger.error(f"Error fetching scan stats: {e}")
        return error_response(str(e))
