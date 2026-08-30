"""
Options Data module - handles options data retrieval and processing
Extracted from the monolithic options_service.py for maintainability.
"""

import logging
import time
from datetime import datetime

from api.services.utils import clean_yfinance_ticker
from core.growth_mode import should_block_for_data_quality
from core.scoring_factors import premium_velocity_per_day
from core.utils import get_closest_friday, is_market_open
from core.wheel_decision import score_contract

logger = logging.getLogger("api.services.options_data")


def _normalize_expiration(expiration):
    value = str(expiration or "").strip()
    if not value:
        return ""
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10].replace("-", "")
    return value.replace("-", "")


def _parse_expiration_date(expiration):
    normalized = _normalize_expiration(expiration)
    if len(normalized) != 8:
        return None
    try:
        return datetime.strptime(normalized, "%Y%m%d").date()
    except ValueError:
        return None


def _annotate_chain_sources(
    chain, price_source="broker", chain_source="broker", iv_source="broker", from_yfinance=False
):
    """Attach provenance metadata to a chain payload and each option row."""
    if not chain:
        return chain

    annotated = dict(chain)
    annotated["price_source"] = price_source
    annotated["chain_source"] = chain_source
    annotated["iv_source"] = iv_source
    annotated["from_yfinance"] = from_yfinance
    annotated["data_source"] = price_source if price_source else chain_source

    options = []
    for opt in chain.get("options", []) or []:
        item = dict(opt)
        item["price_source"] = price_source
        item["chain_source"] = chain_source
        item["iv_source"] = iv_source
        item["from_yfinance"] = from_yfinance
        item["data_source"] = price_source if price_source else chain_source
        options.append(item)
    annotated["options"] = options
    return annotated


def fetch_option_chain_live_first(conn, db, config, ticker, expiration, right, target_strike=None, stock_price=None):
    """Fetch a broker chain live first, falling back to persisted broker data.

    Closed-market scans are planning-only, but they are the user's normal review
    window. Bypass the in-memory after-hours cache for the freshest OpenD
    last-session quote; use the persisted snapshot only when OpenD cannot provide
    a chain. No external source can enter this path.
    """
    market_closed = not is_market_open()
    right = str(right or "C").upper()
    try:
        try:
            chain = conn.get_option_chain(
                ticker,
                expiration,
                right,
                target_strike=target_strike,
                force_refresh=market_closed,
            )
        except TypeError as exc:
            # Keep lightweight test/donor providers compatible while the real
            # MoomooConnection supports force_refresh explicitly.
            if "force_refresh" not in str(exc):
                raise
            chain = conn.get_option_chain(ticker, expiration, right, target_strike=target_strike)

        if chain and chain.get("options"):
            chain = _annotate_chain_sources(
                chain,
                price_source="broker",
                chain_source="broker",
                iv_source="broker",
                from_yfinance=False,
            )
            if db is not None:
                try:
                    db.save_option_chain_snapshot(
                        ticker,
                        expiration,
                        right,
                        stock_price if stock_price is not None else chain.get("stock_price", 0),
                        chain,
                        source="broker",
                    )
                except Exception:
                    logger.debug("Could not persist broker chain snapshot", exc_info=True)
            return chain
    except Exception:
        logger.exception("Error getting live option chain for %s %s %s", ticker, expiration, right)

    use_after_hours_cache = bool((config or {}).get("broker_cache_after_hours", True))
    if market_closed and use_after_hours_cache and db is not None:
        try:
            snapshot = db.get_latest_option_chain(ticker, right, max_age_hours=168)
            if snapshot and snapshot.get("chain_data") and snapshot.get("source") == "broker":
                chain = _annotate_chain_sources(
                    snapshot["chain_data"],
                    price_source="broker",
                    chain_source="persisted-broker",
                    iv_source="persisted-broker",
                    from_yfinance=False,
                )
                chain["quote_timestamp"] = snapshot.get("as_of", "")
                chain["data_source"] = "persisted"
                for option in chain.get("options", []):
                    option["quote_timestamp"] = snapshot.get("as_of", "")
                return chain
        except Exception:
            logger.debug("Could not load persisted broker chain fallback", exc_info=True)
    return None


class OptionsDataService:
    """
    Handles options data retrieval, chain processing, and candidate building.
    """

    def __init__(
        self,
        connection_provider,
        config_provider,
        db,
        iv_earnings_service,
        screening_profile_provider,
        portfolio_context_provider,
    ):
        self._connection_provider = connection_provider
        self._config_provider = config_provider
        self.db = db
        self.iv_earnings_service = iv_earnings_service
        self._screening_profile_provider = screening_profile_provider
        self._portfolio_context_provider = portfolio_context_provider

    def _get_config(self):
        if hasattr(self._config_provider, "config"):
            return self._config_provider.config
        return self._config_provider

    def _get_connection(self):
        return self._connection_provider._ensure_connection()

    def _get_portfolio_context(self):
        return self._portfolio_context_provider.get_portfolio_context()

    def _strip_ticker_prefix(self, ticker):
        return clean_yfinance_ticker(ticker)

    def _build_candidate(self, ticker, option, stock_price, desired_otm, profile, portfolio_context):
        """
        Build a scored candidate for a single option contract.

        Actionability contract: only broker-sourced chains ("broker" or
        "persisted-broker") may become candidates. Any external chain data is
        rejected here, before scoring.
        """
        chain_source = str(option.get("chain_source", "") or "").strip().lower()
        if chain_source not in ("broker", "persisted-broker"):
            logger.debug(
                "Rejecting %s %s: chain_source=%r is not broker data",
                ticker,
                option.get("option_type", ""),
                chain_source,
            )
            return None

        # Gather IV / earnings context (macro enrichment is out of scope)
        option["expiration"] = _normalize_expiration(option.get("expiration", ""))

        if option.get("implied_volatility", 0) > 0:
            self.iv_earnings_service.record_iv_data(
                ticker,
                float(option.get("implied_volatility", 0)),
                stock_price,
                str(option.get("option_type", "") or "").upper(),
                str(option.get("expiration", "") or ""),
                int(
                    (datetime.strptime(str(option.get("expiration", "")), "%Y%m%d").date() - datetime.now().date()).days
                )
                if option.get("expiration")
                else 0,
            )

        iv_env_adjustment, iv_rank, iv_status = self.iv_earnings_service.get_iv_environment_score(
            ticker, float(option.get("implied_volatility", 0) or 0.20)
        )
        earnings_adjustment, earnings_warning = self.iv_earnings_service.get_earnings_score_impact(ticker)
        earnings_info = self.iv_earnings_service.get_earnings_info(ticker)

        # Broker chains carry IV/greeks; no external enrichment is applied.
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
            growth_profile=profile,
        )

        if decision is None:
            return None

        if decision and decision.hard_blockers:
            logger.debug(
                "Filtered %s %s %s %s due to hard blockers: %s",
                decision.ticker,
                decision.option_type,
                decision.expiration,
                decision.strike,
                decision.hard_blockers,
            )
            return None

        from_yfinance = bool(
            option.get("from_yfinance")
            or decision.price_source == "yfinance"
            or decision.chain_source == "yfinance"
            or decision.iv_source == "yfinance"
        )
        blocked, reason = should_block_for_data_quality(
            confidence_score=getattr(decision, "confidence_score", 100) or 0,
            has_blockers=bool(decision.hard_blockers),
            is_from_yfinance=from_yfinance,
            price_source=getattr(decision, "price_source", "broker"),
        )
        if blocked:
            logger.debug(
                "Filtered %s %s %s %s due to data quality gate: %s",
                decision.ticker,
                decision.option_type,
                decision.expiration,
                decision.strike,
                reason,
            )
            return None

        # Convert WheelDecision back to legacy dict format for API compatibility
        candidate = {
            "symbol": decision.ticker
            + decision.expiration
            + ("C" if decision.option_type == "CALL" else "P")
            + str(int(decision.strike)),
            "strike": decision.strike,
            "expiration": decision.expiration,
            "option_type": decision.option_type,
            "bid": decision.bid,
            "ask": decision.ask,
            "last": decision.last if hasattr(decision, "last") else round(decision.mid_price, 4),
            "mid_price": round(decision.mid_price, 4),
            "open_interest": decision.open_interest,
            "volume": decision.volume,
            "implied_volatility": round(decision.implied_volatility, 2),
            "delta": round(decision.delta, 5),
            "gamma": round(decision.gamma, 5),
            "theta": round(decision.theta, 5),
            "vega": round(getattr(decision, "vega", 0), 5),
            "dte": decision.dte,
            "premium_per_contract": round(decision.premium_per_contract, 2),
            "bid_premium_per_contract": round(decision.bid_premium_per_contract, 2),
            "limit_target_per_contract": round(decision.limit_target_per_contract, 2),
            "premium_velocity_per_day": round(decision.premium_velocity_per_day, 4),
            "capital_velocity_per_day": round(decision.capital_velocity_per_day, 8),
            "spread_pct": round(decision.spread_pct, 2),
            "score": round(decision.contract_score, 2),
            "score_details": decision.score_details,
            "rationale": decision.rationale,
            "warnings": decision.warnings,
            "hard_blockers": decision.hard_blockers,
            "quote_quality": decision.quote_quality,
            "blocked_reason_codes": decision.blocked_reason_codes,
            "avg_cost": float(portfolio_context.get("positions", {}).get(ticker, {}).get("avg_cost", 0) or 0),
            "otm_pct": decision.otm_pct,
            "annualized_return": decision.annualized_return,
            "return_on_underlying": decision.return_on_underlying,
            "return_on_secured_cash": decision.return_on_secured_cash,
            "iv_adjusted_return": decision.iv_adjusted_return,
            "iv_rank": decision.iv_rank,
            "iv_status": decision.iv_status,
            "iv_env_adjustment": decision.iv_env_adjustment,
            "profile_type": decision.profile_type,
            "vix_regime": decision.vix_regime,
            "vix_level": decision.vix_level,
            "earnings_date": decision.earnings_date,
            "days_to_earnings": decision.days_to_earnings,
            "earnings_adjustment": decision.earnings_adjustment,
            # Additional unified fields
            "size_fit": decision.size_fit,
            "expected_move_buffer": decision.expected_move_buffer,
            "wheel_decision": decision.to_dict(),
            # Preserve data provenance for actionability checks.
            "price_source": decision.price_source,
            "chain_source": decision.chain_source,
            "greeks_source": decision.greeks_source,
            "iv_source": decision.iv_source,
            "earnings_source": decision.earnings_source,
            "quote_timestamp": decision.quote_timestamp,
            "quote_update_time": decision.quote_update_time,
            "quote_fetched_at_utc": decision.quote_fetched_at_utc,
            "quality_tier": decision.quality_tier,
            "event_tier": decision.event_tier,
            "security_type": decision.security_type,
            "review_only": decision.review_only,
            "copy_eligible": decision.copy_eligible,
            "generated_at": decision.generated_at,
            "from_yfinance": from_yfinance,
        }

        # Add CALL/PUT specific fields
        if decision.option_type == "CALL":
            candidate.update(
                {
                    "if_called_return": decision.if_called_return,
                    "earnings_max_contracts": decision.max_contracts,
                    "earnings_premium_per_contract": round(decision.premium_per_contract, 2),
                    "earnings_total_premium": round(decision.premium_per_contract * decision.max_contracts, 2),
                    "earnings_return_on_capital": round(decision.annualized_return, 2),
                }
            )
        else:
            candidate.update(
                {
                    "breakeven": decision.breakeven,
                    "breakeven_buffer_pct": decision.breakeven_buffer_pct,
                    "cash_required": decision.cash_required,
                    "cash_reserve_enabled": self._config_provider.config.get("cash_reserve_enabled", True),
                    "earnings_max_contracts": 1,
                    "earnings_premium_per_contract": round(decision.premium_per_contract, 2),
                    "earnings_total_premium": round(decision.premium_per_contract, 2),
                    "earnings_return_on_cash": round(decision.annualized_return, 2),
                }
            )

        return candidate

    def _get_candidate_expirations(self, conn, ticker, profile, expiration=None):
        if expiration:
            return [_normalize_expiration(expiration)]

        try:
            # Use the rate-limited method in MoomooConnection
            from moomoo import RET_OK

            ret, data = conn.get_option_expiration_dates(ticker)
            if ret != RET_OK or data is None or data.empty:
                yf_expirations = self._get_yfinance_expiration_dates(ticker, profile)
                if yf_expirations:
                    return [value for value, _ in yf_expirations[: profile.get("max_expirations", 5)]]
                fallback = get_closest_friday().strftime("%Y%m%d")
                logger.debug(f"get_option_expiration_dates failed for {ticker}: ret={ret}, data empty or None")
                return [fallback]

            today = datetime.now().date()
            filtered = []
            fallback = []

            expiration_column = "expiration_date"
            if expiration_column not in data.columns:
                if "strike_time" in data.columns:
                    expiration_column = "strike_time"
                elif "option_expiry_date" in data.columns:
                    expiration_column = "option_expiry_date"
                else:
                    raise KeyError("No expiration column returned by moomoo")

            min_dte = profile.get("min_dte", 0)
            max_dte = profile.get("max_dte", 365)
            logger.debug(
                f"_get_candidate_expirations for {ticker}: min_dte={min_dte} (type={type(min_dte)}), max_dte={max_dte} (type={type(max_dte)})"
            )

            for raw_date in data[expiration_column].tolist():
                normalized = _normalize_expiration(raw_date)
                expiry_date = _parse_expiration_date(normalized)
                if not expiry_date:
                    continue
                dte = (expiry_date - today).days
                logger.debug(f"  Checking expiration {normalized}: dte={dte} (type={type(dte)})")
                if dte <= 0:
                    continue
                fallback.append((normalized, dte))
                try:
                    if min_dte <= dte <= max_dte:
                        filtered.append((normalized, dte))
                except TypeError:
                    logger.error(f"TypeError in DTE comparison: min_dte={min_dte}, dte={dte}, max_dte={max_dte}")
                    logger.error(f"  Types: min_dte={type(min_dte)}, dte={type(dte)}, max_dte={type(max_dte)}")
                    raise

            expirations = filtered or fallback
            result = [value for value, _ in expirations[: profile.get("max_expirations", 5)]] or [
                get_closest_friday().strftime("%Y%m%d")
            ]
            logger.debug(f"_get_candidate_expirations returning {len(result)} expirations: {result}")
            return result
        except Exception as exc:
            logger.exception(f"Error loading option expirations for {ticker}: {exc}")
            yf_expirations = self._get_yfinance_expiration_dates(ticker, profile)
            if yf_expirations:
                return [value for value, _ in yf_expirations[: profile.get("max_expirations", 5)]]
            return [get_closest_friday().strftime("%Y%m%d")]

    def get_otm_options(self, ticker, otm_percentage=10, option_type=None, expiration=None):
        """
        Return ranked wheel candidates near the requested OTM preference.
        """
        start_time = time.time()

        if option_type and option_type not in ["CALL", "PUT"]:
            return {"error": f"Invalid option_type: {option_type}. Must be 'CALL' or 'PUT'"}

        conn = self._get_connection()
        if not conn:
            return {"error": "Failed to establish connection to moomoo"}

        portfolio_context = self._get_portfolio_context()
        result = {}

        try:
            result[ticker] = self._process_ticker_for_otm(
                conn, ticker, otm_percentage, portfolio_context, expiration, option_type
            )
        except Exception as exc:
            logger.exception(f"Error processing {ticker} for optimal options: {exc}")
            result[ticker] = {"error": str(exc)}

        elapsed = time.time() - start_time
        logger.info(f"Ranked option opportunities for {ticker} in {elapsed:.2f}s")
        return {"data": result}

    def _process_ticker_for_otm(
        self,
        conn,
        ticker,
        otm_percentage,
        portfolio_context,
        expiration=None,
        option_type=None,
        screening_profile=None,
    ):
        result = {
            "symbol": ticker,
            "stock_price": 0,
            "otm_percentage": otm_percentage,
            "position": 0,
            "calls": [],
            "puts": [],
        }

        try:
            # Actionable candidates require a fresh Moomoo quote. No external
            # price fallback (portfolio fallback prices lack a quote timestamp).
            stock_price_source = "broker"
            stock_price = conn.get_stock_price(ticker)
            if stock_price is None or stock_price <= 0:
                result["error"] = "Unable to obtain a fresh Moomoo stock price"
                return result

            position = portfolio_context.get("positions", {}).get(ticker, {})
            result["stock_price"] = stock_price
            result["stock_price_source"] = stock_price_source
            result["position"] = float(position.get("position", 0) or 0)
            result["avg_cost"] = float(position.get("avg_cost", 0) or 0)

            sides = [option_type] if option_type else ["CALL", "PUT"]
            options_chains = []

            for side in sides:
                profile = (
                    dict(screening_profile)
                    if screening_profile is not None and side == option_type
                    else self._screening_profile_provider.get_screening_profile(
                        side,
                        vix_regime=portfolio_context.get("vix_regime"),
                        growth_mode_config=self._get_config().get("growth_mode", {}),
                    )
                )
                logger.debug(
                    f"Processing {ticker} {side} with profile: min_dte={profile.get('min_dte')}, max_dte={profile.get('max_dte')}, preferred_dte={profile.get('preferred_dte')}"
                )
                expirations = self._get_candidate_expirations(conn, ticker, profile, expiration)
                logger.debug(f"Got {len(expirations)} expirations for {ticker} {side}: {expirations[:3]}...")

                target_strike = (
                    stock_price * (1 + (otm_percentage / 100))
                    if side == "CALL"
                    else stock_price * (1 - (otm_percentage / 100))
                )
                for expiry in expirations:
                    right = "C" if side == "CALL" else "P"
                    chain = fetch_option_chain_live_first(
                        conn,
                        self.db,
                        self._get_config(),
                        ticker,
                        expiry,
                        right,
                        target_strike=target_strike,
                        stock_price=stock_price,
                    )
                    if chain:
                        options_chains.append(chain)
                    else:
                        logger.debug(
                            f"Moomoo returned no options for {ticker} {expiry} {side}; "
                            "no external chain fallback is permitted"
                        )

            if not options_chains:
                result["error"] = "No options data available from any source"
                return result

            formatted_data = self._process_options_chain(
                options_chains, ticker, stock_price, otm_percentage, portfolio_context, option_type
            )
            result.update(formatted_data)
        except Exception as exc:
            logger.exception(f"Error in _process_ticker_for_otm for {ticker}: {exc}")
            result["error"] = str(exc)

        return result

    def _process_options_chain(
        self, options_chains, ticker, stock_price, otm_percentage, portfolio_context, option_type=None
    ):
        try:
            result = {
                "symbol": ticker,
                "stock_price": stock_price,
                "otm_percentage": otm_percentage,
                "calls": [],
                "puts": [],
            }

            grouped_options = {"CALL": [], "PUT": []}
            for chain in options_chains:
                chain_type = str(chain.get("right", "") or "").upper()
                option_side = "CALL" if chain_type == "C" else "PUT"
                chain_sources = {
                    "price_source": chain.get("price_source", "broker"),
                    "chain_source": chain.get("chain_source", "broker"),
                    "iv_source": chain.get("iv_source", "broker"),
                    "from_yfinance": bool(chain.get("from_yfinance", False)),
                    "data_source": chain.get("data_source", chain.get("price_source", "broker")),
                }
                for option in chain.get("options", []):
                    annotated = dict(option)
                    for key, value in chain_sources.items():
                        annotated.setdefault(key, value)
                    grouped_options[option_side].append(annotated)

            for side in ["CALL", "PUT"]:
                if option_type and option_type != side:
                    continue

                profile = self._screening_profile_provider.get_screening_profile(
                    side,
                    vix_regime=portfolio_context.get("vix_regime"),
                    growth_mode_config=self._get_config().get("growth_mode", {}),
                )
                candidates = []
                seen_contracts = set()

                for option in grouped_options[side]:
                    option["expiration"] = _normalize_expiration(option.get("expiration", ""))
                    contract_key = (option.get("expiration"), option.get("strike"), option.get("option_type"))
                    if contract_key in seen_contracts:
                        continue
                    seen_contracts.add(contract_key)

                    candidate = self._build_candidate(
                        ticker, option, stock_price, otm_percentage, profile, portfolio_context
                    )
                    if candidate:
                        candidates.append(candidate)

                candidates.sort(
                    key=lambda item: (
                        premium_velocity_per_day(item.get("premium_per_contract", 0), item.get("dte", 0)),
                        item.get("annualized_return", 0),
                        item.get("wheel_decision", {}).get("contract_score", 0),
                    ),
                    reverse=True,
                )

                result["calls" if side == "CALL" else "puts"] = candidates[:5]

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
            if not conn:
                return {"error": "No connection"}

            # Use the rate-limited method in MoomooConnection
            from moomoo import RET_OK

            ret, data = conn.get_option_expiration_dates(ticker)
            if ret != RET_OK or data is None or data.empty:
                profile = (
                    self._screening_profile_provider.get_screening_profile(
                        option_type,
                        growth_mode_config=self._get_config().get("growth_mode", {}),
                    )
                    if option_type in ["CALL", "PUT"]
                    else {}
                )
                yf_expirations = self._get_yfinance_expiration_dates(ticker, profile)
                return {
                    "ticker": ticker,
                    "expiration_source": "yfinance",
                    "expirations": [
                        {
                            "value": value,
                            "label": f"{value[0:4]}-{value[4:6]}-{value[6:8]}",
                            "dte": dte,
                        }
                        for value, dte in yf_expirations
                    ],
                }

            expiration_column = "expiration_date"
            if expiration_column not in data.columns:
                if "strike_time" in data.columns:
                    expiration_column = "strike_time"
                elif "option_expiry_date" in data.columns:
                    expiration_column = "option_expiry_date"
                else:
                    return {"error": "No expiration column returned by moomoo"}

            from datetime import date

            today = date.today()

            if option_type in ["CALL", "PUT"]:
                profile = self._screening_profile_provider.get_screening_profile(
                    option_type,
                    growth_mode_config=self._get_config().get("growth_mode", {}),
                )
                min_dte = profile.get("min_dte", 0)
                max_dte = profile.get("max_dte", 365)
            else:
                min_dte, max_dte = 0, 365  # All future dates up to 1 year

            expirations = []
            for date_str in data[expiration_column].tolist():
                exp_date = _parse_expiration_date(date_str)
                if not exp_date:
                    continue

                dte = (exp_date - today).days

                # Filter: must be in the future and within preferred range
                if dte >= min_dte and dte <= max_dte:
                    value = _normalize_expiration(date_str)
                    expirations.append({"value": value, "label": f"{value[0:4]}-{value[4:6]}-{value[6:8]}", "dte": dte})

            # Sort by DTE (ascending)
            expirations.sort(key=lambda x: x["dte"])

            return {"ticker": ticker, "expiration_source": "moomoo", "expirations": expirations}
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
        position = portfolio_context.get("positions", {}).get(ticker, {})
        for field in ("market_price", "avg_cost"):
            value = position.get(field)
            try:
                numeric_value = float(value or 0)
            except (TypeError, ValueError):
                numeric_value = 0
            if numeric_value > 0:
                return numeric_value
        return 0
