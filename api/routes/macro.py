"""
Macro Regime API Routes
Provides macro economic regime data for scoring context and dashboard display.
"""

from flask import Blueprint, jsonify
from api.services.macro_regime_service import get_macro_service

bp = Blueprint('macro', __name__, url_prefix='/api/macro')


@bp.route('/regime', methods=['GET'])
def get_macro_regime():
    """
    Get current macro economic regime.

    Returns:
        dict: Macro regime detection results including:
            - rate_regime: 'rising' | 'stable' | 'falling'
            - credit_stress: 'low' | 'moderate' | 'high'
            - growth_regime: 'expansion' | 'slowdown' | 'contraction'
            - inflation_trend: 'rising' | 'stable' | 'falling'
            - yield_curve_slope: 10y-2y spread
            - macro_multiplier: 0.80 | 0.90 | 1.0 | 1.05
            - summary: Human-readable regime description
            - advice: Actionable wheel strategy guidance
    """
    try:
        macro_service = get_macro_service()
        regime = macro_service.get_macro_regime()

        return jsonify(regime), 200
    except Exception as e:
        return jsonify({
            'error': str(e),
            'enabled': False,
            'macro_multiplier': 1.0,
            'summary': 'Error detecting macro regime',
            'advice': 'Check server logs for details'
        }), 500


@bp.route('/cache/status', methods=['GET'])
def get_cache_status():
    """
    Get macro cache status for monitoring.

    Returns:
        dict: Cache status including age and TTL
    """
    try:
        macro_service = get_macro_service()
        status = macro_service.get_cache_status()
        return jsonify(status), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/cache/clear', methods=['POST'])
def clear_cache():
    """
    Clear macro cache (forces fresh FRED fetch on next request).

    Returns:
        dict: Success message
    """
    try:
        macro_service = get_macro_service()
        macro_service.clear_cache()
        return jsonify({'message': 'Macro cache cleared'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
