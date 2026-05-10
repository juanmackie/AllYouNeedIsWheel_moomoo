"""
IV Tracking and Earnings Service
Manages implied volatility history and earnings calendar data
Uses Alpha Vantage -> OpenBB -> yfinance provider chain
"""

import os
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import traceback
from api.services.utils import clean_yfinance_ticker
from api.services.alpha_vantage_provider import AlphaVantageEarningsProvider

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
        self._iv_cache = {}
        self._earnings_cache = {}
        self._cache_duration_hours = 4
        self._earnings_cache_duration_hours = 24
        self._alpha_vantage = AlphaVantageEarningsProvider()
        
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
    
    def _strip_moomoo_prefix(self, ticker: str) -> str:
        return clean_yfinance_ticker(ticker)

    def fetch_earnings_date(self, ticker: str) -> Dict:
        """
        Fetch earnings date using provider chain:
        Alpha Vantage -> OpenBB -> yfinance.
        """
        clean_ticker = self._strip_moomoo_prefix(ticker)

        # --- Provider 1: Alpha Vantage ---
        if self._alpha_vantage.available:
            try:
                av_data = self._alpha_vantage.get_earnings(clean_ticker)
                if av_data and av_data.get('reportDate'):
                    report_date = av_data['reportDate'][:10]
                    if '-' in report_date:
                        return {
                            'success': True,
                            'earnings_date': report_date,
                            'time_of_day': av_data.get('timeOfDay', ''),
                            'fiscal_date_ending': av_data.get('fiscalDateEnding', ''),
                            'estimate': av_data.get('estimate'),
                            'currency': av_data.get('currency', ''),
                            'earnings_source': 'Alpha Vantage',
                            'error': None,
                        }
            except Exception as e:
                logger.debug(f"Alpha Vantage earnings lookup failed for {clean_ticker}: {e}")

        # --- Provider 2: OpenBB ---
        try:
            from openbb import obb
            result = obb.equity.earnings.calendar(symbol=clean_ticker)
            df = result.to_df() if hasattr(result, 'to_df') else result
            if df is not None and not df.empty:
                date_col = None
                for col in ['date', 'report_date', 'earning_date', 'earnings_date']:
                    if col in df.columns:
                        date_col = col
                        break
                if date_col:
                    earnings_date = str(df[date_col].iloc[0])[:10]
                    if earnings_date:
                        return {
                            'success': True,
                            'earnings_date': earnings_date,
                            'time_of_day': None,
                            'fiscal_date_ending': None,
                            'estimate': None,
                            'currency': None,
                            'earnings_source': 'OpenBB',
                            'error': None,
                        }
        except Exception as e:
            logger.debug(f"OpenBB earnings fetch failed for {clean_ticker}, falling back to yfinance: {e}")

        # --- Provider 3: yfinance ---
        try:
            import yfinance as yf
            time.sleep(1)
            stock = yf.Ticker(clean_ticker)

            try:
                earnings_df = stock.get_earnings_dates(limit=1)
                if earnings_df is not None and not earnings_df.empty:
                    for col in ['earningsDate', 'date', 'report_date', 'Earnings Date']:
                        if col in earnings_df.columns:
                            ed = earnings_df[col].iloc[0]
                            if ed is not None:
                                if hasattr(ed, 'strftime'):
                                    earnings_date = ed.strftime('%Y-%m-%d')
                                else:
                                    earnings_date = str(ed)[:10]
                                if earnings_date and '-' in earnings_date:
                                    return {
                                        'success': True,
                                        'earnings_date': earnings_date,
                                        'time_of_day': None,
                                        'fiscal_date_ending': None,
                                        'estimate': None,
                                        'currency': None,
                                        'earnings_source': 'yfinance',
                                        'error': None,
                                    }
            except Exception as e:
                logger.debug(f"get_earnings_dates failed for {clean_ticker}: {e}")

            try:
                info = stock.info
                for key in ['nextEarningsDate', 'earningsDate']:
                    if info.get(key):
                        ed = info[key]
                        if isinstance(ed, (int, float)):
                            earnings_date = datetime.fromtimestamp(ed).strftime('%Y-%m-%d')
                        else:
                            earnings_date = str(ed)[:10]
                        if earnings_date and '-' in earnings_date:
                            return {
                                'success': True,
                                'earnings_date': earnings_date,
                                'time_of_day': None,
                                'fiscal_date_ending': None,
                                'estimate': None,
                                'currency': None,
                                'earnings_source': 'yfinance',
                                'error': None,
                            }
            except Exception as e:
                logger.debug(f"stock.info earnings check failed for {clean_ticker}: {e}")
        except Exception as e:
            logger.warning(f"yfinance fallback failed for {clean_ticker}: {e}")

        return {
            'success': False,
            'earnings_date': None,
            'time_of_day': None,
            'fiscal_date_ending': None,
            'estimate': None,
            'currency': None,
            'earnings_source': None,
            'error': "No data from Alpha Vantage, OpenBB, or yfinance"
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
                    fetch_status='success',
                    time_of_day=result.get('time_of_day'),
                    fiscal_date_ending=result.get('fiscal_date_ending'),
                    estimate=result.get('estimate'),
                    currency=result.get('currency'),
                    earnings_source=result.get('earnings_source'),
                )
                
                # Update cache
                self._earnings_cache[ticker] = {
                    'earnings_date': result['earnings_date'],
                    'time_of_day': result.get('time_of_day'),
                    'fiscal_date_ending': result.get('fiscal_date_ending'),
                    'estimate': result.get('estimate'),
                    'currency': result.get('currency'),
                    'earnings_source': result.get('earnings_source'),
                    'timestamp': datetime.now(),
                }
                
                logger.info(f"Updated earnings for {ticker}: {result['earnings_date']} ({result.get('earnings_source', '?')})")
                return True
            else:
                self.db.mark_earnings_error(
                    ticker,
                    error_message=result['error'],
                    earnings_source=result.get('earnings_source'),
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
                'error_message': str or None,
                'time_of_day': str or None,
                'fiscal_date_ending': str or None,
                'estimate': float or None,
                'currency': str or None,
                'earnings_source': str or None,
            }
        """
        cache_entry = self._earnings_cache.get(ticker)
        if self._is_cache_valid(cache_entry, self._earnings_cache_duration_hours):
            earnings_date = cache_entry.get('earnings_date')
            time_of_day = cache_entry.get('time_of_day')
            fiscal_date_ending = cache_entry.get('fiscal_date_ending')
            estimate = cache_entry.get('estimate')
            currency = cache_entry.get('currency')
            earnings_source = cache_entry.get('earnings_source')
        elif self.db:
            record = self.db.get_earnings_date(ticker)
            if record:
                earnings_date = record.get('earnings_date')
                time_of_day = record.get('time_of_day')
                fiscal_date_ending = record.get('fiscal_date_ending')
                estimate = record.get('estimate')
                currency = record.get('currency')
                earnings_source = record.get('earnings_source')
                self._earnings_cache[ticker] = {
                    'earnings_date': earnings_date,
                    'time_of_day': time_of_day,
                    'fiscal_date_ending': fiscal_date_ending,
                    'estimate': estimate,
                    'currency': currency,
                    'earnings_source': earnings_source,
                    'timestamp': datetime.now(),
                }
            else:
                earnings_date = None
                time_of_day = fiscal_date_ending = estimate = currency = earnings_source = None
        else:
            earnings_date = None
            time_of_day = fiscal_date_ending = estimate = currency = earnings_source = None

        if not earnings_date:
            return {
                'earnings_date': None,
                'days_to_earnings': None,
                'warning_level': 'none',
                'fetch_status': 'pending' if not self.db else 'unknown',
                'error_message': None,
                'time_of_day': None,
                'fiscal_date_ending': None,
                'estimate': None,
                'currency': None,
                'earnings_source': None,
            }

        try:
            earnings_dt = datetime.strptime(earnings_date, '%Y-%m-%d')
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            days_to_earnings = (earnings_dt - today).days

            if days_to_earnings < 0:
                warning_level = 'past'
            elif days_to_earnings == 0:
                warning_level = 'today'
            elif days_to_earnings <= 3:
                warning_level = 'very_soon'
            elif days_to_earnings <= 7:
                warning_level = 'soon'
            elif days_to_earnings <= 30:
                warning_level = 'upcoming'
            else:
                warning_level = 'none'

            return {
                'earnings_date': earnings_date,
                'days_to_earnings': days_to_earnings,
                'warning_level': warning_level,
                'fetch_status': 'success',
                'error_message': None,
                'time_of_day': time_of_day,
                'fiscal_date_ending': fiscal_date_ending,
                'estimate': estimate,
                'currency': currency,
                'earnings_source': earnings_source,
            }

        except Exception as e:
            return {
                'earnings_date': earnings_date,
                'days_to_earnings': None,
                'warning_level': 'error',
                'fetch_status': 'error',
                'error_message': str(e),
                'time_of_day': time_of_day,
                'fiscal_date_ending': fiscal_date_ending,
                'estimate': estimate,
                'currency': currency,
                'earnings_source': earnings_source,
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
        
        source_tag = ''
        if info.get('earnings_source') or info.get('time_of_day'):
            parts = [p for p in [info.get('time_of_day'), info.get('earnings_source')] if p]
            source_tag = f" [{' · '.join(parts)}]"

        if info['warning_level'] == 'today':
            return (-30, f"Earnings today - extreme risk{source_tag}")
        elif info['warning_level'] == 'very_soon':
            return (-15, f"Earnings in {info['days_to_earnings']} days - high risk{source_tag}")
        elif info['warning_level'] == 'soon':
            return (-5, f"Earnings in {info['days_to_earnings']} days - caution{source_tag}")
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
