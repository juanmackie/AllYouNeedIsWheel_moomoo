"""
Portfolio Service module
Manages portfolio data and calculations
"""

import logging
import random
import time
from core.connection import IBConnection
from core.utils import is_market_hours, print_stock_summary
from config import Config
import traceback

logger = logging.getLogger('api.services.portfolio')

class PortfolioService:
    """
    Service for handling portfolio operations
    """
    def __init__(self):
        self.config = Config()
        logger.info(f"Portfolio service using port: {self.config.get('port')}")
        self.connection = None
        
    def _ensure_connection(self):
        """
        Ensure that the IB connection exists and is connected
        """
        try:
            if not is_market_hours():
                logger.info("Market is closed, skipping TWS connection")
                return None
            if self.connection is None or not self.connection.is_connected():
                # Generate a unique client ID based on current timestamp and random number
                # to avoid conflicts with other connections
                unique_client_id = int(time.time() % 10000) + random.randint(1000, 9999)
                logger.info(f"Creating new TWS connection with client ID: {unique_client_id}")
                
                # Create new connection
                port = self.config.get('port', 7497)
                logger.info(f"Connecting to TWS on port: {port}")
                
                self.connection = IBConnection(
                    host=self.config.get('host', '127.0.0.1'),
                    port=port,
                    client_id=unique_client_id,  # Use the unique client ID instead of fixed ID 1
                    timeout=self.config.get('timeout', 20),
                    readonly=self.config.get('readonly', True)
                )
                
                # Try to connect with proper error handling
                if not self.connection.connect():
                    logger.error("Failed to connect to TWS/IB Gateway")
                else:
                    logger.info("Successfully connected to TWS/IB Gateway")
            return self.connection
        except Exception as e:
            logger.error(f"Error ensuring connection: {str(e)}")
            if "There is no current event loop" in str(e):
                logger.error("Asyncio event loop error - please check connection.py for proper handling")
            return None
        
    def get_portfolio_summary(self):
        """
        Get account summary information including cash balance and account value
        
        Returns:
            dict: Portfolio summary data
        """
        try:
            conn = self._ensure_connection()
            if not conn:
                logger.warning("No connection available for portfolio summary. Using mock data.")
                return self._generate_mock_portfolio_summary()
            
            portfolio = conn.get_portfolio()
            
            # Extract the relevant information
            return {
                'account_id': portfolio.get('account_id', ''),
                'cash_balance': portfolio.get('available_cash', 0),
                'account_value': portfolio.get('account_value', 0),
                'is_mock': portfolio.get('is_mock', True)
            }
        except Exception as e:
            logger.error(f"Error getting portfolio summary: {e}")
            logger.error(traceback.format_exc())
            # Return mock data on error
            return self._generate_mock_portfolio_summary()
    
    def _generate_mock_portfolio_summary(self):
        """
        Generate mock portfolio summary data
        
        Returns:
            dict: Mock portfolio summary
        """
        logger.info("Generating mock portfolio summary data")
        
        # Create consistent mock portfolio data
        account_id = "U1234567"
        cash_balance = 1000000.00  # $1M cash
        
        # Add some stock positions value (like we have in the mock portfolio)
        nvda_price = 900.0
        nvda_position = 5000
        nvda_value = nvda_price * nvda_position
        
        # Add some option values
        options_value = 25000.00  # Value of option positions
        
        # Total account value
        account_value = cash_balance + nvda_value + options_value
        
        # Return mock summary
        return {
            'account_id': account_id,
            'cash_balance': cash_balance,
            'account_value': account_value,
            'is_mock': True
        }
    
    def get_positions(self, security_type=None):
        """
        Get portfolio positions, optionally filtered by security type
        
        Args:
            security_type (str, optional): Filter by security type (e.g., 'STK', 'OPT')
            
        Returns:
            list: List of position dictionaries
        """
        try:
            conn = self._ensure_connection()
            if not conn:
                logger.warning("No connection available for positions. Using mock data.")
                return self._generate_mock_positions(security_type)
            
            # Get portfolio data from IB connection
            portfolio = conn.get_portfolio()
            positions = portfolio.get('positions', {})
            
            # Convert positions dict to list format expected by the API
            positions_list = []
            for key, pos in positions.items():
                contract = pos.get('contract')
                if not contract:
                    continue
                
                # Skip if filtering by security type and this doesn't match
                pos_type = pos.get('security_type', '')
                if security_type and pos_type != security_type:
                    continue
                
                # Build position dictionary
                position_data = {
                    'symbol': contract.symbol if hasattr(contract, 'symbol') else '',
                    'position': pos.get('shares', 0),
                    'market_price': pos.get('market_price', 0),
                    'market_value': pos.get('market_value', 0),
                    'avg_cost': pos.get('avg_cost', 0),
                    'unrealized_pnl': pos.get('unrealized_pnl', 0),
                    'security_type': pos_type,
                    'is_mock': portfolio.get('is_mock', False)
                }
                
                # Add option-specific fields if this is an option
                if pos_type == 'OPT' and hasattr(contract, 'lastTradeDateOrContractMonth') and hasattr(contract, 'strike') and hasattr(contract, 'right'):
                    position_data.update({
                        'expiration': contract.lastTradeDateOrContractMonth,
                        'strike': contract.strike,
                        'option_type': 'CALL' if contract.right == 'C' else 'PUT'
                    })
                
                positions_list.append(position_data)
            
            return positions_list
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            logger.error(traceback.format_exc())
            # Return mock data on error
            return self._generate_mock_positions(security_type)
    
    def _generate_mock_positions(self, security_type=None):
        """
        Generate mock position data, optionally filtered by security type
        
        Args:
            security_type (str, optional): Filter by security type (e.g., 'STK', 'OPT')
            
        Returns:
            list: List of mock position dictionaries
        """
        logger.info(f"Generating mock positions data, security_type={security_type}")
        
        # Create list to hold mock positions
        positions = []
        
        # Add a stock position for NVDA
        nvda_stock = {
            'symbol': 'NVDA',
            'position': 5000,
            'market_price': 900.0,
            'market_value': 5000 * 900.0,
            'avg_cost': 720.0,  # 20% lower than current price
            'unrealized_pnl': 5000 * (900.0 - 720.0),
            'security_type': 'STK',
            'is_mock': True
        }
        
        # Add if all positions requested or specifically stock positions
        if not security_type or security_type == 'STK':
            positions.append(nvda_stock)
        
        # Add some option positions
        if not security_type or security_type == 'OPT':
            # Generate an expiration date (next Friday)
            from datetime import datetime, timedelta
            today = datetime.now()
            days_until_friday = (4 - today.weekday()) % 7
            if days_until_friday == 0:  # If today is Friday
                friday = today
            else:
                friday = today + timedelta(days=days_until_friday)
            expiration = friday.strftime('%Y%m%d')
            
            # Add a NVDA short put position
            nvda_put = {
                'symbol': 'NVDA',
                'position': -10,  # Short 10 contracts
                'market_price': 15.50,
                'market_value': -15.50 * 100 * 10,  # 10 contracts, 100 shares each
                'avg_cost': 18.75,
                'unrealized_pnl': (18.75 - 15.50) * 100 * 10,
                'security_type': 'OPT',
                'expiration': expiration,
                'strike': 850.0,
                'option_type': 'PUT',
                'is_mock': True
            }
            positions.append(nvda_put)
            
            # Add a NVDA short call position
            nvda_call = {
                'symbol': 'NVDA',
                'position': -5,  # Short 5 contracts
                'market_price': 12.75,
                'market_value': -12.75 * 100 * 5,  # 5 contracts, 100 shares each
                'avg_cost': 14.25,
                'unrealized_pnl': (14.25 - 12.75) * 100 * 5,
                'security_type': 'OPT',
                'expiration': expiration,
                'strike': 950.0,
                'option_type': 'CALL',
                'is_mock': True
            }
            positions.append(nvda_call)
        
        return positions
    
    def get_weekly_option_income(self):
        """
        Get expected weekly income from option positions expiring this week
        
        Returns:
            dict: Weekly income summary and position details
        """
        try:
            conn = self._ensure_connection()
            if not conn:
                logger.warning("No connection available for weekly income. Using mock data.")
                return self._generate_mock_weekly_option_income()
            
            # Get all positions from the portfolio
            positions = self.get_positions('OPT')  # Just option positions
            
            # Filter for short option positions expiring this week
            from datetime import datetime, timedelta
            today = datetime.now()
            # Calculate the end of the week (next Friday if today is after Friday)
            days_until_friday = (4 - today.weekday()) % 7
            this_friday = today + timedelta(days=days_until_friday)
            this_friday_str = this_friday.strftime('%Y%m%d')
            
            # Filter positions expiring this week that are short options
            weekly_positions = []
            total_income = 0
            
            for pos in positions:
                # Skip if not a short position (negative position means short)
                if pos.get('position', 0) >= 0:
                    continue
                    
                # Check if option expires this week
                if pos.get('expiration') <= this_friday_str:
                    # Calculate the income for this position
                    # For short options, we receive premium, so we use absolute value
                    contracts = abs(pos.get('position', 0))
                    premium_per_contract = pos.get('avg_cost', 0) * 100  # Each contract is 100 shares
                    income = premium_per_contract * contracts
                    
                    # Add income to total
                    total_income += income
                    
                    # Add position details to the result
                    weekly_positions.append({
                        'symbol': pos.get('symbol', ''),
                        'option_type': pos.get('option_type', ''),
                        'strike': pos.get('strike', 0),
                        'expiration': pos.get('expiration', ''),
                        'position': pos.get('position', 0),
                        'premium_per_contract': pos.get('avg_cost', 0),
                        'income': income,
                        'is_mock': pos.get('is_mock', False)
                    })
            
            # Build result dictionary
            result = {
                'positions': weekly_positions,
                'total_income': total_income,
                'positions_count': len(weekly_positions),
                'is_mock': any(pos.get('is_mock', False) for pos in weekly_positions)
            }
            
            return result
        except Exception as e:
            logger.error(f"Error getting weekly option income: {e}")
            logger.error(traceback.format_exc())
            # Return mock data on error
            return self._generate_mock_weekly_option_income()
    
    def _generate_mock_weekly_option_income(self):
        """
        Generate mock weekly option income data
        
        Returns:
            dict: Mock weekly income data
        """
        logger.info("Generating mock weekly option income data")
        
        # Generate an expiration date (this Friday)
        from datetime import datetime, timedelta
        today = datetime.now()
        days_until_friday = (4 - today.weekday()) % 7
        if days_until_friday == 0:  # If today is Friday
            friday = today
        else:
            friday = today + timedelta(days=days_until_friday)
        expiration = friday.strftime('%Y%m%d')
        
        # Create mock short positions expiring this week
        positions = [
            {
                'symbol': 'NVDA',
                'option_type': 'PUT',
                'strike': 850.0,
                'expiration': expiration,
                'position': -10,  # Short 10 contracts
                'premium_per_contract': 18.75,
                'income': 18.75 * 100 * 10,  # 10 contracts, 100 shares each
                'is_mock': True
            },
            {
                'symbol': 'NVDA',
                'option_type': 'CALL',
                'strike': 950.0,
                'expiration': expiration,
                'position': -5,  # Short 5 contracts
                'premium_per_contract': 14.25,
                'income': 14.25 * 100 * 5,  # 5 contracts, 100 shares each
                'is_mock': True
            }
        ]
        
        # Calculate total income
        total_income = sum(pos['income'] for pos in positions)
        
        # Build result dictionary
        result = {
            'positions': positions,
            'total_income': total_income,
            'positions_count': len(positions),
            'is_mock': True
        }
        
        return result