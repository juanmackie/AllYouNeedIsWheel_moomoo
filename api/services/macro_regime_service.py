"""
Macro Regime Service
Fetches economic data from FRED to detect macro economic regimes.
Provides context for option scoring, portfolio warnings, and strategy guidance.

FRED API Key (free): https://fred.stlouisfed.org/docs/api/api_key.html
"""

import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger('api.services.macro_regime')


class MacroRegimeService:
    """
    Service for detecting macro economic regimes using FRED data.
    Tracks interest rates, credit stress, economic growth, and inflation.
    Provides regime-aware context for wheel strategy decisions.
    """

    # Essential FRED series (minimal set, maximum signal)
    FRED_SERIES = {
        'fed_funds_rate': 'DFF',               # Federal funds effective rate
        'treasury_10y': 'DGS10',               # 10-year Treasury yield
        'treasury_2y': 'DGS2',                 # 2-year Treasury yield
        'high_yield_spread': 'BAMLH0A0HYM2',   # ICE BofA High Yield OAS (credit stress)
        'gdp': 'GDP',                          # Gross Domestic Product
        'cpi': 'CPIALLMINMEI',                 # Consumer Price Index (all items)
    }

    # Regime thresholds
    CREDIT_STRESS_THRESHOLDS = {
        'low': 4.0,      # HY spread < 4% = calm credit markets
        'high': 7.0,     # HY spread > 7% = stressed credit markets
    }

    YIELD_CURVE_THRESHOLDS = {
        'inverted': -0.5,   # 10y-2y < -0.5% = inverted (recession signal)
        'flat': 0.5,        # 10y-2y < 0.5% = flat (caution)
    }

    def __init__(self):
        self._fred = None
        self._initialized = False
        self._init_lock = threading.Lock()
        self._cache: Dict[str, Any] = {}
        self._cache_lock = threading.Lock()
        self._cache_ttl_seconds = 86400  # 24 hours (FRED data updates monthly/quarterly)

    def _ensure_initialized(self) -> bool:
        """Initialize FRED API client with API key from environment."""
        if self._initialized:
            return self._fred is not None

        with self._init_lock:
            if self._initialized:
                return self._fred is not None

            api_key = os.environ.get('FRED_API_KEY')
            if not api_key:
                logger.warning("FRED_API_KEY not set. Macro regime detection disabled.")
                self._initialized = True
                return False

            try:
                from fredapi import Fred
                self._fred = Fred(api_key=api_key)
                self._initialized = True
                logger.info("FRED API client initialized successfully")
                return True
            except ImportError:
                logger.warning("fredapi not installed. Run: pip install fredapi")
                self._initialized = True
                return False
            except Exception as e:
                logger.error(f"Failed to initialize FRED API: {e}")
                self._initialized = True
                return False

    def _get_cache(self) -> Optional[Dict]:
        """Get cached macro regime if still valid."""
        with self._cache_lock:
            if not self._cache:
                return None
            age = (datetime.now() - self._cache['timestamp']).total_seconds()
            if age > self._cache_ttl_seconds:
                self._cache.clear()
                return None
            return self._cache['data']

    def _set_cache(self, data: Dict):
        """Cache macro regime data."""
        with self._cache_lock:
            self._cache = {
                'data': data,
                'timestamp': datetime.now()
            }

    def get_macro_regime(self) -> Dict[str, Any]:
        """
        Get current macro economic regime.

        Returns:
            dict: {
                'rate_regime': 'rising' | 'stable' | 'falling',
                'credit_stress': 'low' | 'moderate' | 'high',
                'growth_regime': 'expansion' | 'slowdown' | 'contraction',
                'inflation_trend': 'rising' | 'stable' | 'falling',
                'yield_curve_slope': float,  # 10y - 2y spread
                'fed_funds_rate': float,
                'hy_spread': float,
                'macro_multiplier': float,  # 0.80 | 0.90 | 1.0 | 1.05
                'summary': str,
                'advice': str,
                'fetched_at': str,
                'enabled': bool
            }
        """
        # Check cache first
        cached = self._get_cache()
        if cached is not None:
            return cached

        # If FRED not enabled or not initialized, return neutral default
        if not self._ensure_initialized():
            default = self._get_neutral_regime()
            self._set_cache(default)
            return default

        # Fetch all FRED series in batch
        data = self._fetch_all_series()
        if not data:
            logger.warning("Failed to fetch FRED data, returning neutral regime")
            default = self._get_neutral_regime()
            self._set_cache(default)
            return default

        # Detect regimes
        regime = self._detect_regimes(data)
        regime['fetched_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
        regime['enabled'] = True

        # Cache and return
        self._set_cache(regime)
        return regime

    def _fetch_all_series(self) -> Optional[Dict[str, float]]:
        """
        Fetch all FRED series. Returns dict of series_id -> latest value.
        """
        result = {}
        errors = []

        for name, series_id in self.FRED_SERIES.items():
            try:
                series_data = self._fred.get_series(series_id)
                if series_data is not None and not series_data.empty:
                    # Drop NaN values and get latest
                    series_data = series_data.dropna()
                    if not series_data.empty:
                        result[name] = float(series_data.iloc[-1])
                        continue
                errors.append(f"{name} ({series_id}): no data")
            except Exception as e:
                errors.append(f"{name} ({series_id}): {e}")

        if errors:
            logger.warning(f"FRED fetch warnings: {', '.join(errors)}")

        # Need at least fed_funds_rate and hy_spread for meaningful regime detection
        if 'fed_funds_rate' not in result:
            logger.error("Critical FRED series missing: fed_funds_rate")
            return None

        return result

    def _detect_regimes(self, data: Dict[str, float]) -> Dict[str, Any]:
        """
        Detect macro regimeses from current FRED data.
        """
        # --- Interest Rate Regime ---
        fed_funds = data.get('fed_funds_rate', 0)
        treasury_10y = data.get('treasury_10y', 0)
        treasury_2y = data.get('treasury_2y', 0)

        # Rate level classification
        if fed_funds < 1.0:
            rate_regime = 'falling'  # Near-zero rates = accommodative
            rate_description = 'Near-zero rates - accommodative policy'
        elif fed_funds < 3.0:
            rate_regime = 'stable'   # Moderate rates = neutral
            rate_description = 'Moderate rates - neutral policy'
        elif fed_funds < 5.0:
            rate_regime = 'rising'   # Elevated rates = restrictive
            rate_description = 'Elevated rates - restrictive policy'
        else:
            rate_regime = 'rising'   # High rates = very restrictive
            rate_description = 'High rates - very restrictive policy'

        # --- Credit Stress ---
        hy_spread = data.get('high_yield_spread', 0)
        if hy_spread < self.CREDIT_STRESS_THRESHOLDS['low']:
            credit_stress = 'low'
            credit_description = 'Calm credit markets - low default risk'
        elif hy_spread < self.CREDIT_STRESS_THRESHOLDS['high']:
            credit_stress = 'moderate'
            credit_description = 'Moderate credit stress - monitor spreads'
        else:
            credit_stress = 'high'
            credit_description = 'High credit stress - elevated default risk'

        # --- Yield Curve Slope (recession signal) ---
        yield_curve_slope = treasury_10y - treasury_2y if (treasury_10y and treasury_2y) else 0

        if yield_curve_slope < self.YIELD_CURVE_THRESHOLDS['inverted']:
            yield_curve_status = 'inverted'
            yield_curve_warning = '⚠️ Yield curve inverted - historical recession signal'
        elif yield_curve_slope < self.YIELD_CURVE_THRESHOLDS['flat']:
            yield_curve_status = 'flat'
            yield_curve_warning = 'Yield curve flatting - economic uncertainty'
        else:
            yield_curve_status = 'normal'
            yield_curve_warning = 'Normal yield curve - growth environment'

        # --- Growth Regime (proxied by yield curve + rate level) ---
        # Note: GDP updates quarterly, so we use yield curve as a faster signal
        if yield_curve_status == 'inverted' and rate_regime == 'rising':
            growth_regime = 'slowdown'
            growth_description = 'Inverted curve + high rates - slowdown risk'
        elif yield_curve_status == 'inverted':
            growth_regime = 'slowdown'
            growth_description = 'Yield curve inverted - economic slowdown risk'
        elif rate_regime == 'falling' and credit_stress == 'low':
            growth_regime = 'expansion'
            growth_description = 'Accommodative rates + calm credit - expansion'
        else:
            growth_regime = 'expansion'  # Default to expansion unless signals disagree
            growth_description = 'Standard growth environment'

        # --- Inflation Trend (proxied by rate regime + yield curve) ---
        # Note: CPI updates monthly, use current rate environment as proxy
        if rate_regime == 'rising' and yield_curve_status == 'inverted':
            inflation_trend = 'stable'  # Fed fighting inflation, may be overdoing it
            inflation_description = 'Rates elevated - inflation being addressed'
        elif rate_regime == 'falling':
            inflation_trend = 'falling'
            inflation_description = 'Rates falling - inflation subsiding'
        else:
            inflation_trend = 'stable'
            inflation_description = 'Inflation stable - no major concerns'

        # --- Macro Multiplier (score impact) ---
        macro_multiplier = self._calculate_multiplier(
            rate_regime, credit_stress, growth_regime, yield_curve_status
        )

        # --- Summary & Advice ---
        summary, advice = self._generate_summary_and_advice(
            rate_regime, credit_stress, growth_regime, inflation_trend,
            yield_curve_status, macro_multiplier, fed_funds, hy_spread
        )

        return {
            'rate_regime': rate_regime,
            'rate_description': rate_description,
            'credit_stress': credit_stress,
            'credit_description': credit_description,
            'growth_regime': growth_regime,
            'growth_description': growth_description,
            'inflation_trend': inflation_trend,
            'inflation_description': inflation_description,
            'yield_curve_slope': round(yield_curve_slope, 2),
            'yield_curve_status': yield_curve_status,
            'yield_curve_warning': yield_curve_warning,
            'fed_funds_rate': round(fed_funds, 2),
            'hy_spread': round(hy_spread, 2),
            'macro_multiplier': macro_multiplier,
            'summary': summary,
            'advice': advice,
        }

    def _calculate_multiplier(
        self, rate_regime: str, credit_stress: str,
        growth_regime: str, yield_curve_status: str
    ) -> float:
        """
        Calculate macro multiplier for option score adjustment.

        Range: 0.80 (crisis) to 1.05 (favorable)
        """
        # Start neutral
        multiplier = 1.0

        # Credit stress is the biggest risk factor
        if credit_stress == 'high':
            multiplier -= 0.10
        elif credit_stress == 'moderate':
            multiplier -= 0.03

        # Yield curve inversion signals caution
        if yield_curve_status == 'inverted':
            multiplier -= 0.05
        elif yield_curve_status == 'flat':
            multiplier -= 0.02

        # Growth slowdown
        if growth_regime == 'slowdown':
            multiplier -= 0.05

        # Favorable environment bonus
        if (credit_stress == 'low' and
            growth_regime == 'expansion' and
            yield_curve_status == 'normal' and
            rate_regime in ('stable', 'falling')):
            multiplier = max(multiplier, 1.05)

        # Clamp to valid range
        return round(max(0.80, min(1.05, multiplier)), 2)

    def _generate_summary_and_advice(
        self, rate_regime, credit_stress, growth_regime, inflation_trend,
        yield_curve_status, macro_multiplier, fed_funds, hy_spread
    ) -> tuple:
        """Generate human-readable summary and actionable advice."""

        # Crisis scenario
        if macro_multiplier <= 0.80:
            return (
                "Macro stress: High credit stress + economic slowdown",
                "⚠️ Defensive posture recommended. Reduce position sizes, prefer shorter DTE (7-14 days), avoid high-beta stocks for CSPs."
            )

        # Stressful scenario
        if macro_multiplier < 1.0:
            if credit_stress == 'high':
                return (
                    f"Credit stress elevated (HY spread: {hy_spread:.1f}%)",
                    "Caution advised. Focus on high-quality names, reduce tech/growth exposure for wheel strategy."
                )
            elif yield_curve_status == 'inverted':
                return (
                    "Yield curve inverted - recession warning",
                    "Historically signals slowdown. Prefer defensive sectors (XLP, XLU, XLV) for CSPs."
                )
            else:
                return (
                    "Macro headwinds present",
                    "Monitor closely. Consider shorter DTE and higher premium thresholds."
                )

        # Favorable scenario
        if macro_multiplier > 1.0:
            return (
                "Favorable macro environment",
                "Ideal conditions for wheel strategy. Standard position sizing, full sector coverage."
            )

        # Neutral scenario
        if rate_regime == 'rising':
            return (
                f"Rising rate environment (Fed funds: {fed_funds:.2f}%)",
                "Consider shorter DTE (14-21 days) to capture elevated premiums. Avoid long-dated options."
            )
        elif rate_regime == 'falling':
            return (
                "Declining rate environment",
                "Premiums may compress. Extend DTE to 30-45 days for better returns."
            )
        else:
            return (
                "Stable macro environment",
                "Standard wheel strategy parameters apply. No macro adjustments needed."
            )

    def _get_neutral_regime(self) -> Dict[str, Any]:
        """Return neutral macro regime when FRED is unavailable."""
        return {
            'rate_regime': 'stable',
            'rate_description': 'FRED not configured - using neutral assumption',
            'credit_stress': 'moderate',
            'credit_description': 'FRED not configured - using neutral assumption',
            'growth_regime': 'expansion',
            'growth_description': 'FRED not configured - using neutral assumption',
            'inflation_trend': 'stable',
            'inflation_description': 'FRED not configured - using neutral assumption',
            'yield_curve_slope': 0.0,
            'yield_curve_status': 'normal',
            'yield_curve_warning': 'Unknown (FRED not configured)',
            'fed_funds_rate': 0.0,
            'hy_spread': 0.0,
            'macro_multiplier': 1.0,
            'summary': 'Macro detection disabled (no FRED API key)',
            'advice': 'Add FRED_API_KEY to .env for macro regime detection. Get free key: https://fred.stlouisfed.org/docs/api/api_key.html',
            'fetched_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'enabled': False,
        }

    def clear_cache(self):
        """Clear cached macro regime data."""
        with self._cache_lock:
            self._cache.clear()
            logger.info("Macro regime cache cleared")

    def get_cache_status(self) -> Dict[str, Any]:
        """Get cache status for monitoring."""
        with self._cache_lock:
            if not self._cache:
                return {'cached': False, 'age_seconds': None}
            age = (datetime.now() - self._cache['timestamp']).total_seconds()
            return {
                'cached': True,
                'age_seconds': round(age, 0),
                'ttl_seconds': self._cache_ttl_seconds,
            }


# ------------------------------------------------------------------ #
#  Singleton                                                           #
# ------------------------------------------------------------------ #

_macro_service = None
_service_lock = threading.Lock()


def get_macro_service() -> MacroRegimeService:
    """Get or create the macro regime service singleton."""
    global _macro_service
    if _macro_service is None:
        with _service_lock:
            if _macro_service is None:
                _macro_service = MacroRegimeService()
    return _macro_service
