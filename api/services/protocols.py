"""
Protocol/interfaces for OptionsService submodules.
Each submodule gets only what it needs instead of the whole parent service.
"""

from typing import Protocol, Dict, Any, Optional, List, runtime_checkable


@runtime_checkable
class ConnectionProvider(Protocol):
    """Provides a moomoo connection instance."""
    def _ensure_connection(self):
        ...


class ConfigProvider(Protocol):
    """Provides configuration dict."""
    @property
    def config(self) -> dict:
        ...


class DatabaseProvider(Protocol):
    """Provides database instance."""
    @property
    def db(self):
        ...


class IVEarningsProvider(Protocol):
    """Provides IV/earnings service."""
    @property
    def iv_earnings_service(self):
        ...


class PortfolioServiceProvider(Protocol):
    """Provides portfolio service."""
    @property
    def portfolio_service(self):
        ...


class ScreeningProfileProvider(Protocol):
    """Provides screening profile configuration."""
    def _get_screening_profile(self, option_type: str, dte: Optional[int] = None,
                               profile_type: Optional[str] = None,
                               vix_regime: Optional[dict] = None,
                               growth_mode_config: Optional[dict] = None) -> dict:
        ...


class PortfolioContextProvider(Protocol):
    """Provides portfolio context dict."""
    def _get_portfolio_context(self, refresh: bool = True) -> dict:
        ...


class OpenBBServiceProvider(Protocol):
    """Provides OpenBB service."""
    def _get_openbb_service(self):
        ...


class VixRegimeProvider(Protocol):
    """Provides VIX regime data."""
    def _get_vix_regime(self) -> dict:
        ...


class CashReservedCalculator(Protocol):
    """Calculates cash reserved for open positions."""
    def _calculate_cash_reserved(self, portfolio_context: dict) -> float:
        ...


class OptionsDataProvider(Protocol):
    """Provides options data processing."""
    def _process_ticker_for_otm(self, conn, ticker: str, otm_percentage: float,
                                 portfolio_context: dict, expiration: Optional[str] = None,
                                 option_type: Optional[str] = None) -> dict:
        ...


class WatchlistProvider(Protocol):
    """Provides watchlist management."""
    def get_effective_watchlist(self, growth_mode_config: Optional[dict] = None) -> List[str]:
        ...
    def get_screening_profile(self, option_type: str, dte: Optional[int] = None,
                               profile_type: Optional[str] = None,
                               vix_regime: Optional[dict] = None,
                               growth_mode_config: Optional[dict] = None) -> dict:
        ...
