"""
Options Data module - handles options data retrieval and processing
Extracted from the monolithic options_service.py for maintainability.
"""

import logging
import math
import time
from datetime import datetime, timedelta
import pandas as pd
from core.wheel_decision import score_contract
from core.utils import get_closest_friday
from db.database import OptionsDatabase
from api.services.iv_earnings_service import IVEarningsService
from api.services.utils import clean_yfinance_ticker
from api.services.macro_regime_service import get_macro_service

logger = logging.getLogger('api.services.options_data')


class OptionsDataService:
    """
    Handles options data retrieval, chain processing, and candidate building.
    """
    
    def __init__(self, connection_provider, config_provider, db, iv_earnings_service,
                 screening_profile_provider, portfolio_context_provider):
        self._connection_provider = connection_provider
        self._config_provider = config_provider
        self.db = db
        self.iv_earnings_service = iv_earnings_service
        self._screening_profile_provider = screening_profile_provider
        self._portfolio_context_provider = portfolio_context_provider
        self._yfinance_iv_cache = {}
        
    def _get_connection(self):
        return self._connection_provider._ensure_connection()
    
    def _get_portfolio_context(self):
        return self._portfolio_context_provider.get_portfolio_context()
    
    def _strip_ticker_prefix(self, ticker):
        return clean_yfinance_ticker(ticker)

    def _get_yfinance_price(self, ticker):
        """Get stock price from yfinance as fallback when Moomoo lacks quote rights."""
        try:
            import yfinance as yf
            bare_ticker = self._strip_ticker_prefix(ticker)
            yf_ticker = yf.Ticker(bare_ticker)
            hist = yf_ticker.history(period="1d")
            if hist.empty:
                logger.warning(f"yfinance: No price data for {bare_ticker}")
                return None
            price = float(hist['Close'].iloc[-1])
            logger.debug(f"yfinance: Got price {price} for {bare_ticker}")
            return price
        except Exception as e:
            logger.warning(f"yfinance: Failed to get price for {ticker}: {e}")
            return None

    def _get_yfinance_option_chain(self, ticker, expiration, option_type):
        """Get option chain from yfinance as fallback when Moomoo lacks quote rights."""
        try:
            import yfinance as yf
            bare_ticker = self._strip_ticker_prefix(ticker)
            yf_ticker = yf.Ticker(bare_ticker)
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
            logger.warning(f"yfinance: Failed to get option chain for {ticker}: {e}")
            return None

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

        # -- Enrich option with yfinance IV and computed BS Greeks --------------
        iv = float(option.get('implied_volatility', 0) or 0)
        delta = float(option.get('delta', 0) or 0)

        # If IV is 0, try yfinance fallback
        if iv <= 0:
            from core.greeks import fetch_yfinance_iv_for_chain
            exp = str(option.get('expiration', ''))
            opt_type = 'C' if str(option.get('option_type', '')).upper() == 'CALL' else 'P'
            iv_map = fetch_yfinance_iv_for_chain(ticker, exp, opt_type, self._yfinance_iv_cache)
            if iv_map:
                strike = float(option.get('strike', 0))
                iv = iv_map.get(strike, 0)
                if iv > 0:
                    option['implied_volatility'] = iv

        # If IV is available but Greeks are 0, compute BS Greeks
        if iv > 0 and abs(delta) < 0.001:
            from core.greeks import enrich_option_with_greeks
            option['delta'] = 0  # Reset so enrich function sees need to compute
            enrich_option_with_greeks(option, stock_price)

        # -- Update IV context with enriched IV --------------------------------
        iv = float(option.get('implied_volatility', 0) or 0)
        if iv > 0:
            iv_env_adjustment, iv_rank, iv_status = self.iv_earnings_service.get_iv_environment_score(
                ticker, iv
            )

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

        # TODO 1.1: Handle hard blockers (returned as WheelDecision with hard_blockers)
        if decision and decision.hard_blockers:
            # Return a minimal candidate with hard blockers for API consumers
            return {
                'symbol': decision.ticker,
                'strike': decision.strike,
                'expiration': decision.expiration,
                'option_type': decision.option_type,
                'hard_blockers': decision.hard_blockers,
                'warnings': decision.warnings,
                'score': 0,
                'score_details': {},
            }

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
            'hard_blockers': decision.hard_blockers,  # TODO 1.1
            'otm_pct': decision.otm_pct,
            'annualized_return': decision.annualized_return,
            'return_on_underlying': decision.return_on_underlying,  # TODO 1.2
            'return_on_secured_cash': decision.return_on_secured_cash,  # TODO 1.2
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
            # Data provenance (TODO 2.1)
            'price_source': decision.price_source,
            'chain_source': decision.chain_source,
            'greeks_source': decision.greeks_source,
            'iv_source': decision.iv_source,
            'earnings_source': decision.earnings_source,
            'macro_source': decision.macro_source,
            'quote_timestamp': decision.quote_timestamp,
            'generated_at': decision.generated_at,
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
                'cash_reserve_enabled': self._config_provider.config.get('cash_reserve_enabled', True),
                'earnings_max_contracts': 1,
                'earnings_premium_per_contract': round(decision.premium_per_contract, 2),
                'earnings_total_premium': round(decision.premium_per_contract, 2),
                'earnings_return_on_cash': round(decision.annualized_return, 2),
            })

        return candidate

    def _get_candidate_expirations(self, conn, ticker, profile, expiration=None):
        if expiration:
            return [expiration]

        try:
            # Use the rate-limited method in MoomooConnection
            from moomoo import RET_OK
            ret, data = conn.get_option_expiration_dates(ticker)
            if ret != RET_OK or data is None or data.empty:
                fallback = get_closest_friday().strftime('%Y%m%d')
                logger.debug(f"get_option_expiration_dates failed for {ticker}: ret={ret}, data empty or None")
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

            min_dte = profile.get('min_dte', 0)
            max_dte = profile.get('max_dte', 365)
            logger.debug(f"_get_candidate_expirations for {ticker}: min_dte={min_dte} (type={type(min_dte)}), max_dte={max_dte} (type={type(max_dte)})")

            for raw_date in data[expiration_column].tolist():
                normalized = raw_date.replace('-', '')
                expiry_date = datetime.strptime(normalized, '%Y%m%d').date()
                dte = (expiry_date - today).days
                logger.debug(f"  Checking expiration {normalized}: dte={dte} (type={type(dte)})")
                if dte <= 0:
                    continue
                fallback.append((normalized, dte))
                try:
                    if min_dte <= dte <= max_dte:
                        filtered.append((normalized, dte))
                except TypeError as te:
                    logger.error(f"TypeError in DTE comparison: min_dte={min_dte}, dte={dte}, max_dte={max_dte}")
                    logger.error(f"  Types: min_dte={type(min_dte)}, dte={type(dte)}, max_dte={type(max_dte)}")
                    raise

            expirations = filtered or fallback
            result = [value for value, _ in expirations[:profile.get('max_expirations', 5)]] or [get_closest_friday().strftime('%Y%m%d')]
            logger.debug(f"_get_candidate_expirations returning {len(result)} expirations: {result}")
            return result
        except Exception as exc:
            logger.exception(f"Error loading option expirations for {ticker}: {exc}")
            return [get_closest_friday().strftime('%Y%m%d')]

    def get_otm_options(self, ticker, otm_percentage=10, option_type=None, expiration=None):
        """
        Return ranked wheel candidates near the requested OTM preference.
        """
        start_time = time.time()

        if option_type and option_type not in ['CALL', 'PUT']:
            return {'error': f"Invalid option_type: {option_type}. Must be 'CALL' or 'PUT'"}

        conn = self._get_connection()
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
            logger.exception(f"Error processing {ticker} for optimal options: {exc}")
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

        try:
            stock_price = conn.get_stock_price(ticker)
            if stock_price is None or stock_price <= 0:
                logger.debug(f"Moomoo returned no/invalid price for {ticker}, trying yfinance fallback")
                stock_price = self._get_yfinance_price(ticker)
            if stock_price is None or stock_price <= 0:
                position = portfolio_context.get('positions', {}).get(ticker, {})
                stock_price = float(position.get('market_price', 0) or position.get('avg_cost', 0) or 0)
            if stock_price is None or stock_price <= 0:
                result['error'] = 'Unable to obtain valid stock price from any source'
                return result

            position = portfolio_context.get('positions', {}).get(ticker, {})
            result['stock_price'] = stock_price
            result['position'] = float(position.get('position', 0) or 0)
            result['avg_cost'] = float(position.get('avg_cost', 0) or 0)

            sides = [option_type] if option_type else ['CALL', 'PUT']
            options_chains = []

            for side in sides:
                profile = self._screening_profile_provider.get_screening_profile(side)
                logger.debug(f"Processing {ticker} {side} with profile: min_dte={profile.get('min_dte')}, max_dte={profile.get('max_dte')}, preferred_dte={profile.get('preferred_dte')}")
                expirations = self._get_candidate_expirations(conn, ticker, profile, expiration)
                logger.debug(f"Got {len(expirations)} expirations for {ticker} {side}: {expirations[:3]}...")
                
                target_strike = stock_price * (1 + (otm_percentage / 100)) if side == 'CALL' else stock_price * (1 - (otm_percentage / 100))
                for expiry in expirations:
                    try:
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
                    except Exception as chain_exc:
                        logger.exception(f"Error getting option chain for {ticker} {expiry} {side}: {chain_exc}")

            if not options_chains:
                result['error'] = 'No options data available from any source'
                return result

            formatted_data = self._process_options_chain(
                options_chains,
                ticker,
                stock_price,
                otm_percentage,
                portfolio_context,
                option_type
            )
            result.update(formatted_data)
        except Exception as exc:
            logger.exception(f"Error in _process_ticker_for_otm for {ticker}: {exc}")
            result['error'] = str(exc)
            
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

                profile = self._screening_profile_provider.get_screening_profile(side)
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
            logger.exception(f"Error processing options chain: {exc}")
            return {}

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
            conn = self._get_connection()
            if not conn: return {"error": "No connection"}
            
            # Use the rate-limited method in MoomooConnection
            from moomoo import RET_OK
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

    def get_stock_price(self, ticker):
        conn = self._get_connection()
        if not conn:
            return 0

        live_price = conn.get_stock_price(ticker)
        if live_price is not None and live_price > 0:
            return live_price

        portfolio_context = self._get_portfolio_context()
        position = portfolio_context.get('positions', {}).get(ticker, {})
        for field in ('market_price', 'avg_cost'):
            value = position.get(field)
            try:
                numeric_value = float(value or 0)
            except (TypeError, ValueError):
                numeric_value = 0
            if numeric_value > 0:
                return numeric_value
        return 0
