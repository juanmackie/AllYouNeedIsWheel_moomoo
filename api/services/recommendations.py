"""
Signals module - handles top options signals.
Extracted from the monolithic options_service.py for maintainability.
"""

import logging
import time
from datetime import datetime

import pandas as pd

from api.services.utils import (
    clean_yfinance_ticker,
    get_yfinance_history,
    get_yfinance_option_chain,
    get_yfinance_options,
    get_yfinance_ticker,
)
from core.growth_mode import should_block_for_data_quality
from core.scoring_factors import premium_velocity_per_day
from core.ticker_utils import canonical_underlying
from core.utils import is_market_open
from core.wheel_decision import WheelDecision, score_contract

logger = logging.getLogger("api.services.recommendations")


def _is_valid_external_option(option: dict, stock_price: float) -> bool:
    """Return True when an external option payload has the minimum safe fields."""
    try:
        strike = float(option.get("strike", 0) or 0)
        bid = float(option.get("bid", 0) or 0)
        ask = float(option.get("ask", 0) or 0)
        last = float(option.get("last", 0) or 0)
        dte = int(option.get("dte", 0) or 0)
    except (TypeError, ValueError):
        return False

    if strike <= 0 or stock_price <= 0 or dte <= 0:
        return False
    if bid < 0 or ask < 0 or last < 0:
        return False
    if bid <= 0 and ask <= 0 and last <= 0:
        return False
    if option.get("option_type") not in {"CALL", "PUT"}:
        return False
    return True


def _normalize_source(value, fallback=""):
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return fallback


def _option_source_value(option: dict, wheel_decision: dict | None, key: str, fallback: str = "broker") -> str:
    option_value = option.get(key)
    if isinstance(option_value, str) and option_value.strip():
        return option_value.strip()
    if wheel_decision:
        wd_value = wheel_decision.get(key)
        if isinstance(wd_value, str) and wd_value.strip():
            return wd_value.strip()
    if key == "data_source":
        data_source = option.get("data_source")
        if isinstance(data_source, str) and data_source.strip():
            return data_source.strip()
    return fallback


def _option_uses_yfinance(option: dict, wheel_decision: dict | None = None) -> bool:
    source_values = [
        _option_source_value(option, wheel_decision, "price_source", ""),
        _option_source_value(option, wheel_decision, "chain_source", ""),
        _option_source_value(option, wheel_decision, "iv_source", ""),
        _option_source_value(option, wheel_decision, "data_source", ""),
    ]
    return bool(
        option.get("from_yfinance")
        or (wheel_decision and wheel_decision.get("from_yfinance"))
        or any(value.lower() == "yfinance" for value in source_values if isinstance(value, str))
    )


def _make_failed_csp_decision(ticker: str, contract: dict, reason: str, reason_codes: list[str]) -> WheelDecision:
    """Create a blocked watchlist CSP decision before composite scoring."""
    return WheelDecision(
        ticker=ticker,
        option_type="PUT",
        strike=float(contract.get("strike", 0) or 0),
        expiration=str(contract.get("expiration", "") or ""),
        hard_blockers=[reason],
        blocked_reason_codes=reason_codes,
    )


def _mark_research_only_candidate(candidate: dict, reason: str | None = None) -> dict:
    """Flag a candidate as research-only and attach a visible warning."""
    candidate["research_only"] = True
    warnings = list(candidate.get("warnings") or [])
    if reason and reason not in warnings:
        warnings.append(reason)
    candidate["warnings"] = warnings
    return candidate


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
        "ticker": ticker,
        "stock_price": stock_price,
        "option_type": decision.option_type,
        "max_contracts": decision.max_contracts,
        "recommended_contracts": decision.recommended_contracts,
        "existing_position": 0,
        "from_watchlist": True,
        "strike": decision.strike,
        "expiration": decision.expiration,
        "dte": decision.dte,
        "mid_price": round(decision.mid_price, 4),
        "premium_per_contract": round(decision.premium_per_contract, 2),
        "bid": decision.bid,
        "ask": decision.ask,
        "annualized_return": decision.annualized_return,
        "iv_adjusted_return": decision.iv_adjusted_return,
        "otm_pct": decision.otm_pct,
        "delta": decision.delta,
        "implied_volatility": decision.implied_volatility,
        "open_interest": decision.open_interest,
        "volume": decision.volume,
        "score": round(decision.contract_score, 2),
        "iv_rank": decision.iv_rank,
        "iv_status": decision.iv_status,
        "iv_env_adjustment": decision.iv_env_adjustment,
        "profile_type": decision.profile_type,
        "earnings_date": decision.earnings_date,
        "days_to_earnings": decision.days_to_earnings,
        "earnings_adjustment": decision.earnings_adjustment,
        "size_fit": decision.size_fit,
        "expected_move_buffer": decision.expected_move_buffer,
        "wheel_decision": decision.to_dict(),
        "score_details": decision.score_details,
        "rationale": decision.rationale,
        "warnings": warnings,
        "cash_reserve_enabled": cash_reserve_enabled,
        "breakeven": decision.breakeven,
        "breakeven_buffer_pct": decision.breakeven_buffer_pct,
        "cash_required": decision.cash_required,
        "from_yfinance": bool(
            getattr(decision, "price_source", "") == "yfinance"
            or getattr(decision, "chain_source", "") == "yfinance"
            or getattr(decision, "iv_source", "") == "yfinance"
        ),
        "price_source": getattr(decision, "price_source", "broker"),
        "chain_source": getattr(decision, "chain_source", "broker"),
        "iv_source": getattr(decision, "iv_source", "broker"),
        "data_source": getattr(decision, "price_source", "broker"),
    }


class RecommendationEngine:
    """
    Handles generating top options signals across portfolio positions and watchlist.
    """

    def __init__(
        self,
        connection_provider,
        config_provider,
        db,
        iv_earnings_service,
        portfolio_context_provider,
        portfolio_service_provider,
        watchlist_provider,
        options_data_provider,
        cash_calculator_provider,
    ):
        self._connection_provider = connection_provider
        self._config_provider = config_provider
        self.config = config_provider.config if hasattr(config_provider, "config") else config_provider
        self.db = db
        self.iv_earnings_service = iv_earnings_service
        self._portfolio_context_provider = portfolio_context_provider
        self._portfolio_service_provider = portfolio_service_provider
        self._watchlist_provider = watchlist_provider
        self._options_data_provider = options_data_provider
        self._cash_calculator_provider = cash_calculator_provider
        self._yfinance_cache = {}
        self._yfinance_iv_cache = {}
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
        if not entry or "timestamp" not in entry:
            return False
        return (datetime.now() - entry["timestamp"]).total_seconds() < self._yfinance_cache_ttl

    def _watchlist_cache_key(
        self, ticker: str, portfolio_context: dict | None = None, ignore_cash_limits: bool = False
    ) -> tuple:
        bp = 0.0
        if portfolio_context:
            bp = float(portfolio_context.get("cash_available_for_csp", 0) or 0)
        return (ticker, int(bp // 1000), bool(ignore_cash_limits))

    def _get_cached_watchlist_data(self, ticker, portfolio_context=None, ignore_cash_limits: bool = False):
        entry = self._yfinance_cache.get(self._watchlist_cache_key(ticker, portfolio_context, ignore_cash_limits))
        if self._is_cache_valid(entry):
            return entry["data"]
        return None

    def _set_cached_watchlist_data(self, ticker, data, portfolio_context=None, ignore_cash_limits: bool = False):
        self._yfinance_cache[self._watchlist_cache_key(ticker, portfolio_context, ignore_cash_limits)] = {
            "data": data,
            "timestamp": datetime.now(),
        }

    def _csp_target_strike_estimate(self, stock_price: float, portfolio_context: dict) -> float:
        """Estimate the cash requirement for the default CSP target strike."""
        growth_cfg = getattr(self, "_growth_screener_config", None)
        sp = (growth_cfg.get("screener_profile", {}) or {}) if growth_cfg else {}
        otm_pct = float(sp.get("csp_default_otm_pct", 10) or 10)
        target_strike = stock_price * (1 - (otm_pct / 100))
        return max(target_strike, 0.0)

    def _can_afford_csp(self, stock_price: float, portfolio_context: dict, require_cash_fit: bool = True) -> bool:
        """Return True when the default CSP target strike fits buying power."""
        cash_available_for_csp = float(portfolio_context.get("cash_available_for_csp", 0) or 0)
        if stock_price <= 0:
            return False
        if not require_cash_fit:
            return True
        if cash_available_for_csp <= 0:
            return False
        return self._csp_target_strike_estimate(stock_price, portfolio_context) * 100 <= cash_available_for_csp

    def _find_affordable_csp_strike(
        self, stock_price: float, portfolio_context: dict, strikes: list, require_cash_fit: bool = True
    ) -> list:
        """Filter strikes to only those that fit buying power, sorted closest to farthest OTM.

        Returns the list of strikes that are affordable (strike * 100 <= cash_available_for_csp),
        sorted by OTM distance ascending (closest OTM first).
        """
        cash_available_for_csp = float(portfolio_context.get("cash_available_for_csp", 0) or 0)
        if stock_price <= 0:
            return []
        if not require_cash_fit:
            return [strike for strike in sorted(strikes, reverse=True) if strike > 0 and strike < stock_price]
        if cash_available_for_csp <= 0:
            return []

        affordable = []
        for strike in strikes:
            if strike <= 0 or strike >= stock_price:
                continue
            cash_required = strike * 100
            if cash_required <= cash_available_for_csp:
                affordable.append(strike)

        affordable.sort(key=lambda s: s, reverse=True)
        return affordable

    def _has_any_affordable_otm_strike(
        self, stock_price: float, portfolio_context: dict, require_cash_fit: bool = True
    ) -> bool:
        """Check whether any strike in the 5-15% OTM range fits buying power."""
        growth_cfg = getattr(self, "_growth_screener_config", None)
        sp = (growth_cfg.get("screener_profile", {}) or {}) if growth_cfg else {}
        min_otm = float(sp.get("csp_min_otm_pct", 5) or 5)
        max_otm = float(sp.get("csp_max_otm_pct", 15) or 15)

        min_strike = stock_price * (1 - (max_otm / 100))
        max_strike = stock_price * (1 - (min_otm / 100))

        cash_available_for_csp = float(portfolio_context.get("cash_available_for_csp", 0) or 0)
        if not require_cash_fit:
            return stock_price > 0
        if cash_available_for_csp <= 0:
            return False

        # Check if any strike in the range is affordable
        affordable_strike = min(max_strike, cash_available_for_csp / 100)
        return affordable_strike >= min_strike

    def _score_csp_contract(
        self, contract, ticker, stock_price, dte, portfolio_context, macro_regime, research_only_mode: bool = False
    ):
        """Enrich missing IV/greeks and score a CSP contract. Used by both moomoo and yfinance paths."""
        from core.greeks import prepare_option_for_scoring

        iv_val, delta_val = prepare_option_for_scoring(
            contract,
            ticker,
            stock_price,
            self._yfinance_iv_cache,
            chain_fetcher=get_yfinance_option_chain,
        )
        if iv_val <= 0:
            return _make_failed_csp_decision(
                ticker, contract, "Missing implied volatility after enrichment", ["missing_iv"]
            )
        if abs(delta_val) < 0.001:
            return _make_failed_csp_decision(
                ticker, contract, "Missing delta/Greeks after enrichment", ["missing_greeks"]
            )

        wl_profile = self._watchlist_provider.get_screening_profile(
            "PUT",
            dte=dte,
            vix_regime=portfolio_context.get("vix_regime"),
            growth_mode_config=getattr(self, "_growth_screener_config", None),
        )
        try:
            iv_env_adj, iv_rank_val, iv_status = self.iv_earnings_service.get_iv_environment_score(ticker, iv_val)
        except Exception:
            iv_env_adj, iv_rank_val, iv_status = 0, 0.5, "unknown"
        try:
            earnings_adjustment, _ = self.iv_earnings_service.get_earnings_score_impact(ticker)
            earnings_adjustment = float(earnings_adjustment or 0)
        except Exception:
            earnings_adjustment = 0
        try:
            earnings_info = self.iv_earnings_service.get_earnings_info(ticker) or {}
            if not isinstance(earnings_info, dict):
                earnings_info = {}
        except Exception:
            earnings_info = {}
        return score_contract(
            ticker=ticker,
            option=contract,
            stock_price=stock_price,
            profile=wl_profile,
            portfolio_context=portfolio_context,
            iv_env_adjustment=iv_env_adj,
            iv_rank=iv_rank_val,
            iv_status_str=iv_status,
            earnings_adjustment=earnings_adjustment,
            earnings_info=earnings_info,
            macro_regime=macro_regime,
            growth_profile=getattr(self, "_growth_profile", None),
            research_only_mode=research_only_mode,
        )

    def _fetch_watchlist_ticker_csp(self, ticker, portfolio_context, ignore_cash_limits: bool = False):
        """
        Fetch CSP candidates for a watchlist ticker.
        Tries Moomoo first, falls back to yfinance.
        Returns list of candidate dicts, a list with a skip diagnostic, or None on error.
        """
        candidates = self._fetch_watchlist_csp_moomoo(ticker, portfolio_context, ignore_cash_limits=ignore_cash_limits)
        if candidates is not None:
            return candidates
        return self._fetch_yfinance_csp_candidates(ticker, portfolio_context, ignore_cash_limits=ignore_cash_limits)

    def _fetch_watchlist_long_options(self, ticker, portfolio_context):
        """Fetch lean research-only long Call/Put ideas from the existing scorer."""
        if (self._growth_profile or {}).get("long_options_mode", "research_only") != "research_only":
            return {"calls": [], "puts": []}

        conn = self._get_connection()
        if not conn:
            return {"calls": [], "puts": []}

        stock_price = conn.get_stock_price(ticker)
        if stock_price is None or stock_price <= 0:
            return {"calls": [], "puts": []}

        from core.wheel_decision import disabled_macro_context

        macro_regime = disabled_macro_context()
        growth_cfg = getattr(self, "_growth_screener_config", None)
        results = {"calls": [], "puts": []}

        for side, right, profile_type, target_multiplier, bucket in (
            ("CALL", "C", "long_call", 1.08, "calls"),
            ("PUT", "P", "long_put", 0.92, "puts"),
        ):
            try:
                profile = dict(
                    self._watchlist_provider.get_screening_profile(
                        side,
                        vix_regime=portfolio_context.get("vix_regime"),
                        growth_mode_config=growth_cfg,
                    )
                )
                profile["profile_type"] = profile_type

                from moomoo import RET_OK

                ret, data = conn.get_option_expiration_dates(ticker)
                if ret != RET_OK or data is None:
                    continue
                expiration_column = "expiration_date"
                if expiration_column not in data.columns:
                    if "strike_time" in data.columns:
                        expiration_column = "strike_time"
                    elif "option_expiry_date" in data.columns:
                        expiration_column = "option_expiry_date"
                    else:
                        continue

                today = datetime.now().date()
                expirations = []
                for raw_date in data[expiration_column].tolist():
                    exp_str = str(raw_date).replace("-", "")
                    try:
                        dte = (datetime.strptime(exp_str, "%Y%m%d").date() - today).days
                    except ValueError:
                        continue
                    if profile.get("min_dte", 0) <= dte <= profile.get("max_dte", 365):
                        expirations.append((exp_str, dte))
                if not expirations:
                    continue
                expirations.sort(
                    key=lambda item: abs(float(profile.get("preferred_dte", item[1]) or item[1]) - item[1])
                )

                chain = conn.get_option_chain(
                    ticker,
                    expirations[0][0],
                    right,
                    target_strike=stock_price * target_multiplier,
                )
                if not chain or not chain.get("options"):
                    continue

                candidates = []
                for opt in chain.get("options", []):
                    if not _is_valid_external_option(opt, stock_price):
                        continue
                    strike = float(opt.get("strike", 0) or 0)
                    if side == "CALL" and strike <= stock_price:
                        continue
                    if side == "PUT" and strike >= stock_price:
                        continue
                    dte = int(opt.get("dte") or expirations[0][1])
                    contract = {
                        "strike": strike,
                        "expiration": str(opt.get("expiration") or expirations[0][0]).replace("-", ""),
                        "option_type": side,
                        "bid": float(opt.get("bid", 0) or 0),
                        "ask": float(opt.get("ask", 0) or 0),
                        "last": float(opt.get("last", 0) or 0),
                        "dte": dte,
                        "implied_volatility": float(opt.get("implied_volatility", 0) or 0),
                        "open_interest": int(opt.get("open_interest", 0) or opt.get("openInterest", 0) or 0),
                        "volume": int(opt.get("volume", 0) or 0),
                        "delta": float(opt.get("delta", 0) or 0),
                        "gamma": float(opt.get("gamma", 0) or 0),
                        "theta": float(opt.get("theta", 0) or 0),
                        "vega": float(opt.get("vega", 0) or 0),
                    }
                    decision = score_contract(
                        ticker=ticker,
                        option=contract,
                        stock_price=stock_price,
                        profile=profile,
                        portfolio_context=portfolio_context,
                        iv_env_adjustment=0,
                        iv_rank=0.5,
                        iv_status_str="unknown",
                        earnings_adjustment=0,
                        earnings_info={},
                        macro_regime=macro_regime,
                        growth_profile=getattr(self, "_growth_profile", None),
                        research_only_mode=True,
                    )
                    if decision is None or decision.hard_blockers:
                        continue
                    candidate = _format_decision_to_candidate(
                        ticker,
                        stock_price,
                        decision,
                        extra_warnings=[f"Research-only long {side.lower()} signal - verify before trading"],
                        cash_reserve_enabled=self.config.get("cash_reserve_enabled", True),
                    )
                    candidate["option_type"] = side
                    candidate["profile_type"] = profile_type
                    candidate["signal_type"] = side.lower()
                    _mark_research_only_candidate(candidate)
                    candidates.append(candidate)

                    candidates.sort(
                        key=lambda item: (
                            premium_velocity_per_day(item.get("premium_per_contract", 0), item.get("dte", 0)),
                            item.get("annualized_return", 0),
                            item.get("score", 0) or 0,
                        ),
                        reverse=True,
                    )
                    results[bucket] = candidates[:2]
            except Exception:
                logger.debug("Long %s scan failed for %s", side, ticker, exc_info=True)

        return results

    def _fetch_watchlist_csp_moomoo(self, ticker, portfolio_context, ignore_cash_limits: bool = False):
        """
        Fetch CSP candidates from Moomoo for a watchlist ticker.
        Returns list of candidate dicts, or None on failure.
        """
        # After hours: skip Moomoo option-chain calls to avoid hanging,
        # fall through to yfinance fallback which works after hours.
        if not is_market_open():
            logger.debug(f"Market closed: skipping Moomoo CSP fetch for {ticker}, will use yfinance fallback")
            return None

        try:
            conn = self._get_connection()
            if not conn:
                return None

            cash_available_for_csp = float(portfolio_context.get("cash_available_for_csp", 0) or 0)
            min_csp_buying_power = (
                float(
                    (getattr(self, "_growth_screener_config", {}) or {})
                    .get("screener_profile", {})
                    .get("min_csp_buying_power", 5000)
                )
                if getattr(self, "_growth_screener_config", None)
                else 5000.0
            )
            research_only_mode = ignore_cash_limits or cash_available_for_csp < min_csp_buying_power
            require_cash_fit = not ignore_cash_limits

            # Short-circuit cash-fit failures using cached price, avoiding rate limiter entirely.
            # Research-only mode can label borderline candidates, but it should not fetch chains
            # for names whose configured OTM strike window cannot fit free CSP cash at all.
            cached_price = conn.get_cached_stock_price(ticker)
            if cached_price is not None and cached_price > 0:
                if not self._has_any_affordable_otm_strike(
                    cached_price, portfolio_context, require_cash_fit=require_cash_fit
                ):
                    logger.debug(
                        f"Watchlist {ticker}: no configured OTM strike fits cached price ${cached_price:.2f} "
                        f"and buying power ${cash_available_for_csp:.2f}"
                    )
                    return [
                        self._make_skip_diagnostic(
                            ticker, "no_cash_fit", f"No CSP strike fits buying power (${cash_available_for_csp:.0f})"
                        )
                    ]

            stock_price = conn.get_stock_price(ticker)
            if stock_price is None or stock_price <= 0:
                return None

            growth_cfg = getattr(self, "_growth_screener_config", None)
            sp = (growth_cfg.get("screener_profile", {}) or {}) if growth_cfg else {}
            min_dte = sp.get("csp_min_dte", 30)
            max_dte = sp.get("csp_max_dte", 45)
            pref_dte = sp.get("csp_preferred_dte", 37)

            # After getting live price, re-check affordability before spending option-chain calls.
            if not self._has_any_affordable_otm_strike(
                stock_price, portfolio_context, require_cash_fit=require_cash_fit
            ):
                logger.debug(
                    f"Watchlist {ticker}: no OTM strike in 5-15% range fits buying power ${cash_available_for_csp:.2f}"
                )
                return [
                    self._make_skip_diagnostic(
                        ticker, "no_cash_fit", f"No CSP strike fits buying power (${cash_available_for_csp:.0f})"
                    )
                ]

            from moomoo import RET_OK

            today = datetime.now()
            ret, data = conn.get_option_expiration_dates(ticker)
            if ret != RET_OK or data is None:
                return None

            expiration_column = "expiration_date"
            if expiration_column not in data.columns:
                if "strike_time" in data.columns:
                    expiration_column = "strike_time"
                elif "option_expiry_date" in data.columns:
                    expiration_column = "option_expiry_date"
                else:
                    return None

            valid_expirations = []
            for raw_date in data[expiration_column].tolist():
                exp_str = str(raw_date).replace("-", "")
                try:
                    exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
                    dte = (exp_date - today.date()).days
                    if min_dte <= dte <= max_dte:
                        valid_expirations.append((exp_str, dte))
                except ValueError:
                    continue

            if not valid_expirations:
                return None

            valid_expirations.sort(key=lambda x: abs(pref_dte - x[1]))
            expirations_to_check = valid_expirations[:2]

            from core.wheel_decision import disabled_macro_context

            macro_regime = disabled_macro_context()

            candidates = []
            seen = set()

            for exp_str, dte in expirations_to_check:
                try:
                    chain = conn.get_option_chain(
                        ticker,
                        exp_str,
                        "P",
                        target_strike=stock_price * (1 - (sp.get("csp_default_otm_pct", 10) / 100)),
                    )
                    if not chain:
                        continue
                    options = chain.get("options", [])
                    if not options:
                        continue

                    # Build list of affordable OTM strikes, pick highest affordable
                    affordable_strikes = []
                    for opt in options:
                        if not _is_valid_external_option(opt, stock_price):
                            continue
                        strike = float(opt.get("strike", 0) or 0)
                        if strike >= stock_price or strike <= 0:
                            continue
                        cash_required = strike * 100
                        if require_cash_fit and cash_required > cash_available_for_csp:
                            continue
                        otm_pct = ((stock_price - strike) / stock_price) * 100
                        min_otm_pct = float(sp.get("csp_min_otm_pct", 5) or 5)
                        max_otm_pct = float(sp.get("csp_max_otm_pct", 15) or 15)
                        if otm_pct < min_otm_pct or otm_pct > max_otm_pct:
                            continue
                        affordable_strikes.append((strike, opt, otm_pct))

                    # Pick the highest affordable strike (closest to ATM within OTM range)
                    affordable_strikes.sort(key=lambda x: x[0], reverse=True)

                    for strike, opt, otm_pct in affordable_strikes:
                        key = (strike, exp_str)
                        if key in seen:
                            continue
                        seen.add(key)

                        bid = float(opt.get("bid", 0) or 0)
                        ask = float(opt.get("ask", 0) or 0)
                        last = float(opt.get("last", 0) or 0)
                        if bid <= 0 and ask <= 0 and last <= 0:
                            continue

                        (bid + ask) / 2 if bid > 0 and ask > 0 else max(bid, ask, last)

                        contract = {
                            "strike": strike,
                            "expiration": exp_str,
                            "option_type": "PUT",
                            "bid": bid,
                            "ask": ask,
                            "last": last,
                            "dte": dte,
                            "implied_volatility": float(opt.get("implied_volatility", 0) or 0),
                            "open_interest": int(opt.get("open_interest", 0) or opt.get("openInterest", 0) or 0),
                            "volume": int(opt.get("volume", 0) or 0),
                            "delta": float(opt.get("delta", 0) or 0),
                            "gamma": float(opt.get("gamma", 0) or 0),
                            "theta": float(opt.get("theta", 0) or 0),
                            "vega": float(opt.get("vega", 0) or 0),
                        }

                        decision = self._score_csp_contract(
                            contract,
                            ticker,
                            stock_price,
                            dte,
                            portfolio_context,
                            macro_regime,
                            research_only_mode=research_only_mode,
                        )

                        if decision is None or decision.hard_blockers:
                            # Capture blocker reasons for diagnostics
                            if decision and decision.hard_blockers:
                                reason_code = (
                                    decision.blocked_reason_codes[0]
                                    if decision.blocked_reason_codes
                                    else "blocked_by_scoring"
                                )
                                reason_text = (
                                    decision.hard_blockers[0] if decision.hard_blockers else "Scored candidate blocked"
                                )
                                candidates.append((-1, self._make_skip_diagnostic(ticker, reason_code, reason_text)))
                            continue

                        pick_score = premium_velocity_per_day(decision.premium_per_contract, decision.dte)

                        result = _format_decision_to_candidate(
                            ticker,
                            stock_price,
                            decision,
                            extra_warnings=[],
                            cash_reserve_enabled=self.config.get("cash_reserve_enabled", True),
                        )
                        if not require_cash_fit:
                            reason = (
                                "Best-plays mode: cash and buying-power limits ignored for ranking"
                                if ignore_cash_limits
                                else "Research-only CSP candidate: insufficient CSP cash for execution"
                            )
                            _mark_research_only_candidate(
                                result,
                                reason,
                            )
                        candidates.append((pick_score, result))

                except Exception:
                    logger.warning("Candidate scoring failed for %s", ticker, exc_info=True)
                    continue

            if not candidates:
                return [self._make_skip_diagnostic(ticker, "blocked_by_scoring", "All candidates filtered by scoring")]

            candidates.sort(key=lambda x: x[0], reverse=True)
            return [c for _, c in candidates[:3]]

        except Exception as e:
            logger.debug(f"Watchlist {ticker}: Moomoo CSP path failed ({e}), will try yfinance")
            return None

    def _fetch_yfinance_csp_candidates(self, ticker, portfolio_context, ignore_cash_limits: bool = False):
        """
        Fallback CSP fetch using yfinance.
        Returns list of candidate dicts, a list with a skip diagnostic, or None on error.
        """
        try:
            cash_available_for_csp = float(portfolio_context.get("cash_available_for_csp", 0) or 0)
            min_csp_buying_power = (
                float(
                    (getattr(self, "_growth_screener_config", {}) or {})
                    .get("screener_profile", {})
                    .get("min_csp_buying_power", 5000)
                )
                if getattr(self, "_growth_screener_config", None)
                else 5000.0
            )
            research_only_mode = ignore_cash_limits or cash_available_for_csp < min_csp_buying_power
            require_cash_fit = not ignore_cash_limits
            if cash_available_for_csp < min_csp_buying_power and not ignore_cash_limits:
                logger.debug(
                    f"Watchlist {ticker}: CSP cash ${cash_available_for_csp:.2f} below minimum "
                    f"${min_csp_buying_power:.2f}; skipping yfinance fallback"
                )
                return [
                    self._make_skip_diagnostic(
                        ticker, "no_cash_fit", f"No CSP strike fits buying power (${cash_available_for_csp:.0f})"
                    )
                ]

            # Try cached Moomoo price first (zero API calls).
            cached_price = None
            conn = self._get_connection()
            if conn:
                cached_price = conn.get_cached_stock_price(ticker)
            if cached_price is not None and cached_price > 0:
                if not self._has_any_affordable_otm_strike(
                    cached_price, portfolio_context, require_cash_fit=require_cash_fit
                ):
                    logger.debug(
                        f"Watchlist {ticker}: no OTM strike fits cached price ${cached_price:.2f} "
                        f"and buying power ${cash_available_for_csp:.2f} (yfinance pre-check)"
                    )
                    return [
                        self._make_skip_diagnostic(
                            ticker, "no_cash_fit", f"No CSP strike fits buying power (${cash_available_for_csp:.0f})"
                        )
                    ]

            hist = get_yfinance_history(ticker, period="1d", ticker_factory=get_yfinance_ticker)
            if hist is None or hist.empty:
                logger.warning(f"Watchlist {ticker}: yfinance history empty")
                return [self._make_skip_diagnostic(ticker, "no_yfinance_data", "yfinance history empty")]
            stock_price = float(hist["Close"].iloc[-1])

            # Re-check affordability with the live yfinance price.
            if not self._has_any_affordable_otm_strike(
                stock_price, portfolio_context, require_cash_fit=require_cash_fit
            ):
                logger.debug(
                    f"Watchlist {ticker}: no OTM strike in 5-15% range fits buying power ${cash_available_for_csp:.2f}"
                )
                return [
                    self._make_skip_diagnostic(
                        ticker, "no_cash_fit", f"No CSP strike fits buying power (${cash_available_for_csp:.0f})"
                    )
                ]

            opts = get_yfinance_options(ticker, ticker_factory=get_yfinance_ticker)
            if not opts:
                logger.warning(f"Watchlist {ticker}: no option expirations available from yfinance")
                return [self._make_skip_diagnostic(ticker, "no_option_chain", "No option expirations from yfinance")]

            growth_cfg = getattr(self, "_growth_screener_config", None)
            sp = (growth_cfg.get("screener_profile", {}) or {}) if growth_cfg else {}
            min_dte = sp.get("csp_min_dte", 30)
            max_dte = sp.get("csp_max_dte", 45)
            pref_dte = sp.get("csp_preferred_dte", 37)

            today = datetime.now()
            valid_expirations = []
            for exp in opts:
                try:
                    exp_date = datetime.strptime(exp, "%Y-%m-%d")
                    dte = (exp_date - today).days
                    if min_dte <= dte <= max_dte:
                        valid_expirations.append(exp)
                except ValueError:
                    continue

            if not valid_expirations:
                logger.warning(f"Watchlist {ticker}: no expiration with DTE {min_dte}-{max_dte} found")
                return [
                    self._make_skip_diagnostic(
                        ticker, "no_valid_dte", f"No expiration with DTE {min_dte}-{max_dte} found"
                    )
                ]

            valid_expirations = sorted(
                valid_expirations, key=lambda e: abs(pref_dte - (datetime.strptime(e, "%Y-%m-%d") - today).days)
            )
            expirations_to_check = valid_expirations[:2]

            candidates = []
            blocked_count = 0

            from core.wheel_decision import disabled_macro_context

            macro_regime = disabled_macro_context()

            for target_exp in expirations_to_check:
                try:
                    chain = get_yfinance_option_chain(
                        ticker,
                        target_exp,
                        ticker_factory=get_yfinance_ticker,
                    )
                    if not chain:
                        continue
                    puts = chain.get("puts")
                    if puts is None or puts.empty:
                        continue
                    puts = puts.copy()

                    # Build list of affordable OTM strikes, pick highest affordable
                    affordable_strikes = []
                    for _, put_row in puts.iterrows():
                        strike = float(put_row["strike"])
                        if strike >= stock_price:
                            continue
                        cash_required = strike * 100
                        if require_cash_fit and cash_required > cash_available_for_csp:
                            continue
                        otm_pct = ((stock_price - strike) / stock_price) * 100
                        min_otm_pct = float(sp.get("csp_min_otm_pct", 5) or 5)
                        max_otm_pct = float(sp.get("csp_max_otm_pct", 15) or 15)
                        if otm_pct < min_otm_pct or otm_pct > max_otm_pct:
                            continue
                        affordable_strikes.append((strike, put_row))

                    # Pick the highest affordable strike (closest to ATM within OTM range)
                    affordable_strikes.sort(key=lambda x: x[0], reverse=True)

                    for strike, put_row in affordable_strikes:
                        bid = float(put_row["bid"]) if not pd.isna(put_row["bid"]) else 0
                        ask = float(put_row["ask"]) if not pd.isna(put_row["ask"]) else 0
                        last_price = float(put_row["lastPrice"]) if not pd.isna(put_row["lastPrice"]) else 0

                        if bid <= 0 and ask <= 0 and last_price <= 0:
                            continue

                        mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else max(bid, ask, last_price)
                        spread_pct = ((ask - bid) / mid_price) * 100 if bid > 0 and ask > 0 and mid_price > 0 else 100
                        if spread_pct > 100:
                            continue

                        dte = (datetime.strptime(target_exp, "%Y-%m-%d") - today).days

                        contract = {
                            "strike": strike,
                            "expiration": target_exp.replace("-", ""),
                            "option_type": "PUT",
                            "bid": bid,
                            "ask": ask,
                            "last": last_price,
                            "dte": dte,
                            "implied_volatility": float(put_row["impliedVolatility"])
                            if not pd.isna(put_row.get("impliedVolatility"))
                            else 0,
                            "open_interest": int(put_row["openInterest"])
                            if not pd.isna(put_row["openInterest"])
                            else 0,
                            "volume": int(put_row["volume"]) if not pd.isna(put_row["volume"]) else 0,
                        }

                        contract["from_yfinance"] = True
                        contract["price_source"] = "yfinance"
                        contract["chain_source"] = "yfinance"
                        contract["iv_source"] = "yfinance"
                        contract["data_source"] = "yfinance"

                        if not _is_valid_external_option(contract, stock_price):
                            continue

                        decision = self._score_csp_contract(
                            contract,
                            ticker,
                            stock_price,
                            dte,
                            portfolio_context,
                            macro_regime,
                            research_only_mode=research_only_mode,
                        )

                        if decision is None or decision.hard_blockers:
                            blocked_count += 1
                            if decision and decision.hard_blockers and decision.blocked_reason_codes:
                                candidates.append(
                                    self._make_skip_diagnostic(
                                        ticker, decision.blocked_reason_codes[0], decision.hard_blockers[0]
                                    )
                                )
                            continue

                        premium_velocity_per_day(decision.premium_per_contract, decision.dte)

                        result = _format_decision_to_candidate(
                            ticker,
                            stock_price,
                            decision,
                            extra_warnings=["Data from yfinance (not Moomoo) - verify before trading"],
                            cash_reserve_enabled=self.config.get("cash_reserve_enabled", True),
                        )
                        reason = (
                            "Best-plays mode: cash and buying-power limits ignored for ranking"
                            if ignore_cash_limits
                            else "Research-only yfinance fallback candidate - verify before trading"
                        )
                        _mark_research_only_candidate(
                            result,
                            reason,
                        )
                        candidates.append(result)

                except Exception:
                    logger.warning("YFinance candidate scoring failed for %s", ticker, exc_info=True)
                    continue

            if not candidates:
                return [self._make_skip_diagnostic(ticker, "blocked_by_scoring", "All candidates filtered by scoring")]

            # Separate actual candidates from diagnostics
            real_candidates = [c for c in candidates if not c.get("_skip_diagnostic")]
            diagnostics = [c for c in candidates if c.get("_skip_diagnostic")]

            if not real_candidates:
                return diagnostics or [
                    self._make_skip_diagnostic(ticker, "blocked_by_scoring", "All candidates filtered by scoring")
                ]

            real_candidates.sort(
                key=lambda x: (
                    premium_velocity_per_day(x.get("premium_per_contract", 0), x.get("dte", 0)),
                    x.get("annualized_return", 0),
                    x.get("score", 0) or 0,
                ),
                reverse=True,
            )
            return real_candidates[:3]

        except Exception as e:
            logger.warning(f"Watchlist {ticker}: yfinance CSP fetch failed: {e}")
            return None

    def _make_skip_diagnostic(self, ticker, reason_code, reason_text):
        """Create a diagnostic entry for a skipped CSP candidate."""
        return {
            "_skip_diagnostic": True,
            "ticker": ticker,
            "reason_code": reason_code,
            "reason_text": reason_text,
        }

    def _format_recommendation(self, option, rank=0):
        """Format a raw option dict into a standardized recommendation dict (signal-only)."""
        wd = option.get("wheel_decision", {})
        opt_type = option.get("option_type", "")
        is_long = option.get("profile_type", "") in ("long_call", "long_put")
        is_csp = opt_type == "PUT" and not option.get("held_position", False)
        is_cc = opt_type == "CALL" and option.get("max_contracts", 0) > 0
        is_covered_call = is_cc
        is_csp_signal = is_csp
        # Research-only: long calls, long puts, earnings-vol calendars
        research_only = bool(
            option.get("research_only") or is_long or option.get("profile_type", "") == "earnings_calendar"
        )
        return {
            "rank": rank,
            "ticker": option["ticker"],
            "option_type": opt_type,
            "strike": option.get("strike"),
            "expiration": option.get("expiration"),
            "dte": option.get("dte"),
            "mid_price": option.get("mid_price"),
            "premium_per_contract": option.get("premium_per_contract"),
            "score": option.get("score"),
            "annualized_return": option.get("annualized_return"),
            "iv_adjusted_return": option.get("iv_adjusted_return"),
            "otm_pct": option.get("otm_pct"),
            "delta": option.get("delta"),
            "iv_rank": option.get("iv_rank"),
            "iv_status": option.get("iv_status"),
            "days_to_earnings": option.get("days_to_earnings"),
            "earnings_date": option.get("earnings_date"),
            "warnings": option.get("warnings", []),
            "rationale": option.get("rationale", []),
            "max_contracts": option.get("max_contracts"),
            "existing_position": option.get("existing_position", 0),
            "profile_type": option.get("profile_type"),
            "stock_price": option.get("stock_price"),
            "avg_cost": option.get("avg_cost"),
            "bid": option.get("bid"),
            "ask": option.get("ask"),
            "open_interest": option.get("open_interest"),
            "volume": option.get("volume"),
            "implied_volatility": option.get("implied_volatility"),
            "score_details": option.get("score_details", {}),
            "size_fit": option.get("size_fit", 0),
            "expected_move_buffer": option.get("expected_move_buffer", 0),
            "wheel_decision": option.get("wheel_decision", {}),
            "from_watchlist": option.get("from_watchlist", False),
            "held_position": option.get("held_position", False),
            # CSP-specific fields
            "cash_required": option.get("cash_required"),
            "breakeven": option.get("breakeven"),
            "breakeven_buffer_pct": option.get("breakeven_buffer_pct"),
            "macro_multiplier": wd.get("macro_multiplier", 1.0),
            # Growth-aware fields (always-on)
            "score_rationale": wd.get("score_rationale", ""),
            "remaining_gap_to_target": wd.get("remaining_gap_to_target", 0),
            "risk_budget_used_pct": wd.get("risk_budget_used_pct", 0),
            "stress_loss": wd.get("stress_loss", 0),
            "confidence_score": wd.get("confidence_score", 100),
            "covered_call_intent": wd.get("covered_call_intent", ""),
            # Signal-only fields
            "signal_type": "covered_call" if is_covered_call else ("csp" if is_csp_signal else opt_type.lower()),
            "strategy": "wheel",
            "broker_feasible": bool(option.get("broker_feasible", not research_only)),
            "capital_required": option.get("cash_required", 0),
            "risk_budget_used": wd.get("risk_budget_used_pct", 0),
            "data_source": wd.get("price_source", "moomoo"),
            "confidence": wd.get("confidence_score", 100),
            "price_source": _option_source_value(option, wd, "price_source", wd.get("price_source", "moomoo")),
            "chain_source": _option_source_value(option, wd, "chain_source", wd.get("chain_source", "broker")),
            "iv_source": _option_source_value(option, wd, "iv_source", wd.get("iv_source", "broker")),
            "from_yfinance": _option_uses_yfinance(option, wd),
            "quote_quality": wd.get("quote_quality", ""),
            "blocked_reason_codes": wd.get("blocked_reason_codes", []) or wd.get("hard_blockers", []),
            "research_only": research_only,
        }

    def get_top_recommendations(
        self, limit=3, include_long_options=False, ignore_cash_limits=False, screener_overrides=None
    ):
        """
        Get top N option signals across all portfolio positions and watchlist.

        Returns a unified signal payload ranked by premium velocity.

        Filters options by capital availability:
        - CALLs: Only if user has 100+ shares
        - PUTs: Only if user has sufficient cash (strike * 100)

        Uses CSP cash as the affordability constraint:
        - CSP cash is true cash not tied up by open short-put collateral
        - Broker buying power is returned separately as account context

        Args:
            limit (int): Number of top signals to return (default: 3, max: 10)
            include_long_options (bool): Include research-only long Call/Put lane
            ignore_cash_limits (bool): Show best plays without CSP cash/buying-power filters
            screener_overrides (dict): User-tunable screener settings (csp_min_otm_pct,
                csp_max_otm_pct, csp_min_dte, csp_max_dte, csp_target_delta,
                min_csp_buying_power) applied on top of defaults.

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

        logger.info(f"Getting top {limit} signals (ignore_cash_limits={ignore_cash_limits})")
        start_time = time.time()

        from core.scan_ledger import (
            ScanLedger,
            ScanLedgerEntry,
            compute_config_hash,
            compute_portfolio_hash,
            extract_data_sources,
        )

        try:
            # Growth mode is always-on — set growth profile from config
            growth_cfg = self.config.get("growth_mode", {}) if self.config else {}
            self._growth_profile = {
                "objective": growth_cfg.get("objective", "time_to_2x"),
                "target_account_multiple": float(growth_cfg.get("target_account_multiple", 2.0)),
                "max_drawdown_pct": float(growth_cfg.get("max_drawdown_pct", 0.40)),
                "execution_scope": growth_cfg.get("execution_scope", "short_premium_wheel"),
                "long_options_mode": growth_cfg.get("long_options_mode", "research_only"),
            }
            # Propagate to options_data service for unified scoring
            if hasattr(self._options_data_provider, "_growth_profile"):
                self._options_data_provider._growth_profile = self._growth_profile

            # Build growth mode config for screener overlay
            self._growth_screener_config = growth_cfg

            # Best-plays / unconstrained mode: widen OTM and DTE windows so the
            # toggle produces a visibly larger, more aggressive ranked set.
            if ignore_cash_limits:
                sp = dict((growth_cfg.get("screener_profile") or {}))
                sp["csp_min_otm_pct"] = 2
                sp["csp_max_otm_pct"] = 25
                sp["csp_min_dte"] = 7
                sp["csp_max_dte"] = 60
                self._growth_screener_config = {**growth_cfg, "screener_profile": sp}

            # Apply user-tunable screener overrides (from UI sliders)
            if screener_overrides:
                sp = dict((self._growth_screener_config.get("screener_profile") or {}))
                for key, val in screener_overrides.items():
                    if val is not None:
                        sp[key] = val
                self._growth_screener_config = {**self._growth_screener_config, "screener_profile": sp}

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
                return {"error": "Failed to establish connection to moomoo"}

            # Get portfolio context for positions and cash balance
            ctx_start = time.time()
            portfolio_context = self._get_portfolio_context()
            logger.info(f"[TIMING] Portfolio context: {time.time() - ctx_start:.2f}s")
            positions = portfolio_context.get("positions", {})
            float(portfolio_context.get("available_cash", 0) or 0)
            broker_buying_power = float(portfolio_context.get("broker_buying_power", 0) or 0)
            cash_available_for_csp = float(portfolio_context.get("cash_available_for_csp", 0) or 0)
            cash_reserved_for_csp = float(portfolio_context.get("cash_reserved_for_csp", 0) or 0)
            short_calls = portfolio_context.get("short_calls", {})
            short_puts = portfolio_context.get("short_puts", {})

            effective_watchlist = self._watchlist_provider.get_effective_watchlist(
                growth_mode_config=self._growth_screener_config,
                portfolio_context=None if ignore_cash_limits else portfolio_context,
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
                        total_candidates=0,
                        passed_count=0,
                        blocked_count=0,
                        data_sources=extract_data_sources(portfolio_context),
                    )
                    ScanLedger(self.db).record(entry)
                except Exception:
                    logger.debug("Scan ledger write skipped (empty scan)", exc_info=True)
                return {
                    "success": True,
                    "count": 0,
                    "total_scored": 0,
                    "generated_at": datetime.now().isoformat(),
                    "signals": [],
                    "broker_buying_power": broker_buying_power,
                    "cash_available_for_csp": cash_available_for_csp,
                    "cash_reserved_for_csp": cash_reserved_for_csp,
                    "ignore_cash_limits": bool(ignore_cash_limits),
                    "blocked_signals": [],
                    "blocked_reason_counts": {},
                    "message": "No positions or watchlist configured",
                }

            # ── Lanes ──────────────────────────────────────────────────
            covered_call_candidates = []
            watchlist_csp_candidates = []
            long_call_candidates = []
            long_put_candidates = []

            # ── Diagnostics ────────────────────────────────────────────
            watchlist_processed = 0
            watchlist_cached = 0
            watchlist_errors = 0
            skipped_csp_diagnostics = []
            ticker_diagnostics = {}

            # ════════════════════════════════════════════════════════════
            # LANE 1: Watchlist CSPs — short-circuit when no cash to deploy
            # so we don't burn rate-limit budget on chain calls that cannot
            # produce signals.
            # ════════════════════════════════════════════════════════════
            csp_start = time.time()
            min_csp_buying_power = (
                float((growth_cfg.get("screener_profile", {}) or {}).get("min_csp_buying_power", 5000))
                if growth_cfg
                else 5000.0
            )

            # Cap tickers per cold scan to bound scan time within the client retry
            # budget. Each ticker may need 2 option-chain calls (3s spacing each)
            # plus price/expiration calls, so ~10-15 tickers fits in ~120s worst case.
            MAX_COLD_SCAN_TICKERS = 12
            scan_watchlist = effective_watchlist[:MAX_COLD_SCAN_TICKERS]
            if len(effective_watchlist) > MAX_COLD_SCAN_TICKERS:
                logger.info(
                    "Capping cold scan to %d of %d tickers to fit retry budget",
                    MAX_COLD_SCAN_TICKERS,
                    len(effective_watchlist),
                )

            if cash_available_for_csp <= 0 and not ignore_cash_limits:
                logger.info(
                    "Skipping CSP lane: cash_available_for_csp is $0, no signals possible for %d tickers",
                    len(effective_watchlist),
                )
                skipped_csp_diagnostics.append(
                    self._make_skip_diagnostic(
                        "__lane__",
                        "no_cash_for_csp",
                        f"Cash available for CSP is $0, skipping {len(effective_watchlist)} tickers",
                    )
                )
                skipped_csp_diagnostics[-1]["ticker_count"] = len(effective_watchlist)
            else:
                if cash_available_for_csp < min_csp_buying_power:
                    logger.info(
                        "Running research-only watchlist CSP scan: CSP cash %.2f < minimum %.2f",
                        cash_available_for_csp,
                        min_csp_buying_power,
                    )

                for ticker in scan_watchlist:
                    is_held = ticker in positions

                    cached = self._get_cached_watchlist_data(
                        ticker,
                        portfolio_context,
                        ignore_cash_limits=ignore_cash_limits,
                    )
                    if cached is not None:
                        watchlist_cached += 1
                        for cached_item in cached:
                            if cached_item.get("_skip_diagnostic"):
                                skipped_csp_diagnostics.append(cached_item)
                                continue
                            cached_item["held_position"] = is_held
                            cached_item["existing_position"] = short_puts.get(ticker, 0)
                            watchlist_csp_candidates.append(cached_item)
                        continue

                    results = self._fetch_watchlist_ticker_csp(
                        ticker,
                        portfolio_context,
                        ignore_cash_limits=ignore_cash_limits,
                    )
                    if not results:
                        watchlist_errors += 1
                        continue

                    self._set_cached_watchlist_data(
                        ticker,
                        results,
                        portfolio_context,
                        ignore_cash_limits=ignore_cash_limits,
                    )

                    for result in results:
                        if result.get("_skip_diagnostic"):
                            skipped_csp_diagnostics.append(result)
                            continue
                        result["held_position"] = is_held
                        result["existing_position"] = short_puts.get(ticker, 0)
                        watchlist_csp_candidates.append(result)
                        watchlist_processed += 1

            csp_elapsed = time.time() - csp_start
            logger.info(
                f"[TIMING] Watchlist CSP scan: {csp_elapsed:.2f}s "
                f"({watchlist_processed} fetched, {watchlist_cached} cached, {watchlist_errors} errors)"
            )

            # ════════════════════════════════════════════════════════════
            # LANE 2: Research-only long Calls/Puts — on-demand only.
            # ════════════════════════════════════════════════════════════
            long_start = time.time()
            if (
                include_long_options
                and (self._growth_profile or {}).get("long_options_mode", "research_only") == "research_only"
            ):
                for ticker in scan_watchlist:
                    long_results = self._fetch_watchlist_long_options(ticker, portfolio_context)
                    for call in long_results.get("calls", []):
                        call["held_position"] = ticker in positions
                        long_call_candidates.append(call)
                    for put in long_results.get("puts", []):
                        put["held_position"] = ticker in positions
                        long_put_candidates.append(put)
            long_elapsed = time.time() - long_start
            logger.info(
                f"[TIMING] Research-only long option scan: {long_elapsed:.2f}s "
                f"({len(long_call_candidates)} calls, {len(long_put_candidates)} puts)"
            )

            # ════════════════════════════════════════════════════════════
            # LANE 3: Covered Calls — from portfolio positions only
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
            # Pre-check: skip CC lane entirely if no position has 100+ shares
            has_cc_capacity = any(float(p.get("position", 0) or 0) >= 100 for p in positions.values())
            if not has_cc_capacity:
                logger.info("Skipping CC lane: no position with 100+ shares")
            if has_cc_capacity:
                for ticker in deduped_position_tickers:
                    try:
                        # Get stock price for this ticker
                        stock_price = conn.get_stock_price(ticker)
                        if stock_price is None or stock_price <= 0:
                            position = portfolio_context.get("positions", {}).get(ticker, {})
                            stock_price = float(position.get("market_price", 0) or position.get("avg_cost", 0) or 0)

                        if not stock_price or stock_price <= 0:
                            logger.warning(f"Skipping {ticker}: Unable to get stock price")
                            continue

                        # Get position data
                        position_data = positions.get(ticker, {})
                        shares_owned = float(position_data.get("position", 0) or 0)

                        # Calculate available contracts for calls (accounting for existing short calls)
                        total_possible_calls = int(shares_owned // 100)
                        existing_short_calls = short_calls.get(ticker, 0)
                        available_calls = max(0, total_possible_calls - existing_short_calls)

                        existing_short_puts = short_puts.get(ticker, 0)

                        logger.info(
                            f"{ticker}: {shares_owned} shares, {total_possible_calls} possible calls, "
                            f"{existing_short_calls} existing short calls, {available_calls} available, "
                            f"{existing_short_puts} existing short puts"
                        )

                        # Determine OTM target — always use growth-tuned default
                        if available_calls <= 0:
                            continue

                        sp = (growth_cfg.get("screener_profile", {}) or {}) if growth_cfg else {}
                        otm_target = sp.get("call_default_otm_pct", 10)

                        # Fetch options data for this ticker
                        result = self._options_data_provider._process_ticker_for_otm(
                            conn=conn,
                            ticker=ticker,
                            otm_percentage=otm_target,  # Growth-tuned or default OTM
                            portfolio_context=portfolio_context,
                            expiration=None,  # Get all expirations
                            option_type="CALL",  # Covered-call lane only
                        )

                        if "error" in result:
                            logger.warning(f"Error processing {ticker}: {result['error']}")
                            continue

                        # Process CALLs — covered call lane
                        if available_calls > 0:
                            for call in result.get("calls", []):
                                covered_call_candidates.append(
                                    {
                                        "ticker": ticker,
                                        "stock_price": stock_price,
                                        "option_type": "CALL",
                                        "max_contracts": available_calls,
                                        "existing_position": existing_short_calls,
                                        "avg_cost": float(position_data.get("avg_cost", 0) or 0),
                                        "held_position": False,
                                        "from_watchlist": False,
                                        **call,
                                    }
                                )

                    except Exception as e:
                        logger.error(f"Error processing {ticker} for signals: {e}")
                        continue

            cc_elapsed = time.time() - cc_start
            logger.info(f"[TIMING] Covered call scan: {cc_elapsed:.2f}s")
            blocked_signals = []
            reason_counts = {}
            # blocked_signals and reason_counts were initialized before gating.

            # ── Per-ticker diagnostics ───────────────────────────────
            def _apply_data_quality_gate(candidates):
                kept = []
                for opt in candidates:
                    wd = opt.get("wheel_decision", {}) or {}
                    price_source = _option_source_value(opt, wd, "price_source", opt.get("data_source", "broker"))
                    chain_source = _option_source_value(opt, wd, "chain_source", "broker")
                    iv_source = _option_source_value(opt, wd, "iv_source", "broker")
                    from_yfinance = _option_uses_yfinance(opt, wd)
                    confidence = float(opt.get("confidence", wd.get("confidence_score", 100)) or 0)
                    has_blockers = bool(
                        opt.get("hard_blockers")
                        or wd.get("hard_blockers")
                        or opt.get("blocked_reason_codes")
                        or wd.get("blocked_reason_codes")
                    )
                    blocked, reason = should_block_for_data_quality(
                        confidence_score=confidence,
                        has_blockers=has_blockers,
                        is_from_yfinance=from_yfinance,
                        price_source=price_source,
                    )
                    if blocked:
                        if from_yfinance and not has_blockers:
                            kept_candidate = dict(opt)
                            _mark_research_only_candidate(kept_candidate, reason)
                            kept.append(kept_candidate)
                            continue
                        blocked_signals.append(
                            {
                                "ticker": opt.get("ticker", ""),
                                "option_type": opt.get("option_type", ""),
                                "signal_type": opt.get("signal_type", ""),
                                "reason_code": "data_quality_blocked",
                                "reason_text": reason,
                                "actionable": False,
                                "price_source": price_source,
                                "chain_source": chain_source,
                                "iv_source": iv_source,
                                "from_yfinance": from_yfinance,
                                "confidence_score": confidence,
                            }
                        )
                        reason_counts["data_quality_blocked"] = reason_counts.get("data_quality_blocked", 0) + 1
                        continue
                    if from_yfinance and not opt.get("research_only"):
                        kept_candidate = dict(opt)
                        _mark_research_only_candidate(
                            kept_candidate,
                            "yfinance fallback data - research only",
                        )
                        kept.append(kept_candidate)
                        continue
                    kept.append(opt)
                return kept

            eligible_covered_call_candidates = _apply_data_quality_gate(covered_call_candidates)
            eligible_watchlist_csp_candidates = _apply_data_quality_gate(watchlist_csp_candidates)
            eligible_long_call_candidates = _apply_data_quality_gate(long_call_candidates)
            eligible_long_put_candidates = _apply_data_quality_gate(long_put_candidates)

            all_candidates = (
                eligible_covered_call_candidates
                + eligible_watchlist_csp_candidates
                + eligible_long_call_candidates
                + eligible_long_put_candidates
            )
            for opt in all_candidates:
                t = opt.get("ticker", "UNKNOWN")
                score = opt.get("score", 0)
                if t not in ticker_diagnostics:
                    ticker_diagnostics[t] = {"top_score": score, "candidate_count": 0, "filtered_out": False}
                else:
                    ticker_diagnostics[t]["top_score"] = max(ticker_diagnostics[t]["top_score"], score)
                ticker_diagnostics[t]["candidate_count"] += 1

            # ── Unified ranking: one engine for CSPs and covered calls ──
            # Sort by premium velocity (premium_per_contract / dte) — the primary ranking axis.
            # Multi-factor scoring still qualifies candidates (via hard_blockers, warnings, risk budget),
            # but the final rank is determined by raw premium earned per day.
            def _get_rank_score(x):
                return premium_velocity_per_day(x.get("premium_per_contract", 0), x.get("dte", 0))

            all_candidates.sort(key=_get_rank_score, reverse=True)

            # ── Build lane results with diversity safeguard ──────────
            def _select_top(candidates, max_per=1):
                """Select top candidates with at most max_per per canonical underlying."""
                selected = []
                for opt in candidates:
                    t = opt.get("ticker", "UNKNOWN")
                    cu = canonical_underlying(t)
                    count = sum(1 for o in selected if canonical_underlying(o.get("ticker", "UNKNOWN")) == cu)
                    if count < max_per:
                        selected.append(opt)
                return selected

            top_covered_calls = _select_top(eligible_covered_call_candidates, max_per=1)
            top_watchlist_csp = _select_top(eligible_watchlist_csp_candidates, max_per=2)
            top_long_calls = _select_top(eligible_long_call_candidates, max_per=1)
            top_long_puts = _select_top(eligible_long_put_candidates, max_per=1)

            # Single ranked signal pipeline combining CSPs and covered calls.
            signals = _select_top(all_candidates, max_per=2)[:limit]

            # Log per-ticker diagnostics
            for t, diag in sorted(ticker_diagnostics.items(), key=lambda x: x[1]["top_score"], reverse=True):
                logger.info(f"  TICKER {t}: top_score={diag['top_score']:.1f}, candidates={diag['candidate_count']}")

            # Format the unified signal list.
            def _format_rec_list(candidates, start_rank=1):
                return [self._format_recommendation(opt, rank) for rank, opt in enumerate(candidates, start_rank)]

            formatted_signals = _format_rec_list(signals)
            formatted_cc = _format_rec_list(top_covered_calls)
            formatted_csp = _format_rec_list(top_watchlist_csp)
            formatted_long_calls = _format_rec_list(top_long_calls)
            formatted_long_puts = _format_rec_list(top_long_puts)

            scoring_elapsed = time.time() - cc_start - cc_elapsed
            elapsed = time.time() - start_time
            logger.info(f"[TIMING] Scoring & ranking: {scoring_elapsed:.2f}s")
            logger.info(
                f"[TIMING] Total: {elapsed:.2f}s — generated {len(formatted_signals)} signals, "
                f"{len(formatted_cc)} CC, {len(formatted_csp)} CSP, "
                f"{len(formatted_long_calls)} long calls, {len(formatted_long_puts)} long puts"
            )

            # Build blocked signal diagnostics.
            for diag in skipped_csp_diagnostics:
                rcode = diag.get("reason_code", "unknown")
                reason_counts[rcode] = reason_counts.get(rcode, 0) + int(diag.get("ticker_count", 1) or 1)
                blocked_signals.append(
                    {
                        "ticker": diag.get("ticker", ""),
                        "ticker_count": int(diag.get("ticker_count", 1) or 1),
                        "reason_code": rcode,
                        "reason_text": diag.get("reason_text", ""),
                        "signal_type": "csp",
                        "actionable": False,
                    }
                )

            enrichment_hint = {
                "mode": "none",
                "removed_from_hot_path": ["catalyst_warnings", "underlying_quality", "signal_overlay", "macro"],
            }

            # Record scan ledger entry (non-blocking)
            try:
                top_signals = [
                    {
                        "ticker": s.get("ticker"),
                        "option_type": s.get("option_type"),
                        "strike": s.get("strike"),
                        "score": s.get("score"),
                        "annualized_return": s.get("annualized_return"),
                    }
                    for s in formatted_signals[:5]
                ]
                blocked_summary = [
                    {
                        "ticker": b.get("ticker"),
                        "reason": b.get("reason_text", b.get("reason", "")),
                        "reason_code": b.get("reason_code", ""),
                        "option_type": b.get("option_type", ""),
                    }
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

            return {
                "success": True,
                "count": len(formatted_signals),
                "total_scored": len(all_candidates),
                "generated_at": datetime.now().isoformat(),
                "signals": formatted_signals,
                "covered_calls": {
                    "signals": formatted_cc,
                    "count": len(formatted_cc),
                },
                "watchlist_csps": {
                    "signals": formatted_csp,
                    "count": len(formatted_csp),
                },
                "long_calls": {
                    "signals": formatted_long_calls,
                    "count": len(formatted_long_calls),
                    "research_only": True,
                },
                "long_puts": {
                    "signals": formatted_long_puts,
                    "count": len(formatted_long_puts),
                    "research_only": True,
                },
                "broker_buying_power": round(broker_buying_power, 2),
                "cash_available_for_csp": round(cash_available_for_csp, 2),
                "cash_reserved_for_csp": round(cash_reserved_for_csp, 2),
                "cash_diagnostics": portfolio_context.get("_cash_diagnostics", {}),
                "ignore_cash_limits": bool(ignore_cash_limits),
                "enrichment": enrichment_hint,
                "blocked_signals": blocked_signals,
                "blocked_reason_counts": dict(sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)),
                "growth_mode": {
                    "enabled": True,
                    "objective": (self._growth_profile or {}).get("objective", "time_to_2x"),
                    "target_account_multiple": (self._growth_profile or {}).get("target_account_multiple", 2.0),
                    "max_drawdown_pct": (self._growth_profile or {}).get("max_drawdown_pct", 0.40),
                    "screener_profile": ((self._growth_screener_config or {}).get("screener_profile", {}) or {}),
                    "csp_profile_summary": _build_csp_profile_summary(self._growth_screener_config),
                },
                "_freshness": {
                    "market_state": "closed" if not is_market_open() else "open",
                    "generated_at": datetime.now().isoformat(),
                    "as_of": datetime.now().isoformat(),
                },
                "_diagnostics": {
                    "tickers": ticker_diagnostics,
                    "limit_applied": limit,
                    "max_per_ticker": 2,
                    "watchlist_processed": watchlist_processed,
                    "watchlist_cached": watchlist_cached,
                    "watchlist_errors": watchlist_errors,
                    "position_tickers": list(positions.keys()) if positions else [],
                    "watchlist_tickers": effective_watchlist,
                    "scan_tickers_count": len(scan_watchlist),
                    "scan_tickers_cap": MAX_COLD_SCAN_TICKERS,
                    "skipped_csp": skipped_csp_diagnostics,
                    "skipped_csp_count": len(skipped_csp_diagnostics),
                },
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
            return {"error": str(e)}


def _build_csp_profile_summary(growth_cfg: dict | None) -> str:
    """
    Build a human-readable summary of the active Growth CSP screener profile.
    Returns an empty string if growth mode is not configured.
    """
    if not growth_cfg:
        return ""
    sp = growth_cfg.get("screener_profile", {}) or {}
    parts = []
    parts.append(f"delta {sp.get('csp_target_delta', 0.30)}")
    parts.append(f"+/-{sp.get('csp_delta_tolerance', 0.12)}")
    dte = sp.get("csp_preferred_dte", 37)
    min_dte = sp.get("csp_min_dte", 30)
    max_dte = sp.get("csp_max_dte", 45)
    min_otm = sp.get("csp_min_otm_pct", 5)
    max_otm = sp.get("csp_max_otm_pct", 15)
    parts.append(f"DTE {min_dte}-{max_dte} (pref {dte})")
    parts.append(f"OTM {min_otm}-{max_otm}% (pref {sp.get('csp_default_otm_pct', 10)}%)")
    parts.append(f"volatility >={sp.get('min_volatility_pct', 4.5)}%")
    parts.append(f"min BP ${sp.get('min_csp_buying_power', 5000):,.0f}")
    if sp.get("require_cash_fit", True):
        parts.append("cash-fit req.")
    return " | ".join(parts)
