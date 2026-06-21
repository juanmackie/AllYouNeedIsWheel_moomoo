"""
Options Service module - Thin orchestrator for options operations
Refactored from monolithic 1627-line file into focused modules.
"""

import logging
from api.services.options_data import OptionsDataService
from api.services.watchlist_manager import WatchlistManager
from api.services.recommendations import RecommendationEngine
from api.services.portfolio_context import PortfolioContext
from api.services.vix_regime_service import VixRegimeService

logger = logging.getLogger('api.services.options')

class OptionsService:
    """
    Service for handling options data operations.
    Now a thin orchestrator that delegates to focused modules.
    """
    def __init__(self):
        from api.services.config import get_config
        self.config = get_config()
        self.connection = None
        db_path = self.config.get('db_path')
        from db.database import OptionsDatabase
        self.db = OptionsDatabase(db_path)
        from api.services.iv_earnings_service import IVEarningsService
        self.iv_earnings_service = IVEarningsService(self.db)
        self.portfolio_service = None
        # Initialize composed services with explicit dependencies
        # Order matters: leaf dependencies first, then consumers
        self.watchlist_manager = WatchlistManager(config_provider=self)
        self.vix_regime_service = VixRegimeService(
            config_provider=self,
        )
        self.portfolio_context_helper = PortfolioContext(
            portfolio_service_provider=self,
            vix_regime_provider=self.vix_regime_service,
            config_provider=self,
        )
        self.options_data = OptionsDataService(
            connection_provider=self,
            config_provider=self,
            db=self.db,
            iv_earnings_service=self.iv_earnings_service,
            screening_profile_provider=self.watchlist_manager,
            portfolio_context_provider=self.portfolio_context_helper,
        )
        self.recommendation_engine = RecommendationEngine(
            connection_provider=self,
            config_provider=self,
            db=self.db,
            iv_earnings_service=self.iv_earnings_service,
            portfolio_context_provider=self.portfolio_context_helper,
            portfolio_service_provider=self,
            watchlist_provider=self.watchlist_manager,
            options_data_provider=self.options_data,
            cash_calculator_provider=self.portfolio_context_helper,
        )
        
    def _ensure_connection(self):
        """
        Ensure that the moomoo connection exists and is connected.
        Reuses existing connection if already established.
        """
        try:
            if self.connection is not None and self.connection.is_connected():
                logger.debug("Reusing existing moomoo connection")
                return self.connection

            if self.connection is not None:
                logger.info("Existing connection found but disconnected, attempting to reconnect")
                if self.connection.connect():
                    logger.info("Successfully reconnected to moomoo OpenD")
                    return self.connection
                else:
                    logger.warning("Failed to reconnect, will create new connection")

            logger.info("Creating new moomoo connection")

            from core.connection import MoomooConnection
            self.connection = MoomooConnection(
                host=str(self.config.get('host', '127.0.0.1')),
                port=int(self.config.get('port', 11111)),
                readonly=bool(self.config.get('readonly', True)),
                account_id=self.config.get('account_id'),
                portfolio_env=self.config.get('portfolio_env'),
                security_firm=self.config.get('security_firm'),
                broker_cache_after_hours=self.config.get('broker_cache_after_hours', True),
            )

            if not self.connection.connect():
                logger.error("Failed to connect to moomoo OpenD")
                return None
            else:
                logger.info("Successfully connected to moomoo OpenD")
                if self.portfolio_service is not None:
                    self.portfolio_service.connection = self.connection
                return self.connection
        except Exception as e:
            logger.error(f"Error ensuring connection: {str(e)}")
            return None

    # Delegate methods to composed services
    
    def get_effective_watchlist(self):
        """Get effective watchlist (delegates to watchlist_manager)"""
        return self.watchlist_manager.get_effective_watchlist(
            growth_mode_config=self.config.get('growth_mode', {})
        )
    
    def _get_screening_profile(self, option_type, dte=None, profile_type=None, vix_regime=None):
        """Get screening profile (delegates to watchlist_manager)"""
        return self.watchlist_manager.get_screening_profile(
            option_type,
            dte,
            profile_type,
            vix_regime,
            growth_mode_config=self.config.get('growth_mode', {}),
        )
    
    def _get_candidate_expirations(self, conn, ticker, profile, expiration=None):
        """Get candidate expirations (delegates to options_data)"""
        return self.options_data._get_candidate_expirations(conn, ticker, profile, expiration)
    
    def _build_candidate(self, ticker, option, stock_price, desired_otm, profile, portfolio_context):
        """Build candidate (delegates to options_data)"""
        return self.options_data._build_candidate(ticker, option, stock_price, desired_otm, profile, portfolio_context)

    def get_otm_options(self, ticker, otm_percentage=10, option_type=None, expiration=None):
        """Get OTM options (delegates to options_data)"""
        return self.options_data.get_otm_options(ticker, otm_percentage, option_type, expiration)

    def _process_ticker_for_otm(self, conn, ticker, otm_percentage, portfolio_context, expiration=None, option_type=None):
        """Process ticker for OTM (delegates to options_data)"""
        return self.options_data._process_ticker_for_otm(conn, ticker, otm_percentage, portfolio_context, expiration, option_type)

    def _process_options_chain(self, options_chains, ticker, stock_price, otm_percentage, portfolio_context, option_type=None):
        """Process options chain (delegates to options_data)"""
        return self.options_data._process_options_chain(options_chains, ticker, stock_price, otm_percentage, portfolio_context, option_type)

    def get_option_expirations(self, ticker, option_type=None):
        """Get option expirations (delegates to options_data)"""
        return self.options_data.get_option_expirations(ticker, option_type)

    def get_stock_price(self, ticker):
        """Get stock price (delegates to options_data)"""
        return self.options_data.get_stock_price(ticker)

    def get_top_recommendations(self, limit=3, include_long_options=False, ignore_cash_limits=False, screener_overrides=None):
        """Get top signals (delegates to recommendation_engine)."""
        return self.recommendation_engine.get_top_recommendations(
            limit,
            include_long_options=include_long_options,
            ignore_cash_limits=ignore_cash_limits,
            screener_overrides=screener_overrides or {},
        )

    def _get_portfolio_context(self, refresh=True):
        """Get portfolio context (delegates to portfolio_context_helper)."""
        return self.portfolio_context_helper.get_portfolio_context(refresh=refresh)

    def _calculate_cash_reserved(self, portfolio_context):
        """Calculate cash reserved (delegates to portfolio_context_helper)"""
        return self.portfolio_context_helper._calculate_cash_reserved(portfolio_context)

    def _get_position_snapshot(self, portfolio_context, ticker):
        """Get position snapshot (delegates to portfolio_context_helper)"""
        return self.portfolio_context_helper._get_position_snapshot(portfolio_context, ticker)

    def _get_fallback_stock_price(self, portfolio_context, ticker):
        """Get fallback stock price (delegates to portfolio_context_helper)"""
        return self.portfolio_context_helper._get_fallback_stock_price(portfolio_context, ticker)

    def _get_vix_regime(self):
        return self.vix_regime_service.get_vix_regime()

    def _sanitize_result(self, result):
        """Sanitize result to remove NaN values"""
        if not result or not isinstance(result, dict):
            return
        import math
        def sanitize_dict(d):
            if not isinstance(d, dict): return
            for key, value in d.items():
                if isinstance(value, float) and math.isnan(value): d[key] = 0
                elif isinstance(value, dict): sanitize_dict(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict): sanitize_dict(item)
        sanitize_dict(result)
