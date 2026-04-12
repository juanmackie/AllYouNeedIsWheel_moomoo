"""
Portfolio API routes
"""

from flask import Blueprint, request, jsonify, current_app
from api.services.portfolio_service import PortfolioService
from api.services.macro_regime_service import get_macro_service
from core.connection import probe_opend_status
import traceback
from datetime import datetime

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
    Get the current portfolio information with macro regime context.
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

        # Add macro regime context to portfolio response
        try:
            macro_service = get_macro_service()
            macro_regime = macro_service.get_macro_regime()
            results['macro_context'] = {
                'rate_regime': macro_regime.get('rate_regime', 'unknown'),
                'credit_stress': macro_regime.get('credit_stress', 'unknown'),
                'growth_regime': macro_regime.get('growth_regime', 'unknown'),
                'inflation_trend': macro_regime.get('inflation_trend', 'unknown'),
                'yield_curve_slope': macro_regime.get('yield_curve_slope', 0),
                'macro_multiplier': macro_regime.get('macro_multiplier', 1.0),
                'summary': macro_regime.get('summary', ''),
                'advice': macro_regime.get('advice', ''),
                'enabled': macro_regime.get('enabled', False),
            }
        except Exception as macro_err:
            results['macro_context'] = {
                'enabled': False,
                'summary': 'Macro detection unavailable',
                'advice': 'Check FRED API configuration',
                'error': str(macro_err)
            }

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


@bp.route('/roll-pressure', methods=['GET'])
def get_roll_pressure():
    """
    Get roll-pressure analysis for all open option positions.

    Returns positions ranked by roll_pressure (0-100), which combines:
    - DTE remaining (higher urgency as DTE shrinks)
    - % distance to strike (higher urgency as price approaches strike)
    - Extrinsic value remaining (higher urgency when extrinsic is low)

    Each position includes the full WheelDecision snapshot for display.

    Response:
    {
        "positions": [
            {
                "ticker": "AAPL",
                "option_type": "PUT",
                "strike": 180.0,
                "expiration": "20240510",
                "dte": 5,
                "roll_pressure": 75.3,
                "extrinsic_remaining": 0.45,
                "profit_target_progress": 85.0,
                "stock_price": 182.5,
                "otm_pct": 1.4,
                "warnings": ["Only 5 DTE remaining", "High roll pressure (75%)"],
                "wheel_decision": {...}
            },
            ...
        ],
        "count": N,
        "generated_at": "ISO timestamp"
    }
    """
    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response

        # Get options service for position data and scoring
        from api.services.options_service import OptionsService
        from core.wheel_decision import score_existing_position
        from api.services.iv_earnings_service import IVEarningsService
        from api.services.macro_regime_service import get_macro_service
        from db.database import OptionsDatabase

        options_service = OptionsService()
        conn = options_service._ensure_connection()
        if not conn:
            return jsonify({'error': 'Failed to establish connection to moomoo'}), 503

        # Get option positions
        positions = portfolio_service.get_positions('OPT')
        if positions is None:
            return jsonify({'error': 'Failed to load positions'}), 500

        option_positions = positions.get('positions', [])
        if not option_positions:
            return jsonify({
                'positions': [],
                'count': 0,
                'generated_at': datetime.now().isoformat()
            })

        # Build portfolio context for scoring
        try:
            summary = portfolio_service.get_portfolio_summary()
            cash_balance = float(summary.get('cash_balance', 0) or 0)
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

        # Build short puts/calls map
        for pos in option_positions:
            ticker = str(pos.get('symbol', '') or '').replace('US.', '')
            pos_qty = int(pos.get('position', 0) or 0)
            opt_type = str(pos.get('option_type', '') or '').upper()
            if pos_qty < 0:
                if opt_type == 'CALL':
                    portfolio_context['short_calls'][ticker] = abs(pos_qty)
                elif opt_type == 'PUT':
                    portfolio_context['short_puts'][ticker] = abs(pos_qty)

        # Score each position
        iv_earnings_service = IVEarningsService(OptionsDatabase(options_service.config.get('db_path')))
        scored_positions = []

        for pos in option_positions:
            ticker = str(pos.get('symbol', '') or '').replace('US.', '')
            option_type = str(pos.get('option_type', '') or '').upper()
            strike = float(pos.get('strike', 0) or 0)
            expiration = str(pos.get('expiration', '') or '')
            position_qty = int(pos.get('position', 0) or 0)

            # Only analyze short options (the ones we sold)
            if position_qty >= 0:
                continue

            # Get current market data
            try:
                current_price = conn.get_stock_price(ticker)
                if current_price is None or current_price <= 0:
                    current_price = options_service._get_fallback_stock_price(portfolio_context, ticker)
                if current_price is None or current_price <= 0:
                    continue
            except Exception:
                continue

            # Calculate DTE
            try:
                exp_date = datetime.strptime(expiration, '%Y%m%d').date()
                dte = (exp_date - datetime.now().date()).days
            except (ValueError, TypeError):
                dte = 0

            # Get current option price (approximate)
            bid = float(pos.get('bid', 0) or 0)
            ask = float(pos.get('ask', 0) or 0)
            mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else float(pos.get('market_price', 0) or 0)

            # Build position data for scorer
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

            # Get IV context
            iv = float(pos.get('implied_volatility', 0) or 0)
            if iv > 0:
                iv_earnings_service.record_iv_data(
                    ticker, iv, current_price, option_type, expiration, dte
                )
            iv_env_adj, iv_rank, iv_status = iv_earnings_service.get_iv_environment_score(
                ticker, iv if iv > 0 else 0.20
            )
            earnings_adj, _ = iv_earnings_service.get_earnings_score_impact(ticker)
            macro = get_macro_service().get_macro_regime()

            # Score the position
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

            scored_positions.append({
                'ticker': decision.ticker,
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

        # Sort by roll_pressure descending (most urgent first)
        scored_positions.sort(key=lambda x: x['roll_pressure'], reverse=True)

        return jsonify({
            'positions': scored_positions,
            'count': len(scored_positions),
            'generated_at': datetime.now().isoformat()
        })

    except Exception as e:
        logger.error(f"Error getting roll pressure: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@bp.route('/alerts', methods=['GET'])
def get_position_alerts():
    """
    Get alert conditions based on WheelDecision thresholds for all open positions.

    Alert conditions:
    - roll_pressure >= 70: "URGENT: High roll pressure"
    - roll_pressure >= 40: "WATCH: Moderate roll pressure"
    - profit_target_progress >= 50: "TARGET: 50% profit reached"
    - hard_blockers present: "BLOCKED: [reason]"
    - otm_pct < 0: "DANGER: Strike crossed"

    Returns:
    {
        "alerts": [
            {
                "ticker": "AAPL",
                "option_type": "PUT",
                "strike": 180.0,
                "alert_type": "roll_pressure_urgent",
                "severity": "urgent",  # urgent | warning | info | danger
                "message": "High roll pressure (75%)",
                "wheel_decision": {...}
            },
            ...
        ],
        "count": N
    }
    """
    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response

        # Reuse the roll-pressure logic
        from api.services.options_service import OptionsService
        from core.wheel_decision import score_existing_position
        from api.services.iv_earnings_service import IVEarningsService
        from db.database import OptionsDatabase

        options_service = OptionsService()
        conn = options_service._ensure_connection()
        if not conn:
            return jsonify({'error': 'Failed to establish connection to moomoo'}), 503

        positions = portfolio_service.get_positions('OPT')
        if positions is None:
            return jsonify({'error': 'Failed to load positions'}), 500

        option_positions = positions.get('positions', [])
        if not option_positions:
            return jsonify({'alerts': [], 'count': 0})

        # Build portfolio context
        try:
            summary = portfolio_service.get_portfolio_summary()
            cash_balance = float(summary.get('cash_balance', 0) or 0)
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

        iv_earnings_service = IVEarningsService(OptionsDatabase(options_service.config.get('db_path')))
        alerts = []

        for pos in option_positions:
            ticker = str(pos.get('symbol', '') or '').replace('US.', '')
            option_type = str(pos.get('option_type', '') or '').upper()
            strike = float(pos.get('strike', 0) or 0)
            expiration = str(pos.get('expiration', '') or '')
            position_qty = int(pos.get('position', 0) or 0)

            if position_qty >= 0:
                continue

            try:
                current_price = conn.get_stock_price(ticker)
                if current_price is None or current_price <= 0:
                    current_price = options_service._get_fallback_stock_price(portfolio_context, ticker)
                if current_price is None or current_price <= 0:
                    continue
            except Exception:
                continue

            try:
                exp_date = datetime.strptime(expiration, '%Y%m%d').date()
                dte = (exp_date - datetime.now().date()).days
            except (ValueError, TypeError):
                dte = 0

            bid = float(pos.get('bid', 0) or 0)
            ask = float(pos.get('ask', 0) or 0)
            mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else float(pos.get('market_price', 0) or 0)

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
                iv_earnings_service.record_iv_data(ticker, iv, current_price, option_type, expiration, dte)
            iv_env_adj, iv_rank, iv_status = iv_earnings_service.get_iv_environment_score(ticker, iv if iv > 0 else 0.20)
            earnings_adj, _ = iv_earnings_service.get_earnings_score_impact(ticker)
            from api.services.macro_regime_service import get_macro_service
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

            # Generate alerts based on WheelDecision thresholds
            if decision.roll_pressure >= 70:
                alerts.append({
                    'ticker': decision.ticker,
                    'option_type': decision.option_type,
                    'strike': decision.strike,
                    'expiration': decision.expiration,
                    'alert_type': 'roll_pressure_urgent',
                    'severity': 'urgent',
                    'message': f'High roll pressure ({decision.roll_pressure:.0f}%)',
                    'wheel_decision': decision.to_dict(),
                })
            elif decision.roll_pressure >= 40:
                alerts.append({
                    'ticker': decision.ticker,
                    'option_type': decision.option_type,
                    'strike': decision.strike,
                    'expiration': decision.expiration,
                    'alert_type': 'roll_pressure_watch',
                    'severity': 'warning',
                    'message': f'Moderate roll pressure ({decision.roll_pressure:.0f}%)',
                    'wheel_decision': decision.to_dict(),
                })

            if decision.profit_target_progress >= 50:
                alerts.append({
                    'ticker': decision.ticker,
                    'option_type': decision.option_type,
                    'strike': decision.strike,
                    'expiration': decision.expiration,
                    'alert_type': 'profit_target_50',
                    'severity': 'info',
                    'message': f'50% profit target reached ({decision.profit_target_progress:.0f}%)',
                    'wheel_decision': decision.to_dict(),
                })

            if decision.otm_pct < 0:
                alerts.append({
                    'ticker': decision.ticker,
                    'option_type': decision.option_type,
                    'strike': decision.strike,
                    'expiration': decision.expiration,
                    'alert_type': 'strike_crossed',
                    'severity': 'danger',
                    'message': f'Strike crossed ({abs(decision.otm_pct):.1f}% ITM)',
                    'wheel_decision': decision.to_dict(),
                })

            for blocker in decision.hard_blockers:
                alerts.append({
                    'ticker': decision.ticker,
                    'option_type': decision.option_type,
                    'strike': decision.strike,
                    'expiration': decision.expiration,
                    'alert_type': 'hard_blocker',
                    'severity': 'danger',
                    'message': f'Blocked: {blocker}',
                    'wheel_decision': decision.to_dict(),
                })

        # Sort by severity: danger > urgent > warning > info
        severity_order = {'danger': 0, 'urgent': 1, 'warning': 2, 'info': 3}
        alerts.sort(key=lambda x: severity_order.get(x['severity'], 4))

        return jsonify({'alerts': alerts, 'count': len(alerts)})

    except Exception as e:
        logger.error(f"Error getting position alerts: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
