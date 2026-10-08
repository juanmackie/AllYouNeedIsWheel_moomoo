"""
Signals module - handles top options signals.
Extracted from the monolithic options_service.py for maintainability.
"""

import logging
import time
from datetime import datetime, timedelta
from math import floor

from api.services.options_data import fetch_option_chain_live_first
from api.services.recommendation_ranking import (
    attach_readonly_trade_diagnostics,
    format_recommendation,
    option_code_for_candidate,
    rank_candidates,
)
from api.services.recommendation_ranking import (
    option_source_value as _option_source_value,
)
from api.services.recommendation_ranking import (
    option_uses_yfinance as _option_uses_yfinance,
)
from api.services.utils import clean_yfinance_ticker
from core.risk_metrics import should_block_for_data_quality
from core.scan_plan import chain_budget, csp_affordability, partition_universe
from core.sizing import (
    annotate_pick_funding,
    deployment_plan,
    existing_short_exposure_by_underlying,
    remaining_underlying_capital,
)
from core.ticker_utils import canonical_underlying, earnings_underlying_ticker
from core.utils import entry_window_advice, is_market_open, market_now, safe_float
from core.wheel_decision import WheelDecision, score_contract

logger = logging.getLogger("api.services.recommendations")


def _is_valid_external_option(option: dict, stock_price: float) -> bool:
    """Return True when an external option payload has the minimum safe fields."""
    try:
        strike = float(option.get("strike", 0) or 0)
        bid = float(option.get("bid", 0) or 0)
        ask = float(option.get("ask", 0) or 0)
        last = float(option.get("last", 0) or 0)
        raw_dte = option.get("dte")
        dte = int(raw_dte) if raw_dte not in (None, "") else None
    except (TypeError, ValueError):
        return False

    if strike <= 0 or stock_price <= 0 or (dte is not None and dte <= 0):
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


def _attach_iv_vs_hv_diagnostics(signals, connection, limit=3):
    """Attach Moomoo IV/HV context after ranking; never changes signal order."""
    get_historical_volatility = getattr(connection, "get_historical_volatility", None)
    if not callable(get_historical_volatility):
        return

    for signal in (signals or [])[: max(0, int(limit))]:
        chain_source = str(signal.get("chain_source") or "").strip().lower()
        iv_source = str(signal.get("iv_source") or "").strip().lower()
        if chain_source not in {"broker", "persisted-broker"} or iv_source != "broker":
            continue
        iv = safe_float(signal.get("implied_volatility"), default=None)
        if iv is None or iv <= 0:
            continue
        iv_percent = iv if iv > 3.0 else iv * 100
        try:
            history = get_historical_volatility(signal.get("ticker"), window_sessions=20)
        except Exception:
            logger.debug("Historical volatility unavailable for %s", signal.get("ticker"), exc_info=True)
            history = None
        if not isinstance(history, dict):
            continue

        hv_percent = safe_float(history.get("historical_volatility_pct"), default=None)
        available = history.get("status") == "available" and hv_percent is not None and hv_percent >= 0
        signal["iv_vs_hv"] = {
            "status": "available" if available else "unavailable",
            "source": history.get("source") or "moomoo_adjusted_daily_close",
            "window_sessions": 20,
            "sample_sessions": int(history.get("sample_sessions", 0) or 0),
            "implied_volatility_pct": round(iv_percent, 2),
            "historical_volatility_pct": round(hv_percent, 2) if available else None,
            "spread_percentage_points": round(iv_percent - hv_percent, 2) if available else None,
            "as_of_date": history.get("as_of_date") if available else None,
            "reason": None if available else history.get("reason"),
        }
        trend = history.get("falling_knife")
        trend_available = isinstance(trend, dict) and trend.get("status") == "available"
        decline_pct = safe_float(trend.get("decline_pct"), default=None) if trend_available else None
        signal["falling_knife"] = {
            "status": "available" if trend_available and decline_pct is not None else "unavailable",
            "decline_window_sessions": 20,
            "moving_average_sessions": 50,
            "decline_pct": round(decline_pct, 2) if decline_pct is not None else None,
            "below_moving_average": bool(trend.get("below_moving_average")) if trend_available else None,
            "flagged": bool(trend.get("flagged")) if trend_available and decline_pct is not None else None,
            "as_of_date": history.get("as_of_date") if trend_available else None,
            "rule": "20-session decline of at least 15% and close below 50-session average",
        }


def _build_capital_recovery_cards(positions, calls_by_ticker, best_csp, available_contracts_by_ticker=None):
    """Build read-only recovery context using broker average cost as the safe basis."""
    calls_by_underlying = {}
    for ticker, candidates in (calls_by_ticker or {}).items():
        key = canonical_underlying(ticker).upper()
        calls_by_underlying.setdefault(key, []).extend(candidates or [])

    available_by_underlying = {
        canonical_underlying(ticker).upper(): max(0, floor(safe_float(count)))
        for ticker, count in (available_contracts_by_ticker or {}).items()
    }
    csp_rate = safe_float((best_csp or {}).get("capital_velocity_per_day"))
    csp_summary = None
    if best_csp and csp_rate > 0:
        csp_summary = {
            "ticker": best_csp.get("ticker", ""),
            "strike": best_csp.get("strike"),
            "expiration": best_csp.get("expiration", ""),
            "capital_velocity_per_day": csp_rate,
        }

    cards = []
    for ticker, position in (positions or {}).items():
        key = canonical_underlying(ticker).upper()
        shares = safe_float((position or {}).get("position"))
        position = position or {}
        basis_source = str(position.get("gate_basis_source") or "")
        basis = safe_float(position.get("gate_basis"))
        if basis_source not in {"ledger_adjusted", "moomoo_avg_cost"} or basis <= 0:
            basis = safe_float(position.get("avg_cost"))
            basis_source = "moomoo_avg_cost" if basis > 0 else "missing"
        if basis <= 0:
            basis = None
        available = available_by_underlying.get(key, max(0, floor(shares / 100)))
        above_basis = []
        below_basis = []

        for candidate in calls_by_underlying.get(key, []):
            decision = candidate.get("wheel_decision") or {}
            blocker_codes = candidate.get("blocked_reason_codes") or decision.get("blocked_reason_codes") or []
            hard_blockers = candidate.get("hard_blockers") or decision.get("hard_blockers") or []
            is_below_basis_block = set(blocker_codes) == {"below_cost_basis"}
            if hard_blockers and not is_below_basis_block:
                continue
            strike = safe_float(candidate.get("strike"))
            if basis is None or strike <= 0:
                continue
            pnl = candidate.get("if_called_pnl_vs_basis", decision.get("if_called_pnl_vs_basis"))
            if pnl is None:
                pnl = (strike - basis + safe_float(candidate.get("bid"))) * 100
            pnl = safe_float(pnl)
            summary = {
                "strike": strike,
                "expiration": candidate.get("expiration", ""),
                "bid": safe_float(candidate.get("bid")),
                "capital_velocity_per_day": safe_float(candidate.get("capital_velocity_per_day")),
                "if_called_pnl_vs_basis": round(pnl, 2),
                "loss_if_called": round(max(0.0, -pnl), 2),
                "loss_if_called_total": round(max(0.0, -pnl) * available, 2),
                "copy_eligible": bool(candidate.get("copy_eligible", decision.get("copy_eligible", False))),
            }
            if is_below_basis_block and strike < basis:
                below_basis.append(summary)
            elif not hard_blockers and strike >= basis:
                above_basis.append(summary)

        best_above = max(above_basis, key=lambda item: item["capital_velocity_per_day"], default=None)
        best_below = max(below_basis, key=lambda item: item["capital_velocity_per_day"], default=None)
        cards.append(
            {
                "ticker": str(ticker),
                "shares": shares,
                "available_contracts": available,
                "basis_per_share": basis,
                "basis_source": basis_source if basis is not None else None,
                "basis_notice": (
                    "Cycle-adjusted basis is used because fill-window, fee, and share reconciliation are proven."
                    if basis_source == "ledger_adjusted"
                    else "Moomoo average cost is the conservative basis; historical option credits excluded "
                    "because fill-history completeness is unverified."
                    if basis_source == "moomoo_avg_cost"
                    else "Moomoo average-cost basis unavailable; no adjusted basis is shown."
                ),
                "best_call_at_or_above_basis": best_above,
                "best_call_below_basis": best_below,
                "best_csp_opportunity": csp_summary,
                "opportunity_return_per_day_pct": round(csp_rate * 100, 5) if csp_summary else None,
            }
        )

    return sorted(cards, key=lambda item: item["ticker"].upper())


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
        "capital_velocity_per_day": round(decision.capital_velocity_per_day, 8),
        "bid": decision.bid,
        "ask": decision.ask,
        "annualized_return": decision.annualized_return,
        "otm_pct": decision.otm_pct,
        "delta": decision.delta,
        "implied_volatility": decision.implied_volatility,
        "open_interest": decision.open_interest,
        "volume": decision.volume,
        "iv_rank": decision.iv_rank,
        "iv_status": decision.iv_status,
        "iv_percentile": decision.iv_percentile,
        "iv_env_adjustment": decision.iv_env_adjustment,
        "profile_type": decision.profile_type,
        "earnings_date": decision.earnings_date,
        "days_to_earnings": decision.days_to_earnings,
        "earnings_adjustment": decision.earnings_adjustment,
        "size_fit": decision.size_fit,
        "expected_move_buffer": decision.expected_move_buffer,
        "wheel_decision": decision.to_dict(),
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
        "price_source": getattr(decision, "price_source", "unknown"),
        "chain_source": getattr(decision, "chain_source", "unknown"),
        "iv_source": getattr(decision, "iv_source", "unknown"),
        "data_source": getattr(decision, "price_source", "unknown"),
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

    def _cached_watchlist_evidence_key(self, ticker: str) -> tuple:
        # Raw broker evidence is invariant to preset/cash/reservation state, so
        # the cache key is the ticker alone. Scored decisions are NEVER cached;
        # they are recomputed from this evidence on every run (C01).
        return (ticker,)

    def _get_cached_watchlist_evidence(self, ticker):
        entry = self._yfinance_cache.get(self._cached_watchlist_evidence_key(ticker))
        if isinstance(entry, dict) and self._is_cache_valid(entry):
            return entry["data"]
        return None

    def _set_cached_watchlist_evidence(self, ticker, evidence):
        self._yfinance_cache[self._cached_watchlist_evidence_key(ticker)] = {
            "data": evidence,
            "timestamp": datetime.now(),
        }

    def _has_any_affordable_otm_strike(
        self, stock_price: float, portfolio_context: dict, require_cash_fit: bool = True, ticker: str | None = None
    ) -> bool:
        """Check whether any strike in the 5-15% OTM range fits buying power."""
        cash_available_for_csp = float(portfolio_context.get("cash_available_for_csp", 0) or 0)
        if not require_cash_fit:
            return stock_price > 0
        exposure_room = (
            remaining_underlying_capital(ticker, portfolio_context, self._preset_profile) if ticker else None
        )
        if exposure_room is None:
            exposure_room = cash_available_for_csp
        return csp_affordability(stock_price, cash_available_for_csp, exposure_room, self._preset_profile)

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
            growth_profile=self._preset_profile,
            research_only_mode=research_only_mode,
        )

    def _fetch_watchlist_ticker_csp(self, ticker, portfolio_context, price_snapshot=None):
        """
        Fetch CSP candidates for a watchlist ticker (Moomoo broker data only).

        Raw broker evidence is cached and reused, but qualification, capacity,
        quantities, and eligibility are recomputed on every run against the
        current preset and portfolio context so a cached decision can never
        outlive a preset/cash/reservation/market change (C01).

        Returns a list of candidate dicts, a list with a skip diagnostic, or
        None on error.
        """
        return self._fetch_watchlist_csp_moomoo(ticker, portfolio_context, price_snapshot=price_snapshot)

    def _fetch_watchlist_csp_moomoo(self, ticker, portfolio_context, price_snapshot=None):
        """
        Fetch CSP candidates from Moomoo for a watchlist ticker.

        Broker evidence (raw option chains) is collected and cached separately;
        scoring/qualification/quantity/eligibility is recomputed on every run
        against the current preset and portfolio context so that a cached
        decision can never outlive a preset, cash, reservation, or market
        change (C01).
        """
        evidence = self._get_cached_watchlist_evidence(ticker)
        if evidence is None:
            evidence = self._collect_watchlist_csp_evidence(ticker, portfolio_context, price_snapshot=price_snapshot)
            if evidence is None:
                return None
            if not evidence.get("skip_diagnostics"):
                self._set_cached_watchlist_evidence(ticker, evidence)
        if evidence.get("skip_diagnostics"):
            return evidence["skip_diagnostics"]
        return self._score_watchlist_csp_evidence(evidence, ticker, portfolio_context)

    def _collect_watchlist_csp_evidence(self, ticker, portfolio_context, price_snapshot=None):
        """
        Fetch raw CSP option-chain evidence from Moomoo (broker data only).

        Returns a normalized evidence payload that is safe to cache across
        preset/cash/reservation changes: the stock price, the quote timestamp,
        and the raw option contracts for the sampled expirations. Affordability,
        scoring, quantity, and eligibility are NOT applied here; they are
        recomputed from this evidence on every run via
        `_score_watchlist_csp_evidence`.

        Returns either:
          - {"ok": True, "stock_price": float, "quote_fetched_at_utc": str,
             "chains": [{"exp_str", "dte", "options": [contract dict]}]}
          - {"ok": True, "skip_diagnostics": [diagnostic dict]} for cash-fit
            short-circuits
          - None on broker/fetch failure.
        """
        try:
            conn = self._get_connection()
            if not conn:
                return None

            cash_available_for_csp = float(portfolio_context.get("cash_available_for_csp", 0) or 0)
            require_cash_fit = True

            # Short-circuit cash-fit failures using cached price, avoiding rate limiter entirely.
            cached_price = conn.get_cached_stock_price(ticker)
            if cached_price is not None and cached_price > 0:
                if not self._has_any_affordable_otm_strike(
                    cached_price, portfolio_context, require_cash_fit=require_cash_fit, ticker=ticker
                ):
                    logger.debug(
                        f"Watchlist {ticker}: no configured OTM strike fits cached price ${cached_price:.2f} "
                        f"and buying power ${cash_available_for_csp:.2f}"
                    )
                    return {
                        "ok": True,
                        "skip_diagnostics": [
                            self._make_skip_diagnostic(
                                ticker,
                                "no_cash_fit",
                                f"No CSP strike fits buying power (${cash_available_for_csp:.0f})",
                            )
                        ],
                    }

            ticker_code = str(ticker).upper()
            if not ticker_code.startswith("US."):
                ticker_code = f"US.{ticker_code}"
            snapshot = price_snapshot.get(ticker_code) if isinstance(price_snapshot, dict) else None
            if isinstance(price_snapshot, dict):
                stock_price = safe_float((snapshot or {}).get("last_price"), default=None)
            else:
                stock_price = conn.get_stock_price(ticker)
            if stock_price is None or stock_price <= 0:
                return None

            # After getting the live price, re-check affordability before spending
            # any option-chain calls (this is part of qualification recomputation,
            # not a cached/scored decision).
            if not self._has_any_affordable_otm_strike(
                stock_price, portfolio_context, require_cash_fit=require_cash_fit, ticker=ticker
            ):
                logger.debug(
                    f"Watchlist {ticker}: no OTM strike in 5-15% range fits buying power ${cash_available_for_csp:.2f}"
                )
                return {
                    "ok": True,
                    "skip_diagnostics": [
                        self._make_skip_diagnostic(
                            ticker,
                            "no_cash_fit",
                            f"No CSP strike fits buying power (${cash_available_for_csp:.0f})",
                        )
                    ],
                }

            sp = self._preset_profile
            today = market_now()
            pref_dte = int(sp.get("csp_preferred_dte", 35) or 35)
            min_dte = int(sp.get("csp_min_dte", 30) or 30)
            max_dte = int(sp.get("csp_max_dte", 45) or 45)

            quote_fetched_at_utc = str(datetime.utcnow().isoformat() + "Z")
            chains = []
            range_fetch = getattr(conn, "get_option_chain_range", None)
            if callable(range_fetch) and isinstance(price_snapshot, dict):
                exposure_room = remaining_underlying_capital(ticker, portfolio_context, self._preset_profile)
                if exposure_room is None:
                    exposure_room = cash_available_for_csp
                max_affordable_strike = min(
                    stock_price * (1 - float(sp.get("csp_min_otm_pct", 5) or 5) / 100),
                    min(cash_available_for_csp, exposure_room) / 100,
                )
                min_band_strike = stock_price * (1 - float(sp.get("csp_max_otm_pct", 15) or 15) / 100)
                range_options = []
                budget = chain_budget(sp)
                try:
                    for offset in range(0, budget["span_days"], 30):
                        start_dte = min_dte + offset
                        end_dte = min(max_dte, start_dte + 29)
                        chain = range_fetch(
                            ticker,
                            "P",
                            (today.date() + timedelta(days=start_dte)).isoformat(),
                            (today.date() + timedelta(days=end_dte)).isoformat(),
                            {"min_strike": min_band_strike, "max_strike": max_affordable_strike},
                        )
                        if not isinstance(chain, dict):
                            return None
                        range_options.extend(chain.get("options", []) or [])
                except Exception:
                    logger.debug("Watchlist %s: range chain fetch failed", ticker, exc_info=True)
                    return None

                expirations = {}
                for option in range_options:
                    exp_str = str(option.get("expiration", "") or "").replace("-", "")
                    try:
                        dte = (datetime.strptime(exp_str, "%Y%m%d").date() - today.date()).days
                    except ValueError:
                        continue
                    if min_dte <= dte <= max_dte:
                        expirations.setdefault(exp_str, {"dte": dte, "options": []})["options"].append(option)
                valid_expirations = [(exp_str, value["dte"]) for exp_str, value in expirations.items()]
                if valid_expirations:
                    by_dte = sorted(valid_expirations, key=lambda item: item[1])
                    by_preference = sorted(valid_expirations, key=lambda item: abs(pref_dte - item[1]))
                    selected_expirations = []
                    for selected_expiration in (by_dte[0], by_preference[0], by_dte[-1]):
                        if selected_expiration not in selected_expirations:
                            selected_expirations.append(selected_expiration)
                    for exp_str, dte in selected_expirations:
                        raw_contracts = []
                        for opt in expirations[exp_str]["options"]:
                            if not _is_valid_external_option(opt, stock_price):
                                continue
                            raw_contracts.append(
                                {
                                    "strike": float(opt.get("strike", 0) or 0),
                                    "expiration": exp_str,
                                    "option_type": "PUT",
                                    "bid": float(opt.get("bid", 0) or 0),
                                    "ask": float(opt.get("ask", 0) or 0),
                                    "last": float(opt.get("last", 0) or 0),
                                    "dte": dte,
                                    "implied_volatility": float(opt.get("implied_volatility", 0) or 0),
                                    "open_interest": int(opt.get("open_interest", 0) or 0),
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
                            )
                        if raw_contracts:
                            chains.append({"exp_str": exp_str, "dte": dte, "options": raw_contracts})
            else:
                # Compatibility path for test donors and older connection adapters.
                from moomoo import RET_OK

                ret, data = conn.get_option_expiration_dates(ticker)
                if ret != RET_OK or data is None:
                    return None
                expiration_column = next(
                    (name for name in ("expiration_date", "strike_time", "option_expiry_date") if name in data.columns),
                    None,
                )
                if expiration_column is None:
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
                by_dte = sorted(valid_expirations, key=lambda item: item[1])
                by_preference = sorted(valid_expirations, key=lambda item: abs(pref_dte - item[1]))
                expirations_to_check = []
                for expiration_choice in (by_dte[0], by_preference[0], by_dte[-1]):
                    if expiration_choice not in expirations_to_check:
                        expirations_to_check.append(expiration_choice)
                for exp_str, dte in expirations_to_check:
                    chain = fetch_option_chain_live_first(
                        conn,
                        self.db,
                        self.config,
                        ticker,
                        exp_str,
                        "P",
                        target_strike=stock_price * (1 - (sp.get("csp_default_otm_pct", 10) / 100)),
                        stock_price=stock_price,
                    )
                    if not chain:
                        continue
                    raw_contracts = []
                    for opt in chain.get("options", []):
                        if not _is_valid_external_option(opt, stock_price):
                            continue
                        raw_contracts.append(
                            {
                                **opt,
                                "strike": float(opt.get("strike", 0) or 0),
                                "expiration": exp_str,
                                "option_type": "PUT",
                                "dte": dte,
                                "security_type": self._scan_security_types.get(
                                    str(ticker).upper().split(".")[-1], str(opt.get("security_type", "") or "stock")
                                ),
                            }
                        )
                    if raw_contracts:
                        chains.append({"exp_str": exp_str, "dte": dte, "options": raw_contracts})

            if not chains:
                return None

            return {
                "ok": True,
                "stock_price": stock_price,
                "quote_fetched_at_utc": quote_fetched_at_utc,
                "chains": chains,
            }

        except Exception as e:
            logger.debug(f"Watchlist {ticker}: Moomoo CSP evidence path failed ({e})")
            return None

    def _score_watchlist_csp_evidence(self, evidence, ticker, portfolio_context):
        """
        Recompute qualification, capacity, quantities, and eligibility from raw
        broker evidence under the current preset and portfolio context (C01).
        Affordability filtering and scoring are applied here, so a cached
        evidence payload always yields decisions consistent with today's inputs.
        """
        cash_available_for_csp = float(portfolio_context.get("cash_available_for_csp", 0) or 0)
        min_csp_buying_power = float(self._preset_profile.get("min_csp_buying_power", 5000) or 5000)
        research_only_mode = cash_available_for_csp < min_csp_buying_power
        require_cash_fit = True

        stock_price = float(evidence.get("stock_price", 0) or 0)
        if stock_price <= 0:
            return [self._make_skip_diagnostic(ticker, "no_price", "No valid stock price for CSP")]
        if not self._has_any_affordable_otm_strike(
            stock_price, portfolio_context, require_cash_fit=require_cash_fit, ticker=ticker
        ):
            return [
                self._make_skip_diagnostic(
                    ticker, "no_cash_fit", f"No CSP strike fits buying power (${cash_available_for_csp:.0f})"
                )
            ]

        sp = self._preset_profile
        candidates = []
        seen = set()

        for chain in evidence.get("chains", []):
            dte = int(chain.get("dte", 0) or 0)
            exp_str = chain.get("exp_str", "")
            for opt in chain.get("options", []):
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
                key = (strike, exp_str)
                if key in seen:
                    continue
                seen.add(key)

                bid = float(opt.get("bid", 0) or 0)
                ask = float(opt.get("ask", 0) or 0)
                last = float(opt.get("last", 0) or 0)
                if bid <= 0 and ask <= 0 and last <= 0:
                    continue

                contract = dict(opt)

                decision = self._score_csp_contract(
                    contract,
                    ticker,
                    stock_price,
                    dte,
                    portfolio_context,
                    research_only_mode=research_only_mode,
                )

                if decision is None or decision.hard_blockers:
                    if decision and decision.hard_blockers:
                        reason_code = (
                            decision.blocked_reason_codes[0] if decision.blocked_reason_codes else "blocked_by_scoring"
                        )
                        reason_text = (
                            decision.hard_blockers[0] if decision.hard_blockers else "Scored candidate blocked"
                        )
                        candidates.append(self._make_skip_diagnostic(ticker, reason_code, reason_text))
                    continue

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

                result["quote_fetched_at_utc"] = evidence.get("quote_fetched_at_utc", "")
                candidates.append(result)

        if not candidates:
            return [self._make_skip_diagnostic(ticker, "blocked_by_scoring", "All candidates filtered by scoring")]

        return rank_candidates(candidates)[:3]

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

            # ── Scan universe ────────────────────────────────────────────
            # The active CSP scan universe is the signed-in OpenD session's
            # Moomoo watchlist group (US-listed securities). Legacy config/app
            # additions are archived (settings preserved, never deleted) and are
            # excluded from new scans; covered calls come from Moomoo positions
            # below. get_scan_universe() reports DISTINCT explanations for a
            # missing group, an empty group, and a connection failure — and
            # never substitutes the config watchlist as the Moomoo universe.
            watchlist_origins = {}
            wl_group_name = ""
            wl_group_status = "ok"
            wl_explanation = ""
            wl_groups_available = []
            wl_unsupported = []
            wl_raw_codes = {}
            wl_fetched_at = ""
            scan_universe = self._watchlist_provider.get_scan_universe(
                growth_mode_config=self._preset_profile,
                portfolio_context=portfolio_context,
            )
            if isinstance(scan_universe, dict) and isinstance(scan_universe.get("tickers"), list):
                effective_watchlist = [str(t) for t in (scan_universe.get("tickers") or []) if str(t).strip()]
                watchlist_origins = {t: ["moomoo"] for t in effective_watchlist}
                wl_group_name = str(scan_universe.get("group_name") or "")
                wl_group_status = str(scan_universe.get("status") or "ok")
                wl_explanation = str(scan_universe.get("explanation") or "")
                wl_groups_available = list(scan_universe.get("groups_available") or [])
                wl_unsupported = _sanitize_unsupported(scan_universe.get("unsupported") or [])
                wl_raw_codes = {str(k): str(v) for k, v in (scan_universe.get("raw_codes") or {}).items()}
                wl_fetched_at = str(scan_universe.get("fetched_at") or "")
            else:
                # Mock/stub providers: fall back to the canonical merged union.
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
            price_snapshot = None
            security_types = {}
            get_price_snapshot = getattr(conn, "get_price_snapshot", None)
            if callable(get_price_snapshot) and effective_watchlist:
                try:
                    raw_price_snapshot = get_price_snapshot(effective_watchlist)
                    if isinstance(raw_price_snapshot, dict):
                        price_snapshot = raw_price_snapshot
                        security_types = {
                            str(symbol).upper().split(".")[-1]: str(value.get("security_type")).lower()
                            for symbol, value in raw_price_snapshot.items()
                            if isinstance(value, dict) and value.get("security_type")
                        }
                except Exception:
                    logger.debug("Broker price snapshot unavailable", exc_info=True)
            get_session_calendar = getattr(conn, "get_session_calendar", None)
            try:
                from core.session_clock import SessionCalendar

                session_calendar = get_session_calendar() if callable(get_session_calendar) else None
                if not isinstance(session_calendar, SessionCalendar):
                    session_calendar = None
            except Exception:
                session_calendar = None
            self._scan_security_types = security_types
            price_snapshot_by_ticker = {}
            if isinstance(price_snapshot, dict):
                price_snapshot_by_ticker = {
                    str(symbol).upper().split(".")[-1]: value for symbol, value in price_snapshot.items()
                }
            cash_fit_tickers = list(effective_watchlist)
            cash_skip_evidence = []
            price_snapshot_gaps = []
            if isinstance(price_snapshot, dict):
                exposure_room = {}
                for ticker in effective_watchlist:
                    room = remaining_underlying_capital(ticker, portfolio_context, self._preset_profile)
                    if room is None and "max_account_exposure_pct_per_underlying" not in self._preset_profile:
                        room = cash_available_for_csp
                    exposure_room[ticker] = room
                price_map = {
                    ticker: price_snapshot_by_ticker.get(str(ticker).upper().split(".")[-1])
                    for ticker in effective_watchlist
                }
                cash_fit_tickers, cash_skip_evidence, price_snapshot_gaps = partition_universe(
                    effective_watchlist,
                    price_map,
                    cash_available_for_csp,
                    exposure_room,
                    self._preset_profile,
                    fetched_at=datetime.utcnow().isoformat() + "Z",
                )
            logger.info(
                f"Effective watchlist: {len(effective_watchlist)} tickers from group '{wl_group_name}' (status={wl_group_status})"
            )

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
                    "state": "planning" if wl_group_status != "ok" else None,
                    "scan_coverage": {"scanned": 0, "total": 0, "complete": False},
                    "watchlist_origins": watchlist_origins,
                    "active_watchlist": _build_active_watchlist(
                        wl_group_name=wl_group_name,
                        wl_group_status=wl_group_status,
                        wl_explanation=wl_explanation,
                        wl_groups_available=wl_groups_available,
                        wl_unsupported=wl_unsupported,
                        wl_raw_codes=wl_raw_codes,
                        effective_watchlist=effective_watchlist,
                        scan_status_by_ticker={},
                        position_tickers=list(positions.keys()),
                        quote_fetched_at={},
                        price_snapshot_by_ticker=price_snapshot_by_ticker,
                        session_calendar=session_calendar,
                        fetched_at=wl_fetched_at,
                    ),
                    "message": wl_explanation or "No positions or watchlist configured",
                }

            # ── Lanes ──────────────────────────────────────────────────
            covered_call_candidates = []
            recovery_call_candidates_by_ticker = {}
            available_call_contracts_by_ticker = {}
            watchlist_csp_candidates = []

            # ── Diagnostics ────────────────────────────────────────────
            watchlist_processed = 0
            watchlist_cached = 0
            watchlist_errors = 0
            skipped_csp_diagnostics = []
            ticker_diagnostics = {}
            scan_quote_fetched_at = {}
            scan_status_by_ticker = {}
            for item in cash_skip_evidence:
                ticker = item["ticker"]
                scan_status_by_ticker[ticker] = "skipped_no_cash_fit"
                skipped_csp_diagnostics.append(
                    self._make_skip_diagnostic(
                        ticker,
                        "no_cash_fit",
                        f"No CSP strike fits available CSP cash and exposure room (${item['cash_at_scan']:.0f})",
                    )
                )
                skipped_csp_diagnostics[-1]["coverage_evidence"] = item
            for item in price_snapshot_gaps:
                ticker = item["ticker"]
                scan_status_by_ticker[ticker] = "error"
                watchlist_errors += 1
                skipped_csp_diagnostics.append(
                    self._make_skip_diagnostic(ticker, item["reason"], "Price or exposure evidence is unavailable")
                )

            # ════════════════════════════════════════════════════════════
            # LANE 1: Watchlist CSPs — short-circuit when no cash to deploy
            # so we don't burn rate-limit budget on chain calls that cannot
            # produce signals.
            # ════════════════════════════════════════════════════════════
            csp_start = time.time()
            min_csp_buying_power = float(self._preset_profile.get("min_csp_buying_power", 5000) or 5000)

            # A broken/empty watchlist group leaves no CSP universe to scan. The
            # CSP lane and feasibility preflight are skipped with an explicit
            # lane diagnostic; owned shares still get covered-call assessment
            # from Moomoo positions. Config/app symbols are never substituted.
            scan_universe_ok = bool(effective_watchlist) and wl_group_status == "ok"

            # ── Scan feasibility preflight ────────────────────────────────
            # Every actionable run must scan the COMPLETE scan universe. If it
            # cannot fit the OpenD quota + freshness window, we publish planning
            # diagnostics and direct the user to reduce the Moomoo group. We
            # never silently truncate and still claim a global top three.
            preflight_infeasible = False
            if scan_universe_ok:
                preflight_count = (
                    len(cash_fit_tickers) if isinstance(price_snapshot, dict) else len(effective_watchlist)
                )
                preflight = self._watchlist_provider.preflight_scan_feasibility(
                    preflight_count, session_calendar=session_calendar
                )
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
                    preflight_infeasible = True
                    for ticker in cash_fit_tickers:
                        scan_status_by_ticker[ticker] = "error"
                        watchlist_errors += 1
                    skipped_csp_diagnostics.append(
                        self._make_skip_diagnostic(
                            "__lane__",
                            "planning_quota_infeasible",
                            f"{preflight_count} affordable names exceed the current scan budget",
                        )
                    )
            elif wl_group_status != "ok":
                logger.warning(
                    "Watchlist CSP lane skipped: group '%s' status=%s (%d positions still scanned for CC)",
                    wl_group_name,
                    wl_group_status,
                    len(positions),
                )
                skipped_csp_diagnostics.append(
                    self._make_skip_diagnostic(
                        "__lane__",
                        f"watchlist_{wl_group_status}",
                        wl_explanation or f"Watchlist group '{wl_group_name}' unavailable ({wl_group_status})",
                    )
                )

            # Scan the COMPLETE scan universe. The preflight above already
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
                for ticker in scan_watchlist:
                    scan_status_by_ticker.setdefault(ticker, "skipped_no_cash")
            elif not preflight_infeasible:
                if cash_available_for_csp < min_csp_buying_power:
                    logger.info(
                        "Running research-only watchlist CSP scan: CSP cash %.2f < minimum %.2f",
                        cash_available_for_csp,
                        min_csp_buying_power,
                    )

                for ticker in cash_fit_tickers if isinstance(price_snapshot, dict) else scan_watchlist:
                    is_held = ticker in positions

                    # Score decisions are recomputed on every run from cached raw
                    # evidence (C01); the evidence cache lookup happens inside the
                    # fetch, so we record evidence-cache hits here for observability.
                    is_evidence_cached = self._get_cached_watchlist_evidence(ticker) is not None

                    results = self._fetch_watchlist_ticker_csp(ticker, portfolio_context, price_snapshot=price_snapshot)
                    if results is None:
                        watchlist_errors += 1
                        scan_status_by_ticker[ticker] = "error"
                        continue
                    if is_evidence_cached:
                        watchlist_cached += 1
                    scan_status_by_ticker[ticker] = "scanned"

                    for result in results:
                        if result.get("_skip_diagnostic"):
                            scan_status_by_ticker[ticker] = "skipped"
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
            existing_short_calls_by_ticker = {}
            for ticker in deduped_position_tickers:
                shares_owned = float(positions.get(ticker, {}).get("position", 0) or 0)
                underlying = earnings_underlying_ticker(ticker).upper()
                existing_short_calls = 0
                if isinstance(short_calls, dict):
                    for symbol, contracts in short_calls.items():
                        if earnings_underlying_ticker(str(symbol or "")).upper() != underlying:
                            continue
                        try:
                            existing_short_calls += max(int(contracts or 0), 0)
                        except (TypeError, ValueError):
                            continue
                existing_short_calls_by_ticker[ticker] = existing_short_calls
                available_call_contracts_by_ticker[ticker] = max(0, floor(shares_owned / 100) - existing_short_calls)

            has_cc_capacity = any(available_call_contracts_by_ticker.values())
            if not has_cc_capacity:
                logger.info("Skipping CC lane: no unencumbered 100-share covered-call capacity")
            if has_cc_capacity:
                for ticker in deduped_position_tickers:
                    try:
                        available_calls = available_call_contracts_by_ticker.get(ticker, 0)
                        if available_calls <= 0:
                            continue
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

                        total_possible_calls = floor(shares_owned / 100)
                        existing_short_calls = existing_short_calls_by_ticker.get(ticker, 0)
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

                        # Fetch options data for this ticker. Pass the active
                        # preset's resolved CALL profile so its DTE window is
                        # applied rather than the legacy generic profile.
                        call_profile = self._watchlist_provider.get_screening_profile(
                            "CALL",
                            growth_mode_config=self._preset_profile,
                        )
                        result = self._options_data_provider._process_ticker_for_otm(
                            conn=conn,
                            ticker=ticker,
                            otm_percentage=otm_target,  # Growth-tuned or default OTM
                            portfolio_context=portfolio_context,
                            expiration=None,  # Get all expirations
                            option_type="CALL",  # Covered-call lane only
                            screening_profile=call_profile,
                            include_recovery_calls=True,
                        )

                        if "error" in result:
                            logger.warning(f"Error processing {ticker}: {result['error']}")
                            continue

                        recovery_call_candidates_by_ticker[ticker] = result.get(
                            "recovery_calls", result.get("calls", [])
                        )

                        # Process eligible CALLs — covered call lane
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
                    price_source = _option_source_value(opt, wd, "price_source", opt.get("data_source", "unknown"))
                    chain_source = _option_source_value(opt, wd, "chain_source", "unknown")
                    iv_source = _option_source_value(opt, wd, "iv_source", "unknown")
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
                annualized = opt.get("annualized_return", 0) or 0
                if t not in ticker_diagnostics:
                    ticker_diagnostics[t] = {
                        "top_annualized_return": annualized,
                        "candidate_count": 0,
                        "filtered_out": False,
                    }
                else:
                    ticker_diagnostics[t]["top_annualized_return"] = max(
                        ticker_diagnostics[t]["top_annualized_return"], annualized
                    )
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
            best_csp_opportunity = next(iter(rank_candidates(eligible_watchlist_csp_candidates)), None)
            capital_recovery = _build_capital_recovery_cards(
                positions,
                recovery_call_candidates_by_ticker,
                best_csp_opportunity,
                available_call_contracts_by_ticker,
            )

            # Single ranked signal pipeline combining CSPs and covered calls.
            signals = _select_top(all_candidates, max_per=2)[:limit]
            signals = annotate_pick_funding(signals, portfolio_context, self._preset_profile)
            attach_readonly_trade_diagnostics(signals[:3], conn)

            # Lane cards reuse diagnostic evidence only when that exact option
            # also appears in the bounded combined top-three query set.
            diagnostics_by_code = {
                option_code_for_candidate(candidate): {
                    key: value
                    for key, value in candidate.items()
                    if key.startswith("working_order_")
                    or key == "has_working_order"
                    or key.startswith("broker_capacity_")
                    or key.startswith("broker_max_")
                }
                for candidate in signals[:3]
                if option_code_for_candidate(candidate)
            }
            for candidate in [*top_covered_calls, *top_watchlist_csp]:
                diagnostics = diagnostics_by_code.get(option_code_for_candidate(candidate))
                if diagnostics:
                    candidate.update(diagnostics)

            # Log per-ticker diagnostics
            for t, diag in sorted(
                ticker_diagnostics.items(), key=lambda x: x[1]["top_annualized_return"], reverse=True
            ):
                logger.info(
                    f"  TICKER {t}: top_annualized_return={diag['top_annualized_return']:.1f}, "
                    f"candidates={diag['candidate_count']}"
                )

            # Format the unified signal list.
            def _format_rec_list(candidates, start_rank=1):
                return [format_recommendation(opt, rank) for rank, opt in enumerate(candidates, start_rank)]

            formatted_signals = _format_rec_list(signals)
            formatted_cc = _format_rec_list(top_covered_calls)
            formatted_csp = _format_rec_list(top_watchlist_csp)
            _attach_iv_vs_hv_diagnostics(formatted_signals, conn, limit=3)

            # Reserve the recommended cash from the visible top signals,
            # then offer the next ranked affordable CSPs for idle cash. This
            # is a planning surface only; it does not create candidates or
            # place orders.
            selected_keys = {
                (
                    str(opt.get("ticker", "")).upper(),
                    str(opt.get("option_type", "")).upper(),
                    str(opt.get("expiration", "")),
                    float(opt.get("strike", 0) or 0),
                )
                for opt in signals
            }
            reserved_cash = 0.0
            for opt in signals:
                if str(opt.get("option_type", "")).upper() != "PUT":
                    continue
                try:
                    reserved_cash += max(int(opt.get("recommended_contracts", 0) or 0), 0) * max(
                        float(opt.get("cash_required", 0) or 0), 0.0
                    )
                except (TypeError, ValueError):
                    continue
            remaining_cash = max(cash_available_for_csp - reserved_cash, 0.0)
            deployment_candidates = [
                opt
                for opt in all_candidates
                if (
                    str(opt.get("ticker", "")).upper(),
                    str(opt.get("option_type", "")).upper(),
                    str(opt.get("expiration", "")),
                    float(opt.get("strike", 0) or 0),
                )
                not in selected_keys
            ]
            deployment = deployment_plan(deployment_candidates, remaining_cash)
            formatted_deployment = []
            for rank, opt in enumerate(deployment, 1):
                formatted = format_recommendation(opt, rank)
                for field_name in (
                    "deployment_contracts",
                    "deployment_cash_required",
                    "deployment_income",
                    "deployment_cash_remaining",
                ):
                    formatted[field_name] = opt.get(field_name)
                formatted_deployment.append(formatted)

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
                # Transient archive input; WheelRunSnapshot.to_dict excludes
                # this list so immutable snapshot JSON remains compact.
                "all_candidates": all_candidates,
                "state": "planning" if preflight_infeasible else None,
                "preflight": preflight if scan_universe_ok else None,
                "message": (
                    f"CSP scanning for {preflight.get('affordable_names', preflight.get('watchlist_size', 0))} affordable names "
                    "exceeds the current open-session freshness budget; covered calls remain review-only."
                    if preflight_infeasible
                    else None
                ),
                "count": len(formatted_signals),
                "total_scored": len(all_candidates),
                "generated_at": datetime.now().isoformat(),
                "signals": formatted_signals,
                "entry_context": entry_window_advice(session_calendar=session_calendar),
                "covered_calls": {
                    "signals": formatted_cc,
                    "count": len(formatted_cc),
                },
                "watchlist_csps": {
                    "signals": formatted_csp,
                    "count": len(formatted_csp),
                },
                "capital_recovery": capital_recovery,
                "deployment_plan": {
                    "signals": formatted_deployment,
                    "count": len(formatted_deployment),
                    "cash_available": round(cash_available_for_csp, 2),
                    "cash_reserved_by_top_signals": round(reserved_cash, 2),
                    "cash_remaining": round(remaining_cash, 2),
                },
                "broker_buying_power": round(broker_buying_power, 2),
                "cash_available_for_csp": round(cash_available_for_csp, 2),
                "watchlist_cash_fit": _build_watchlist_cash_fit_summary(
                    effective_watchlist, skipped_csp_diagnostics, cash_available_for_csp
                ),
                "coverage_cash_skipped": len(cash_skip_evidence),
                "cash_skipped": cash_skip_evidence,
                "cash_reserved_for_csp": round(cash_reserved_for_csp, 2),
                "cash_diagnostics": portfolio_context.get("_cash_diagnostics", {}),
                "enrichment": enrichment_hint,
                "blocked_signals": blocked_signals,
                "blocked_reason_counts": dict(sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)),
                "scan_coverage": {
                    "scanned": max(0, len(scan_watchlist) - watchlist_errors),
                    "total": len(effective_watchlist),
                    "complete": len(scan_watchlist) > 0
                    and (len(scan_watchlist) - watchlist_errors) >= len(effective_watchlist),
                },
                "watchlist_origins": watchlist_origins,
                "active_watchlist": _build_active_watchlist(
                    wl_group_name=wl_group_name,
                    wl_group_status=wl_group_status,
                    wl_explanation=wl_explanation,
                    wl_groups_available=wl_groups_available,
                    wl_unsupported=wl_unsupported,
                    wl_raw_codes=wl_raw_codes,
                    effective_watchlist=effective_watchlist,
                    scan_status_by_ticker=scan_status_by_ticker,
                    position_tickers=list(positions.keys()),
                    quote_fetched_at=scan_quote_fetched_at,
                    price_snapshot_by_ticker=price_snapshot_by_ticker,
                    session_calendar=session_calendar,
                    fetched_at=wl_fetched_at,
                ),
                "quote_fetched_at": {ticker: scan_quote_fetched_at.get(ticker, "") for ticker in effective_watchlist},
                "preset": {
                    "key": self._preset.key,
                    "version": self._preset.version,
                    "label": self._preset.label,
                    "screener_profile": self._preset_profile,
                    "csp_profile_summary": _build_csp_profile_summary(self._preset_profile),
                },
                "_freshness": {
                    "market_state": "closed" if not is_market_open(session_calendar) else "open",
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
                    "scan_status": dict(scan_status_by_ticker),
                    "watchlist_group": {
                        "name": wl_group_name,
                        "status": wl_group_status,
                        "explanation": wl_explanation,
                        "groups_available": wl_groups_available,
                        "fetched_at": wl_fetched_at,
                    },
                    "scan_tickers_count": len(scan_watchlist),
                    "scan_tickers_cap": len(effective_watchlist),
                    "skipped_csp": skipped_csp_diagnostics,
                    "skipped_csp_count": len(skipped_csp_diagnostics),
                    "coverage_cash_skipped": len(cash_skip_evidence),
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
    parts.append(f"min BP ${sp.get('min_csp_buying_power', 5000):,.0f}")
    if sp.get("require_cash_fit", True):
        parts.append("cash-fit req.")
    return " | ".join(parts)


def _sanitize_unsupported(entries) -> list[dict]:
    """Coerce the unsupported-symbol list into plain {symbol, reason} dicts."""
    cleaned = []
    for entry in entries or []:
        if isinstance(entry, dict):
            cleaned.append(
                {
                    "symbol": str(entry.get("symbol") or ""),
                    "reason": str(entry.get("reason") or ""),
                }
            )
    return cleaned


def _build_active_watchlist(
    *,
    wl_group_name: str,
    wl_group_status: str,
    wl_explanation: str,
    wl_groups_available: list,
    wl_unsupported: list,
    wl_raw_codes: dict,
    effective_watchlist: list,
    scan_status_by_ticker: dict,
    position_tickers: list,
    quote_fetched_at: dict,
    fetched_at: str,
    price_snapshot_by_ticker: dict | None = None,
    session_calendar=None,
) -> dict:
    """Build the ACTIVE WATCHLIST payload persisted into the run snapshot and
    rendered by the dashboard foot.

    Documents exactly which scan universe was used: the tickers evaluated, the
    Moomoo group name + status (with a distinct explanation for a missing,
    empty, or unreachable group), per-ticker scan status, the last successful
    group sync time, unsupported symbols, and the holdings checked for covered
    calls. Never contains config/app additions (those are archived but not
    scanned).
    """
    per_ticker = []
    for ticker in effective_watchlist:
        quote = (price_snapshot_by_ticker or {}).get(str(ticker).upper().split(".")[-1])
        price_context = None
        if isinstance(quote, dict):
            current = safe_float(quote.get("last_price"), default=None)
            high = safe_float(quote.get("highest52weeks_price"), default=None)
            low = safe_float(quote.get("lowest52weeks_price"), default=None)
            price_context = {
                "last_price": current if current is not None and current > 0 else None,
                "week_52_high": high if high is not None and high > 0 else None,
                "week_52_low": low if low is not None and low > 0 else None,
                "source": "moomoo",
            }
            if current is not None and current > 0 and high is not None and high >= current:
                price_context["pct_below_52w_high"] = round((high - current) / high * 100, 2)
            else:
                price_context["pct_below_52w_high"] = None
            if not any(price_context.get(key) is not None for key in ("last_price", "week_52_high", "week_52_low")):
                price_context = None
        per_ticker.append(
            {
                "symbol": ticker,
                "raw_code": wl_raw_codes.get(ticker, ticker),
                "status": scan_status_by_ticker.get(ticker, "not_scanned"),
                "quote_fetched_at": quote_fetched_at.get(ticker, ""),
                "price_context": price_context,
            }
        )
    market_session = None
    if session_calendar is not None:
        next_open = session_calendar.next_regular_open(market_now())
        if next_open is not None:
            market_session = {
                "next_open_at": next_open.isoformat(),
                "calendar_source": session_calendar.source,
            }
    return {
        "group_name": wl_group_name,
        "group_status": wl_group_status,
        "explanation": wl_explanation,
        "groups_available": sorted(str(g) for g in wl_groups_available if str(g).strip()),
        "fetched_at": fetched_at,
        "last_successful_sync": "",
        "tickers": per_ticker,
        "market_session": market_session,
        "unsupported": wl_unsupported,
        "holdings_checked": sorted(str(t) for t in position_tickers if str(t).strip()),
    }


def _build_watchlist_cash_fit_summary(effective_watchlist: list, skipped_diagnostics: list, csp_cash: float) -> dict:
    """Summarize watchlist names with no affordable CSP strike in the active OTM range."""
    symbols = {canonical_underlying(str(ticker)).upper() for ticker in effective_watchlist if str(ticker).strip()}
    cash = max(0.0, safe_float(csp_cash))
    unaffordable = set()
    if cash <= 0:
        unaffordable = symbols.copy()
    else:
        for diagnostic in skipped_diagnostics:
            if diagnostic.get("reason_code") != "no_cash_fit":
                continue
            ticker = canonical_underlying(str(diagnostic.get("ticker") or "")).upper()
            if ticker in symbols:
                unaffordable.add(ticker)

    return {
        "unaffordable_count": len(unaffordable),
        "total_count": len(symbols),
        "max_affordable_strike": round(cash / 100, 2),
    }
