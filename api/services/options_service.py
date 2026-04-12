"""
Options Service module
Handles options data retrieval and processing
"""

import logging
import math
import time
import threading
from datetime import datetime, timedelta, time as datetime_time
import pandas as pd
from moomoo import RET_OK
from core.connection import MoomooConnection
from core.cache_manager import recommendation_cache, RecommendationCache
from core.wheel_decision import (
    score_contract,
    score_existing_position,
    WheelDecision,
)
from core.utils import get_closest_friday, get_next_monthly_expiration
from config import Config
from db.database import OptionsDatabase
from api.services.iv_earnings_service import IVEarningsService
from api.services.openbb_service import get_openbb_service
from api.services.macro_regime_service import get_macro_service
import traceback

logger = logging.getLogger('api.services.options')

class OptionsService:
    """
    Service for handling options data operations
    """
    def __init__(self):
        self.config = Config()
        self.connection = None
        db_path = self.config.get('db_path')
        self.db = OptionsDatabase(db_path)
        self.iv_earnings_service = IVEarningsService(self.db)
        self.portfolio_service = None
        self._openbb_service = None
        
    def _get_openbb_service(self):
        """
        Lazy initialization of OpenBB service.
        Returns the service if available, None otherwise.
        Uses a sentinel (False) to avoid repeated failed initialization attempts.
        """
        if self._openbb_service is None:
            try:
                self._openbb_service = get_openbb_service()
                if self._openbb_service and self._openbb_service._ensure_initialized():
                    logger.info("OpenBB service initialized successfully")
                else:
                    logger.debug("OpenBB service not available, skipping quality checks")
                    self._openbb_service = False
            except Exception as e:
                logger.debug(f"OpenBB service initialization failed: {e}, skipping quality checks")
                self._openbb_service = False
        return self._openbb_service if self._openbb_service else None
        
    def _ensure_connection(self):
        """
        Ensure that the moomoo connection exists and is connected.
        Reuses existing connection if already established.
        """
        try:
            if self.connection is not None and self.connection.is_connected():
                logger.debug("Reusing existing moomoo connection")
                return self.connection

            if self.connection is not None:
                logger.info("Existing connection found but disconnected, attempting to reconnect")
                if self.connection.connect():
                    logger.info("Successfully reconnected to moomoo OpenD")
                    return self.connection
                else:
                    logger.warning("Failed to reconnect, will create new connection")

            logger.info("Creating new moomoo connection")

            self.connection = MoomooConnection(
                host=str(self.config.get('host', '127.0.0.1')),
                port=int(self.config.get('port', 11111)),
                readonly=bool(self.config.get('readonly', True)),
                account_id=self.config.get('account_id'),
                security_firm=self.config.get('security_firm')
            )

            if not self.connection.connect():
                logger.error("Failed to connect to moomoo OpenD")
                return None
            else:
                logger.info("Successfully connected to moomoo OpenD")
                if self.portfolio_service is not None:
                    self.portfolio_service.connection = self.connection
                return self.connection
        except Exception as e:
            logger.error(f"Error ensuring connection: {str(e)}")
            return None
        
    def _adjust_to_standard_strike(self, price):
        """
        Adjust a price to a standard strike price
        """
        return round(price)
      
    def execute_order(self, order_id, db):
        """
        Execute an order by sending it to moomoo
        """
        logger.info(f"Executing order with ID {order_id}")
        
        try:
            # Try to get the order first to ensure it exists
            order = db.get_order(order_id)
            if not order:
                logger.error(f"Order with ID {order_id} not found")
                return {
                    "success": False,
                    "error": f"Order with ID {order_id} not found"
                }, 404
                
            # Check if order is in executable state
            if order['status'] != 'pending':
                logger.error(f"Cannot execute order with status '{order['status']}'")
                return {
                    "success": False,
                    "error": f"Cannot execute order with status '{order['status']}'. Only 'pending' orders can be executed."
                }, 400
                
            # Get connection to moomoo
            conn = self._ensure_connection()
            if not conn:
                logger.error("Failed to connect to moomoo")
                return {
                    "success": False,
                    "error": "Failed to connect to moomoo"
                }, 500
                
            ticker = order.get('ticker')
            quantity = int(order.get('quantity', 0))
            action = order.get('action')
            
            # Extract option details
            expiry = order.get('expiration')
            strike = order.get('strike')
            option_type = order.get('option_type')
            
            if not all([expiry, strike, option_type]):
                return {
                    "success": False,
                    "error": "Missing option details (expiry, strike, or option_type)"
                }, 400
            
            # Find the moomoo option code
            option_code = conn.create_option_contract(ticker, expiry, strike, option_type)
            if not option_code:
                return {
                    "success": False,
                    "error": f"Failed to find moomoo option code for {ticker} {expiry} {strike} {option_type}"
                }, 400

            # Calculate limit price (similar logic as before but adapted)
            bid = float(order.get('bid', 0) or 0)
            ask = float(order.get('ask', 0) or 0)
            last = float(order.get('last', 0) or 0)

            if bid > 0 and ask > 0:
                limit_price = (bid + ask) / 2
            elif bid > 0:
                limit_price = bid
            elif last > 0:
                limit_price = last
            else:
                limit_price = 0.05

            limit_price = round(limit_price, 2)

            # Place order
            result = conn.place_order(option_code, quantity, action, limit_price)
            
            if not result:
                return {
                    "success": False,
                    "error": "Failed to place order in moomoo"
                }, 500

            logger.info(f"Order placed successfully in moomoo: {result}")

            # Update order status in database
            execution_details = {
                "moomoo_order_id": result.get('order_id'),
                "moomoo_status": result.get('status'),
                "filled": result.get('filled'),
                "remaining": result.get('remaining'),
                "avg_fill_price": result.get('avg_fill_price'),
                "limit_price": limit_price,
            }
            
            db.update_order_status(
                order_id=order_id,
                status="processing",
                executed=True,
                execution_details=execution_details
            )

            # Auto-capture lifecycle event
            try:
                is_rollover = bool(order.get('isRollover', False))
                event_type = 'roll' if is_rollover else 'entry'

                event_data = {
                    'event_type': event_type,
                    'ticker': ticker,
                    'option_type': option_type,
                    'strike': float(strike),
                    'expiration': str(expiry),
                    'premium_in': round(limit_price * 100, 2) if action == 'BUY' else round(limit_price * 100, 2),
                    'premium_out': 0,
                    'pnl': 0,
                    'leakage': 0,
                    'reason': 'rollover' if is_rollover else 'new_entry',
                    'details': {
                        'order_id': order_id,
                        'moomoo_order_id': result.get('order_id'),
                        'action': action,
                        'quantity': quantity,
                        'limit_price': limit_price,
                    }
                }

                # For rollovers, capture from/to transition
                if is_rollover:
                    event_data['from_strike'] = float(order.get('from_strike', 0) or 0)
                    event_data['from_expiration'] = str(order.get('from_expiration', '') or '')
                    event_data['to_strike'] = float(order.get('to_strike', 0) or strike)
                    event_data['to_expiration'] = str(order.get('to_expiration', '') or expiry)

                db.save_trade_event(event_data)
            except Exception as event_err:
                logger.warning(f"Failed to save trade event: {event_err}")

            return {
                "success": True,
                "message": "Order sent to moomoo",
                "order_id": order_id,
                "moomoo_order_id": result.get('order_id'),
                "status": "processing",
                "execution_details": execution_details
            }, 200
                
        except Exception as e:
            logger.error(f"Error executing order: {str(e)}")
            logger.error(traceback.format_exc())
            return {
                "success": False,
                "error": str(e)
            }, 500
      
    def _get_vix_regime(self):
        """
        Get current VIX market regime for adaptive delta targeting.
        Uses OpenBB as primary source with yfinance fallback.
        
        Returns:
            dict: {
                'vix': float,
                'regime': str ('complacency', 'normal', 'fear'),
                'delta_adjustment': float,
                'exposure_multiplier': float,
                'description': str
            }
        """
        cache_key = '_vix_regime_cache'
        if hasattr(self, cache_key):
            cache_entry = getattr(self, cache_key)
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < 300:
                return cache_entry['data']
        
        vix_value = None
        
        try:
            openbb = self._get_openbb_service()
            if openbb:
                vix_data = openbb.get_vix()
                if vix_data and 'vix' in vix_data:
                    vix_value = float(vix_data['vix'])
                    logger.debug(f"VIX from OpenBB: {vix_value}")
        except Exception as e:
            logger.debug(f"OpenBB VIX fetch failed, trying yfinance: {e}")
        
        if vix_value is None:
            try:
                import yfinance as yf
                vix_ticker = yf.Ticker('^VIX')
                hist = vix_ticker.history(period='1d')
                if not hist.empty:
                    vix_value = float(hist['Close'].iloc[-1])
                    logger.debug(f"VIX from yfinance: {vix_value}")
            except Exception as e:
                logger.debug(f"yfinance VIX fetch failed: {e}")
        
        if vix_value is None:
            logger.warning("Unable to fetch VIX, using default normal regime")
            result = {
                'vix': 20.0,
                'regime': 'normal',
                'delta_adjustment': 0.0,
                'exposure_multiplier': 1.0,
                'description': 'Normal volatility (VIX 15-30) - standard delta targets'
            }
            setattr(self, cache_key, {'data': result, 'timestamp': datetime.now()})
            return result
        
        if vix_value < 15:
            result = {
                'vix': round(vix_value, 2),
                'regime': 'complacency',
                'delta_adjustment': 0.10,
                'exposure_multiplier': 0.7,
                'description': 'Low volatility (VIX < 15) - higher delta targets, reduced exposure'
            }
        elif vix_value <= 30:
            result = {
                'vix': round(vix_value, 2),
                'regime': 'normal',
                'delta_adjustment': 0.0,
                'exposure_multiplier': 1.0,
                'description': 'Normal volatility (VIX 15-30) - standard delta targets'
            }
        else:
            result = {
                'vix': round(vix_value, 2),
                'regime': 'fear',
                'delta_adjustment': -0.05,
                'exposure_multiplier': 0.5,
                'description': 'High volatility (VIX > 30) - lower delta targets, conservative exposure'
            }
        
        setattr(self, cache_key, {'data': result, 'timestamp': datetime.now()})
        return result

    def _get_portfolio_context(self):
        context = {
            'cash_balance': 0.0,
            'account_value': 0.0,
            'positions': {},
            'short_calls': {},
            'short_puts': {},
            'vix_regime': self._get_vix_regime()
        }

        try:
            if self.portfolio_service is None:
                from api.services.portfolio_service import PortfolioService
                self.portfolio_service = PortfolioService()

            summary = self.portfolio_service.get_portfolio_summary() or {}
            stock_positions = self.portfolio_service.get_positions('STK') or []
            option_positions = self.portfolio_service.get_positions('OPT') or []

            context['cash_balance'] = float(summary.get('cash_balance', 0) or 0)
            context['account_value'] = float(summary.get('account_value', 0) or 0)

            for position in stock_positions:
                symbol = str(position.get('symbol', '') or '').replace('US.', '')
                if not symbol:
                    continue
                context['positions'][symbol] = position

            for position in option_positions:
                symbol = str(position.get('symbol', '') or '').replace('US.', '')
                if not symbol:
                    continue
                
                pos_qty = int(position.get('position', 0) or 0)
                option_type = str(position.get('option_type', '') or '').upper()
                
                if pos_qty < 0:
                    contracts = abs(pos_qty)
                    if option_type == 'CALL':
                        context['short_calls'][symbol] = context['short_calls'].get(symbol, 0) + contracts
                    elif option_type == 'PUT':
                        context['short_puts'][symbol] = context['short_puts'].get(symbol, 0) + contracts
                        
        except Exception as exc:
            logger.error(f"Error building portfolio context for options scoring: {exc}")

        watchlist = self.config.get('watchlist', [])
        owned = set(context['positions'].keys())
        context['watchlist'] = [t for t in watchlist if t not in owned]
        if context['watchlist']:
            logger.debug(f"Watchlist tickers for CSP scanning: {context['watchlist']}")

        return context

    def _get_position_snapshot(self, portfolio_context, ticker):
        return portfolio_context.get('positions', {}).get(ticker, {})

    def _get_yfinance_price(self, ticker):
        """Get stock price from yfinance as fallback when Moomoo lacks quote rights."""
        try:
            import yfinance as yf
            yf_ticker = yf.Ticker(ticker)
            hist = yf_ticker.history(period="1d")
            if hist.empty:
                logger.debug(f"yfinance: No price data for {ticker}")
                return None
            price = float(hist['Close'].iloc[-1])
            logger.debug(f"yfinance: Got price {price} for {ticker}")
            return price
        except Exception as e:
            logger.debug(f"yfinance: Failed to get price for {ticker}: {e}")
            return None

    def _get_yfinance_option_chain(self, ticker, expiration, option_type):
        """Get option chain from yfinance as fallback when Moomoo lacks quote rights."""
        try:
            import yfinance as yf
            yf_ticker = yf.Ticker(ticker)
            exp_formatted = expiration.replace('-', '')
            if len(exp_formatted) == 8:
                exp_yf = f"{exp_formatted[0:4]}-{exp_formatted[4:6]}-{exp_formatted[6:8]}"
            else:
                exp_yf = expiration

            all_exps = yf_ticker.options
            if not all_exps:
                return None

            target_exp = None
            for exp in all_exps:
                if exp == exp_yf or exp.startswith(exp_formatted[:10]):
                    target_exp = exp
                    break

            if not target_exp:
                logger.debug(f"yfinance: Expiration {expiration} not found for {ticker}, using closest")
                from datetime import datetime
                today = datetime.now()
                target_date = None
                for exp in all_exps:
                    exp_date = datetime.strptime(exp, '%Y-%m-%d')
                    dte = (exp_date - today).days
                    if 7 <= dte <= 45:
                        target_exp = exp
                        break
                if not target_exp:
                    target_exp = all_exps[0]

            chain = yf_ticker.option_chain(target_exp)
            if option_type == 'C':
                df = chain.calls
            else:
                df = chain.puts

            if df.empty:
                return None

            options = []
            for _, row in df.iterrows():
                opt = {
                    'strike': float(row.get('strike', 0)),
                    'expiration': target_exp.replace('-', ''),
                    'option_type': 'CALL' if option_type == 'C' else 'PUT',
                    'bid': float(row.get('bid', 0)) if not pd.isna(row.get('bid')) else 0,
                    'ask': float(row.get('ask', 0)) if not pd.isna(row.get('ask')) else 0,
                    'last': float(row.get('lastPrice', 0)) if not pd.isna(row.get('lastPrice')) else 0,
                    'volume': int(row.get('volume', 0)) if not pd.isna(row.get('volume')) else 0,
                    'open_interest': int(row.get('openInterest', 0)) if not pd.isna(row.get('openInterest')) else 0,
                    'implied_volatility': float(row.get('impliedVolatility', 0)) if not pd.isna(row.get('impliedVolatility')) else 0,
                    'delta': None,
                    'gamma': None,
                    'theta': None,
                    'vega': None,
                }
                options.append(opt)

            result = {
                'symbol': ticker,
                'expiration': target_exp.replace('-', ''),
                'stock_price': None,
                'right': option_type,
                'options': options
            }
            logger.debug(f"yfinance: Got {len(options)} options for {ticker} {expiration} {option_type}")
            return result
        except Exception as e:
            logger.debug(f"yfinance: Failed to get option chain for {ticker}: {e}")
            return None

    def _calculate_cash_reserved(self, portfolio_context):
        """
        Calculate cash reserved for existing short put positions.
        Each short put requires cash equal to strike * 100 per contract.
        
        Args:
            portfolio_context: Dict with 'short_puts' and 'cash_balance'
            
        Returns:
            float: Total cash reserved for open short puts
        """
        reserved = 0.0
        short_puts = portfolio_context.get('short_puts', {})
        
        if not short_puts:
            return reserved
            
        try:
            # Get option positions to find strike prices
            if self.portfolio_service is None:
                from api.services.portfolio_service import PortfolioService
                self.portfolio_service = PortfolioService()
                
            option_positions = self.portfolio_service.get_positions('OPT') or []
            
            for position in option_positions:
                symbol = str(position.get('symbol', '') or '').replace('US.', '')
                pos_qty = int(position.get('position', 0) or 0)
                option_type = str(position.get('option_type', '') or '').upper()
                
                # Only count short puts (negative quantity)
                if pos_qty < 0 and option_type == 'PUT':
                    strike = float(position.get('strike', 0) or 0)
                    contracts = abs(pos_qty)
                    cash_required = strike * 100 * contracts
                    reserved += cash_required
                    
        except Exception as e:
            logger.error(f"Error calculating cash reserved: {e}")
            
        return reserved

    def _get_fallback_stock_price(self, portfolio_context, ticker):
        position = self._get_position_snapshot(portfolio_context, ticker)
        for field in ('market_price', 'avg_cost'):
            value = position.get(field)
            try:
                numeric_value = float(value or 0)
            except (TypeError, ValueError):
                numeric_value = 0
            if numeric_value > 0:
                return numeric_value
        return 0.0

    def _get_screening_profile(self, option_type, dte=None, profile_type=None, vix_regime=None):
        """
        Get screening profile based on option type, DTE, and VIX regime.
        
        Args:
            option_type: 'CALL' or 'PUT'
            dte: Days to expiration (auto-detects profile if None)
            profile_type: 'weekly', 'monthly', 'quarterly', or None (auto-detect)
            vix_regime: dict from _get_vix_regime() with delta_adjustment, exposure_multiplier
            
        Returns:
            dict: Screening profile parameters with VIX regime adjustments
        """
        if profile_type is None and dte is not None:
            if dte <= 14:
                profile_type = 'weekly'
            elif dte <= 45:
                profile_type = 'monthly'
            else:
                profile_type = 'quarterly'
        elif profile_type is None:
            profile_type = 'monthly'
        
        # Base profile with targets from Phase 1
        base_profile = {
            'max_expirations': 4,
            'min_mid_price': 0.05,
            'min_open_interest': 10,
            'ideal_open_interest': 500,
            'min_volume': 1,
            'ideal_volume': 100,
            'max_spread_pct': 60,
            'ideal_spread_pct': 12,
            'profile_type': profile_type,
            # Risk-adjusted scoring targets (Phase 1)
            'target_iv_adjusted': 50,
            'target_theta_delta_ratio': 0.005,
            'target_capital_efficiency': 100,
            # IV environment thresholds (Phase 2)
            'min_iv_percentile_for_bonus': 60,
            'max_iv_percentile_for_penalty': 30,
            'earnings_warning_days': 7,
        }
        
        # Dynamic profiles based on expiration type
        if profile_type == 'weekly':
            # Weeklies (0-14 DTE): Tighter delta, higher liquidity focus
            if option_type == 'CALL':
                base_profile.update({
                    'min_dte': 3,
                    'max_dte': 14,
                    'preferred_dte': 7,
                    'target_delta': 0.18,
                    'delta_tolerance': 0.14,
                    'min_premium_per_contract': 8,
                    'liquidity_weight_multiplier': 1.5,  # 35% effective
                    'delta_fit_weight_multiplier': 0.5,  # 8% effective
                })
            else:  # PUT
                base_profile.update({
                    'min_dte': 3,
                    'max_dte': 14,
                    'preferred_dte': 7,
                    'target_delta': 0.16,
                    'delta_tolerance': 0.12,
                    'min_premium_per_contract': 10,
                    'liquidity_weight_multiplier': 1.5,
                    'delta_fit_weight_multiplier': 0.5,
                })
        
        elif profile_type == 'quarterly':
            # Quarterlies (46-90 DTE): Wider delta, lower liquidity focus
            if option_type == 'CALL':
                base_profile.update({
                    'min_dte': 46,
                    'max_dte': 90,
                    'preferred_dte': 60,
                    'target_delta': 0.28,
                    'delta_tolerance': 0.22,
                    'min_premium_per_contract': 25,
                    'liquidity_weight_multiplier': 0.75,  # 15% effective
                    'delta_fit_weight_multiplier': 1.2,  # 18% effective
                })
            else:  # PUT
                base_profile.update({
                    'min_dte': 46,
                    'max_dte': 90,
                    'preferred_dte': 60,
                    'target_delta': 0.26,
                    'delta_tolerance': 0.20,
                    'min_premium_per_contract': 30,
                    'liquidity_weight_multiplier': 0.75,
                    'delta_fit_weight_multiplier': 1.2,
                })
        
        else:  # 'monthly' (default, 15-45 DTE)
            if option_type == 'CALL':
                base_profile.update({
                    'min_dte': 5,
                    'max_dte': 35,
                    'preferred_dte': 14,
                    'target_delta': 0.24,
                    'delta_tolerance': 0.18,
                    'min_premium_per_contract': 12,
                    'liquidity_weight_multiplier': 1.0,
                    'delta_fit_weight_multiplier': 1.0,
                })
            else:  # PUT
                base_profile.update({
                    'min_dte': 7,
                    'max_dte': 45,
                    'preferred_dte': 21,
                    'target_delta': 0.22,
                    'delta_tolerance': 0.16,
                    'min_premium_per_contract': 15,
                    'liquidity_weight_multiplier': 1.0,
                    'delta_fit_weight_multiplier': 1.0,
                })
        
        if vix_regime:
            delta_adj = vix_regime.get('delta_adjustment', 0.0)
            regime_name = vix_regime.get('regime', 'normal')
            
            if 'target_delta' in base_profile:
                base_profile['target_delta'] = max(0.10, min(0.40,
                    base_profile['target_delta'] + delta_adj))
            
            if 'delta_tolerance' in base_profile:
                base_profile['delta_tolerance'] = max(0.08,
                    base_profile['delta_tolerance'] + (delta_adj * 0.5))
            
            if regime_name == 'fear':
                base_profile['min_premium_per_contract'] *= 1.2
            elif regime_name == 'complacency':
                base_profile['min_premium_per_contract'] *= 0.8
            
            base_profile['vix_regime'] = regime_name
        
        return base_profile

    def _calculate_mid_price(self, bid, ask, last):
        bid = float(bid or 0)
        ask = float(ask or 0)
        last = float(last or 0)

        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        if bid > 0:
            return bid
        if ask > 0:
            return ask
        if last > 0:
            return last
        return 0.0

    def _clamp(self, value, minimum=0.0, maximum=1.0):
        return max(minimum, min(maximum, value))

    def _score_proximity(self, value, target, tolerance):
        if tolerance <= 0:
            return 0.0
        return self._clamp(1 - (abs(value - target) / tolerance))

    def _score_positive_metric(self, value, ideal_value):
        if ideal_value <= 0:
            return 0.0
        return self._clamp(value / ideal_value)

    def _get_candidate_expirations(self, conn, ticker, profile, expiration=None):
        if expiration:
            return [expiration]

        try:
            # Use the rate-limited method in MoomooConnection
            ret, data = conn.get_option_expiration_dates(ticker)
            if ret != RET_OK or data is None or data.empty:
                fallback = get_closest_friday().strftime('%Y%m%d')
                return [fallback]

            today = datetime.now().date()
            filtered = []
            fallback = []

            expiration_column = 'expiration_date'
            if expiration_column not in data.columns:
                if 'strike_time' in data.columns:
                    expiration_column = 'strike_time'
                elif 'option_expiry_date' in data.columns:
                    expiration_column = 'option_expiry_date'
                else:
                    raise KeyError('No expiration column returned by moomoo')

            for raw_date in data[expiration_column].tolist():
                normalized = raw_date.replace('-', '')
                expiry_date = datetime.strptime(normalized, '%Y%m%d').date()
                dte = (expiry_date - today).days
                if dte <= 0:
                    continue
                fallback.append((normalized, dte))
                if profile['min_dte'] <= dte <= profile['max_dte']:
                    filtered.append((normalized, dte))

            expirations = filtered or fallback
            return [value for value, _ in expirations[:profile['max_expirations']]] or [get_closest_friday().strftime('%Y%m%d')]
        except Exception as exc:
            logger.error(f"Error loading option expirations for {ticker}: {exc}")
            return [get_closest_friday().strftime('%Y%m%d')]

    def _build_candidate(self, ticker, option, stock_price, desired_otm, profile, portfolio_context):
        """
        Build a scored candidate for a single option contract.

        Delegates to the unified WheelDecision engine, then converts
        the result back to the legacy dict format for API compatibility.
        """
        # Gather IV / earnings / macro context
        vix_regime = portfolio_context.get('vix_regime')
        if option.get('implied_volatility', 0) > 0:
            self.iv_earnings_service.record_iv_data(
                ticker,
                float(option.get('implied_volatility', 0)),
                stock_price,
                str(option.get('option_type', '') or '').upper(),
                str(option.get('expiration', '') or ''),
                int((datetime.strptime(str(option.get('expiration', '')), '%Y%m%d').date() - datetime.now().date()).days)
                if option.get('expiration') else 0,
            )

        iv_env_adjustment, iv_rank, iv_status = self.iv_earnings_service.get_iv_environment_score(
            ticker, float(option.get('implied_volatility', 0) or 0.20)
        )
        earnings_adjustment, earnings_warning = self.iv_earnings_service.get_earnings_score_impact(ticker)
        earnings_info = self.iv_earnings_service.get_earnings_info(ticker)
        macro_regime = get_macro_service().get_macro_regime()

        # Delegate to unified scorer
        decision = score_contract(
            ticker=ticker,
            option=option,
            stock_price=stock_price,
            profile=profile,
            portfolio_context=portfolio_context,
            iv_env_adjustment=iv_env_adjustment,
            iv_rank=iv_rank,
            iv_status_str=iv_status,
            earnings_adjustment=earnings_adjustment,
            earnings_info=earnings_info,
            macro_regime=macro_regime,
        )

        if decision is None:
            return None

        # Convert WheelDecision back to legacy dict format for API compatibility
        candidate = {
            'symbol': decision.ticker + decision.expiration + ('C' if decision.option_type == 'CALL' else 'P') + str(int(decision.strike)),
            'strike': decision.strike,
            'expiration': decision.expiration,
            'option_type': decision.option_type,
            'bid': decision.bid,
            'ask': decision.ask,
            'last': decision.last if hasattr(decision, 'last') else round(decision.mid_price, 4),
            'mid_price': round(decision.mid_price, 4),
            'open_interest': decision.open_interest,
            'volume': decision.volume,
            'implied_volatility': round(decision.implied_volatility, 2),
            'delta': round(decision.delta, 5),
            'gamma': round(decision.gamma, 5),
            'theta': round(decision.theta, 5),
            'vega': round(getattr(decision, 'vega', 0), 5),
            'dte': decision.dte,
            'premium_per_contract': round(decision.premium_per_contract, 2),
            'spread_pct': round(decision.spread_pct, 2),
            'score': round(decision.contract_score, 2),
            'score_details': decision.score_details,
            'rationale': decision.rationale,
            'warnings': decision.warnings,
            'otm_pct': decision.otm_pct,
            'annualized_return': decision.annualized_return,
            'iv_adjusted_return': decision.iv_adjusted_return,
            'iv_rank': decision.iv_rank,
            'iv_status': decision.iv_status,
            'iv_env_adjustment': decision.iv_env_adjustment,
            'profile_type': decision.profile_type,
            'vix_regime': decision.vix_regime,
            'vix_level': decision.vix_level,
            'macro_multiplier': decision.macro_multiplier,
            'macro_regime': decision.macro_regime,
            'macro_credit_stress': decision.macro_credit_stress,
            'macro_summary': decision.macro_summary,
            'macro_advice': decision.macro_advice,
            'earnings_date': decision.earnings_date,
            'days_to_earnings': decision.days_to_earnings,
            'earnings_adjustment': decision.earnings_adjustment,
            # Additional unified fields
            'size_fit': decision.size_fit,
            'expected_move_buffer': decision.expected_move_buffer,
            'wheel_decision': decision.to_dict(),
        }

        # Add CALL/PUT specific fields
        if decision.option_type == 'CALL':
            candidate.update({
                'if_called_return': decision.if_called_return,
                'earnings_max_contracts': decision.max_contracts,
                'earnings_premium_per_contract': round(decision.premium_per_contract, 2),
                'earnings_total_premium': round(decision.premium_per_contract * decision.max_contracts, 2),
                'earnings_return_on_capital': round(decision.annualized_return, 2),
            })
        else:
            candidate.update({
                'breakeven': decision.breakeven,
                'breakeven_buffer_pct': decision.breakeven_buffer_pct,
                'cash_required': decision.cash_required,
                'cash_reserve_enabled': self.config.get('cash_reserve_enabled', True),
                'earnings_max_contracts': 1,
                'earnings_premium_per_contract': round(decision.premium_per_contract, 2),
                'earnings_total_premium': round(decision.premium_per_contract, 2),
                'earnings_return_on_cash': round(decision.annualized_return, 2),
            })

        return candidate

    def get_otm_options(self, ticker, otm_percentage=10, option_type=None, expiration=None):
        """
        Return ranked wheel candidates near the requested OTM preference.
        """
        start_time = time.time()

        if option_type and option_type not in ['CALL', 'PUT']:
            return {'error': f"Invalid option_type: {option_type}. Must be 'CALL' or 'PUT'"}

        conn = self._ensure_connection()
        if not conn:
            return {'error': 'Failed to establish connection to moomoo'}

        portfolio_context = self._get_portfolio_context()
        result = {}

        try:
            result[ticker] = self._process_ticker_for_otm(
                conn,
                ticker,
                otm_percentage,
                portfolio_context,
                expiration,
                option_type
            )
        except Exception as exc:
            logger.error(f"Error processing {ticker} for optimal options: {exc}")
            logger.error(traceback.format_exc())
            result[ticker] = {'error': str(exc)}

        elapsed = time.time() - start_time
        logger.info(f"Ranked option opportunities for {ticker} in {elapsed:.2f}s")
        return {'data': result}

    def _process_ticker_for_otm(self, conn, ticker, otm_percentage, portfolio_context, expiration=None, option_type=None):
        result = {
            'symbol': ticker,
            'stock_price': 0,
            'otm_percentage': otm_percentage,
            'position': 0,
            'calls': [],
            'puts': []
        }

        stock_price = conn.get_stock_price(ticker)
        if stock_price is None or stock_price <= 0:
            logger.debug(f"Moomoo returned no/invalid price for {ticker}, trying yfinance fallback")
            stock_price = self._get_yfinance_price(ticker)
        if stock_price is None or stock_price <= 0:
            stock_price = self._get_fallback_stock_price(portfolio_context, ticker)
        if stock_price is None or stock_price <= 0:
            return {'error': 'Unable to obtain valid stock price from any source'}

        position = self._get_position_snapshot(portfolio_context, ticker)
        result['stock_price'] = stock_price
        result['position'] = float(position.get('position', 0) or 0)
        result['avg_cost'] = float(position.get('avg_cost', 0) or 0)

        sides = [option_type] if option_type else ['CALL', 'PUT']
        options_chains = []

        for side in sides:
            profile = self._get_screening_profile(side)
            expirations = self._get_candidate_expirations(conn, ticker, profile, expiration)
            target_strike = stock_price * (1 + (otm_percentage / 100)) if side == 'CALL' else stock_price * (1 - (otm_percentage / 100))
            for expiry in expirations:
                chain = conn.get_option_chain(
                    ticker,
                    expiry,
                    'C' if side == 'CALL' else 'P',
                    target_strike=target_strike
                )
                if chain and chain.get('options'):
                    options_chains.append(chain)
                else:
                    logger.debug(f"Moomoo returned no options for {ticker} {expiry} {side}, trying yfinance fallback")
                    yf_chain = self._get_yfinance_option_chain(ticker, expiry, 'C' if side == 'CALL' else 'P')
                    if yf_chain and yf_chain.get('options'):
                        options_chains.append(yf_chain)

        if not options_chains:
            return {'error': 'No options data available from any source'}

        formatted_data = self._process_options_chain(
            options_chains,
            ticker,
            stock_price,
            otm_percentage,
            portfolio_context,
            option_type
        )
        result.update(formatted_data)
        return result

    def _process_options_chain(self, options_chains, ticker, stock_price, otm_percentage, portfolio_context, option_type=None):
        try:
            result = {
                'symbol': ticker,
                'stock_price': stock_price,
                'otm_percentage': otm_percentage,
                'calls': [],
                'puts': []
            }

            grouped_options = {'CALL': [], 'PUT': []}
            for chain in options_chains:
                chain_type = str(chain.get('right', '') or '').upper()
                option_side = 'CALL' if chain_type == 'C' else 'PUT'
                grouped_options[option_side].extend(chain.get('options', []))

            for side in ['CALL', 'PUT']:
                if option_type and option_type != side:
                    continue

                profile = self._get_screening_profile(side)
                candidates = []
                seen_contracts = set()

                for option in grouped_options[side]:
                    contract_key = (
                        option.get('expiration'),
                        option.get('strike'),
                        option.get('option_type')
                    )
                    if contract_key in seen_contracts:
                        continue
                    seen_contracts.add(contract_key)

                    candidate = self._build_candidate(
                        ticker,
                        option,
                        stock_price,
                        otm_percentage,
                        profile,
                        portfolio_context
                    )
                    if candidate:
                        candidates.append(candidate)

                candidates.sort(
                    key=lambda item: (
                        item.get('score', 0),
                        item.get('annualized_return', 0),
                        item.get('premium_per_contract', 0)
                    ),
                    reverse=True
                )

                result['calls' if side == 'CALL' else 'puts'] = candidates[:5]

            return result
        except Exception as exc:
            logger.error(f"Error processing options chain: {exc}")
            logger.error(traceback.format_exc())
            return {}

    def _sanitize_result(self, result):
        if not result or not isinstance(result, dict):
            return
        def sanitize_dict(d):
            if not isinstance(d, dict): return
            for key, value in d.items():
                if isinstance(value, float) and math.isnan(value): d[key] = 0
                elif isinstance(value, dict): sanitize_dict(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict): sanitize_dict(item)
        sanitize_dict(result)
        
    def check_pending_orders(self):
        """
        Check status of pending/processing orders in moomoo
        """
        try:
            orders = self.db.get_orders(status_filter=['pending', 'processing'], limit=50)
            if not orders:
                return {"success": True, "message": "No pending orders", "updated_orders": []}
                
            conn = self._ensure_connection()
            updated_orders = []
            for order in orders:
                moomoo_order_id = order.get('moomoo_order_id')
                if order.get('status') == 'processing' and moomoo_order_id:
                    status_info = conn.check_order_status(moomoo_order_id)
                    if status_info:
                        new_status = "processing"
                        executed = False
                        if status_info.get('status') in ['Filled', 'Cancelled', 'Dealt']:
                            new_status = "executed" if status_info.get('status') in ['Filled', 'Dealt'] else "canceled"
                            executed = True
                            
                        execution_details = {
                            "moomoo_order_id": moomoo_order_id,
                            "moomoo_status": status_info.get('status'),
                            "filled": status_info.get('filled', 0),
                            "remaining": status_info.get('remaining', 0),
                            "avg_fill_price": status_info.get('avg_fill_price', 0),
                            "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        }

                        self.db.update_order_status(order_id=order.get('id'), status=new_status, executed=executed, execution_details=execution_details)
                        updated_orders.append({**order, 'status': new_status, **execution_details})
            
            return {"success": True, "updated_orders": updated_orders}
        except Exception as e:
            logger.error(f"Error checking pending orders: {e}")
            return {"success": False, "error": str(e)}

    def cancel_order(self, order_id):
        """
        Cancel an order in moomoo
        """
        try:
            order = self.db.get_order(order_id)
            if not order: return {"success": False, "error": "Order not found"}, 404
            
            if order['status'] == 'processing' and order.get('moomoo_order_id'):
                conn = self._ensure_connection()
                res = conn.cancel_order(order.get('moomoo_order_id'))
                if res.get('success'):
                    self.db.update_order_status(order_id=order_id, status="canceled", executed=True)
                    return {"success": True, "message": "Order canceled"}, 200
            
            self.db.update_order_status(order_id=order_id, status="canceled", executed=True)
            return {"success": True, "message": "Order canceled"}, 200
        except Exception as e:
            return {"success": False, "error": str(e)}, 500

    def get_stock_price(self, ticker):
        conn = self._ensure_connection()
        if not conn:
            return 0

        live_price = conn.get_stock_price(ticker)
        if live_price is not None and live_price > 0:
            return live_price

        portfolio_context = self._get_portfolio_context()
        fallback_price = self._get_fallback_stock_price(portfolio_context, ticker)
        return fallback_price if fallback_price > 0 else 0

    def get_option_expirations(self, ticker, option_type=None):
        """
        Get available expiration dates for options from moomoo
        
        Args:
            ticker: The ticker symbol
            option_type: Optional 'CALL' or 'PUT' to filter by preferred DTE ranges
                        CALL: 5-35 days, PUT: 7-45 days
                        If None, returns all future expirations
        """
        try:
            conn = self._ensure_connection()
            if not conn: return {"error": "No connection"}
            
            # Use the rate-limited method in MoomooConnection
            ret, data = conn.get_option_expiration_dates(ticker)
            if ret != RET_OK: return {"error": f"Failed to get expirations: {data}"}
            
            expiration_column = 'expiration_date'
            if expiration_column not in data.columns:
                if 'strike_time' in data.columns:
                    expiration_column = 'strike_time'
                elif 'option_expiry_date' in data.columns:
                    expiration_column = 'option_expiry_date'
                else:
                    return {"error": "No expiration column returned by moomoo"}

            from datetime import datetime, date
            today = date.today()
            
            # Define DTE ranges based on option type
            if option_type == 'CALL':
                min_dte, max_dte = 5, 35
            elif option_type == 'PUT':
                min_dte, max_dte = 7, 45
            else:
                min_dte, max_dte = 0, 365  # All future dates up to 1 year
            
            expirations = []
            for date_str in data[expiration_column].tolist():
                try:
                    # Parse the date string (format: YYYY-MM-DD)
                    exp_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    
                    # Calculate DTE
                    dte = (exp_date - today).days
                    
                    # Filter: must be in the future and within preferred range
                    if dte >= min_dte and dte <= max_dte:
                        expirations.append({
                            "value": date_str.replace('-', ''),
                            "label": date_str,
                            "dte": dte
                        })
                except ValueError:
                    # Skip invalid date formats
                    continue
            
            # Sort by DTE (ascending)
            expirations.sort(key=lambda x: x['dte'])
            
            return {"ticker": ticker, "expirations": expirations}
        except Exception as e:
            return {"error": str(e)}

    def get_top_recommendations(self, limit=5):
        """
        Get top N option recommendations across all portfolio positions.
        
        Filters options by capital availability:
        - CALLs: Only if user has 100+ shares
        - PUTs: Only if user has sufficient cash (strike * 100)
        
        Returns options ranked by composite score including warnings for earnings, IV, etc.
        
        Args:
            limit (int): Number of top recommendations to return (default: 3, max: 10)
            
        Returns:
            dict: {
                'success': True,
                'count': int,
                'total_scored': int,
                'generated_at': str (ISO timestamp),
                'recommendations': [list of recommendation dicts]
            }
        """
        import concurrent.futures
        from datetime import datetime
        
        logger.info(f"Getting top {limit} recommendations")
        start_time = time.time()
        
        try:
            # Ensure connection
            conn = self._ensure_connection()
            if not conn:
                return {'error': 'Failed to establish connection to moomoo'}
            
            # Get portfolio context for positions and cash balance
            portfolio_context = self._get_portfolio_context()
            positions = portfolio_context.get('positions', {})
            cash_balance = float(portfolio_context.get('cash_balance', 0) or 0)
            short_calls = portfolio_context.get('short_calls', {})
            short_puts = portfolio_context.get('short_puts', {})
            watchlist = portfolio_context.get('watchlist', [])
            
            if not positions and not watchlist:
                return {
                    'success': True,
                    'count': 0,
                    'total_scored': 0,
                    'generated_at': datetime.now().isoformat(),
                    'recommendations': [],
                    'message': 'No positions or watchlist configured'
                }
            
            # Collect all options across all tickers
            all_options = []
            
            # Process each ticker
            for ticker in positions.keys():
                try:
                    # Get stock price for this ticker
                    stock_price = conn.get_stock_price(ticker)
                    if stock_price is None or stock_price <= 0:
                        stock_price = self._get_fallback_stock_price(portfolio_context, ticker)
                    
                    if not stock_price or stock_price <= 0:
                        logger.warning(f"Skipping {ticker}: Unable to get stock price")
                        continue
                    
                    # Get position data
                    position_data = positions.get(ticker, {})
                    shares_owned = float(position_data.get('position', 0) or 0)
                    
                    # Calculate available contracts for calls (accounting for existing short calls)
                    total_possible_calls = int(shares_owned // 100)
                    existing_short_calls = short_calls.get(ticker, 0)
                    available_calls = max(0, total_possible_calls - existing_short_calls)
                    
                    # Calculate available contracts for puts (accounting for existing short puts)
                    existing_short_puts = short_puts.get(ticker, 0)
                    
                    logger.info(f"{ticker}: {shares_owned} shares, {total_possible_calls} possible calls, "
                              f"{existing_short_calls} existing short calls, {available_calls} available, "
                              f"{existing_short_puts} existing short puts")
                    
                    # Fetch options data for this ticker
                    result = self._process_ticker_for_otm(
                        conn=conn,
                        ticker=ticker,
                        otm_percentage=10,  # Default OTM
                        portfolio_context=portfolio_context,
                        expiration=None,  # Get all expirations
                        option_type=None  # Get both CALL and PUT
                    )
                    
                    if 'error' in result:
                        logger.warning(f"Error processing {ticker}: {result['error']}")
                        continue
                    
                    # Process CALLs - only if user has available contracts after accounting for existing shorts
                    if available_calls > 0:
                        for call in result.get('calls', []):
                            all_options.append({
                                'ticker': ticker,
                                'stock_price': stock_price,
                                'option_type': 'CALL',
                                'max_contracts': available_calls,
                                'existing_position': existing_short_calls,
                                **call
                            })
                    
                    # Process PUTs - only if user has sufficient cash AND available contracts
                    for put in result.get('puts', []):
                        cash_required = float(put.get('strike', 0)) * 100
                        if cash_required > 0 and cash_balance >= cash_required:
                            # For puts, we don't limit by existing short puts in the same way
                            # Each put is cash-secured independently
                            available_puts = 1  # Start with 1
                            
                            all_options.append({
                                'ticker': ticker,
                                'stock_price': stock_price,
                                'option_type': 'PUT',
                                'max_contracts': available_puts,
                                'existing_position': existing_short_puts,
                                **put
                            })
                    
                except Exception as e:
                    logger.error(f"Error processing {ticker} for recommendations: {e}")
                    continue
            
            # Process watchlist tickers for CSP candidates (stocks not in portfolio)
            # Use yfinance since Moomoo account doesn't have US stock quote rights
            for ticker in portfolio_context.get('watchlist', []):
                try:
                    # Use yfinance for stock price (no Moomoo quote rights needed)
                    import yfinance as yf
                    yf_ticker = yf.Ticker(ticker)
                    hist = yf_ticker.history(period="1d")
                    if hist.empty:
                        logger.debug(f"Watchlist {ticker}: No price data from yfinance")
                        continue
                    stock_price = float(hist['Close'].iloc[-1])
                    
                    # Calculate available cash after reserves
                    reserved = self._calculate_cash_reserved(portfolio_context)
                    available_cash = max(0, cash_balance - reserved)
                    
                    # Skip if even a 10% OTM put would exceed available cash
                    min_strike = stock_price * 0.9
                    if min_strike * 100 > available_cash:
                        logger.debug(f"Watchlist {ticker}: min strike ${min_strike:.0f} exceeds available cash ${available_cash:.0f}")
                        continue
                    
                    # Get option chain from yfinance
                    try:
                        opts = yf_ticker.options
                        if not opts:
                            logger.debug(f"Watchlist {ticker}: No options available")
                            continue
                        
                        # Find expiration within 7-45 days (CSP sweet spot)
                        from datetime import datetime, timedelta
                        today = datetime.now()
                        target_exp = None
                        for exp in opts:
                            exp_date = datetime.strptime(exp, '%Y-%m-%d')
                            dte = (exp_date - today).days
                            if 7 <= dte <= 45:
                                target_exp = exp
                                break
                        
                        if not target_exp:
                            logger.debug(f"Watchlist {ticker}: No suitable expiration found")
                            continue
                        
                        # Get option chain for target expiration
                        chain = yf_ticker.option_chain(target_exp)
                        puts = chain.puts
                        if puts.empty:
                            logger.debug(f"Watchlist {ticker}: No puts available")
                            continue
                        
                        # Filter for OTM puts (~10% OTM)
                        target_strike = stock_price * 0.9
                        puts['strike_diff'] = abs(puts['strike'] - target_strike)
                        best_put = puts.loc[puts['strike_diff'].idxmin()]
                        
                        strike = float(best_put['strike'])
                        cash_required = strike * 100
                        
                        # Skip if exceeds available cash
                        if cash_required > available_cash:
                            logger.debug(f"Watchlist {ticker}: Strike ${strike:.0f} requires ${cash_required:.0f} > available ${available_cash:.0f}")
                            continue
                        
                        # Calculate metrics
                        last_price = float(best_put['lastPrice']) if not pd.isna(best_put['lastPrice']) else 0
                        bid = float(best_put['bid']) if not pd.isna(best_put['bid']) else 0
                        ask = float(best_put['ask']) if not pd.isna(best_put['ask']) else 0
                        mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else last_price
                        
                        if mid_price <= 0:
                            logger.debug(f"Watchlist {ticker}: Invalid option price")
                            continue
                        
                        dte = (datetime.strptime(target_exp, '%Y-%m-%d') - today).days
                        premium_per_contract = mid_price * 100
                        annualized_return = (premium_per_contract / cash_required) * (365 / dte) * 100 if cash_required > 0 and dte > 0 else 0
                        otm_pct = ((stock_price - strike) / stock_price) * 100

                        # Build a normalized contract dict for the unified scorer
                        # Greeks are None from yfinance; scorer handles missing data gracefully
                        wl_contract = {
                            'strike': strike,
                            'expiration': target_exp.replace('-', ''),
                            'option_type': 'PUT',
                            'bid': bid,
                            'ask': ask,
                            'last': last_price,
                            'delta': 0,        # Not available from yfinance
                            'gamma': 0,
                            'theta': 0,
                            'vega': 0,
                            'implied_volatility': float(best_put['impliedVolatility']) if not pd.isna(best_put.get('impliedVolatility')) else 0,
                            'open_interest': int(best_put['openInterest']) if not pd.isna(best_put['openInterest']) else 0,
                            'volume': int(best_put['volume']) if not pd.isna(best_put['volume']) else 0,
                        }

                        # Use a lenient profile for watchlist (yfinance data has gaps)
                        wl_profile = self._get_screening_profile('PUT', dte=dte)
                        # Relax minimums for watchlist candidates
                        wl_profile['min_open_interest'] = 0
                        wl_profile['min_volume'] = 0
                        wl_profile['min_premium_per_contract'] = 0

                        # Use neutral IV/earnings/macro for watchlist
                        wl_decision = score_contract(
                            ticker=ticker,
                            option=wl_contract,
                            stock_price=stock_price,
                            profile=wl_profile,
                            portfolio_context=portfolio_context,
                            iv_env_adjustment=0,
                            iv_rank=0.5,
                            iv_status_str='normal',
                            earnings_adjustment=0,
                            earnings_info={},
                            macro_regime=get_macro_service().get_macro_regime(),
                        )

                        if wl_decision is not None:
                            put_data = {
                                'ticker': ticker,
                                'stock_price': stock_price,
                                'option_type': 'PUT',
                                'max_contracts': 1,
                                'existing_position': 0,
                                'from_watchlist': True,
                                'strike': wl_decision.strike,
                                'expiration': wl_decision.expiration,
                                'dte': wl_decision.dte,
                                'mid_price': round(wl_decision.mid_price, 4),
                                'premium_per_contract': round(wl_decision.premium_per_contract, 2),
                                'bid': wl_decision.bid,
                                'ask': wl_decision.ask,
                                'annualized_return': wl_decision.annualized_return,
                                'iv_adjusted_return': wl_decision.iv_adjusted_return,
                                'otm_pct': wl_decision.otm_pct,
                                'delta': wl_decision.delta,
                                'implied_volatility': wl_decision.implied_volatility,
                                'open_interest': wl_decision.open_interest,
                                'volume': wl_decision.volume,
                                'score': round(wl_decision.contract_score, 2),
                                'iv_rank': wl_decision.iv_rank,
                                'iv_status': wl_decision.iv_status,
                                'iv_env_adjustment': wl_decision.iv_env_adjustment,
                                'profile_type': wl_decision.profile_type,
                                'earnings_date': None,
                                'days_to_earnings': None,
                                'earnings_adjustment': 0,
                                'size_fit': wl_decision.size_fit,
                                'expected_move_buffer': wl_decision.expected_move_buffer,
                                'wheel_decision': wl_decision.to_dict(),
                                'score_details': wl_decision.score_details,
                                'rationale': wl_decision.rationale,
                                'warnings': wl_decision.warnings + [
                                    'Data from yfinance (not Moomoo) - verify before trading'
                                ],
                                'cash_reserve_enabled': self.config.get('cash_reserve_enabled', True),
                                'breakeven': wl_decision.breakeven,
                                'breakeven_buffer_pct': wl_decision.breakeven_buffer_pct,
                                'cash_required': wl_decision.cash_required,
                                'earnings_max_contracts': 1,
                                'earnings_premium_per_contract': round(wl_decision.premium_per_contract, 2),
                                'earnings_total_premium': round(wl_decision.premium_per_contract, 2),
                                'earnings_return_on_cash': round(wl_decision.annualized_return, 2),
                            }
                            all_options.append(put_data)
                            logger.info(f"Watchlist {ticker}: Found CSP candidate - ${strike}P exp {target_exp}, score={wl_decision.contract_score:.1f}")
                        else:
                            logger.debug(f"Watchlist {ticker}: Unified scorer rejected the candidate")
                        
                    except Exception as opt_err:
                        logger.debug(f"Watchlist {ticker}: Option chain error: {opt_err}")
                        continue
                        
                except Exception as e:
                    logger.error(f"Error processing watchlist ticker {ticker}: {e}")
                    continue
            
            # Sort by score descending
            all_options.sort(key=lambda x: x.get('score', 0), reverse=True)
            
            # Take top N
            top_options = all_options[:limit]
            
            # Format recommendations with rank
            recommendations = []
            for rank, option in enumerate(top_options, 1):
                rec = {
                    'rank': rank,
                    'ticker': option['ticker'],
                    'option_type': option['option_type'],
                    'strike': option.get('strike'),
                    'expiration': option.get('expiration'),
                    'dte': option.get('dte'),
                    'mid_price': option.get('mid_price'),
                    'premium_per_contract': option.get('premium_per_contract'),
                    'score': option.get('score'),
                    'annualized_return': option.get('annualized_return'),
                    'iv_adjusted_return': option.get('iv_adjusted_return'),
                    'otm_pct': option.get('otm_pct'),
                    'delta': option.get('delta'),
                    'iv_rank': option.get('iv_rank'),
                    'iv_status': option.get('iv_status'),
                    'days_to_earnings': option.get('days_to_earnings'),
                    'earnings_date': option.get('earnings_date'),
                    'warnings': option.get('warnings', []),
                    'rationale': option.get('rationale', []),
                    'max_contracts': option.get('max_contracts'),
                    'existing_position': option.get('existing_position', 0),
                    'profile_type': option.get('profile_type'),
                    'stock_price': option.get('stock_price'),
                    'bid': option.get('bid'),
                    'ask': option.get('ask'),
                    'open_interest': option.get('open_interest'),
                    'volume': option.get('volume'),
                    'implied_volatility': option.get('implied_volatility'),
                    'score_details': option.get('score_details', {}),
                    # Unified WheelDecision fields
                    'size_fit': option.get('size_fit', 0),
                    'expected_move_buffer': option.get('expected_move_buffer', 0),
                    'wheel_decision': option.get('wheel_decision', {}),
                    'from_watchlist': option.get('from_watchlist', False),
                }
                recommendations.append(rec)
            
            elapsed = time.time() - start_time
            logger.info(f"Generated {len(recommendations)} top recommendations in {elapsed:.2f}s")
            
            return {
                'success': True,
                'count': len(recommendations),
                'total_scored': len(all_options),
                'generated_at': datetime.now().isoformat(),
                'recommendations': recommendations
            }
            
        except Exception as e:
            logger.error(f"Error getting top recommendations: {e}")
            logger.error(traceback.format_exc())
            return {'error': str(e)}
