"""
Watchlist Manager module - handles watchlist and screening profile management
Extracted from the monolithic options_service.py for maintainability.
"""

import logging

try:
    from moomoo import RET_ERROR, RET_OK
except ImportError:
    RET_OK = None
    RET_ERROR = None


logger = logging.getLogger("api.services.watchlist_manager")


class WatchlistManager:
    """
    Handles watchlist management (merged Moomoo group + app-managed SQLite + config)
    and screening profile configuration.
    """

    def __init__(self, config_provider, db=None):
        self._config_provider = config_provider
        self._db = db

    @property
    def config(self):
        if hasattr(self._config_provider, "config"):
            return self._config_provider.config
        return self._config_provider

    def _get_moomoo_connection(self):
        if not hasattr(self, "_moomoo_connection"):
            try:
                from core.connection import MoomooConnection

                cfg = self.config
                self._moomoo_connection = MoomooConnection(
                    host=str(cfg.get("host", "127.0.0.1")),
                    port=int(cfg.get("port", 11111)),
                    readonly=bool(cfg.get("readonly", True)),
                    account_id=cfg.get("account_id"),
                    portfolio_env=cfg.get("portfolio_env"),
                    security_firm=cfg.get("security_firm"),
                    broker_cache_after_hours=cfg.get("broker_cache_after_hours", True),
                    chain_rate_limit_max_requests=cfg.get("chain_rate_limit_max_requests", 10),
                    chain_rate_limit_window_sec=cfg.get("chain_rate_limit_window_sec", 30),
                    chain_min_request_spacing_sec=cfg.get("chain_min_request_spacing_sec", 3.0),
                )
            except Exception as e:
                logger.warning(f"Moomoo watchlist connection init failed: {e}")
                self._moomoo_connection = False
        return self._moomoo_connection if self._moomoo_connection else None

    def _fetch_moomoo_watchlist(self):
        conn = self._get_moomoo_connection()
        if conn is None:
            logger.warning("Moomoo watchlist: no connection available")
            return []
        # Fail fast: TCP probe before any SDK connect attempt (SDK connect can
        # block with reconnect retries when OpenD is absent).
        try:
            from core.context_factory import probe_opend_status

            probe = probe_opend_status(
                host=str(self.config.get("host", "127.0.0.1")), port=int(self.config.get("port", 11111))
            )
            if probe.get("status") != "connected":
                logger.warning("Moomoo watchlist: OpenD not reachable")
                return []
        except Exception as exc:
            logger.warning(f"Moomoo watchlist: probe failed ({exc})")
            return []
        if not conn.is_connected() and not conn.connect():
            logger.warning("Moomoo watchlist: failed to connect")
            return []
        group_name = self.config.get("moomoo_watchlist_group", "My Watchlist")
        try:
            ret, data = conn.get_user_security(group_name)
            if ret != (RET_OK or 0) or data is None or (hasattr(data, "empty") and data.empty):
                logger.warning(f"Moomoo watchlist: group '{group_name}' returned no securities, falling back")
                return self.config.get("watchlist", [])
            if hasattr(data, "to_dict"):
                records = data.to_dict("records")
            else:
                records = list(data)
            tickers = []
            for record in records:
                code = record.get("code", "")
                if code:
                    if "." in code:
                        code = code.rsplit(".", 1)[-1]
                    tickers.append(code.upper())
            logger.info(f"Moomoo watchlist: fetched {len(tickers)} tickers from group '{group_name}'")
            return tickers
        except Exception as e:
            logger.warning(f"Moomoo watchlist fetch failed: {e}")
            return []

    def get_watchlist_sources(self):
        """Return each watchlist source as a labelled list.

        Sources:
          - moomoo: the named OpenD/Moomoo watchlist group
          - app:    app-managed SQLite symbols
          - config: legacy WATCHLIST env/connection.json list (compat)
        """
        sources = {"moomoo": [], "app": [], "config": []}
        try:
            sources["moomoo"] = self._fetch_moomoo_watchlist() or []
        except Exception as exc:
            logger.warning(f"Moomoo watchlist fetch failed: {exc}")
            sources["moomoo"] = []
        if self._db is not None:
            try:
                sources["app"] = [row["symbol"] for row in self._db.get_watchlist_symbols()]
            except Exception as exc:
                logger.warning(f"App watchlist load failed: {exc}")
                sources["app"] = []
        sources["config"] = list(self.config.get("watchlist", []) or [])
        return sources

    def get_effective_watchlist_with_origins(self, growth_mode_config=None, portfolio_context=None):
        """Return the canonical merged union with per-ticker origin labels.

        Returns a list of dicts: {"ticker": str, "origins": [str, ...]}. Tickers
        are canonicalized (UBER vs US.UBER) and deduplicated.
        """
        from core.ticker_utils import canonical_underlying

        sources = self.get_watchlist_sources()
        merged: dict[str, list[str]] = {}
        for origin, tickers in sources.items():
            for raw in tickers:
                ticker = str(raw or "").strip().upper()
                if not ticker:
                    continue
                canonical = canonical_underlying(ticker)
                if canonical not in merged:
                    merged[canonical] = []
                if origin not in merged[canonical]:
                    merged[canonical].append(origin)
        return [{"ticker": ticker, "origins": sorted(origins)} for ticker, origins in sorted(merged.items())]

    def preflight_scan_feasibility(self, watchlist_size: int) -> dict:
        """Estimate whether a full watchlist scan fits the quota + freshness budget.

        Model: per symbol ~1 price + 1 expiration call (cheap) and 3 option-chain
        calls spaced >= 3s by the chain rate limiter. Total chain time is
        approximately 9s per symbol.
        """
        freshness_window = max(1, int(self.config.get("max_tradeable_quote_age_sec", 300) or 300))
        max_requests = max(1, int(self.config.get("chain_rate_limit_max_requests", 10) or 10))
        rate_window = max(1.0, float(self.config.get("chain_rate_limit_window_sec", 30) or 30))
        chain_spacing_sec = max(0.0, float(self.config.get("chain_min_request_spacing_sec", 3.0) or 0))
        per_symbol_chain_sec = 3 * chain_spacing_sec
        estimated_scan_sec = watchlist_size * per_symbol_chain_sec
        chain_calls = watchlist_size * 3
        quota_windows = max(1, int(freshness_window // rate_window))
        chain_quota_ok = chain_calls <= max_requests * quota_windows
        feasible = watchlist_size > 0 and estimated_scan_sec <= freshness_window and chain_quota_ok
        recommended_max_size = (
            max(1, int(freshness_window // per_symbol_chain_sec)) if per_symbol_chain_sec else watchlist_size
        )
        return {
            "feasible": feasible,
            "watchlist_size": watchlist_size,
            "estimated_scan_sec": round(estimated_scan_sec, 1),
            "freshness_window_sec": freshness_window,
            "chain_calls": chain_calls,
            "chain_quota_ok": chain_quota_ok,
            "chain_rate_limit_max_requests": max_requests,
            "chain_rate_limit_window_sec": rate_window,
            "chain_min_request_spacing_sec": chain_spacing_sec,
            "recommended_max_size": recommended_max_size,
        }

    def get_effective_watchlist(self, growth_mode_config=None, portfolio_context=None):
        """
        Return the canonical merged watchlist (Moomoo group + app SQLite + config).
        Tickers are canonicalized and deduplicated.
        """
        return [item["ticker"] for item in self.get_effective_watchlist_with_origins()]

    def get_screening_profile(self, option_type, dte=None, profile_type=None, vix_regime=None, growth_mode_config=None):
        """
        Get screening profile based on option type, DTE, and VIX regime.

        When growth_mode is enabled with a screener_profile block, PUT profiles
        are tuned for shorter DTE, higher delta, and closer OTM targets.

        Args:
            option_type: 'CALL' or 'PUT'
            dte: Days to expiration (auto-detects profile if None)
            profile_type: 'weekly', 'monthly', 'quarterly', or None (auto-detect)
            vix_regime: dict from _get_vix_regime() with delta_adjustment, exposure_multiplier
            growth_mode_config: Optional growth_mode dict. When enabled with
                                screener_profile, overrides PUT profile defaults.

        Returns:
            dict: Screening profile parameters with VIX regime adjustments
        """
        if profile_type is None and dte is not None:
            if dte <= 14:
                profile_type = "weekly"
            elif dte <= 45:
                profile_type = "monthly"
            else:
                profile_type = "quarterly"
        elif profile_type is None:
            profile_type = "monthly"

        # Base profile with targets from Phase 1
        base_profile = {
            "max_expirations": 2,
            "min_mid_price": 0.05,
            "min_open_interest": 10,
            "ideal_open_interest": 500,
            "min_volume": 1,
            "ideal_volume": 100,
            "max_spread_pct": 60,
            "ideal_spread_pct": 12,
            "profile_type": profile_type,
            # Risk-adjusted scoring targets (Phase 1)
            "target_iv_adjusted": 50,
            "target_theta_delta_ratio": 0.005,
            "target_capital_efficiency": 100,
            # IV environment thresholds (Phase 2)
            "min_iv_percentile_for_bonus": 60,
            "max_iv_percentile_for_penalty": 30,
            "earnings_warning_days": 7,
        }

        # Dynamic profiles based on expiration type
        if profile_type == "weekly":
            # Weeklies (0-14 DTE): Tighter delta, higher liquidity focus
            if option_type == "CALL":
                base_profile.update(
                    {
                        "min_dte": 3,
                        "max_dte": 14,
                        "preferred_dte": 7,
                        "target_delta": 0.18,
                        "delta_tolerance": 0.14,
                        "min_premium_per_contract": 8,
                        "liquidity_weight_multiplier": 1.5,  # 35% effective
                        "delta_fit_weight_multiplier": 0.5,  # 8% effective
                    }
                )
            else:  # PUT
                base_profile.update(
                    {
                        "min_dte": 3,
                        "max_dte": 14,
                        "preferred_dte": 7,
                        "target_delta": 0.16,
                        "delta_tolerance": 0.12,
                        "min_premium_per_contract": 10,
                        "liquidity_weight_multiplier": 1.5,
                        "delta_fit_weight_multiplier": 0.5,
                    }
                )

        elif profile_type == "quarterly":
            # Quarterlies (46-90 DTE): Wider delta, lower liquidity focus
            if option_type == "CALL":
                base_profile.update(
                    {
                        "min_dte": 46,
                        "max_dte": 90,
                        "preferred_dte": 60,
                        "target_delta": 0.28,
                        "delta_tolerance": 0.22,
                        "min_premium_per_contract": 25,
                        "liquidity_weight_multiplier": 0.75,  # 15% effective
                        "delta_fit_weight_multiplier": 1.2,  # 18% effective
                    }
                )
            else:  # PUT
                base_profile.update(
                    {
                        "min_dte": 46,
                        "max_dte": 90,
                        "preferred_dte": 60,
                        "target_delta": 0.26,
                        "delta_tolerance": 0.20,
                        "min_premium_per_contract": 30,
                        "liquidity_weight_multiplier": 0.75,
                        "delta_fit_weight_multiplier": 1.2,
                    }
                )

        else:  # 'monthly' (default, 15-45 DTE)
            if option_type == "CALL":
                base_profile.update(
                    {
                        "min_dte": 5,
                        "max_dte": 35,
                        "preferred_dte": 14,
                        "target_delta": 0.24,
                        "delta_tolerance": 0.18,
                        "min_premium_per_contract": 12,
                        "liquidity_weight_multiplier": 1.0,
                        "delta_fit_weight_multiplier": 1.0,
                    }
                )
            else:  # PUT
                base_profile.update(
                    {
                        "min_dte": 7,
                        "max_dte": 45,
                        "preferred_dte": 21,
                        "target_delta": 0.22,
                        "delta_tolerance": 0.16,
                        "min_premium_per_contract": 15,
                        "liquidity_weight_multiplier": 1.0,
                        "delta_fit_weight_multiplier": 1.0,
                    }
                )

        if vix_regime:
            delta_adj = vix_regime.get("delta_adjustment", 0.0)
            regime_name = vix_regime.get("regime", "normal")

            if "target_delta" in base_profile:
                base_profile["target_delta"] = max(0.10, min(0.40, base_profile["target_delta"] + delta_adj))

            if "delta_tolerance" in base_profile:
                base_profile["delta_tolerance"] = max(0.08, base_profile["delta_tolerance"] + (delta_adj * 0.5))

            if regime_name == "fear":
                base_profile["min_premium_per_contract"] *= 1.2
            elif regime_name == "complacency":
                base_profile["min_premium_per_contract"] *= 0.8

            base_profile["vix_regime"] = regime_name

        # -- Selected-preset merge -------------------------------------------
        # The recommendation engine passes the active preset's flat screener
        # profile (from WheelPreset.to_screener_profile()). Merge its explicit
        # thresholds over the base profile so score_contract() honours the
        # selected preset instead of silently falling back to legacy defaults.
        # Retired growth/VIX overlay behavior was removed: the preset is the
        # single source of these thresholds.
        if growth_mode_config:
            sp = growth_mode_config or {}
            if not isinstance(sp, dict):
                sp = {}
            # Generic liquidity / premium floors apply to both CALL and PUT.
            generic = {
                "min_mid_price": sp.get("min_mid_price"),
                "min_premium_per_contract": sp.get("min_premium_per_contract"),
                "max_spread_pct": sp.get("max_spread_pct"),
                "min_open_interest": sp.get("min_open_interest"),
                "min_volume": sp.get("min_volume"),
                "max_buying_power_pct_per_csp": sp.get("max_buying_power_pct_per_csp"),
                "target_account_multiple": sp.get("target_account_multiple"),
            }
            for key, value in generic.items():
                if value is not None:
                    base_profile[key] = value
            if option_type == "PUT":
                put_overrides = {
                    "target_delta": sp.get("csp_target_delta"),
                    "delta_tolerance": sp.get("csp_delta_tolerance"),
                    "min_dte": sp.get("csp_min_dte"),
                    "max_dte": sp.get("csp_max_dte"),
                    "preferred_dte": sp.get("csp_preferred_dte"),
                    "default_otm_pct": sp.get("csp_default_otm_pct"),
                    "min_otm_pct": sp.get("csp_min_otm_pct"),
                    "max_otm_pct": sp.get("csp_max_otm_pct"),
                }
                for key, value in put_overrides.items():
                    if value is not None:
                        base_profile[key] = value
            if option_type == "CALL":
                call_overrides = {
                    "default_otm_pct": sp.get("call_default_otm_pct"),
                    # Covered calls use the same active preset DTE risk
                    # window as CSPs; only the OTM target differs by side.
                    "min_dte": sp.get("csp_min_dte"),
                    "max_dte": sp.get("csp_max_dte"),
                    "preferred_dte": sp.get("csp_preferred_dte"),
                }
                for key, value in call_overrides.items():
                    if value is not None:
                        base_profile[key] = value
            if sp.get("require_cash_fit", True):
                base_profile["require_cash_fit"] = True

        return base_profile
