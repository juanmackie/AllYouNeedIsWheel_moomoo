"""
Options Service module
Handles options data retrieval and processing
"""

import logging
import math
import random
import time
from datetime import datetime, timedelta, time as datetime_time
import pandas as pd
from core.connection import MoomooConnection
from core.utils import get_closest_friday, get_next_monthly_expiration, is_market_hours
from config import Config
from db.database import OptionsDatabase
import traceback
import concurrent.futures
from functools import partial
import json

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
        self.portfolio_service = None  # Will be initialized when needed
        
    def _ensure_connection(self):
        """
        Ensure that the moomoo connection exists and is connected.
        Reuses existing connection if already established.
        """
        try:
            # If we already have a connected instance, just return it
            if self.connection is not None and self.connection.is_connected():
                logger.debug("Reusing existing moomoo connection")
                return self.connection
            
            # If connection exists but is disconnected, try to reconnect
            if self.connection is not None:
                logger.info("Existing connection found but disconnected, attempting to reconnect")
                if self.connection.connect():
                    logger.info("Successfully reconnected to moomoo OpenD")
                    return self.connection
                else:
                    logger.warning("Failed to reconnect, will create new connection")
        
            # No connection or reconnection failed, create a new one
            logger.info("Creating new moomoo connection")
            
            self.connection = MoomooConnection(
                host=str(self.config.get('host', '127.0.0.1')),
                port=int(self.config.get('port', 11111)),
                readonly=bool(self.config.get('readonly', True)),
                account_id=self.config.get('account_id'),
                security_firm=self.config.get('security_firm')
            )
            
            # Try to connect with proper error handling
            if not self.connection.connect():
                logger.error("Failed to connect to moomoo OpenD")
                return None
            else:
                logger.info("Successfully connected to moomoo OpenD")
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
      
    def _get_portfolio_context(self):
        context = {
            'cash_balance': 0.0,
            'account_value': 0.0,
            'positions': {}
        }

        try:
            if self.portfolio_service is None:
                from api.services.portfolio_service import PortfolioService
                self.portfolio_service = PortfolioService()

            summary = self.portfolio_service.get_portfolio_summary() or {}
            positions = self.portfolio_service.get_positions('STK') or []

            context['cash_balance'] = float(summary.get('cash_balance', 0) or 0)
            context['account_value'] = float(summary.get('account_value', 0) or 0)

            for position in positions:
                symbol = str(position.get('symbol', '') or '').replace('US.', '')
                if not symbol:
                    continue
                context['positions'][symbol] = position
        except Exception as exc:
            logger.error(f"Error building portfolio context for options scoring: {exc}")

        return context

    def _get_position_snapshot(self, portfolio_context, ticker):
        return portfolio_context.get('positions', {}).get(ticker, {})

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

    def _get_screening_profile(self, option_type):
        base_profile = {
            'max_expirations': 4,
            'min_mid_price': 0.05,
            'min_open_interest': 10,
            'ideal_open_interest': 500,
            'min_volume': 1,
            'ideal_volume': 100,
            'max_spread_pct': 60,
            'ideal_spread_pct': 12,
        }

        if option_type == 'CALL':
            base_profile.update({
                'min_dte': 5,
                'max_dte': 35,
                'preferred_dte': 14,
                'target_delta': 0.24,
                'delta_tolerance': 0.18,
                'min_premium_per_contract': 12,
            })
        else:
            base_profile.update({
                'min_dte': 7,
                'max_dte': 45,
                'preferred_dte': 21,
                'target_delta': 0.22,
                'delta_tolerance': 0.16,
                'min_premium_per_contract': 15,
            })

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

        symbol = conn._format_symbol(ticker)
        try:
            ret, data = conn.quote_ctx.get_option_expiration_date(code=symbol)
            if ret != 0 or data is None or data.empty:
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
        strike = float(option.get('strike', 0) or 0)
        expiration = str(option.get('expiration', '') or '')
        if strike <= 0 or not expiration:
            return None

        try:
            expiry_date = datetime.strptime(expiration, '%Y%m%d').date()
        except ValueError:
            return None

        dte = (expiry_date - datetime.now().date()).days
        if dte <= 0:
            return None

        bid = float(option.get('bid', 0) or 0)
        ask = float(option.get('ask', 0) or 0)
        last = float(option.get('last', 0) or 0)
        mid_price = self._calculate_mid_price(bid, ask, last)
        if mid_price < profile['min_mid_price']:
            return None

        spread_pct = 100.0
        if bid > 0 and ask > 0 and mid_price > 0:
            spread_pct = ((ask - bid) / mid_price) * 100
        elif bid == 0 and ask == 0:
            spread_pct = 100.0

        if spread_pct > profile['max_spread_pct']:
            return None

        option_type = str(option.get('option_type', '') or '').upper()
        delta = float(option.get('delta', 0) or 0)
        abs_delta = abs(delta)
        implied_volatility = float(option.get('implied_volatility', 0) or 0)
        open_interest = int(option.get('open_interest', 0) or 0)
        volume = int(option.get('volume', 0) or 0)
        premium_per_contract = mid_price * 100

        if premium_per_contract < profile['min_premium_per_contract']:
            return None
        if open_interest < profile['min_open_interest'] and volume < profile['min_volume']:
            return None

        position = portfolio_context.get('positions', {}).get(ticker, {})
        shares_owned = float(position.get('position', 0) or 0)
        avg_cost = float(position.get('avg_cost', 0) or 0)
        cash_balance = float(portfolio_context.get('cash_balance', 0) or 0)

        candidate = {
            'symbol': f"{ticker}{expiration}{'C' if option_type == 'CALL' else 'P'}{int(strike)}",
            'strike': strike,
            'expiration': expiration,
            'option_type': option_type,
            'bid': bid,
            'ask': ask,
            'last': last if last > 0 else round(mid_price, 4),
            'mid_price': round(mid_price, 4),
            'open_interest': open_interest,
            'volume': volume,
            'implied_volatility': round(implied_volatility, 2),
            'delta': round(delta, 5),
            'gamma': round(float(option.get('gamma', 0) or 0), 5),
            'theta': round(float(option.get('theta', 0) or 0), 5),
            'vega': round(float(option.get('vega', 0) or 0), 5),
            'dte': dte,
            'premium_per_contract': round(premium_per_contract, 2),
            'spread_pct': round(spread_pct, 2),
            'score': 0.0,
            'score_details': {},
            'rationale': [],
            'warnings': []
        }

        delta_score = self._score_proximity(abs_delta, profile['target_delta'], profile['delta_tolerance'])
        dte_score = self._score_proximity(dte, profile['preferred_dte'], max(profile['preferred_dte'], 10))
        oi_score = self._score_positive_metric(open_interest, profile['ideal_open_interest'])
        volume_score = self._score_positive_metric(volume, profile['ideal_volume'])
        spread_score = self._clamp(1 - (spread_pct / max(profile['ideal_spread_pct'], 1)))
        liquidity_score = (oi_score * 0.45) + (volume_score * 0.2) + (spread_score * 0.35)

        if option_type == 'CALL':
            if stock_price <= 0 or strike <= stock_price:
                return None
            max_contracts = max(int(shares_owned // 100), 0)
            if max_contracts < 1:
                return None

            otm_pct = ((strike - stock_price) / stock_price) * 100
            annualized_return = (premium_per_contract / (stock_price * 100)) * (365 / dte) * 100 if stock_price > 0 else 0
            if_called_return = (((strike - stock_price) + mid_price) / stock_price) * 100 if stock_price > 0 else 0
            cost_basis_score = 1.0 if avg_cost <= 0 or strike >= avg_cost else self._clamp(1 - ((avg_cost - strike) / avg_cost) * 4)
            otm_score = self._score_proximity(otm_pct, desired_otm, max(desired_otm * 0.75, 6))
            annualized_score = self._score_positive_metric(annualized_return, 24)
            upside_score = self._score_positive_metric(if_called_return, 12)

            score = (
                annualized_score * 0.28 +
                upside_score * 0.22 +
                liquidity_score * 0.2 +
                delta_score * 0.12 +
                otm_score * 0.1 +
                dte_score * 0.08
            ) * 100
            score *= (0.65 + (0.35 * cost_basis_score))

            candidate.update({
                'otm_pct': round(otm_pct, 2),
                'annualized_return': round(annualized_return, 2),
                'if_called_return': round(if_called_return, 2),
                'earnings_max_contracts': max_contracts,
                'earnings_premium_per_contract': round(premium_per_contract, 2),
                'earnings_total_premium': round(premium_per_contract * max_contracts, 2),
                'earnings_return_on_capital': round(annualized_return, 2),
                'score': round(score, 2),
                'score_details': {
                    'annualized': round(annualized_score * 100, 1),
                    'upside': round(upside_score * 100, 1),
                    'liquidity': round(liquidity_score * 100, 1),
                    'delta_fit': round(delta_score * 100, 1),
                    'otm_fit': round(otm_score * 100, 1),
                    'cost_basis_fit': round(cost_basis_score * 100, 1)
                },
                'rationale': [
                    f"{annualized_return:.1f}% annualized call yield",
                    f"{otm_pct:.1f}% OTM with {abs_delta:.2f} delta",
                    f"{open_interest} OI / {volume} volume / {spread_pct:.1f}% spread"
                ]
            })

            if spread_pct > profile['ideal_spread_pct']:
                candidate['warnings'].append('Wide bid/ask spread')
            if open_interest < profile['ideal_open_interest']:
                candidate['warnings'].append('Below ideal open interest')
            if avg_cost > 0 and strike < avg_cost:
                candidate['warnings'].append('Strike below stock cost basis')
        else:
            if stock_price <= 0 or strike >= stock_price:
                return None

            otm_pct = ((stock_price - strike) / stock_price) * 100
            cash_required = strike * 100
            annualized_return = (premium_per_contract / cash_required) * (365 / dte) * 100 if cash_required > 0 else 0
            breakeven = strike - mid_price
            breakeven_buffer_pct = ((stock_price - breakeven) / stock_price) * 100 if stock_price > 0 else 0
            otm_score = self._score_proximity(otm_pct, desired_otm, max(desired_otm * 0.75, 6))
            annualized_score = self._score_positive_metric(annualized_return, 18)
            buffer_score = self._score_positive_metric(breakeven_buffer_pct, max(desired_otm, 8))
            capital_fit = 1.0 if cash_balance <= 0 else self._clamp(cash_balance / cash_required)

            score = (
                annualized_score * 0.3 +
                buffer_score * 0.22 +
                liquidity_score * 0.2 +
                delta_score * 0.12 +
                otm_score * 0.08 +
                dte_score * 0.08
            ) * 100
            score *= (0.75 + (0.25 * capital_fit))

            candidate.update({
                'otm_pct': round(otm_pct, 2),
                'annualized_return': round(annualized_return, 2),
                'breakeven': round(breakeven, 2),
                'breakeven_buffer_pct': round(breakeven_buffer_pct, 2),
                'cash_required': round(cash_required, 2),
                'earnings_max_contracts': 1,
                'earnings_premium_per_contract': round(premium_per_contract, 2),
                'earnings_total_premium': round(premium_per_contract, 2),
                'earnings_return_on_cash': round(annualized_return, 2),
                'score': round(score, 2),
                'score_details': {
                    'annualized': round(annualized_score * 100, 1),
                    'buffer': round(buffer_score * 100, 1),
                    'liquidity': round(liquidity_score * 100, 1),
                    'delta_fit': round(delta_score * 100, 1),
                    'otm_fit': round(otm_score * 100, 1),
                    'capital_fit': round(capital_fit * 100, 1)
                },
                'rationale': [
                    f"{annualized_return:.1f}% annualized cash yield",
                    f"{otm_pct:.1f}% OTM with {breakeven_buffer_pct:.1f}% breakeven buffer",
                    f"{open_interest} OI / {volume} volume / {spread_pct:.1f}% spread"
                ]
            })

            if spread_pct > profile['ideal_spread_pct']:
                candidate['warnings'].append('Wide bid/ask spread')
            if open_interest < profile['ideal_open_interest']:
                candidate['warnings'].append('Below ideal open interest')
            if cash_balance > 0 and cash_required > cash_balance:
                candidate['warnings'].append('Cash required exceeds current cash balance')

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
            stock_price = self._get_fallback_stock_price(portfolio_context, ticker)
        if stock_price is None or stock_price <= 0:
            return {'error': 'Unable to obtain valid stock price from moomoo'}

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

        if not options_chains:
            return {'error': 'No options data available from moomoo'}

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

    def get_option_expirations(self, ticker):
        """
        Get available expiration dates for options from moomoo
        """
        try:
            conn = self._ensure_connection()
            if not conn: return {"error": "No connection"}
            
            ticker = conn._format_symbol(ticker)
            ret, data = conn.quote_ctx.get_option_expiration_date(code=ticker)
            if ret != 0: return {"error": f"Failed to get expirations: {data}"}
            
            expiration_column = 'expiration_date'
            if expiration_column not in data.columns:
                if 'strike_time' in data.columns:
                    expiration_column = 'strike_time'
                elif 'option_expiry_date' in data.columns:
                    expiration_column = 'option_expiry_date'
                else:
                    return {"error": "No expiration column returned by moomoo"}

            expirations = []
            for date in data[expiration_column].tolist():
                expirations.append({
                    "value": date.replace('-', ''),
                    "label": date
                })
            return {"ticker": ticker, "expirations": expirations}
        except Exception as e:
            return {"error": str(e)}
