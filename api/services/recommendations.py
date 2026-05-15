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

            cash_available_for_csp = float(portfolio_context.get('cash_available_for_csp', 0) or 0)
            available_cash = float(portfolio_context.get('available_cash', 0) or 0)

            opts = yf_ticker.options
            if not opts:
                logger.warning(f"Watchlist {ticker}: no option expirations available from yfinance")
                return self._make_skip_diagnostic(ticker, 'no_option_chain', 'No option expirations from yfinance')

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
                return self._make_skip_diagnostic(ticker, 'no_valid_dte', 'No expiration with DTE 7-50 found')

            if cash_available_for_csp <= 0:
                logger.warning(f"Watchlist {ticker}: no cash available for CSPs")
                return self._make_skip_diagnostic(ticker, 'no_cash', 'No CSP buying power available')

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
                    puts = puts[puts['strike'] * 100 <= cash_available_for_csp]
                    if puts.empty:
                        logger.debug(f"Watchlist {ticker}: all strikes exceed CSP buying power for {target_exp}")
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

                        # Check spread
                        mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else max(bid, ask, last_price)
                        spread_pct = ((ask - bid) / mid_price) * 100 if bid > 0 and ask > 0 and mid_price > 0 else 100
                        if spread_pct > 100:
                            logger.debug(f"Watchlist {ticker}: spread too wide ({spread_pct:.1f}%) for strike {strike}")
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
                return self._make_skip_diagnostic(ticker, 'blocked_by_scoring', 'All candidates filtered by scoring')

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

    def _make_skip_diagnostic(self, ticker, reason_code, reason_text):
        """Create a diagnostic entry for a skipped CSP candidate."""
        return {
            '_skip_diagnostic': True,
            'ticker': ticker,
            'reason_code': reason_code,
            'reason_text': reason_text,
        }

    def _format_recommendation(self, option, rank=0):
        """Format a raw option dict into a standardized recommendation dict."""
        return {
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
            'size_fit': option.get('size_fit', 0),
            'expected_move_buffer': option.get('expected_move_buffer', 0),
            'wheel_decision': option.get('wheel_decision', {}),
            'from_watchlist': option.get('from_watchlist', False),
            'held_position': option.get('held_position', False),
            # CSP-specific fields
            'cash_required': option.get('cash_required'),
            'breakeven': option.get('breakeven'),
            'breakeven_buffer_pct': option.get('breakeven_buffer_pct'),
        }

    def get_top_recommendations(self, limit=5):
        """
        Get top N option recommendations across all portfolio positions and watchlist.

        Returns separate lanes for covered calls and watchlist CSPs,
        plus the legacy mixed recommendations list for backwards compatibility.

        Filters options by capital availability:
        - CALLs: Only if user has 100+ shares
        - PUTs: Only if user has sufficient cash (strike * 100)

        Args:
            limit (int): Number of top recommendations to return (default: 5, max: 10)

        Returns:
            dict: {
                'success': True,
                'count': int,
                'total_scored': int,
                'generated_at': str (ISO timestamp),
                'recommendations': [list of recommendation dicts],
                'lanes': {
                    'covered_calls': {
                        'count': int,
                        'recommendations': [...],
                    },
                    'watchlist_csp': {
                        'count': int,
                        'recommendations': [...],
                    },
                },
                'cash_available_for_csp': float,
                'cash_reserved_for_csp': float,
            }
        """
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
            available_cash = float(portfolio_context.get('available_cash', 0) or 0)
            cash_available_for_csp = float(portfolio_context.get('cash_available_for_csp', 0) or 0)
            cash_reserved_for_csp = float(portfolio_context.get('cash_reserved_for_csp', 0) or 0)
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
                    'lanes': {
                        'covered_calls': {'count': 0, 'recommendations': []},
                        'watchlist_csp': {'count': 0, 'recommendations': []},
                    },
                    'cash_available_for_csp': cash_available_for_csp,
                    'cash_reserved_for_csp': cash_reserved_for_csp,
                    'message': 'No positions or watchlist configured'
                }

            # ── Lanes ──────────────────────────────────────────────────
            covered_call_candidates = []
            watchlist_csp_candidates = []

            # ── Diagnostics ────────────────────────────────────────────
            watchlist_processed = 0
            watchlist_cached = 0
            watchlist_errors = 0
            skipped_csp_diagnostics = []
            ticker_diagnostics = {}

            # ════════════════════════════════════════════════════════════
            # LANE 1: Watchlist CSPs — process ALL watchlist tickers
            # (including those already held as positions)
            # ════════════════════════════════════════════════════════════
            for ticker in effective_watchlist:
                is_held = ticker in positions

                cached = self._get_cached_watchlist_data(ticker)
                if cached:
                    watchlist_cached += 1
                    if cached.get('_skip_diagnostic'):
                        skipped_csp_diagnostics.append(cached)
                        continue
                    cached['held_position'] = is_held
                    watchlist_csp_candidates.append(cached)
                    continue

                result = self._fetch_watchlist_ticker_csp(ticker, portfolio_context)
                if result is None:
                    watchlist_errors += 1
                    continue

                if result.get('_skip_diagnostic'):
                    skipped_csp_diagnostics.append(result)
                    self._set_cached_watchlist_data(ticker, result)
                    continue

                self._set_cached_watchlist_data(ticker, result)
                result['held_position'] = is_held
                watchlist_csp_candidates.append(result)
                watchlist_processed += 1

            logger.info(f"Watchlist CSP: {watchlist_cached} cached, {watchlist_processed} fetched, "
                       f"{watchlist_errors} errors, {len(skipped_csp_diagnostics)} skipped")

            # ════════════════════════════════════════════════════════════
            # LANE 2: Covered Calls — from portfolio positions only
            # ════════════════════════════════════════════════════════════
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

                    # Process CALLs — covered call lane
                    if available_calls > 0:
                        for call in result.get('calls', []):
                            covered_call_candidates.append({
                                'ticker': ticker,
                                'stock_price': stock_price,
                                'option_type': 'CALL',
                                'max_contracts': available_calls,
                                'existing_position': existing_short_calls,
                                'held_position': False,
                                'from_watchlist': False,
                                **call
                            })

                    # Process PUTs — add to watchlist CSP lane if ticker is
                    # also on watchlist (supplementary data from Moomoo)
                    if ticker in effective_watchlist:
                        cash_remaining = cash_available_for_csp
                        for put in result.get('puts', []):
                            cash_required = float(put.get('strike', 0)) * 100
                            if cash_required > 0 and cash_remaining >= cash_required:
                                watchlist_csp_candidates.append({
                                    'ticker': ticker,
                                    'stock_price': stock_price,
                                    'option_type': 'PUT',
                                    'max_contracts': 1,
                                    'existing_position': existing_short_puts,
                                    'held_position': True,
                                    'from_watchlist': True,
                                    **put
                                })
                                cash_remaining -= cash_required

                except Exception as e:
                    logger.error(f"Error processing {ticker} for recommendations: {e}")
                    continue

            # ── Per-ticker diagnostics ───────────────────────────────
            all_candidates = covered_call_candidates + watchlist_csp_candidates
            for opt in all_candidates:
                t = opt.get('ticker', 'UNKNOWN')
                score = opt.get('score', 0)
                if t not in ticker_diagnostics:
                    ticker_diagnostics[t] = {'top_score': score, 'candidate_count': 0, 'filtered_out': False}
                else:
                    ticker_diagnostics[t]['top_score'] = max(ticker_diagnostics[t]['top_score'], score)
                ticker_diagnostics[t]['candidate_count'] += 1

            # ── Sort each lane by score ──────────────────────────────
            covered_call_candidates.sort(key=lambda x: x.get('score', 0), reverse=True)
            watchlist_csp_candidates.sort(key=lambda x: x.get('score', 0), reverse=True)

            # ── Build lane results with diversity safeguard ──────────
            def _select_top(candidates, max_per=1):
                """Select top candidates with at most max_per per ticker."""
                seen = set()
                selected = []
                for opt in candidates:
                    t = opt.get('ticker', 'UNKNOWN')
                    count = sum(1 for o in selected if o.get('ticker') == t)
                    if count < max_per:
                        selected.append(opt)
                        seen.add(t)
                return selected

            top_covered_calls = _select_top(covered_call_candidates, max_per=1)
            top_watchlist_csp = _select_top(watchlist_csp_candidates, max_per=1)

            # ── Legacy mixed recommendations list ────────────────────
            # Merge both lanes, interleaved by score, for backwards compatibility
            all_sorted = sorted(all_candidates, key=lambda x: x.get('score', 0), reverse=True)
            top_mixed = _select_top(all_sorted, max_per=1)[:limit]

            # Log per-ticker diagnostics
            for t, diag in sorted(ticker_diagnostics.items(), key=lambda x: x[1]['top_score'], reverse=True):
                logger.info(f"  TICKER {t}: top_score={diag['top_score']:.1f}, candidates={diag['candidate_count']}")

            # ── Format all recommendations ───────────────────────────
            recommendations = []
            for rank, option in enumerate(top_mixed, 1):
                recommendations.append(self._format_recommendation(option, rank))

            formatted_cc = []
            for rank, option in enumerate(top_covered_calls, 1):
                formatted_cc.append(self._format_recommendation(option, rank))

            formatted_csp = []
            for rank, option in enumerate(top_watchlist_csp, 1):
                formatted_csp.append(self._format_recommendation(option, rank))

            elapsed = time.time() - start_time
            logger.info(f"Generated {len(recommendations)} mixed recs, "
                       f"{len(formatted_cc)} CC, {len(formatted_csp)} CSP in {elapsed:.2f}s")

            return {
                'success': True,
                'count': len(recommendations),
                'total_scored': len(all_candidates),
                'generated_at': datetime.now().isoformat(),
                'recommendations': recommendations,
                'lanes': {
                    'covered_calls': {
                        'count': len(formatted_cc),
                        'recommendations': formatted_cc,
                    },
                    'watchlist_csp': {
                        'count': len(formatted_csp),
                        'recommendations': formatted_csp,
                    },
                },
                'cash_available_for_csp': round(cash_available_for_csp, 2),
                'cash_reserved_for_csp': round(cash_reserved_for_csp, 2),
                '_diagnostics': {
                    'tickers': ticker_diagnostics,
                    'limit_applied': limit,
                    'max_per_ticker': 1,
                    'watchlist_processed': watchlist_processed,
                    'watchlist_cached': watchlist_cached,
                    'watchlist_errors': watchlist_errors,
                    'position_tickers': list(positions.keys()) if positions else [],
                    'watchlist_tickers': effective_watchlist,
                    'skipped_csp': skipped_csp_diagnostics,
                    'skipped_csp_count': len(skipped_csp_diagnostics),
                }
            }

        except Exception as e:
            logger.exception(f"Error getting top recommendations: {e}")
            return {'error': str(e)}
