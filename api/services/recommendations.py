"""
Recommendations module - handles top options recommendations
Extracted from the monolithic options_service.py for maintainability.
"""

import logging
import time
import pandas as pd
from datetime import datetime
from core.utils import get_closest_friday
from core.wheel_decision import score_contract
from api.services.iv_earnings_service import IVEarningsService
from api.services.utils import clean_yfinance_ticker
from api.services.macro_regime_service import get_macro_service

logger = logging.getLogger('api.services.recommendations')


class RecommendationEngine:
    """
    Handles generating top options recommendations across portfolio positions and watchlist.
    """
    
    def __init__(self, connection_provider, config_provider, db, iv_earnings_service,
                 portfolio_context_provider, portfolio_service_provider,
                 watchlist_provider, options_data_provider, cash_calculator_provider):
        self._connection_provider = connection_provider
        self._config_provider = config_provider
        self.config = config_provider.config if hasattr(config_provider, 'config') else config_provider
        self.db = db
        self.iv_earnings_service = iv_earnings_service
        self._portfolio_context_provider = portfolio_context_provider
        self._portfolio_service_provider = portfolio_service_provider
        self._watchlist_provider = watchlist_provider
        self._options_data_provider = options_data_provider
        self._cash_calculator_provider = cash_calculator_provider
        self._yfinance_cache = {}
        self._yfinance_cache_ttl = 300  # 5 minutes
        
    def _get_connection(self):
        return self._connection_provider._ensure_connection()
    
    def _get_portfolio_context(self):
        return self._portfolio_context_provider.get_portfolio_context()
    
    def _get_portfolio_service(self):
        if self._portfolio_service_provider:
            return self._portfolio_service_provider.portfolio_service
        return None
    
    def _strip_ticker_prefix(self, ticker):
        return clean_yfinance_ticker(ticker)

    def _is_cache_valid(self, entry):
        if not entry or 'timestamp' not in entry:
            return False
        return (datetime.now() - entry['timestamp']).total_seconds() < self._yfinance_cache_ttl

    def _get_cached_watchlist_data(self, ticker):
        entry = self._yfinance_cache.get(ticker)
        if self._is_cache_valid(entry):
            return entry['data']
        return None

    def _set_cached_watchlist_data(self, ticker, data):
        self._yfinance_cache[ticker] = {'data': data, 'timestamp': datetime.now()}

    def _fetch_watchlist_ticker_csp(self, ticker, portfolio_context):
        try:
            import yfinance as yf
            yf_ticker = yf.Ticker(ticker)
            hist = yf_ticker.history(period="1d")
            if hist.empty:
                logger.warning(f"Watchlist {ticker}: yfinance history empty")
                return None
            stock_price = float(hist['Close'].iloc[-1])

            cash_balance = float(portfolio_context.get('cash_balance', 0) or 0)
            excess_liquidity = float(portfolio_context.get('excess_liquidity', 0) or 0)
            available_cash = max(cash_balance, excess_liquidity)

            opts = yf_ticker.options
            if not opts:
                logger.warning(f"Watchlist {ticker}: no option expirations available from yfinance")
                return None

            today = datetime.now()
            valid_expirations = []
            for exp in opts:
                try:
                    exp_date = datetime.strptime(exp, '%Y-%m-%d')
                    dte = (exp_date - today).days
                    if 7 <= dte <= 50:
                        valid_expirations.append(exp)
                except ValueError:
                    continue

            if not valid_expirations:
                logger.warning(f"Watchlist {ticker}: no expiration with DTE 7-45 found")
                return None

            valid_expirations = sorted(valid_expirations, key=lambda e: abs(21 - (datetime.strptime(e, '%Y-%m-%d') - today).days))
            expirations_to_check = valid_expirations[:3]

            best_result = None
            best_score = -1

            for target_exp in expirations_to_check:
                try:
                    chain = yf_ticker.option_chain(target_exp)
                    if chain.puts.empty:
                        continue
                    puts = chain.puts.copy()
                    puts = puts[puts['strike'] * 100 <= available_cash]
                    if puts.empty:
                        continue

                    for _, put_row in puts.iterrows():
                        strike = float(put_row['strike'])
                        if strike >= stock_price:
                            continue

                        bid = float(put_row['bid']) if not pd.isna(put_row['bid']) else 0
                        ask = float(put_row['ask']) if not pd.isna(put_row['ask']) else 0
                        last_price = float(put_row['lastPrice']) if not pd.isna(put_row['lastPrice']) else 0

                        if bid <= 0 and ask <= 0 and last_price <= 0:
                            continue

                        dte = (datetime.strptime(target_exp, '%Y-%m-%d') - today).days

                        contract = {
                            'strike': strike,
                            'expiration': target_exp.replace('-', ''),
                            'option_type': 'PUT',
                            'bid': bid,
                            'ask': ask,
                            'last': last_price,
                            'dte': dte,
                            'implied_volatility': float(put_row['impliedVolatility']) if not pd.isna(put_row.get('impliedVolatility')) else 0,
                            'open_interest': int(put_row['openInterest']) if not pd.isna(put_row['openInterest']) else 0,
                            'volume': int(put_row['volume']) if not pd.isna(put_row['volume']) else 0,
                        }

                        from core.greeks import enrich_option_with_greeks
                        enrich_option_with_greeks(contract, stock_price)

                        wl_profile = self._watchlist_provider.get_screening_profile('PUT', dte=dte)
                        wl_profile['min_open_interest'] = 0
                        wl_profile['min_volume'] = 0
                        wl_profile['min_premium_per_contract'] = 0
                        wl_profile['max_spread_pct'] = 100

                        decision = score_contract(
                            ticker=ticker,
                            option=contract,
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

                        if decision is not None and decision.contract_score > best_score:
                            best_score = decision.contract_score
                            best_result = (strike, target_exp, dte, decision, stock_price)

                except Exception:
                    continue

            if best_result is None:
                logger.warning(f"Watchlist {ticker}: all candidates filtered by score_contract")
                return None

            _, _, _, wl_decision, stock_price = best_result
            return {
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
                'warnings': wl_decision.warnings + ['Data from yfinance (not Moomoo) - verify before trading'],
                'cash_reserve_enabled': self.config.get('cash_reserve_enabled', True),
                'breakeven': wl_decision.breakeven,
                'breakeven_buffer_pct': wl_decision.breakeven_buffer_pct,
                'cash_required': wl_decision.cash_required,
            }
        except Exception as e:
            logger.warning(f"Watchlist {ticker}: yfinance fetch failed: {e}")
            return None

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
            conn = self._get_connection()
            if not conn:
                return {'error': 'Failed to establish connection to moomoo'}
            
            # Get portfolio context for positions and cash balance
            portfolio_context = self._get_portfolio_context()
            positions = portfolio_context.get('positions', {})
            cash_balance = float(portfolio_context.get('cash_balance', 0) or 0)
            excess_liquidity = float(portfolio_context.get('excess_liquidity', 0) or 0)
            available_cash = max(cash_balance, excess_liquidity)
            short_calls = portfolio_context.get('short_calls', {})
            short_puts = portfolio_context.get('short_puts', {})

            effective_watchlist = self._watchlist_provider.get_effective_watchlist()
            logger.info(f"Effective watchlist: {len(effective_watchlist)} tickers")

            if not positions and not effective_watchlist:
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

            # Process watchlist tickers FIRST (before positions, since it's fast/cached)
            # Use yfinance since Moomoo account doesn't have US stock quote rights
            watchlist_processed = 0
            watchlist_cached = 0
            watchlist_errors = 0
            watchlist_candidates = []

            for ticker in effective_watchlist:
                if ticker in positions:
                    logger.debug(f"Watchlist {ticker}: skipped (already in portfolio positions)")
                    continue

                cached = self._get_cached_watchlist_data(ticker)
                if cached:
                    watchlist_cached += 1
                    logger.debug(f"Watchlist {ticker}: using cached data")
                    watchlist_candidates.append(cached)
                    continue

                result = self._fetch_watchlist_ticker_csp(ticker, portfolio_context)
                if result:
                    self._set_cached_watchlist_data(ticker, result)
                    watchlist_candidates.append(result)
                    watchlist_processed += 1
                else:
                    watchlist_errors += 1

            logger.info(f"Watchlist: {watchlist_cached} cached, {watchlist_processed} fetched, {watchlist_errors} errors")

            all_options.extend(watchlist_candidates)
            watchlist_candidates = []  # Reset for potential reuse

            # Process each ticker
            for ticker in positions.keys():
                try:
                    # Get stock price for this ticker
                    stock_price = conn.get_stock_price(ticker)
                    if stock_price is None or stock_price <= 0:
                        position = portfolio_context.get('positions', {}).get(ticker, {})
                        stock_price = float(position.get('market_price', 0) or position.get('avg_cost', 0) or 0)
                    
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
                    result = self._options_data_provider._process_ticker_for_otm(
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
                        if cash_required > 0 and available_cash >= cash_required:
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

            # Per-ticker diagnostic: collect top score and candidate count per ticker
            ticker_diagnostics = {}
            for opt in all_options:
                t = opt.get('ticker', 'UNKNOWN')
                score = opt.get('score', 0)
                if t not in ticker_diagnostics:
                    ticker_diagnostics[t] = {'top_score': score, 'candidate_count': 0, 'filtered_out': False}
                else:
                    ticker_diagnostics[t]['top_score'] = max(ticker_diagnostics[t]['top_score'], score)
                ticker_diagnostics[t]['candidate_count'] += 1

            # Sort by score descending
            all_options.sort(key=lambda x: x.get('score', 0), reverse=True)

            # Ticker diversity safeguard: pick top N with at most max_per_ticker per ticker
            # This ensures UBER can't dominate all top spots even if it has the best scores
            max_per_ticker = 1  # 1 recommendation max per ticker
            seen_tickers = set()
            top_options = []
            for opt in all_options:
                t = opt.get('ticker', 'UNKNOWN')
                if t not in seen_tickers:
                    top_options.append(opt)
                    seen_tickers.add(t)
                else:
                    # Count how many we already have for this ticker
                    count = sum(1 for o in top_options if o.get('ticker') == t)
                    if count < max_per_ticker:
                        top_options.append(opt)
                if len(top_options) >= limit:
                    break

            # Log per-ticker diagnostics
            for t, diag in sorted(ticker_diagnostics.items(), key=lambda x: x[1]['top_score'], reverse=True):
                logger.info(f"  TICKER {t}: top_score={diag['top_score']:.1f}, candidates={diag['candidate_count']}")
            
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
                'recommendations': recommendations,
                '_diagnostics': {
                    'tickers': ticker_diagnostics,
                    'limit_applied': limit,
                    'max_per_ticker': max_per_ticker,
                    'watchlist_processed': watchlist_processed,
                    'watchlist_cached': watchlist_cached,
                    'watchlist_errors': watchlist_errors,
                    'position_tickers': list(positions.keys()) if positions else [],
                    'watchlist_tickers': effective_watchlist,
                }
            }
            
        except Exception as e:
            logger.exception(f"Error getting top recommendations: {e}")
            return {'error': str(e)}
