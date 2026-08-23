"""
Signals module - handles top options signals.
Extracted from the monolithic options_service.py for maintainability.
"""

import logging
import time
from datetime import datetime

from api.services.recommendation_ranking import (
    format_recommendation,
    rank_candidates,
)
from api.services.recommendation_ranking import (
    option_source_value as _option_source_value,
)
from api.services.recommendation_ranking import (
    option_uses_yfinance as _option_uses_yfinance,
)
from api.services.utils import clean_yfinance_ticker
from core.growth_mode import should_block_for_data_quality
from core.scoring_factors import premium_velocity_per_day
from core.sizing import existing_short_exposure_by_underlying
from core.ticker_utils import canonical_underlying
from core.utils import entry_window_advice, is_market_open
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
        "bid_premium_per_contract": round(decision.bid_premium_per_contract, 2),
        "limit_target_per_contract": round(decision.limit_target_per_contract, 2),
        "premium_velocity_per_day": round(decision.premium_velocity_per_day, 4),
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
        "quality_tier": decision.quality_tier,
        "event_tier": decision.event_tier,
        "security_type": decision.security_type,
        "review_only": decision.review_only,
        "copy_eligible": decision.copy_eligible,
        "quote_update_time": decision.quote_update_time,
        "quote_fetched_at_utc": decision.quote_fetched_at_utc,
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
        self._yfinance_cache_ttl = 300  # 5 minutes
        # Versioned risk preset (replaces growth mode + granular overrides)
        from core.presets import get_preset

        self._preset = get_preset((self.config or {}).get("wheel_preset"))
        self._preset_profile = self._preset.to_screener_profile()
        self._scan_security_types = {}

    def set_active_preset(self, key: str) -> dict:
        """Switch the live engine to a persisted preset (values stay immutable)."""
        from core.presets import get_preset

        self._preset = get_preset(key)
        self._preset_profile = self._preset.to_screener_profile()
        return self._preset.to_dict()

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

    def _watchlist_cache_key(self, ticker: str, portfolio_context: dict | None = None) -> tuple:
        bp = 0.0
        if portfolio_context:
            bp = float(portfolio_context.get("cash_available_for_csp", 0) or 0)
        return (ticker, int(bp // 1000))

    def _get_cached_watchlist_data(self, ticker, portfolio_context=None):
        entry = self._yfinance_cache.get(self._watchlist_cache_key(ticker, portfolio_context))
        if self._is_cache_valid(entry):
            return entry["data"]
        return None

    def _set_cached_watchlist_data(self, ticker, data, portfolio_context=None):
        self._yfinance_cache[self._watchlist_cache_key(ticker, portfolio_context)] = {
            "data": data,
            "timestamp": datetime.now(),
        }

    def _csp_target_strike_estimate(self, stock_price: float, portfolio_context: dict) -> float:
        """Estimate the cash requirement for the default CSP target strike."""
        otm_pct = float(self._preset_profile.get("csp_default_otm_pct", 10) or 10)
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
        sp = self._preset_profile
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
        self, contract, ticker, stock_price, dte, portfolio_context, research_only_mode: bool = False
    ):
        """Score a CSP contract from broker data (no external enrichment)."""
        iv_val = float(contract.get("implied_volatility", 0) or 0)
        delta_val = float(contract.get("delta", 0) or 0)
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
            growth_mode_config=self._preset_profile,
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
            growth_profile=None,
            research_only_mode=research_only_mode,
        )

    def _fetch_watchlist_ticker_csp(self, ticker, portfolio_context):
        """
        Fetch CSP candidates for a watchlist ticker (Moomoo broker data only).
        Returns list of candidate dicts, a list with a skip diagnostic, or None on error.
        """
        return self._fetch_watchlist_csp_moomoo(ticker, portfolio_context)

    def _fetch_watchlist_csp_moomoo(self, ticker, portfolio_context):
        """
        Fetch CSP candidates from Moomoo for a watchlist ticker.
        Returns list of candidate dicts, or None on failure.
        """
        # Market-closed: no actionable quotes; planning handled at run level.
        if not is_market_open():
            logger.debug(f"Market closed: skipping Moomoo CSP fetch for {ticker}")
            return None

        try:
            conn = self._get_connection()
            if not conn:
                return None

            cash_available_for_csp = float(portfolio_context.get("cash_available_for_csp", 0) or 0)
            min_csp_buying_power = float(self._preset_profile.get("min_csp_buying_power", 5000) or 5000)
            research_only_mode = cash_available_for_csp < min_csp_buying_power
            require_cash_fit = True

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

            sp = self._preset_profile
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
                            "update_time": str(opt.get("update_time", "") or ""),
                            "quote_fetched_at_utc": str(opt.get("quote_fetched_at_utc", "") or ""),
                            "security_type": self._scan_security_types.get(
                                str(ticker).upper().split(".")[-1], str(opt.get("security_type", "") or "stock")
                            ),
                        }

                        decision = self._score_csp_contract(
                            contract,
                            ticker,
                            stock_price,
                            dte,
                            portfolio_context,
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
                            _mark_research_only_candidate(
                                result,
                                "Research-only CSP candidate: insufficient CSP cash for execution",
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

    def _make_skip_diagnostic(self, ticker, reason_code, reason_text):
        """Create a diagnostic entry for a skipped CSP candidate."""
        return {
            "_skip_diagnostic": True,
            "ticker": ticker,
            "reason_code": reason_code,
            "reason_text": reason_text,
        }

    def get_top_recommendations(self, limit=3):
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

        logger.info(f"Getting top {limit} signals (preset={self._preset.key})")
        start_time = time.time()

        from core.scan_ledger import (
            ScanLedger,
            ScanLedgerEntry,
            compute_config_hash,
            compute_portfolio_hash,
            extract_data_sources,
        )

        try:
            # Preset is resolved in __init__; nothing to reconfigure per run.
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

            # Canonical merged union: Moomoo group + app-managed SQLite + config.
            # The manager deduplicates by canonical underlying and labels origins.
            watchlist_with_origins = self._watchlist_provider.get_effective_watchlist_with_origins(
                growth_mode_config=self._preset_profile,
                portfolio_context=portfolio_context,
            )
            if not isinstance(watchlist_with_origins, list):
                # Mock/stub providers: fall back to the plain list form.
                plain = self._watchlist_provider.get_effective_watchlist(
                    growth_mode_config=self._preset_profile,
                    portfolio_context=portfolio_context,
                )
                plain = plain if isinstance(plain, list) else []
                watchlist_with_origins = [{"ticker": str(t), "origins": ["config"]} for t in plain]
            effective_watchlist = [item["ticker"] for item in watchlist_with_origins]
            watchlist_origins = {item["ticker"]: item["origins"] for item in watchlist_with_origins}
            security_types = {}
            get_security_types = getattr(conn, "get_security_types", None)
            if callable(get_security_types) and effective_watchlist:
                try:
                    raw_security_types = get_security_types(effective_watchlist)
                    if isinstance(raw_security_types, dict):
                        security_types = {
                            str(symbol).upper().split(".")[-1]: str(value).lower()
                            for symbol, value in raw_security_types.items()
                        }
                except Exception:
                    logger.debug("Broker security-type batch lookup unavailable", exc_info=True)
            self._scan_security_types = security_types
            logger.info(f"Effective watchlist: {len(effective_watchlist)} tickers (canonical union)")

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
                    "blocked_signals": [],
                    "blocked_reason_counts": {},
                    "message": "No positions or watchlist configured",
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
            scan_quote_fetched_at = {}

            # ════════════════════════════════════════════════════════════
            # LANE 1: Watchlist CSPs — short-circuit when no cash to deploy
            # so we don't burn rate-limit budget on chain calls that cannot
            # produce signals.
            # ════════════════════════════════════════════════════════════
            csp_start = time.time()
            min_csp_buying_power = float(self._preset_profile.get("min_csp_buying_power", 5000) or 5000)

            # ── Scan feasibility preflight ────────────────────────────────
            # Every actionable run must scan the COMPLETE canonical union. If the
            # union cannot fit the OpenD quota + freshness window, we publish
            # planning diagnostics and direct the user to reduce a source list.
            # We never silently truncate and still claim a global top three.
            preflight = self._watchlist_provider.preflight_scan_feasibility(len(effective_watchlist))
            if not isinstance(preflight, dict):
                # Mock/stub providers in tests: assume feasible.
                preflight = {
                    "feasible": True,
                    "watchlist_size": len(effective_watchlist),
                    "estimated_scan_sec": 0.0,
                    "freshness_window_sec": 300,
                    "chain_calls": 0,
                    "chain_quota_ok": True,
                    "recommended_max_size": max(12, len(effective_watchlist)),
                }
            if not preflight["feasible"]:
                logger.warning(
                    "Scan infeasible: %d tickers, est %.0fs vs freshness window %ds",
                    preflight["watchlist_size"],
                    preflight["estimated_scan_sec"],
                    preflight["freshness_window_sec"],
                )
                return {
                    "success": True,
                    "count": 0,
                    "total_scored": 0,
                    "generated_at": datetime.now().isoformat(),
                    "signals": [],
                    "broker_buying_power": broker_buying_power,
                    "cash_available_for_csp": cash_available_for_csp,
                    "cash_reserved_for_csp": cash_reserved_for_csp,
                    "blocked_signals": [],
                    "blocked_reason_counts": {},
                    "state": "planning",
                    "scan_coverage": {"scanned": 0, "total": len(effective_watchlist), "complete": False},
                    "preflight": preflight,
                    "message": (
                        f"Full watchlist scan is infeasible within the freshness window "
                        f"({preflight['watchlist_size']} tickers, est {preflight['estimated_scan_sec']:.0f}s "
                        f"vs {preflight['freshness_window_sec']}s). Reduce the Moomoo watchlist group "
                        f"or the app-managed list to at most {preflight['recommended_max_size']} symbols "
                        f"for actionable top-three results."
                    ),
                }

            # Scan the COMPLETE canonical union. The preflight above already
            # guaranteed feasibility (or we returned `planning`). We never
            # silently truncate and still claim a global top three.
            scan_watchlist = effective_watchlist

            if cash_available_for_csp <= 0:
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

                    cached = self._get_cached_watchlist_data(ticker, portfolio_context)
                    if cached is not None:
                        watchlist_cached += 1
                        for cached_item in cached:
                            if cached_item.get("_skip_diagnostic"):
                                skipped_csp_diagnostics.append(cached_item)
                                continue
                            cached_item["held_position"] = is_held
                            cached_item["existing_position"] = short_puts.get(ticker, 0)
                            cached_item["security_type"] = security_types.get(
                                str(ticker).upper().split(".")[-1], "stock"
                            )
                            cached_fetched_at = str(
                                cached_item.get("quote_fetched_at_utc")
                                or (cached_item.get("wheel_decision") or {}).get("quote_fetched_at_utc", "")
                                or ""
                            )
                            if cached_fetched_at:
                                scan_quote_fetched_at[str(ticker)] = cached_fetched_at
                            watchlist_csp_candidates.append(cached_item)
                        continue

                    results = self._fetch_watchlist_ticker_csp(ticker, portfolio_context)
                    if not results:
                        watchlist_errors += 1
                        continue

                    self._set_cached_watchlist_data(ticker, results, portfolio_context)

                    for result in results:
                        if result.get("_skip_diagnostic"):
                            skipped_csp_diagnostics.append(result)
                            continue
                        result["held_position"] = is_held
                        result["existing_position"] = short_puts.get(ticker, 0)
                        result["security_type"] = security_types.get(str(ticker).upper().split(".")[-1], "stock")
                        result_fetched_at = str(
                            result.get("quote_fetched_at_utc")
                            or (result.get("wheel_decision") or {}).get("quote_fetched_at_utc", "")
                            or ""
                        )
                        if result_fetched_at:
                            scan_quote_fetched_at[str(ticker)] = result_fetched_at
                        watchlist_csp_candidates.append(result)
                        watchlist_processed += 1

            csp_elapsed = time.time() - csp_start
            logger.info(
                f"[TIMING] Watchlist CSP scan: {csp_elapsed:.2f}s "
                f"({watchlist_processed} fetched, {watchlist_cached} cached, {watchlist_errors} errors)"
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

                        # Determine OTM target — from the active preset
                        if available_calls <= 0:
                            continue

                        sp = self._preset_profile
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

            all_candidates = eligible_covered_call_candidates + eligible_watchlist_csp_candidates
            for opt in all_candidates:
                t = opt.get("ticker", "UNKNOWN")
                score = opt.get("score", 0)
                if t not in ticker_diagnostics:
                    ticker_diagnostics[t] = {"top_score": score, "candidate_count": 0, "filtered_out": False}
                else:
                    ticker_diagnostics[t]["top_score"] = max(ticker_diagnostics[t]["top_score"], score)
                ticker_diagnostics[t]["candidate_count"] += 1

            # ── Unified deterministic ranking (see recommendation_ranking) ──
            all_candidates = rank_candidates(all_candidates)

            # ── Build lane results with diversity safeguard ──────────
            # Portfolio-aware concentration guard: an underlying you already
            # have short options on gets at most ONE new pick, not max_per.
            short_exposure = existing_short_exposure_by_underlying(portfolio_context)

            def _select_top(candidates, max_per=1):
                """Select top candidates with at most max_per per canonical underlying."""
                selected = []
                for opt in candidates:
                    t = opt.get("ticker", "UNKNOWN")
                    cu = canonical_underlying(t)
                    already_on = int(short_exposure.get(str(cu).upper(), 0) or 0)
                    effective_cap = min(max_per, 1) if already_on > 0 else max_per
                    count = sum(1 for o in selected if canonical_underlying(o.get("ticker", "UNKNOWN")) == cu)
                    opt["existing_exposure_contracts"] = already_on
                    if count < effective_cap:
                        selected.append(opt)
                return selected

            top_covered_calls = _select_top(rank_candidates(eligible_covered_call_candidates), max_per=1)
            top_watchlist_csp = _select_top(rank_candidates(eligible_watchlist_csp_candidates), max_per=2)

            # Single ranked signal pipeline combining CSPs and covered calls.
            signals = _select_top(all_candidates, max_per=2)[:limit]

            # Log per-ticker diagnostics
            for t, diag in sorted(ticker_diagnostics.items(), key=lambda x: x[1]["top_score"], reverse=True):
                logger.info(f"  TICKER {t}: top_score={diag['top_score']:.1f}, candidates={diag['candidate_count']}")

            # Format the unified signal list.
            def _format_rec_list(candidates, start_rank=1):
                return [format_recommendation(opt, rank) for rank, opt in enumerate(candidates, start_rank)]

            formatted_signals = _format_rec_list(signals)
            formatted_cc = _format_rec_list(top_covered_calls)
            formatted_csp = _format_rec_list(top_watchlist_csp)

            scoring_elapsed = time.time() - cc_start - cc_elapsed
            elapsed = time.time() - start_time
            logger.info(f"[TIMING] Scoring & ranking: {scoring_elapsed:.2f}s")
            logger.info(
                f"[TIMING] Total: {elapsed:.2f}s — generated {len(formatted_signals)} signals, "
                f"{len(formatted_cc)} CC, {len(formatted_csp)} CSP"
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
                "removed_from_hot_path": ["catalyst_warnings", "underlying_quality", "signal_overlay"],
            }

            # Record scan ledger entry (non-blocking)
            try:
                top_signals = [
                    {
                        "ticker": s.get("ticker"),
                        "option_type": s.get("option_type"),
                        "strike": s.get("strike"),
                        "score": s.get("score"),
                        "bid_premium_per_contract": s.get("bid_premium_per_contract"),
                        "premium_velocity_per_day": s.get("premium_velocity_per_day"),
                        "quality_tier": s.get("quality_tier"),
                        "event_tier": s.get("event_tier"),
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
                "entry_context": entry_window_advice(),
                "covered_calls": {
                    "signals": formatted_cc,
                    "count": len(formatted_cc),
                },
                "watchlist_csps": {
                    "signals": formatted_csp,
                    "count": len(formatted_csp),
                },
                "broker_buying_power": round(broker_buying_power, 2),
                "cash_available_for_csp": round(cash_available_for_csp, 2),
                "cash_reserved_for_csp": round(cash_reserved_for_csp, 2),
                "cash_diagnostics": portfolio_context.get("_cash_diagnostics", {}),
                "enrichment": enrichment_hint,
                "blocked_signals": blocked_signals,
                "blocked_reason_counts": dict(sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)),
                "scan_coverage": {
                    "scanned": len(scan_watchlist),
                    "total": len(effective_watchlist),
                    "complete": len(scan_watchlist) >= len(effective_watchlist),
                },
                "watchlist_origins": watchlist_origins,
                "quote_fetched_at": {ticker: scan_quote_fetched_at.get(ticker, "") for ticker in effective_watchlist},
                "preset": {
                    "key": self._preset.key,
                    "version": self._preset.version,
                    "label": self._preset.label,
                    "screener_profile": self._preset_profile,
                    "csp_profile_summary": _build_csp_profile_summary(self._preset_profile),
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
                    "scan_tickers_cap": len(effective_watchlist),
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


def _build_csp_profile_summary(profile: dict | None) -> str:
    """
    Build a human-readable summary of the active preset screener profile.
    Returns an empty string when no profile is configured.
    """
    if not profile:
        return ""
    sp = profile
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
