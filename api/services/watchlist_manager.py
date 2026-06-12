"""
Watchlist Manager module - handles watchlist and screening profile management
Extracted from the monolithic options_service.py for maintainability.
"""

import logging

logger = logging.getLogger('api.services.watchlist_manager')


class WatchlistManager:
    """
    Handles watchlist management (static, dynamic, hybrid) and screening profile configuration.
    """
    
    def __init__(self, config_provider):
        self._config_provider = config_provider

    @property
    def config(self):
        if hasattr(self._config_provider, 'config'):
            return self._config_provider.config
        return self._config_provider
        
    def _get_tvscreener_service(self):
        """
        Lazy initialization of tvscreener service.
        Returns the service if available, None otherwise.
        Uses a sentinel (False) to avoid repeated failed initialization attempts.
        """
        if not hasattr(self, '_tvscreener_service'):
            try:
                from api import get_service
                self._tvscreener_service = get_service('tvscreener')
                logger.info("tvscreener service initialized")
            except Exception as e:
                logger.warning(f"tvscreener service not available: {e}")
                self._tvscreener_service = False
        return self._tvscreener_service if self._tvscreener_service else None

    def get_effective_watchlist(self, growth_mode_config=None, portfolio_context=None):
        """
        Get effective watchlist based on configuration.
        Supports static, dynamic, and hybrid modes.

        When growth_mode is enabled, uses growth-tuned screener parameters
        (higher min_iv_rank, smaller max_stocks) to reduce wasted scanning.
        When portfolio_context is provided, computes max_price from CSP cash
        to cap CSP underlyings.

        Args:
            growth_mode_config: Optional growth mode config dict. When provided
                                and enabled, overrides screening_criteria values.
            portfolio_context: Optional portfolio context dict. When provided,
                               computes max_price from CSP cash.

        Returns:
            List of ticker symbols
        """
        static_watchlist = self.config.get('watchlist', [])

        # Check watchlist mode
        watchlist_mode = self.config.get('watchlist_mode', 'static')

        if watchlist_mode == 'static':
            return static_watchlist

        # Try dynamic screening
        try:
            tvscreener = self._get_tvscreener_service()
            if tvscreener:
                criteria = self.config.get('screening_criteria', {})
                min_iv_rank = criteria.get('min_iv_rank', 30)
                min_volume = criteria.get('min_volume', 1000000)
                max_stocks = criteria.get('max_stocks', 50)

                # Apply growth mode overlay on screening criteria
                if growth_mode_config:
                    screener_profile = growth_mode_config.get('screener_profile', {})
                    min_iv_rank = screener_profile.get('min_iv_rank', min_iv_rank)
                    max_stocks = screener_profile.get('max_watchlist_tickers', max_stocks)

                # Compute max_price from portfolio context
                max_price = None
                if portfolio_context:
                    csp_cash = float(portfolio_context.get('cash_available_for_csp', 0) or 0)
                    if csp_cash > 0:
                        # Max price = CSP cash / 100 / (1 - max_otm_pct/100)
                        # With 15% max OTM: strike = price * 0.85, cash = strike * 100
                        # So max_price = cash / 100 / 0.85
                        max_otm_pct = 15.0
                        if growth_mode_config:
                            sp = growth_mode_config.get('screener_profile', {})
                            max_otm_pct = float(sp.get('csp_max_otm_pct', 15) or 15)
                        max_price = csp_cash / 100 / (1 - max_otm_pct / 100)

                dynamic = tvscreener.get_wheel_candidates(
                    min_iv_rank=min_iv_rank,
                    min_volume=min_volume,
                    limit=max_stocks,
                    max_price=max_price,
                )

                if dynamic:
                    if watchlist_mode == 'hybrid':
                        # Combine dynamic and static watchlists
                        combined = list(set(dynamic + static_watchlist))
                        logger.info(f"Hybrid watchlist: {len(dynamic)} dynamic + {len(static_watchlist)} static = {len(combined)} total")
                        return combined
                    else:  # 'dynamic'
                        logger.info(f"Dynamic watchlist: {len(dynamic)} stocks")
                        return dynamic
        except Exception as e:
            logger.warning(f"Dynamic screening failed: {e}, using static watchlist")

        # Fallback to static watchlist
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
                profile_type = 'weekly'
            elif dte <= 45:
                profile_type = 'monthly'
            else:
                profile_type = 'quarterly'
        elif profile_type is None:
            profile_type = 'monthly'
        
        # Base profile with targets from Phase 1
        base_profile = {
            'max_expirations': 2,
            'min_mid_price': 0.05,
            'min_open_interest': 10,
            'ideal_open_interest': 500,
            'min_volume': 1,
            'ideal_volume': 100,
            'max_spread_pct': 60,
            'ideal_spread_pct': 12,
            'profile_type': profile_type,
            # Risk-adjusted scoring targets (Phase 1)
            'target_iv_adjusted': 50,
            'target_theta_delta_ratio': 0.005,
            'target_capital_efficiency': 100,
            # IV environment thresholds (Phase 2)
            'min_iv_percentile_for_bonus': 60,
            'max_iv_percentile_for_penalty': 30,
            'earnings_warning_days': 7,
        }
        
        # Dynamic profiles based on expiration type
        if profile_type == 'weekly':
            # Weeklies (0-14 DTE): Tighter delta, higher liquidity focus
            if option_type == 'CALL':
                base_profile.update({
                    'min_dte': 3,
                    'max_dte': 14,
                    'preferred_dte': 7,
                    'target_delta': 0.18,
                    'delta_tolerance': 0.14,
                    'min_premium_per_contract': 8,
                    'liquidity_weight_multiplier': 1.5,  # 35% effective
                    'delta_fit_weight_multiplier': 0.5,  # 8% effective
                })
            else:  # PUT
                base_profile.update({
                    'min_dte': 3,
                    'max_dte': 14,
                    'preferred_dte': 7,
                    'target_delta': 0.16,
                    'delta_tolerance': 0.12,
                    'min_premium_per_contract': 10,
                    'liquidity_weight_multiplier': 1.5,
                    'delta_fit_weight_multiplier': 0.5,
                })
        
        elif profile_type == 'quarterly':
            # Quarterlies (46-90 DTE): Wider delta, lower liquidity focus
            if option_type == 'CALL':
                base_profile.update({
                    'min_dte': 46,
                    'max_dte': 90,
                    'preferred_dte': 60,
                    'target_delta': 0.28,
                    'delta_tolerance': 0.22,
                    'min_premium_per_contract': 25,
                    'liquidity_weight_multiplier': 0.75,  # 15% effective
                    'delta_fit_weight_multiplier': 1.2,  # 18% effective
                })
            else:  # PUT
                base_profile.update({
                    'min_dte': 46,
                    'max_dte': 90,
                    'preferred_dte': 60,
                    'target_delta': 0.26,
                    'delta_tolerance': 0.20,
                    'min_premium_per_contract': 30,
                    'liquidity_weight_multiplier': 0.75,
                    'delta_fit_weight_multiplier': 1.2,
                })
        
        else:  # 'monthly' (default, 15-45 DTE)
            if option_type == 'CALL':
                base_profile.update({
                    'min_dte': 5,
                    'max_dte': 35,
                    'preferred_dte': 14,
                    'target_delta': 0.24,
                    'delta_tolerance': 0.18,
                    'min_premium_per_contract': 12,
                    'liquidity_weight_multiplier': 1.0,
                    'delta_fit_weight_multiplier': 1.0,
                })
            else:  # PUT
                base_profile.update({
                    'min_dte': 7,
                    'max_dte': 45,
                    'preferred_dte': 21,
                    'target_delta': 0.22,
                    'delta_tolerance': 0.16,
                    'min_premium_per_contract': 15,
                    'liquidity_weight_multiplier': 1.0,
                    'delta_fit_weight_multiplier': 1.0,
                })
        
        if vix_regime:
            delta_adj = vix_regime.get('delta_adjustment', 0.0)
            regime_name = vix_regime.get('regime', 'normal')
            
            if 'target_delta' in base_profile:
                base_profile['target_delta'] = max(0.10, min(0.40,
                    base_profile['target_delta'] + delta_adj))
            
            if 'delta_tolerance' in base_profile:
                base_profile['delta_tolerance'] = max(0.08,
                    base_profile['delta_tolerance'] + (delta_adj * 0.5))
            
            if regime_name == 'fear':
                base_profile['min_premium_per_contract'] *= 1.2
            elif regime_name == 'complacency':
                base_profile['min_premium_per_contract'] *= 0.8
            
            base_profile['vix_regime'] = regime_name

        # -- Growth Mode screener overlay ------------------------------------
        if growth_mode_config and option_type == 'PUT':
            sp = growth_mode_config.get('screener_profile', {})
            if sp:
                overrides = {
                    'target_delta': sp.get('csp_target_delta'),
                    'delta_tolerance': sp.get('csp_delta_tolerance'),
                    'min_dte': sp.get('csp_min_dte'),
                    'max_dte': sp.get('csp_max_dte'),
                    'preferred_dte': sp.get('csp_preferred_dte'),
                    'default_otm_pct': sp.get('csp_default_otm_pct'),
                    'min_otm_pct': sp.get('csp_min_otm_pct'),
                    'max_otm_pct': sp.get('csp_max_otm_pct'),
                }
                for key, value in overrides.items():
                    if value is not None:
                        base_profile[key] = value

                # Tag the profile so consumers know it came from growth mode
                base_profile['growth_screener'] = True

                # When require_cash_fit is set, flag it in the profile
                if sp.get('require_cash_fit', True):
                    base_profile['require_cash_fit'] = True

        return base_profile
