"""
Portfolio Service module
Manages portfolio data and calculations for moomoo
"""

import logging
import time
import traceback
from datetime import datetime, timedelta

from core.connection_constants import _safe_float

logger = logging.getLogger('api.services.portfolio')

class PortfolioService:
    """
    Service for handling portfolio operations
    """
    def __init__(self, connection=None):
        from api.services.config import get_config
        self.config = get_config()
        self.connection = connection
        self.last_error = None
        
        # Portfolio cache for in-memory storage
        self._portfolio_cache = None
        self._portfolio_cache_time = None
        self._portfolio_cache_ttl = 30  # seconds

    def _get_db(self):
        if not hasattr(self, '_db') or self._db is None:
            from db.database import OptionsDatabase
            db_path = self.config.get('db_path')
            self._db = OptionsDatabase(db_path)
        return self._db

    def _get_earnings_service(self):
        if not hasattr(self, '_earnings_service') or self._earnings_service is None:
            from api.services.iv_earnings_service import IVEarningsService
            self._earnings_service = IVEarningsService(self._get_db())
        return self._earnings_service

    def _set_error(self, message):
        self.last_error = message
        logger.error(message)
        
    def _ensure_connection(self):
        """
        Ensure that the moomoo connection exists and is connected.
        If a shared connection was provided, use it. Otherwise create one.
        """
        try:
            self.last_error = None
            if self.connection is not None:
                if self.connection.is_connected():
                    return self.connection
                logger.info("Shared connection found but disconnected, attempting to reconnect")
                if self.connection.connect():
                    return self.connection
                logger.warning("Failed to reconnect shared connection, will create new one")

            logger.info("Creating new moomoo connection for portfolio")
            from core.connection import MoomooConnection
            self.connection = MoomooConnection(
                host=str(self.config.get('host', '127.0.0.1')),
                port=int(self.config.get('port', 11111)),
                readonly=bool(self.config.get('readonly', True)),
                account_id=self.config.get('account_id'),
                portfolio_env=self.config.get('portfolio_env'),
                security_firm=self.config.get('security_firm')
            )
            
            if not self.connection.connect():
                self._set_error(self.connection.last_error or "Failed to connect to moomoo OpenD")
            
            return self.connection
        except Exception as e:
            self._set_error(f"Error ensuring connection: {str(e)}")
            return None

    def _fetch_portfolio(self):
        """Internal method to actually fetch from connection."""
        conn = self._ensure_connection()
        if not conn:
            return None
        return conn.get_portfolio()

    def _build_short_option_income_summary(self, positions, this_friday_str=None):
        """
        Build weekly and open short premium summaries from option positions.

        Weekly income is limited to short options expiring on or before the
        provided Friday cutoff. Open short premium includes every short option
        position regardless of expiration.
        """
        weekly_positions = []
        weekly_total_income = 0.0
        open_short_positions_count = 0
        open_short_contracts_count = 0
        open_short_total_income = 0.0

        for pos in positions or []:
            try:
                quantity = int(float(pos.get('position', 0) or 0))
            except (TypeError, ValueError):
                quantity = 0

            if quantity >= 0:
                continue

            contracts = abs(quantity)
            avg_cost = _safe_float(pos.get('avg_cost', 0))
            income = avg_cost * contracts * 100

            open_short_positions_count += 1
            open_short_contracts_count += contracts
            open_short_total_income += income

            expiration = str(pos.get('expiration', '') or '')
            if this_friday_str and expiration and expiration <= this_friday_str:
                weekly_positions.append({
                    'symbol': pos.get('symbol', ''),
                    'option_type': pos.get('option_type', ''),
                    'strike': pos.get('strike', 0),
                    'expiration': expiration,
                    'position': quantity,
                    'income': income,
                })
                weekly_total_income += income

        return {
            'positions': weekly_positions,
            'total_income': weekly_total_income,
            'positions_count': len(weekly_positions),
            'open_short_positions_count': open_short_positions_count,
            'open_short_contracts_count': open_short_contracts_count,
            'open_short_total_income': open_short_total_income,
        }

    def _get_cached_portfolio(self):
        """
        Get portfolio with in-memory caching.
        
        Returns cached data if within TTL, otherwise fetches fresh data.
        """
        now = datetime.now()
        
        # Check cache validity
        if (self._portfolio_cache is not None and 
            self._portfolio_cache_time is not None):
            age = (now - self._portfolio_cache_time).total_seconds()
            if age < self._portfolio_cache_ttl:
                return self._portfolio_cache
        
        # Fetch fresh data
        self._portfolio_cache = self._fetch_portfolio()
        self._portfolio_cache_time = now
        return self._portfolio_cache

    def peek_cached_portfolio(self):
        """
        Return the in-memory portfolio snapshot without refreshing it.

        This is intentionally non-blocking and may return stale data. It is
        useful for UI cache-key generation where freshness is less important
        than avoiding a slow broker round-trip.
        """
        return self._portfolio_cache

    def invalidate_cache(self):
        """Manually invalidate portfolio cache (call after trades)."""
        self._portfolio_cache = None
        self._portfolio_cache_time = None

    def get_portfolio_summary(self):
        """
        Get a summary of the current portfolio state.
        Uses cached portfolio data when available.
        """
        try:
            portfolio = self._get_cached_portfolio()
            if not portfolio:
                return {'error': self.last_error or 'Failed to load portfolio'}
                
            # Create a copy without positions
            summary = {k: v for k, v in portfolio.items() if k != 'positions'}
            return summary
        except Exception as e:
            self._set_error(f"Error getting portfolio summary: {e}")
            return {'error': str(e)}

    def get_positions(self, security_type=None):
        """
        Get current portfolio positions.
        Uses cached portfolio data when available.
        """
        try:
            portfolio = self._get_cached_portfolio()
            if portfolio is None:
                return None
                
            positions_dict = portfolio.get('positions', {})
            positions = []
            
            # Map dict into list format
            for symbol, pos in positions_dict.items():
                p = pos.copy()
                p['symbol'] = symbol
                p['position'] = pos.get('shares', 0)
                positions.append(p)
                
            # Filter by type if requested
            if security_type:
                positions = [p for p in positions if p.get('security_type') == security_type]
                
            # Enrich with earnings data
            positions_list = []
            for pos in positions:
                symbol = pos.get('symbol', 'UNKNOWN')
                pos_type = pos.get('security_type', 'STK')
                avg_cost = pos.get('avg_cost', 0)
                
                position_data = {
                    'symbol': symbol,
                    'position': pos.get('position', 0),
                    'avg_cost': avg_cost,
                    'market_price': pos.get('market_price', 0),
                    'market_value': pos.get('market_value', 0),
                    'unrealized_pnl': pos.get('unrealized_pnl', 0),
                    'security_type': pos_type
                }
                
                if pos_type == 'OPT':
                    position_data.update({
                        'expiration': pos.get('expiration', ''),
                        'strike': pos.get('strike', 0),
                        'option_type': pos.get('option_type', '')
                    })
                
                # Add earnings info for stock AND option positions
                if pos_type in ['STK', 'OPT']:
                    try:
                        import re
                        underlying_symbol = symbol
                        if pos_type == 'OPT':
                            # Extract underlying from standard option format
                            match = re.match(r'^([A-Z]+)\d{6}[CP]\d+', symbol)
                            if not match: match = re.match(r'^([A-Z]+)', symbol)
                            if match: underlying_symbol = match.group(1)
                        
                        earnings_service = self._get_earnings_service()
                        info = earnings_service.get_earnings_info(underlying_symbol)
                        
                        # Only include if earnings upcoming (today to 30 days)
                        if info.get('warning_level') in ['today', 'very_soon', 'soon', 'upcoming']:
                            position_data['earnings'] = {
                                'days': info.get('days_to_earnings'),
                                'date': info.get('earnings_date'),
                                'level': info.get('warning_level'),
                                'time_of_day': info.get('time_of_day'),
                                'earnings_source': info.get('earnings_source'),
                            }
                    except Exception as e:
                        # Log at debug but continue
                        logger.debug(f"Failed to fetch earnings for {symbol}: {e}")
                
                positions_list.append(position_data)
            
            return positions_list
        except Exception as e:
            self._set_error(f"Error getting positions: {e}")
            logger.error(traceback.format_exc())
            return None
    
    def get_weekly_option_income(self):
        """
        Get expected weekly income from option positions expiring this week
        """
        try:
            positions = self.get_positions('OPT')
            if positions is None:
                return {
                    'error': self.last_error or 'Failed to load option positions from moomoo',
                    'positions': [],
                    'total_income': 0,
                    'positions_count': 0
                }
            
            today = datetime.now()
            days_until_friday = (4 - today.weekday()) % 7
            this_friday = today + timedelta(days=days_until_friday)
            this_friday_str = this_friday.strftime('%Y%m%d')

            summary = self._build_short_option_income_summary(positions, this_friday_str)
            
            summary['this_friday'] = this_friday.strftime('%Y-%m-%d')
            return summary
        except Exception as e:
            self._set_error(f"Error getting weekly option income: {e}")
            return {
                'positions': [],
                'total_income': 0,
                'positions_count': 0,
                'open_short_positions_count': 0,
                'open_short_contracts_count': 0,
                'open_short_total_income': 0,
                'error': str(e)
            }
