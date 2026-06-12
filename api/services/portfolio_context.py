"""
Portfolio Context module - handles portfolio data and cash calculations
Extracted from the monolithic options_service.py for maintainability.
"""

import logging

from core.position_utils import parse_moomoo_symbol, parse_position_qty

logger = logging.getLogger('api.services.portfolio_context')


TRUE_CASH_FIELDS = ('available_cash', 'cash_balance', 'cash_available')
MARGIN_CAPACITY_FIELDS = ('buying_power', 'excess_liquidity')


def _first_positive_number(summary: dict, fields: tuple[str, ...]) -> tuple[float, str]:
    for field in fields:
        value = summary.get(field)
        if value is None:
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if numeric_value > 0:
            return numeric_value, field
    return 0.0, 'none'


class PortfolioContext:
    """
    Handles portfolio context building and cash reservation calculations.
    """
    
    def __init__(self, portfolio_service_provider, vix_regime_provider=None, config_provider=None):
        self._portfolio_service_provider = portfolio_service_provider
        self._vix_regime_provider = vix_regime_provider
        self._config_provider = config_provider
        self.config = config_provider.config if config_provider and hasattr(config_provider, 'config') else {}
        self._portfolio_service = None

    def _default_vix_regime(self):
        """Return a neutral VIX regime without making any live requests."""
        return {
            'regime': 'normal',
            'vix': 20.0,
            'delta_adjustment': 0.0,
            'exposure_multiplier': 1.0,
            'description': 'Normal volatility (VIX 15-30) - standard delta targets',
        }
        
    def _get_portfolio_service(self):
        if self._portfolio_service is not None:
            return self._portfolio_service
        if self._portfolio_service_provider:
            ps = self._portfolio_service_provider.portfolio_service
            if ps is None:
                from api.services.portfolio_service import PortfolioService
                ps = PortfolioService()
            self._portfolio_service = ps
            return ps
        from api.services.portfolio_service import PortfolioService
        self._portfolio_service = PortfolioService()
        return self._portfolio_service
    
    def _build_context_from_cached_portfolio(self, portfolio):
        """
        Build a lightweight portfolio context from an in-memory snapshot.

        This path is intentionally non-blocking: it avoids broker refreshes,
        earnings lookups, and market-regime calls so the dashboard can compute
        cache keys immediately even when the connection is slow.
        """
        context = {
            'cash_balance': 0.0,
            'account_value': 0.0,
            'excess_liquidity': 0.0,
            'positions': {},
            'short_calls': {},
            'short_puts': {},
            'cash_reserved_for_csp': 0.0,
            'cash_available_for_csp': 0.0,
            'broker_buying_power': 0.0,
            'broker_buying_power_source': 'none',
            'open_short_put_collateral': 0.0,
            'vix_regime': self._default_vix_regime(),
        }

        if not isinstance(portfolio, dict):
            return context

        summary = {k: v for k, v in portfolio.items() if k != 'positions'}
        raw_positions = portfolio.get('positions', {}) or {}

        true_cash, true_cash_source = _first_positive_number(summary, TRUE_CASH_FIELDS)
        margin_capacity, margin_source = _first_positive_number(summary, MARGIN_CAPACITY_FIELDS)

        context['cash_balance'] = float(summary.get('cash_balance', summary.get('available_cash', 0)) or 0)
        context['account_value'] = float(summary.get('account_value', 0) or 0)
        context['excess_liquidity'] = float(summary.get('excess_liquidity', 0) or 0)
        context['available_cash'] = true_cash
        context['broker_buying_power'] = margin_capacity if margin_capacity > 0 else true_cash
        context['broker_buying_power_source'] = margin_source if margin_capacity > 0 else true_cash_source

        cash_reserved = 0.0
        for raw_symbol, position in raw_positions.items():
            raw_symbol = str(raw_symbol or '')
            symbol = parse_moomoo_symbol(raw_symbol)
            if not symbol:
                continue

            pos = dict(position or {})
            qty_value = pos.get('position', pos.get('shares', 0))
            try:
                pos_qty = parse_position_qty(qty_value)
            except (TypeError, ValueError):
                pos_qty = 0
            pos['position'] = pos_qty
            if 'shares' not in pos:
                pos['shares'] = pos_qty

            security_type = str(pos.get('security_type', '') or '').upper()
            if security_type == 'STK':
                context['positions'][symbol] = pos
                if raw_symbol and raw_symbol != symbol:
                    context['positions'][raw_symbol] = pos
            elif security_type == 'OPT' and pos_qty < 0:
                option_type = str(pos.get('option_type', '') or '').upper()
                contracts = abs(pos_qty)
                if option_type == 'CALL':
                    context['short_calls'][symbol] = context['short_calls'].get(symbol, 0) + contracts
                elif option_type == 'PUT':
                    context['short_puts'][symbol] = context['short_puts'].get(symbol, 0) + contracts
                    strike = float(pos.get('strike', 0) or 0)
                    cash_required = strike * 100 * contracts
                    cash_reserved += cash_required

        context['cash_reserved_for_csp'] = cash_reserved
        context['open_short_put_collateral'] = cash_reserved
        context['cash_available_for_csp'] = max(0, context['available_cash'] - cash_reserved)
        context['_cash_diagnostics'] = {
            'raw_summary_fields': {f: summary.get(f) for f in [*TRUE_CASH_FIELDS, *MARGIN_CAPACITY_FIELDS]},
            'available_cash': context['available_cash'],
            'available_cash_source': true_cash_source,
            'broker_buying_power': context['broker_buying_power'],
            'broker_buying_power_source': context['broker_buying_power_source'],
            'excess_liquidity': context['excess_liquidity'],
            'open_short_put_collateral': cash_reserved,
            'cash_reserved_for_csp': cash_reserved,
            'cash_available_for_csp': context['cash_available_for_csp'],
            'cash_available_for_csp_source': 'available_cash_minus_open_short_put_collateral',
        }
        return context

    def get_portfolio_context(self, refresh=True):
        context = {
            'cash_balance': 0.0,
            'account_value': 0.0,
            'excess_liquidity': 0.0,
            'positions': {},
            'short_calls': {},
            'short_puts': {},
            'cash_reserved_for_csp': 0.0,
            'cash_available_for_csp': 0.0,
            'broker_buying_power': 0.0,
            'broker_buying_power_source': 'none',
            'open_short_put_collateral': 0.0,
            'vix_regime': self._vix_regime_provider.get_vix_regime() if refresh and self._vix_regime_provider else self._default_vix_regime()
        }

        if not refresh:
            try:
                portfolio_service = self._get_portfolio_service()
                if portfolio_service is None:
                    return context

                cached_portfolio = getattr(portfolio_service, 'peek_cached_portfolio', None)
                if cached_portfolio is None:
                    return context

                portfolio = cached_portfolio()
                if not portfolio:
                    return context

                return self._build_context_from_cached_portfolio(portfolio)
            except Exception as exc:
                logger.debug(f"Error building cached portfolio context: {exc}")
                return context

        try:
            portfolio_service = self._get_portfolio_service()
            if portfolio_service is None:
                return context
                
            summary = portfolio_service.get_portfolio_summary() or {}
            stock_positions = portfolio_service.get_positions('STK') or []
            option_positions = portfolio_service.get_positions('OPT') or []

            # Parse cash from first valid field: available_cash, cash_balance, cash_available, buying_power, excess_liquidity
            true_cash, true_cash_source = _first_positive_number(summary, TRUE_CASH_FIELDS)
            margin_capacity, margin_source = _first_positive_number(summary, MARGIN_CAPACITY_FIELDS)

            context['cash_balance'] = float(summary.get('cash_balance', summary.get('available_cash', 0)) or 0)
            context['account_value'] = float(summary.get('account_value', 0) or 0)
            context['excess_liquidity'] = float(summary.get('excess_liquidity', 0) or 0)

            # Use max of available cash and excess liquidity (more accurate for CSP buying power)
            context['available_cash'] = true_cash

            # Moomoo buying power is authoritative — broker already accounts for open positions
            context['broker_buying_power'] = margin_capacity if margin_capacity > 0 else true_cash
            context['broker_buying_power_source'] = margin_source if margin_capacity > 0 else true_cash_source

            for position in stock_positions:
                raw_symbol = str(position.get('symbol', '') or '')
                symbol = parse_moomoo_symbol(raw_symbol)
                if not symbol:
                    continue
                context['positions'][symbol] = position
                if raw_symbol and raw_symbol != symbol:
                    context['positions'][raw_symbol] = position

            for position in option_positions:
                symbol = parse_moomoo_symbol(position.get('symbol', ''))
                if not symbol:
                    continue
                
                pos_qty = parse_position_qty(position.get('position', 0))
                option_type = str(position.get('option_type', '') or '').upper()
                
                if pos_qty < 0:
                    contracts = abs(pos_qty)
                    if option_type == 'CALL':
                        context['short_calls'][symbol] = context['short_calls'].get(symbol, 0) + contracts
                    elif option_type == 'PUT':
                        context['short_puts'][symbol] = context['short_puts'].get(symbol, 0) + contracts

            # Calculate cash reserved for existing short puts (diagnostics only)
            cash_reserved = self._calculate_cash_reserved(context, option_positions=option_positions)
            context['cash_reserved_for_csp'] = cash_reserved
            context['open_short_put_collateral'] = cash_reserved

            context['cash_available_for_csp'] = max(0, context['available_cash'] - cash_reserved)

            # Diagnostics: expose raw summary fields for debugging
            context['_cash_diagnostics'] = {
                'raw_summary_fields': {f: summary.get(f) for f in [*TRUE_CASH_FIELDS, *MARGIN_CAPACITY_FIELDS]},
                'available_cash': context['available_cash'],
                'available_cash_source': true_cash_source,
                'broker_buying_power': context['broker_buying_power'],
                'broker_buying_power_source': context['broker_buying_power_source'],
                'excess_liquidity': context['excess_liquidity'],
                'open_short_put_collateral': cash_reserved,
                'cash_reserved_for_csp': cash_reserved,
                'cash_available_for_csp': context['cash_available_for_csp'],
                'cash_available_for_csp_source': 'available_cash_minus_open_short_put_collateral',
            }
                        
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

    def _calculate_cash_reserved(self, portfolio_context, option_positions=None):
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
            if option_positions is None:
                portfolio_service = self._get_portfolio_service()
                if portfolio_service is None:
                    return reserved
                option_positions = portfolio_service.get_positions('OPT') or []

            for position in option_positions:
                symbol = parse_moomoo_symbol(position.get('symbol', ''))
                pos_qty = parse_position_qty(position.get('position', 0))
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
