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


def _screening_min_volatility_pct(criteria: dict, fallback: float) -> float:
    if "min_volatility_pct" in criteria:
        return float(criteria.get("min_volatility_pct") or fallback)
    legacy_iv_rank = criteria.get("min_iv_rank")
    if legacy_iv_rank is not None:
        return float(legacy_iv_rank) / 10
    return fallback


logger = logging.getLogger("api.services.watchlist_manager")


class WatchlistManager:
    """
    Handles watchlist management (static, dynamic, hybrid) and screening profile configuration.
    """

    def __init__(self, config_provider):
        self._config_provider = config_provider

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
                )
            except Exception as e:
                logger.warning(f"Moomoo watchlist connection init failed: {e}")
                self._moomoo_connection = False
        return self._moomoo_connection if self._moomoo_connection else None

    def _fetch_moomoo_watchlist(self):
        conn = self._get_moomoo_connection()
        if conn is None:
            logger.warning("Moomoo watchlist: no connection, falling back to static watchlist")
            return self.config.get("watchlist", [])
        if not conn.is_connected() and not conn.connect():
            logger.warning("Moomoo watchlist: failed to connect, falling back to static watchlist")
            return self.config.get("watchlist", [])
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
            logger.warning(f"Moomoo watchlist fetch failed: {e}, falling back to static watchlist")
            return self.config.get("watchlist", [])

    def _get_tvscreener_service(self):
        """Dynamic screening is out of scope; always returns None."""
        return None

    def get_effective_watchlist(self, growth_mode_config=None, portfolio_context=None):
        """
        Get the effective watchlist.

        Supports static, moomoo, and hybrid (moomoo + static union) modes.
        Dynamic broad-market screening is out of scope.

        Args:
            growth_mode_config: Optional growth mode config dict (retained for
                                call-site compatibility; replaced by presets).
            portfolio_context: Optional portfolio context dict. When provided,
                               computes max_price from CSP cash.

        Returns:
            List of ticker symbols
        """
        static_watchlist = self.config.get("watchlist", [])
        watchlist_mode = self.config.get("watchlist_mode", "static")

        if watchlist_mode == "static":
            return static_watchlist

        if watchlist_mode == "moomoo":
            return self._fetch_moomoo_watchlist()

        if watchlist_mode == "hybrid":
            moomoo_tickers = self._fetch_moomoo_watchlist() or []
            combined = list(dict.fromkeys(list(moomoo_tickers) + list(static_watchlist)))
            logger.info(
                f"Hybrid watchlist: {len(moomoo_tickers)} moomoo + {len(static_watchlist)} static = {len(combined)} total"
            )
            return combined

        # Unknown mode: fall back to static
        return static_watchlist

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

        # -- Growth Mode screener overlay ------------------------------------
        if growth_mode_config and option_type == "PUT":
            sp = growth_mode_config.get("screener_profile", {})
            if sp:
                overrides = {
                    "target_delta": sp.get("csp_target_delta"),
                    "delta_tolerance": sp.get("csp_delta_tolerance"),
                    "min_dte": sp.get("csp_min_dte"),
                    "max_dte": sp.get("csp_max_dte"),
                    "preferred_dte": sp.get("csp_preferred_dte"),
                    "default_otm_pct": sp.get("csp_default_otm_pct"),
                    "min_otm_pct": sp.get("csp_min_otm_pct"),
                    "max_otm_pct": sp.get("csp_max_otm_pct"),
                }
                for key, value in overrides.items():
                    if value is not None:
                        base_profile[key] = value

                # Tag the profile so consumers know it came from growth mode
                base_profile["growth_screener"] = True

                # When require_cash_fit is set, flag it in the profile
                if sp.get("require_cash_fit", True):
                    base_profile["require_cash_fit"] = True

        return base_profile
