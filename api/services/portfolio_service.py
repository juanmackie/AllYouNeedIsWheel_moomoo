"""
Portfolio Service module
Manages portfolio data and calculations for moomoo
"""

import logging
import time
from core.connection import MoomooConnection
from config import Config
import traceback
from datetime import datetime, timedelta

logger = logging.getLogger('api.services.portfolio')

class PortfolioService:
    """
    Service for handling portfolio operations
    """
    def __init__(self):
        self.config = Config()
        self.connection = None
        
    def _ensure_connection(self):
        """
        Ensure that the moomoo connection exists and is connected
        """
        try:
            if self.connection is None or not self.connection.is_connected():
                logger.info("Creating new moomoo connection for portfolio")
                
                self.connection = MoomooConnection(
                    host=self.config.get('host', '127.0.0.1'),
                    port=self.config.get('port', 11111),
                    readonly=self.config.get('readonly', True)
                )
                
                if not self.connection.connect():
                    logger.error("Failed to connect to moomoo OpenD")
            return self.connection
        except Exception as e:
            logger.error(f"Error ensuring connection: {str(e)}")
            return None
        
    def get_portfolio_summary(self):
        """
        Get account summary information
        """
        try:
            conn = self._ensure_connection()
            if not conn: return None
            
            portfolio = conn.get_portfolio()
            if not portfolio: return None
            
            return {
                'account_id': portfolio.get('account_id', ''),
                'cash_balance': portfolio.get('available_cash', 0),
                'account_value': portfolio.get('account_value', 0),
                'excess_liquidity': portfolio.get('excess_liquidity', 0),
                'initial_margin': portfolio.get('initial_margin', 0),
                'leverage_percentage': portfolio.get('leverage_percentage', 0),
                'is_frozen': portfolio.get('is_frozen', False)
            }
        except Exception as e:
            logger.error(f"Error getting portfolio summary: {e}")
            return None
    
    def get_positions(self, security_type=None):
        """
        Get portfolio positions
        """
        try:
            conn = self._ensure_connection()
            if not conn: return []
            
            portfolio = conn.get_portfolio()
            if not portfolio: return []
            
            positions = portfolio.get('positions', {})
            positions_list = []

            for key, pos in positions.items():
                pos_type = pos.get('security_type', '')
                if security_type and pos_type != security_type:
                    continue

                symbol = key.split('.')[-1] if '.' in key else key

                position_data = {
                    'symbol': symbol,
                    'position': pos.get('shares', 0),
                    'market_price': pos.get('market_price', 0),
                    'market_value': pos.get('market_value', 0),
                    'avg_cost': pos.get('avg_cost', 0),
                    'unrealized_pnl': pos.get('unrealized_pnl', 0),
                    'security_type': pos_type
                }
                
                if pos_type == 'OPT':
                    position_data.update({
                        'expiration': pos.get('expiration', ''),
                        'strike': pos.get('strike', 0),
                        'option_type': pos.get('option_type', '')
                    })
                
                positions_list.append(position_data)
            
            return positions_list
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return []
    
    def get_weekly_option_income(self):
        """
        Get expected weekly income from option positions expiring this week
        """
        try:
            positions = self.get_positions('OPT')
            
            today = datetime.now()
            days_until_friday = (4 - today.weekday()) % 7
            this_friday = today + timedelta(days=days_until_friday)
            this_friday_str = this_friday.strftime('%Y%m%d')
            
            weekly_positions = []
            total_income = 0
            
            for pos in positions:
                # Filter for short positions expiring this week
                if pos.get('position', 0) >= 0: continue

                expiration = pos.get('expiration', '')
                if expiration and expiration <= this_friday_str:
                    contracts = abs(pos.get('position', 0))
                    # Assuming standard US option (multiplier 100)
                    income = pos.get('avg_cost', 0) * contracts * 100
                    total_income += income
                    
                    weekly_positions.append({
                        'symbol': pos.get('symbol', ''),
                        'option_type': pos.get('option_type', ''),
                        'strike': pos.get('strike', 0),
                        'expiration': expiration,
                        'position': pos.get('position', 0),
                        'income': income,
                    })
            
            return {
                'positions': weekly_positions,
                'total_income': total_income,
                'positions_count': len(weekly_positions),
                'this_friday': this_friday.strftime('%Y-%m-%d')
            }
        except Exception as e:
            logger.error(f"Error getting weekly option income: {e}")
            return {'positions': [], 'total_income': 0, 'positions_count': 0}
