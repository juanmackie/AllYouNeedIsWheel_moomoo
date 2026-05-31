"""
Signals module - handles top options signals.
Extracted from the monolithic options_service.py for maintainability.
"""

import logging
import time
import pandas as pd
from datetime import datetime
from core.wheel_decision import score_contract
from core.ticker_utils import canonical_underlying
from core.connection_constants import _normalize_iv
from api.services.utils import clean_yfinance_ticker, get_yfinance_ticker

logger = logging.getLogger('api.services.recommendations')


def _is_valid_external_option(option: dict, stock_price: float) -> bool:
    """Return True when an external option payload has the minimum safe fields."""
    try:
        strike = float(option.get('strike', 0) or 0)
        bid = float(option.get('bid', 0) or 0)
        ask = float(option.get('ask', 0) or 0)
        last = float(option.get('last', 0) or 0)
        dte = int(option.get('dte', 0) or 0)
    except (TypeError, ValueError):
        return False

    if strike <= 0 or stock_price <= 0 or dte <= 0:
        return False
    if bid < 0 or ask < 0 or last < 0:
        return False
    if bid <= 0 and ask <= 0 and last <= 0:
        return False
    if option.get('option_type') not in {'CALL', 'PUT'}:
        return False
    return True


def _format_decision_to_candidate(
    ticker: str,
    stock_price: float,
    decision,
    extra_warnings: list[str] | None = None,
    cash_reserve_enabled: bool = True,
) -> dict:
    """
    Standardised result dict from a WheelDecision for CSP candidates.

    Shared by both Moomoo and yfinance CSP paths so that fields
    never diverge between data sources.
    """
    warnings = list(decision.warnings)
    if extra_warnings:
        warnings.extend(extra_warnings)
    return {
        'ticker': ticker,
        'stock_price': stock_price,
        'option_type': 'PUT',
        'max_contracts': 1,
        'existing_position': 0,
        'from_watchlist': True,
        'strike': decision.strike,
        'expiration': decision.expiration,
        'dte': decision.dte,
        'mid_price': round(decision.mid_price, 4),
        'premium_per_contract': round(decision.premium_per_contract, 2),
        'bid': decision.bid,
        'ask': decision.ask,
        'annualized_return': decision.annualized_return,
        'iv_adjusted_return': decision.iv_adjusted_return,
        'otm_pct': decision.otm_pct,
        'delta': decision.delta,
        'implied_volatility': decision.implied_volatility,
        'open_interest': decision.open_interest,
        'volume': decision.volume,
        'score': round(decision.contract_score, 2),
        'iv_rank': decision.iv_rank,
        'iv_status': decision.iv_status,
        'iv_env_adjustment': decision.iv_env_adjustment,
        'profile_type': decision.profile_type,
        'earnings_date': None,
        'days_to_earnings': None,
        'earnings_adjustment': 0,
        'size_fit': decision.size_fit,
        'expected_move_buffer': decision.expected_move_buffer,
        'wheel_decision': decision.to_dict(),
        'score_details': decision.score_details,
        'rationale': decision.rationale,
        'warnings': warnings,
        'cash_reserve_enabled': cash_reserve_enabled,
        'breakeven': decision.breakeven,
        'breakeven_buffer_pct': decision.breakeven_buffer_pct,
        'cash_required': decision.cash_required,
    }


class RecommendationEngine:
    """
    Handles generating top options signals across portfolio positions and watchlist.
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

    def _csp_target_strike_estimate(self, stock_price: float, portfolio_context: dict) -> float:
        """Estimate the cash requirement for the default CSP target strike."""
        growth_cfg = getattr(self, '_growth_screener_config', None)
        sp = (growth_cfg.get('screener_profile', {}) or {}) if growth_cfg else {}
        otm_pct = float(sp.get('csp_default_otm_pct', 10) or 10)
        target_strike = stock_price * (1 - (otm_pct / 100))
        return max(target_strike, 0.0)

    def _can_afford_csp(self, stock_price: float, portfolio_context: dict) -> bool:
        """Return True when the default CSP target strike fits buying power."""
        cash_available_for_csp = float(portfolio_context.get('cash_available_for_csp', 0) or 0)
        if cash_available_for_csp <= 0 or stock_price <= 0:
            return False
        return self._csp_target_strike_estimate(stock_price, portfolio_context) * 100 <= cash_available_for_csp

    def _fetch_watchlist_ticker_csp(self, ticker, portfolio_context):
        """
        Fetch CSP candidates for a watchlist ticker.
        Tries Moomoo first, falls back to yfinance.
        Returns list of candidate dicts, a list with a skip diagnostic, or None on error.
        """
        candidates = self._fetch_watchlist_csp_moomoo(ticker, portfolio_context)
        if candidates is not None:
            return candidates
        return self._fetch_yfinance_csp_candidates(ticker, portfolio_context)

    def _fetch_watchlist_csp_moomoo(self, ticker, portfolio_context):
        """
        Fetch CSP candidates from Moomoo for a watchlist ticker.
        Returns list of candidate dicts, or None on failure.
        """
        try:
            conn = self._get_connection()
            if not conn:
                return None

            cash_available_for_csp = float(portfolio_context.get('cash_available_for_csp', 0) or 0)
            if cash_available_for_csp <= 0:
                return None

            # Short-circuit cash-fit failures using cached price, avoiding rate limiter entirely
            cached_price = conn.get_cached_stock_price(ticker)
            if cached_price is not None and cached_price > 0:
                min_strike_estimate = cached_price * 0.50
                if min_strike_estimate * 100 > cash_available_for_csp:
                    logger.debug(f"Watchlist {ticker}: even deep OTM put (${min_strike_estimate:.2f}) exceeds buying power ${cash_available_for_csp:.2f}")
                    return None

            stock_price = conn.get_stock_price(ticker)
            if stock_price is None or stock_price <= 0:
                return None

            growth_cfg = getattr(self, '_growth_screener_config', None)
            sp = (growth_cfg.get('screener_profile', {}) or {}) if growth_cfg else {}
            min_dte = sp.get('csp_min_dte', 30)
            max_dte = sp.get('csp_max_dte', 45)
            pref_dte = sp.get('csp_preferred_dte', 37)

            # Cash-fit prefilter: skip if the default target strike is still too expensive.
            target_strike_estimate = self._csp_target_strike_estimate(stock_price, portfolio_context)
            if not self._can_afford_csp(stock_price, portfolio_context):
                logger.debug(
                    f"Watchlist {ticker}: target CSP strike (${target_strike_estimate:.2f}) exceeds buying power ${cash_available_for_csp:.2f}"
                )
                return None

            from moomoo import RET_OK
            today = datetime.now()
            ret, data = conn.get_option_expiration_dates(ticker)
            if ret != RET_OK or data is None:
                return None

            expiration_column = 'expiration_date'
            if expiration_column not in data.columns:
                if 'strike_time' in data.columns:
                    expiration_column = 'strike_time'
                elif 'option_expiry_date' in data.columns:
                    expiration_column = 'option_expiry_date'
                else:
                    return None

            valid_expirations = []
            for raw_date in data[expiration_column].tolist():
                exp_str = str(raw_date).replace('-', '')
                try:
                    exp_date = datetime.strptime(exp_str, '%Y%m%d').date()
                    dte = (exp_date - today.date()).days
                    if min_dte <= dte <= max_dte:
                        valid_expirations.append((exp_str, dte))
                except ValueError:
                    continue

            if not valid_expirations:
                return None

            valid_expirations.sort(key=lambda x: abs(pref_dte - x[1]))
            expirations_to_check = valid_expirations[:1]

            from api.services.macro_regime_service import get_macro_service
            macro_regime = get_macro_service().get_macro_regime()

            candidates = []
            seen = set()

            for exp_str, dte in expirations_to_check:
                try:
                    chain = conn.get_option_chain(
                        ticker, exp_str, 'P',
                        target_strike=stock_price * (1 - (sp.get('csp_default_otm_pct', 10) / 100))
                    )
                    if not chain:
                        continue
                    options = chain.get('options', [])
                    if not options:
                        continue

                    for opt in options:
                        if not _is_valid_external_option(opt, stock_price):
                            continue
                        strike = float(opt.get('strike', 0) or 0)
                        if strike >= stock_price or strike <= 0:
                            continue
                        cash_required = strike * 100
                        if cash_required > cash_available_for_csp:
                            continue

                        key = (strike, exp_str)
                        if key in seen:
                            continue
                        seen.add(key)

                        bid = float(opt.get('bid', 0) or 0)
                        ask = float(opt.get('ask', 0) or 0)
                        last = float(opt.get('last', 0) or 0)
                        if bid <= 0 and ask <= 0 and last <= 0:
                            continue

                        mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else max(bid, ask, last)

                        contract = {
                            'strike': strike,
                            'expiration': exp_str,
                            'option_type': 'PUT',
                            'bid': bid,
                            'ask': ask,
                            'last': last,
                            'dte': dte,
                            'implied_volatility': float(opt.get('implied_volatility', 0) or 0),
                            'open_interest': int(opt.get('open_interest', 0) or opt.get('openInterest', 0) or 0),
                            'volume': int(opt.get('volume', 0) or 0),
                            'delta': float(opt.get('delta', 0) or 0),
                            'gamma': float(opt.get('gamma', 0) or 0),
                            'theta': float(opt.get('theta', 0) or 0),
                            'vega': float(opt.get('vega', 0) or 0),
                        }

                        from core.greeks import enrich_option_with_greeks
                        enrich_option_with_greeks(contract, stock_price)

                        wl_profile = self._watchlist_provider.get_screening_profile(
                            'PUT', dte=dte,
                            growth_mode_config=getattr(self, '_growth_screener_config', None)
                        )
                        iv_val = _normalize_iv(contract.get('implied_volatility', 0))
                        iv_env_adj, iv_rank_val, iv_status = self.iv_earnings_service.get_iv_environment_score(ticker, iv_val)
                        decision = score_contract(
                            ticker=ticker,
                            option=contract,
                            stock_price=stock_price,
                            profile=wl_profile,
                            portfolio_context=portfolio_context,
                            iv_env_adjustment=iv_env_adj,
                            iv_rank=iv_rank_val,
                            iv_status_str=iv_status,
                            earnings_adjustment=0,
                            earnings_info={},
                            macro_regime=macro_regime,
                            growth_profile=getattr(self, '_growth_profile', None),
                            evaluator_repo=self._get_evaluator_repo(),
                        )

                        if decision is None or decision.hard_blockers:
                            continue

                        pick_score = decision.contract_score

                        result = _format_decision_to_candidate(
                            ticker, stock_price, decision,
                            extra_warnings=[],
                            cash_reserve_enabled=self.config.get('cash_reserve_enabled', True),
                        )
                        candidates.append((pick_score, result))

                except Exception:
                    logger.warning("Candidate scoring failed for %s", ticker, exc_info=True)
                    continue

            if not candidates:
                return None

            candidates.sort(key=lambda x: x[0], reverse=True)
            return [c for _, c in candidates[:3]]

        except Exception as e:
            logger.debug(f"Watchlist {ticker}: Moomoo CSP path failed ({e}), will try yfinance")
            return None

    def _fetch_yfinance_csp_candidates(self, ticker, portfolio_context):
        """
        Fallback CSP fetch using yfinance.
        Returns list of candidate dicts, a list with a skip diagnostic, or None on error.
        """
        try:
            yf_ticker = get_yfinance_ticker(ticker)
            hist = yf_ticker.history(period="1d")
            if hist.empty:
                logger.warning(f"Watchlist {ticker}: yfinance history empty")
                return [self._make_skip_diagnostic(ticker, 'no_yfinance_data', 'yfinance history empty')]
            stock_price = float(hist['Close'].iloc[-1])

            cash_available_for_csp = float(portfolio_context.get('cash_available_for_csp', 0) or 0)
            available_cash = float(portfolio_context.get('available_cash', 0) or 0)

            target_strike_estimate = self._csp_target_strike_estimate(stock_price, portfolio_context)
            if not self._can_afford_csp(stock_price, portfolio_context):
                logger.debug(
                    f"Watchlist {ticker}: target CSP strike (${target_strike_estimate:.2f}) exceeds buying power ${cash_available_for_csp:.2f}"
                )
                return [self._make_skip_diagnostic(
                    ticker,
                    'no_cash_fit',
                    f'No CSP strike fits buying power (${cash_available_for_csp:.0f})'
                )]

            opts = yf_ticker.options
            if not opts:
                logger.warning(f"Watchlist {ticker}: no option expirations available from yfinance")
                return [self._make_skip_diagnostic(ticker, 'no_option_chain', 'No option expirations from yfinance')]

            growth_cfg = getattr(self, '_growth_screener_config', None)
            sp = (growth_cfg.get('screener_profile', {}) or {}) if growth_cfg else {}
            min_dte = sp.get('csp_min_dte', 30)
            max_dte = sp.get('csp_max_dte', 45)
            pref_dte = sp.get('csp_preferred_dte', 37)

            today = datetime.now()
            valid_expirations = []
            for exp in opts:
                try:
                    exp_date = datetime.strptime(exp, '%Y-%m-%d')
                    dte = (exp_date - today).days
                    if min_dte <= dte <= max_dte:
                        valid_expirations.append(exp)
                except ValueError:
                    continue

            if not valid_expirations:
                logger.warning(f"Watchlist {ticker}: no expiration with DTE {min_dte}-{max_dte} found")
                return [self._make_skip_diagnostic(ticker, 'no_valid_dte', f'No expiration with DTE {min_dte}-{max_dte} found')]

            valid_expirations = sorted(valid_expirations, key=lambda e: abs(pref_dte - (datetime.strptime(e, '%Y-%m-%d') - today).days))
            expirations_to_check = valid_expirations[:1]

            candidates = []
            any_strike_fit_cash = False

            from api.services.macro_regime_service import get_macro_service
            macro_regime = get_macro_service().get_macro_regime()

            for target_exp in expirations_to_check:
                try:
                    chain = yf_ticker.option_chain(target_exp)
                    if chain.puts.empty:
                        continue
                    puts = chain.puts.copy()
                    cash_filtered = puts[puts['strike'] * 100 <= cash_available_for_csp]
                    if cash_filtered.empty:
                        logger.debug(f"Watchlist {ticker}: all strikes exceed CSP buying power ({cash_available_for_csp}) for {target_exp}")
                        continue
                    puts = cash_filtered
                    any_strike_fit_cash = True

                    for _, put_row in puts.iterrows():
                        strike = float(put_row['strike'])
                        if strike >= stock_price:
                            continue

                        bid = float(put_row['bid']) if not pd.isna(put_row['bid']) else 0
                        ask = float(put_row['ask']) if not pd.isna(put_row['ask']) else 0
                        last_price = float(put_row['lastPrice']) if not pd.isna(put_row['lastPrice']) else 0

                        if bid <= 0 and ask <= 0 and last_price <= 0:
                            continue

                        mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else max(bid, ask, last_price)
                        spread_pct = ((ask - bid) / mid_price) * 100 if bid > 0 and ask > 0 and mid_price > 0 else 100
                        if spread_pct > 100:
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

                        if not _is_valid_external_option(contract, stock_price):
                            continue

                        from core.greeks import enrich_option_with_greeks
                        enrich_option_with_greeks(contract, stock_price)

                        wl_profile = self._watchlist_provider.get_screening_profile(
                            'PUT', dte=dte,
                            growth_mode_config=getattr(self, '_growth_screener_config', None)
                        )
                        iv_val = _normalize_iv(contract.get('implied_volatility', 0))
                        iv_env_adj, iv_rank_val, iv_status = self.iv_earnings_service.get_iv_environment_score(ticker, iv_val)
                        decision = score_contract(
                            ticker=ticker,
                            option=contract,
                            stock_price=stock_price,
                            profile=wl_profile,
                            portfolio_context=portfolio_context,
                            iv_env_adjustment=iv_env_adj,
                            iv_rank=iv_rank_val,
                            iv_status_str=iv_status,
                            earnings_adjustment=0,
                            earnings_info={},
                            macro_regime=macro_regime,
                            growth_profile=getattr(self, '_growth_profile', None),
                            evaluator_repo=self._get_evaluator_repo(),
                        )

                        if decision is None or decision.hard_blockers:
                            continue

                        pick_score = decision.contract_score

                        result = _format_decision_to_candidate(
                            ticker, stock_price, decision,
                            extra_warnings=['Data from yfinance (not Moomoo) - verify before trading'],
                            cash_reserve_enabled=self.config.get('cash_reserve_enabled', True),
                        )
                        candidates.append((pick_score, result))

                except Exception:
                    logger.warning("YFinance candidate scoring failed for %s", ticker, exc_info=True)
                    continue

            if not candidates:
                if not any_strike_fit_cash:
                    return [self._make_skip_diagnostic(
                        ticker, 'no_cash_fit',
                        f'No CSP strike fits buying power (${cash_available_for_csp:.0f})'
                    )]
                logger.warning(f"Watchlist {ticker}: all yfinance candidates filtered by score_contract")
                return [self._make_skip_diagnostic(ticker, 'blocked_by_scoring', 'All candidates filtered by scoring')]

            candidates.sort(key=lambda x: x[0], reverse=True)
            return [c for _, c in candidates[:3]]

        except Exception as e:
            logger.warning(f"Watchlist {ticker}: yfinance CSP fetch failed: {e}")
            return None

    def _make_skip_diagnostic(self, ticker, reason_code, reason_text):
        """Create a diagnostic entry for a skipped CSP candidate."""
        return {
            '_skip_diagnostic': True,
            'ticker': ticker,
            'reason_code': reason_code,
            'reason_text': reason_text,
        }

    def _get_evaluator_repo(self):
        """Return evaluator repo only if feedback is enabled in config."""
        evaluator_cfg = self.config.get('evaluator', {})
        if evaluator_cfg.get('feedback_enabled', False):
            return self.db.evaluator
        return None

    def _format_recommendation(self, option, rank=0):
        """Format a raw option dict into a standardized recommendation dict (signal-only)."""
        wd = option.get('wheel_decision', {})
        opt_type = option.get('option_type', '')
        is_long = option.get('profile_type', '') in ('long_call', 'long_put')
        is_csp = opt_type == 'PUT' and not option.get('held_position', False)
        is_cc = opt_type == 'CALL' and option.get('max_contracts', 0) > 0
        is_covered_call = is_cc
        is_csp_signal = is_csp
        # Research-only: long calls, long puts, earnings-vol calendars
        research_only = is_long or option.get('profile_type', '') == 'earnings_calendar'
        return {
            'rank': rank,
            'ticker': option['ticker'],
            'option_type': opt_type,
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
            'macro_multiplier': wd.get('macro_multiplier', 1.0),
            # Growth-aware fields (always-on)
            'score_rationale': wd.get('score_rationale', ''),
            'remaining_gap_to_target': wd.get('remaining_gap_to_target', 0),
            'risk_budget_used_pct': wd.get('risk_budget_used_pct', 0),
            'stress_loss': wd.get('stress_loss', 0),
            'confidence_score': wd.get('confidence_score', 100),
            'covered_call_intent': wd.get('covered_call_intent', ''),
            # Signal-only fields
            'signal_type': 'covered_call' if is_covered_call else ('csp' if is_csp_signal else opt_type.lower()),
            'strategy': 'wheel',
            'broker_feasible': True,
            'capital_required': option.get('cash_required', 0),
            'risk_budget_used': wd.get('risk_budget_used_pct', 0),
            'data_source': wd.get('price_source', 'moomoo'),
            'confidence': wd.get('confidence_score', 100),
            'quote_quality': wd.get('quote_quality', ''),
            'blocked_reason_codes': wd.get('blocked_reason_codes', []) or wd.get('hard_blockers', []),
            'research_only': research_only,
        }

    def get_top_recommendations(self, limit=5):
        """
        Get top N option signals across all portfolio positions and watchlist.

        Returns a unified signal payload ranked by contract score.

        Filters options by capital availability:
        - CALLs: Only if user has 100+ shares
        - PUTs: Only if user has sufficient cash (strike * 100)

        Uses broker buying power as authoritative constraint:
        - Broker buying power is NOT reduced by open short puts (broker already accounts for them)
        - Local staging capacity = broker buying power - pending staged CSP collateral

        Args:
            limit (int): Number of top signals to return (default: 5, max: 10)

        Returns:
            dict: {
                'success': True,
                'count': int,
                'total_scored': int,
                'generated_at': str (ISO timestamp),
                'signals': [list of signal dicts, unified ranked],
                'broker_buying_power': float,
                'cash_available_for_csp': float,
                'cash_reserved_for_csp': float,
                'blocked_signals': [list of diagnostic dicts],
            }
        """
        from datetime import datetime

        logger.info(f"Getting top {limit} signals")
        start_time = time.time()

        from core.scan_ledger import ScanLedger, ScanLedgerEntry, compute_config_hash, compute_portfolio_hash, extract_data_sources

        try:
            # Growth mode is always-on — set growth profile from config
            growth_cfg = self.config.get('growth_mode', {}) if self.config else {}
            self._growth_profile = {
                'objective': growth_cfg.get('objective', 'time_to_2x'),
                'target_account_multiple': float(growth_cfg.get('target_account_multiple', 2.0)),
                'max_drawdown_pct': float(growth_cfg.get('max_drawdown_pct', 0.40)),
                'execution_scope': growth_cfg.get('execution_scope', 'short_premium_wheel'),
                'long_options_mode': growth_cfg.get('long_options_mode', 'research_only'),
            }
            # Propagate to options_data service for unified scoring
            if hasattr(self._options_data_provider, '_growth_profile'):
                self._options_data_provider._growth_profile = self._growth_profile

            # Build growth mode config for screener overlay
            self._growth_screener_config = growth_cfg

            # Ensure connection
            conn_start = time.time()
            conn = self._get_connection()
            logger.info(f"[TIMING] Get connection: {time.time() - conn_start:.2f}s")
            if not conn:
                try:
                    entry = ScanLedgerEntry(
                        scan_type="recommendations",
                        config_hash=compute_config_hash(self.config or {}),
                        portfolio_hash="unknown",
                        elapsed_seconds=time.time() - start_time,
                        error_message="Failed to establish connection to moomoo",
                    )
                    ScanLedger(self.db).record(entry)
                except Exception:
                    logger.debug("Scan ledger write skipped (connection failure)", exc_info=True)
                return {'error': 'Failed to establish connection to moomoo'}

            # Get portfolio context for positions and cash balance
            ctx_start = time.time()
            portfolio_context = self._get_portfolio_context()
            logger.info(f"[TIMING] Portfolio context: {time.time() - ctx_start:.2f}s")
            positions = portfolio_context.get('positions', {})
            available_cash = float(portfolio_context.get('available_cash', 0) or 0)
            broker_buying_power = float(portfolio_context.get('broker_buying_power', 0) or 0)
            cash_available_for_csp = float(portfolio_context.get('cash_available_for_csp', 0) or 0)
            cash_reserved_for_csp = float(portfolio_context.get('cash_reserved_for_csp', 0) or 0)
            short_calls = portfolio_context.get('short_calls', {})
            short_puts = portfolio_context.get('short_puts', {})

            effective_watchlist = self._watchlist_provider.get_effective_watchlist(
                growth_mode_config=self._growth_screener_config
            )
            # Deduplicate watchlist by canonical underlying (UBER vs US.UBER)
            seen_canonical = set()
            deduped_watchlist = []
            for wt in effective_watchlist:
                cu = canonical_underlying(wt)
                if cu not in seen_canonical:
                    seen_canonical.add(cu)
                    deduped_watchlist.append(wt)
            effective_watchlist = deduped_watchlist
            logger.info(f"Effective watchlist: {len(effective_watchlist)} tickers (after dedup)")

            if not positions and not effective_watchlist:
                try:
                    entry = ScanLedgerEntry(
                        scan_type="recommendations",
                        config_hash=compute_config_hash(self.config or {}),
                        portfolio_hash=compute_portfolio_hash(portfolio_context),
                        elapsed_seconds=time.time() - start_time,
                        total_candidates=0, passed_count=0, blocked_count=0,
                        data_sources=extract_data_sources(portfolio_context),
                    )
                    ScanLedger(self.db).record(entry)
                except Exception:
                    logger.debug("Scan ledger write skipped (empty scan)", exc_info=True)
                return {
                    'success': True,
                    'count': 0,
                    'total_scored': 0,
                    'generated_at': datetime.now().isoformat(),
                    'signals': [],
                    'broker_buying_power': broker_buying_power,
                    'cash_available_for_csp': cash_available_for_csp,
                    'cash_reserved_for_csp': cash_reserved_for_csp,
                    'blocked_signals': [],
                    'blocked_reason_counts': {},
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
            # LANE 1: Watchlist CSPs — process watchlist tickers only when
            # there is enough buying power for at least very small CSPs.
            # Uses Moomoo as primary data source, falls back to yfinance.
            # ════════════════════════════════════════════════════════════
            csp_start = time.time()
            min_csp_buying_power = float(
                (growth_cfg.get('screener_profile', {}) or {}).get('min_csp_buying_power', 5000)
            ) if growth_cfg else 5000.0
            if cash_available_for_csp < min_csp_buying_power:
                logger.info(
                    "Watchlist CSP scan will continue below the usual minimum buying power: %.2f < %.2f",
                    cash_available_for_csp,
                    min_csp_buying_power,
                )

            for ticker in effective_watchlist:
                is_held = ticker in positions

                cached = self._get_cached_watchlist_data(ticker)
                if cached is not None:
                    watchlist_cached += 1
                    for cached_item in cached:
                        if cached_item.get('_skip_diagnostic'):
                            skipped_csp_diagnostics.append(cached_item)
                            continue
                        cached_item['held_position'] = is_held
                        cached_item['existing_position'] = short_puts.get(ticker, 0)
                        watchlist_csp_candidates.append(cached_item)
                    continue

                results = self._fetch_watchlist_ticker_csp(ticker, portfolio_context)
                if not results:
                    watchlist_errors += 1
                    continue

                self._set_cached_watchlist_data(ticker, results)

                for result in results:
                    if result.get('_skip_diagnostic'):
                        skipped_csp_diagnostics.append(result)
                        continue
                    result['held_position'] = is_held
                    result['existing_position'] = short_puts.get(ticker, 0)
                    watchlist_csp_candidates.append(result)
                    watchlist_processed += 1

            csp_elapsed = time.time() - csp_start
            logger.info(f"[TIMING] Watchlist CSP scan: {csp_elapsed:.2f}s "
                       f"({watchlist_processed} fetched, {watchlist_cached} cached, {watchlist_errors} errors)")

            # ════════════════════════════════════════════════════════════
            # LANE 2: Covered Calls — from portfolio positions only
            # ════════════════════════════════════════════════════════════
            cc_start = time.time()
            # Deduplicate positions by canonical underlying
            seen_position_canonical = set()
            deduped_position_tickers = []
            for pk in positions.keys():
                cu = canonical_underlying(pk)
                if cu not in seen_position_canonical:
                    seen_position_canonical.add(cu)
                    deduped_position_tickers.append(pk)
            for ticker in deduped_position_tickers:
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

                    # Determine OTM target — always use growth-tuned default
                    if available_calls <= 0:
                        continue

                    sp = (growth_cfg.get('screener_profile', {}) or {}) if growth_cfg else {}
                    otm_target = sp.get('call_default_otm_pct', 10)

                    # Fetch options data for this ticker
                    result = self._options_data_provider._process_ticker_for_otm(
                        conn=conn,
                        ticker=ticker,
                        otm_percentage=otm_target,  # Growth-tuned or default OTM
                        portfolio_context=portfolio_context,
                        expiration=None,  # Get all expirations
                        option_type='CALL'  # Covered-call lane only
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

                except Exception as e:
                    logger.error(f"Error processing {ticker} for signals: {e}")
                    continue

            cc_elapsed = time.time() - cc_start
            logger.info(f"[TIMING] Covered call scan: {cc_elapsed:.2f}s")

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

            # ── Unified ranking: one engine for CSPs and covered calls ──
            # Always sort by contract_score
            def _get_rank_score(x):
                return x.get('wheel_decision', {}).get('contract_score', 0) or x.get('score', 0) or 0

            all_candidates.sort(key=_get_rank_score, reverse=True)

            # ── Build lane results with diversity safeguard ──────────
            def _select_top(candidates, max_per=1):
                """Select top candidates with at most max_per per canonical underlying."""
                seen_canonical = set()
                selected = []
                for opt in candidates:
                    t = opt.get('ticker', 'UNKNOWN')
                    cu = canonical_underlying(t)
                    count = sum(1 for o in selected if canonical_underlying(o.get('ticker', 'UNKNOWN')) == cu)
                    if count < max_per:
                        selected.append(opt)
                        seen_canonical.add(cu)
                return selected

            top_covered_calls = _select_top(covered_call_candidates, max_per=1)
            top_watchlist_csp = _select_top(watchlist_csp_candidates, max_per=2)

            # Single ranked signal pipeline combining CSPs and covered calls.
            signals = _select_top(all_candidates, max_per=1)[:limit]

            # Log per-ticker diagnostics
            for t, diag in sorted(ticker_diagnostics.items(), key=lambda x: x[1]['top_score'], reverse=True):
                logger.info(f"  TICKER {t}: top_score={diag['top_score']:.1f}, candidates={diag['candidate_count']}")

            # Format the unified signal list.
            def _format_rec_list(candidates, start_rank=1):
                return [self._format_recommendation(opt, rank) for rank, opt in enumerate(candidates, start_rank)]

            formatted_signals = _format_rec_list(signals)
            formatted_cc = _format_rec_list(top_covered_calls)
            formatted_csp = _format_rec_list(top_watchlist_csp)

            scoring_elapsed = time.time() - cc_start - cc_elapsed
            elapsed = time.time() - start_time
            logger.info(f"[TIMING] Scoring & ranking: {scoring_elapsed:.2f}s")
            logger.info(f"[TIMING] Total: {elapsed:.2f}s — generated {len(formatted_signals)} signals, "
                       f"{len(formatted_cc)} CC, {len(formatted_csp)} CSP")

            # Build blocked signal diagnostics.
            blocked_signals = []
            reason_counts = {}
            for diag in skipped_csp_diagnostics:
                rcode = diag.get('reason_code', 'unknown')
                reason_counts[rcode] = reason_counts.get(rcode, 0) + 1
                blocked_signals.append({
                    'ticker': diag.get('ticker', ''),
                    'reason_code': rcode,
                    'reason_text': diag.get('reason_text', ''),
                    'signal_type': 'csp',
                    'actionable': False,
                })

            # ── Catalyst flow defensive warnings ──────────────
            try:
                from api.services.catalyst_flow_service import CatalystFlowService
                if not hasattr(self, '_cat_warn_svc'):
                    self._cat_warn_svc = CatalystFlowService(
                        config_provider=self.config,
                        watchlist_provider=self._watchlist_provider,
                    )
                catalyst_svc = self._cat_warn_svc
                seen = set()
                for sig_list in (formatted_signals, formatted_cc, formatted_csp):
                    for sig in sig_list:
                        t = sig.get('ticker', '')
                        if not t or t in seen:
                            continue
                        seen.add(t)
                        cw = catalyst_svc.get_ticker_warnings(t)
                        if cw:
                            sig.setdefault('warnings', [])
                            for w in cw:
                                if w not in sig['warnings']:
                                    sig['warnings'].append(w)
            except Exception as exc:
                logger.debug("Catalyst flow warnings skipped: %s", exc)

            # ── Underlying quality gate (free yfinance data) ──────────
            try:
                from api.services.underlying_quality import get_underlying_quality
                unique_tickers = set()
                for sig in formatted_signals + formatted_cc + formatted_csp:
                    t = sig.get('ticker', '')
                    if t:
                        unique_tickers.add(t)
                for b in blocked_signals:
                    t = b.get('ticker', '')
                    if t:
                        unique_tickers.add(t)
                quality_cache = {}
                for t in unique_tickers:
                    try:
                        quality_cache[t] = get_underlying_quality(t)
                    except Exception:
                        quality_cache[t] = None
                for sig in formatted_signals + formatted_cc + formatted_csp:
                    t = sig.get('ticker', '')
                    q = quality_cache.get(t)
                    if q:
                        sig['underlying_quality'] = q['grade']
                        sig['underlying_score'] = q['score']
                        sig['underlying_warnings'] = q.get('warnings', [])
                for b in blocked_signals:
                    t = b.get('ticker', '')
                    q = quality_cache.get(t)
                    if q:
                        b['underlying_quality'] = q['grade']
                        b['underlying_score'] = q['score']
                        b['underlying_warnings'] = q.get('warnings', [])
            except Exception as exc:
                logger.debug("Underlying quality gate skipped: %s", exc)

            # Record scan ledger entry (non-blocking)
            try:
                top_signals = [
                    {'ticker': s.get('ticker'), 'option_type': s.get('option_type'),
                     'strike': s.get('strike'), 'score': s.get('score'),
                     'annualized_return': s.get('annualized_return')}
                    for s in formatted_signals[:5]
                ]
                blocked_summary = [
                    {'ticker': b.get('ticker'), 'reason': b.get('blocked_reason', b.get('reason', '')),
                     'option_type': b.get('option_type', '')}
                    for b in blocked_signals[:10]
                ]
                entry = ScanLedgerEntry(
                    scan_type="recommendations",
                    config_hash=compute_config_hash(self.config or {}),
                    portfolio_hash=compute_portfolio_hash(portfolio_context),
                    elapsed_seconds=time.time() - start_time,
                    total_candidates=len(all_candidates),
                    passed_count=len(formatted_signals),
                    blocked_count=len(blocked_signals),
                    data_sources=extract_data_sources(portfolio_context, formatted_signals),
                    top_signals=top_signals,
                    blocked_candidates=blocked_summary,
                )
                ScanLedger(self.db).record(entry)
            except Exception:
                logger.debug("Scan ledger write skipped", exc_info=True)

            # Record surfaced signals for the evaluator (non-blocking)
            try:
                evaluator_cfg = self.config.get('evaluator', {})
                if evaluator_cfg.get('enabled', True) and evaluator_cfg.get('record_signals', True):
                    self.db.evaluator.record_surfaced_signals(formatted_signals, source='recommendations')
            except Exception:
                logger.warning("Evaluator signal recording failed", exc_info=True)

            return {
                'success': True,
                'count': len(formatted_signals),
                'total_scored': len(all_candidates),
                'generated_at': datetime.now().isoformat(),
                'signals': formatted_signals,
                'broker_buying_power': round(broker_buying_power, 2),
                'cash_available_for_csp': round(cash_available_for_csp, 2),
                'cash_reserved_for_csp': round(cash_reserved_for_csp, 2),
                'cash_diagnostics': portfolio_context.get('_cash_diagnostics', {}),
                'blocked_signals': blocked_signals,
                'blocked_reason_counts': dict(sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)),
                'growth_mode': {
                    'enabled': True,
                    'objective': (self._growth_profile or {}).get('objective', 'time_to_2x'),
                    'target_account_multiple': (self._growth_profile or {}).get('target_account_multiple', 2.0),
                    'max_drawdown_pct': (self._growth_profile or {}).get('max_drawdown_pct', 0.40),
                    'screener_profile': ((self._growth_screener_config or {}).get('screener_profile', {}) or {}),
                    'csp_profile_summary': _build_csp_profile_summary(self._growth_screener_config),
                },
                '_diagnostics': {
                    'tickers': ticker_diagnostics,
                    'limit_applied': limit,
                    'max_per_ticker': 2,
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
            logger.exception(f"Error getting top signals: {e}")
            try:
                entry = ScanLedgerEntry(
                    scan_type="recommendations",
                    config_hash=compute_config_hash(self.config or {}),
                    portfolio_hash="unknown",
                    elapsed_seconds=time.time() - start_time,
                    error_message=str(e),
                )
                ScanLedger(self.db).record(entry)
            except Exception:
                logger.debug("Scan ledger write skipped (scan error)", exc_info=True)
            return {'error': str(e)}


def _build_csp_profile_summary(growth_cfg: dict | None) -> str:
    """
    Build a human-readable summary of the active Growth CSP screener profile.
    Returns an empty string if growth mode is not configured.
    """
    if not growth_cfg:
        return ''
    sp = growth_cfg.get('screener_profile', {}) or {}
    parts = []
    parts.append(f"delta {sp.get('csp_target_delta', 0.30)}")
    parts.append(f"+/-{sp.get('csp_delta_tolerance', 0.12)}")
    dte = sp.get('csp_preferred_dte', 37)
    min_dte = sp.get('csp_min_dte', 30)
    max_dte = sp.get('csp_max_dte', 45)
    min_otm = sp.get('csp_min_otm_pct', 5)
    max_otm = sp.get('csp_max_otm_pct', 15)
    parts.append(f"DTE {min_dte}-{max_dte} (pref {dte})")
    parts.append(f"OTM {min_otm}-{max_otm}% (pref {sp.get('csp_default_otm_pct', 10)}%)")
    parts.append(f"IV rank >={sp.get('min_iv_rank', 45)}%")
    if sp.get('require_cash_fit', True):
        parts.append("cash-fit req.")
    return " | ".join(parts)
