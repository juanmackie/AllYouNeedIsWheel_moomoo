"""
IV Tracking and Earnings Service
Manages implied volatility history and earnings calendar data using yfinance
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import traceback

logger = logging.getLogger('api.services.iv_tracking')

class IVEarningsService:
    """
    Service for tracking implied volatility and earnings data
    """
    
    def __init__(self, database=None):
        """
        Initialize the IV/Earnings service
        
        Args:
            database: OptionsDatabase instance
        """
        self.db = database
        self._iv_cache = {}  # In-memory cache: {ticker: {'iv': float, 'timestamp': datetime, 'iv_rank': float}}
        self._earnings_cache = {}  # In-memory cache: {ticker: {'earnings_date': str, 'timestamp': datetime}}
        self._cache_duration_hours = 4
        self._earnings_cache_duration_hours = 24
        
    def _is_cache_valid(self, cache_entry, duration_hours):
        """Check if a cache entry is still valid"""
        if not cache_entry or 'timestamp' not in cache_entry:
            return False
        
        age = datetime.now() - cache_entry['timestamp']
        return age < timedelta(hours=duration_hours)
    
    def record_iv_data(self, ticker: str, implied_volatility: float, 
                      stock_price: Optional[float] = None,
                      option_type: Optional[str] = None,
                      expiration: Optional[str] = None,
                      dte: Optional[int] = None):
        """
        Record IV data for a ticker
        
        Args:
            ticker: Stock ticker symbol
            implied_volatility: IV as decimal (e.g., 0.25 for 25%)
            stock_price: Current stock price
            option_type: CALL or PUT
            expiration: Expiration date
            dte: Days to expiration
        """
        if not self.db:
            return
        
        try:
            # Save to database
            self.db.save_iv_data(ticker, implied_volatility, stock_price, 
                                option_type, expiration, dte)
            
            # Calculate IV rank
            iv_rank = self._calculate_iv_rank(ticker, implied_volatility)
            
            # Update cache
            self._iv_cache[ticker] = {
                'iv': implied_volatility,
                'timestamp': datetime.now(),
                'iv_rank': iv_rank
            }
            
            logger.debug(f"Recorded IV for {ticker}: {implied_volatility:.2%} (rank: {iv_rank:.1%})")
            
        except Exception as e:
            logger.error(f"Error recording IV data for {ticker}: {e}")
    
    def _calculate_iv_rank(self, ticker: str, current_iv: float, days: int = 30) -> float:
        """
        Calculate IV Rank: where current IV falls in 30-day range
        
        Args:
            ticker: Stock ticker symbol
            current_iv: Current implied volatility
            days: Number of days to look back
            
        Returns:
            float: IV rank as percentage (0-1)
        """
        if not self.db:
            return 0.5  # Neutral if no DB
        
        try:
            # Get historical IV data
            history = self.db.get_iv_history(ticker, days)
            
            if len(history) < 5:  # Need at least 5 data points
                return 0.5  # Neutral if insufficient data
            
            iv_values = [record['implied_volatility'] for record in history]
            iv_values.append(current_iv)  # Include current
            
            min_iv = min(iv_values)
            max_iv = max(iv_values)
            
            if max_iv == min_iv:
                return 0.5  # Neutral if no range
            
            iv_rank = (current_iv - min_iv) / (max_iv - min_iv)
            return max(0.0, min(1.0, iv_rank))  # Clamp to 0-1
            
        except Exception as e:
            logger.error(f"Error calculating IV rank for {ticker}: {e}")
            return 0.5
    
    def get_iv_environment_score(self, ticker: str, current_iv: float) -> tuple:
        """
        Get IV environment score and metadata
        
        Args:
            ticker: Stock ticker symbol
            current_iv: Current implied volatility
            
        Returns:
            tuple: (score_adjustment, iv_rank, status_message)
                score_adjustment: -20 to +20 percentage points
                iv_rank: 0-1 (percentile)
                status_message: 'low', 'neutral', 'high', 'extreme'
        """
        # Check cache first
        cache_entry = self._iv_cache.get(ticker)
        if self._is_cache_valid(cache_entry, self._cache_duration_hours):
            iv_rank = cache_entry.get('iv_rank', 0.5)
        else:
            iv_rank = self._calculate_iv_rank(ticker, current_iv)
            self._iv_cache[ticker] = {
                'iv': current_iv,
                'timestamp': datetime.now(),
                'iv_rank': iv_rank
            }
        
        # Determine score adjustment and status
        if iv_rank < 0.20:
            return (-20, iv_rank, 'extreme_low')  # Dangerous - very low IV
        elif iv_rank < 0.30:
            return (-10, iv_rank, 'low')  # Low IV warning
        elif iv_rank < 0.40:
            return (-5, iv_rank, 'below_avg')  # Slightly below average
        elif iv_rank < 0.60:
            return (0, iv_rank, 'neutral')  # Normal range
        elif iv_rank < 0.70:
            return (5, iv_rank, 'above_avg')  # Slightly above average
        elif iv_rank < 0.80:
            return (10, iv_rank, 'high')  # Good premium environment
        else:
            return (20, iv_rank, 'extreme_high')  # Excellent - very high IV
    
    def fetch_earnings_date(self, ticker: str) -> Dict:
        """
        Fetch earnings date using yfinance
        
        Args:
            ticker: Stock ticker symbol
            
        Returns:
            dict: {'success': bool, 'earnings_date': str or None, 'error': str or None}
        """
        try:
            import yfinance as yf
            
            # Add delay to be polite to yfinance
            time.sleep(1)
            
            stock = yf.Ticker(ticker)
            calendar = stock.calendar
            
            if calendar is not None and not calendar.empty:
                # Get the next earnings date
                earnings_date = calendar.index[0] if hasattr(calendar.index[0], 'strftime') else str(calendar.index[0])
                if hasattr(earnings_date, 'strftime'):
                    earnings_date = earnings_date.strftime('%Y-%m-%d')
                
                return {
                    'success': True,
                    'earnings_date': earnings_date,
                    'error': None
                }
            else:
                return {
                    'success': True,
                    'earnings_date': None,
                    'error': 'No earnings data available'
                }
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error fetching earnings for {ticker}: {error_msg}")
            return {
                'success': False,
                'earnings_date': None,
                'error': error_msg
            }
    
    def update_earnings_data(self, ticker: str) -> bool:
        """
        Update earnings data for a ticker
        
        Args:
            ticker: Stock ticker symbol
            
        Returns:
            bool: True if successful
        """
        if not self.db:
            logger.warning("No database connection for earnings update")
            return False
        
        try:
            result = self.fetch_earnings_date(ticker)
            
            if result['success']:
                self.db.save_earnings_date(
                    ticker, 
                    result['earnings_date'], 
                    fetch_status='success'
                )
                
                # Update cache
                self._earnings_cache[ticker] = {
                    'earnings_date': result['earnings_date'],
                    'timestamp': datetime.now()
                }
                
                logger.info(f"Updated earnings for {ticker}: {result['earnings_date']}")
                return True
            else:
                self.db.save_earnings_date(
                    ticker,
                    None,
                    fetch_status='error',
                    error_message=result['error']
                )
                return False
                
        except Exception as e:
            logger.error(f"Error updating earnings for {ticker}: {e}")
            return False
    
    def get_earnings_info(self, ticker: str) -> Dict:
        """
        Get earnings information for a ticker
        
        Args:
            ticker: Stock ticker symbol
            
        Returns:
            dict: {
                'earnings_date': str or None,
                'days_to_earnings': int or None,
                'warning_level': str ('none', 'soon', 'very_soon', 'today', 'error'),
                'fetch_status': str,
                'error_message': str or None
            }
        """
        # Check cache first
        cache_entry = self._earnings_cache.get(ticker)
        if self._is_cache_valid(cache_entry, self._earnings_cache_duration_hours):
            earnings_date = cache_entry.get('earnings_date')
        elif self.db:
            # Fetch from database
            record = self.db.get_earnings_date(ticker)
            if record:
                earnings_date = record.get('earnings_date')
                self._earnings_cache[ticker] = {
                    'earnings_date': earnings_date,
                    'timestamp': datetime.now()
                }
            else:
                earnings_date = None
        else:
            earnings_date = None
        
        if not earnings_date:
            return {
                'earnings_date': None,
                'days_to_earnings': None,
                'warning_level': 'none',
                'fetch_status': 'pending' if not self.db else 'unknown',
                'error_message': None
            }
        
        # Calculate days to earnings
        try:
            earnings_dt = datetime.strptime(earnings_date, '%Y-%m-%d')
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            days_to_earnings = (earnings_dt - today).days
            
            # Determine warning level
            if days_to_earnings < 0:
                warning_level = 'past'
            elif days_to_earnings == 0:
                warning_level = 'today'
            elif days_to_earnings <= 3:
                warning_level = 'very_soon'
            elif days_to_earnings <= 7:
                warning_level = 'soon'
            else:
                warning_level = 'none'
            
            return {
                'earnings_date': earnings_date,
                'days_to_earnings': days_to_earnings,
                'warning_level': warning_level,
                'fetch_status': 'success',
                'error_message': None
            }
            
        except Exception as e:
            return {
                'earnings_date': earnings_date,
                'days_to_earnings': None,
                'warning_level': 'error',
                'fetch_status': 'error',
                'error_message': str(e)
            }
    
    def get_earnings_score_impact(self, ticker: str) -> tuple:
        """
        Get earnings-based score adjustment
        
        Args:
            ticker: Stock ticker symbol
            
        Returns:
            tuple: (score_adjustment, warning_message)
                score_adjustment: -30 to 0
                warning_message: str or None
        """
        info = self.get_earnings_info(ticker)
        
        if info['warning_level'] == 'today':
            return (-30, "Earnings today - extreme risk")
        elif info['warning_level'] == 'very_soon':
            return (-15, f"Earnings in {info['days_to_earnings']} days - high risk")
        elif info['warning_level'] == 'soon':
            return (-5, f"Earnings in {info['days_to_earnings']} days - caution")
        elif info['warning_level'] == 'error':
            return (0, "Failed to fetch earnings data")
        else:
            return (0, None)
    
    def batch_update_earnings(self, tickers: List[str]) -> Dict:
        """
        Update earnings data for multiple tickers
        
        Args:
            tickers: List of ticker symbols
            
        Returns:
            dict: {'successful': int, 'failed': int, 'errors': List[str]}
        """
        successful = 0
        failed = 0
        errors = []
        
        for ticker in tickers:
            if self.update_earnings_data(ticker):
                successful += 1
            else:
                failed += 1
                errors.append(f"{ticker}: Failed to fetch earnings")
        
        logger.info(f"Batch earnings update complete: {successful} successful, {failed} failed")
        
        return {
            'successful': successful,
            'failed': failed,
            'errors': errors
        }
    
    def purge_old_data(self):
        """Purge old IV history data"""
        if self.db:
            deleted = self.db.purge_old_iv_data(days=45)
            logger.info(f"Purged {deleted} old IV history records")
    
    def get_cache_stats(self) -> Dict:
        """Get cache statistics for monitoring"""
        return {
            'iv_cache_entries': len(self._iv_cache),
            'earnings_cache_entries': len(self._earnings_cache),
            'iv_cache_valid': sum(1 for entry in self._iv_cache.values() 
                                 if self._is_cache_valid(entry, self._cache_duration_hours)),
            'earnings_cache_valid': sum(1 for entry in self._earnings_cache.values() 
                                       if self._is_cache_valid(entry, self._earnings_cache_duration_hours))
        }
