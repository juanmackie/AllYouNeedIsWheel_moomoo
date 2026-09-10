"""
IV Tracking and Earnings Service
Manages implied volatility history and earnings calendar data
Uses Alpha Vantage -> yfinance provider chain.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from api.services.alpha_vantage_provider import ALPHA_VANTAGE_FREE_DAILY_ALLOWANCE, AlphaVantageEarningsProvider
from api.services.utils import clean_yfinance_ticker, get_yfinance_ticker
from core.connection_constants import _normalize_iv
from core.ticker_utils import earnings_underlying_ticker

logger = logging.getLogger("api.services.iv_tracking")

# Normalized, UI-facing source tokens (per repo contract: alpha_vantage|yfinance).
SOURCE_ALPHA_VANTAGE = "alpha_vantage"
SOURCE_YFINANCE = "yfinance"


class IVEarningsService:
    """
    Service for tracking implied volatility and earnings data
    """

    def __init__(self, database=None):
        """
        Initialize the IV/Earnings service

        Args:
            database: OptionsDatabase instance
        """
        self.db = database
        self._iv_cache = {}
        self._earnings_cache = {}
        self._cache_duration_hours = 4
        self._earnings_cache_duration_hours = 24
        self._max_stale_event_tickers = 20
        self._alpha_vantage = AlphaVantageEarningsProvider()

    # -- small helpers -------------------------------------------------------

    @staticmethod
    def _normalize_source(value) -> Optional[str]:
        """Standardize an earnings source to ``alpha_vantage`` or ``yfinance``.

        Legacy rows may store "Alpha Vantage" / "yfinance" in free text; this
        keeps display and payload tokens consistent without a data rewrite.
        """
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None
        lowered = text.lower()
        if lowered in ("alpha vantage", "alphavantage", "alpha_vantage"):
            return SOURCE_ALPHA_VANTAGE
        if lowered in ("yfinance", "yahoo", "yfinance api", "yfinance-api"):
            return SOURCE_YFINANCE
        return lowered or None

    @staticmethod
    def _coerce_date(value):
        """Best-effort conversion of a yfinance/calendar date value to a date."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        try:
            import pandas as pd

            if isinstance(value, pd.Timestamp):
                return value.date()
        except Exception:
            pass
        text = str(value).strip()[:10]
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    def _parse_earnings_df_date(self, df) -> Tuple[Optional[str], Optional[str]]:
        """Extract the next earnings date from a yfinance earnings DataFrame.

        yfinance 1.5.x returns a DataFrame whose earnings dates live in the
        **index** (``Earnings Date``) with columns ``EPS Estimate / Reported
        EPS / Surprise(%)`` — scanning columns for a date silently no-ops. This
        parses the index first, with a column scan as a forward-compatible
        fallback, and prefers the nearest upcoming date (>= today).

        Returns ``(date_str, error_msg)``.
        """
        try:
            if df is None or getattr(df, "empty", True):
                return None, None
            candidates = []
            index = getattr(df, "index", None)
            if index is not None:
                for value in list(index):
                    d = self._coerce_date(value)
                    if d:
                        candidates.append(d)
            if not candidates and hasattr(df, "columns"):
                for column in ("Earnings Date", "earningsDate", "date", "report_date"):
                    if column not in df.columns:
                        continue
                    for value in df[column].tolist():
                        d = self._coerce_date(value)
                        if d:
                            candidates.append(d)
            if not candidates:
                return None, None
            candidates.sort()
            today = datetime.now().date()
            upcoming = [d for d in candidates if d >= today]
            chosen = (upcoming or candidates)[0]
            return chosen.strftime("%Y-%m-%d"), None
        except Exception as exc:  # pragma: no cover - defensive
            return None, str(exc)

    def _parse_calendar_ex_dividend(self, calendar):
        """Extract the upcoming ex-dividend date from yfinance Ticker.calendar.

        yfinance 1.5.x ``Ticker.calendar`` is a dict keyed by event name
        (``'Ex-Dividend Date'`` is a ``datetime.date``). A transposed DataFrame
        shape (index = event name) is handled as a fallback. Returns
        ``(date_str, error_msg)``; unknown stays ``None`` — never fabricated.
        """
        try:
            if calendar is None:
                return None, None
            value = None
            if isinstance(calendar, dict):
                value = calendar.get("Ex-Dividend Date")
            else:
                # DataFrame with an "Ex-Dividend Date" column or index row.
                if hasattr(calendar, "columns") and "Ex-Dividend Date" in calendar.columns:
                    col = calendar["Ex-Dividend Date"]
                    if hasattr(col, "dropna"):
                        col = col.dropna()
                    if len(col):
                        value = col.iloc[0]
                elif hasattr(calendar, "index") and "Ex-Dividend Date" in calendar.index:
                    row = calendar.loc["Ex-Dividend Date"]
                    if hasattr(row, "iloc"):
                        row = row.iloc[0] if len(row) else None
                    value = row
            d = self._coerce_date(value)
            return (d.strftime("%Y-%m-%d"), None) if d else (None, None)
        except Exception as exc:  # pragma: no cover - defensive
            return None, str(exc)

    def _build_provider_status(self) -> dict:
        """Concise provider health block carried in event payloads and status."""
        av = self._alpha_vantage.get_status()
        return {
            "alpha_vantage": {
                "available": bool(av.get("available")),
                "status": av.get("status"),
                "error": av.get("error"),
                "requests_today": av.get("requests_today", 0),
                "daily_allowance": av.get("daily_allowance", ALPHA_VANTAGE_FREE_DAILY_ALLOWANCE),
            },
            "yfinance": {
                "available": True,
                "note": "per-ticker informational; best-effort, cached 24h",
            },
        }

    def _is_cache_valid(self, cache_entry, duration_hours):
        """Check if a cache entry is still valid"""
        if not cache_entry or "timestamp" not in cache_entry:
            return False

        age = datetime.now() - cache_entry["timestamp"]
        return age < timedelta(hours=duration_hours)

    def record_iv_data(
        self,
        ticker: str,
        implied_volatility: float,
        stock_price: Optional[float] = None,
        option_type: Optional[str] = None,
        expiration: Optional[str] = None,
        dte: Optional[int] = None,
    ):
        """
        Record IV data for a ticker

        Args:
            ticker: Stock ticker symbol
            implied_volatility: IV as decimal (e.g., 0.25 for 25%)
            stock_price: Current stock price
            option_type: CALL or PUT
            expiration: Expiration date
            dte: Days to expiration
        """
        if not self.db:
            return

        try:
            # Normalize IV before saving
            normalized_iv = _normalize_iv(implied_volatility)

            # Save to database
            self.db.save_iv_data(ticker, normalized_iv, stock_price, option_type, expiration, dte)

            # Calculate IV rank
            iv_rank, iv_status = self._calculate_iv_rank(ticker, normalized_iv)

            # Update cache
            self._iv_cache[ticker] = {
                "iv": normalized_iv,
                "timestamp": datetime.now(),
                "iv_rank": iv_rank,
                "iv_status": iv_status,
            }

            logger.debug(f"Recorded IV for {ticker}: {normalized_iv:.2%} (rank: {iv_rank:.1%})")

        except Exception as e:
            logger.error(f"Error recording IV data for {ticker}: {e}")

    def _calculate_iv_rank(self, ticker: str, current_iv: float, days: int = 30) -> tuple:
        """
        Calculate IV Rank: where current IV falls in 30-day range.

        Returns (rank, status) where:
        - rank: float 0-1
        - status: 'normal' | 'unknown'

        Unknown status when current_iv is 0 or missing.
        """
        current_iv = _normalize_iv(current_iv)
        if current_iv <= 0:
            return (0.5, "unknown")

        if not self.db:
            return (0.5, "normal")

        try:
            # Get historical IV data
            history = self.db.get_iv_history(ticker, days)

            if len(history) < 5:  # Need at least 5 data points
                return (0.5, "normal")  # Neutral if insufficient data

            iv_values = [_normalize_iv(record["implied_volatility"]) for record in history]
            iv_values.append(current_iv)  # Include current

            min_iv = min(iv_values)
            max_iv = max(iv_values)

            if max_iv == min_iv:
                return (0.5, "normal")  # Neutral if no range

            iv_rank = (current_iv - min_iv) / (max_iv - min_iv)
            return (max(0.0, min(1.0, iv_rank)), "normal")

        except Exception as e:
            logger.error(f"Error calculating IV rank for {ticker}: {e}")
            return (0.5, "normal")

    def get_iv_environment_score(self, ticker: str, current_iv: float) -> tuple:
        """
        Get IV environment score and metadata

        Args:
            ticker: Stock ticker symbol
            current_iv: Current implied volatility

        Returns:
            tuple: (score_adjustment, iv_rank, status_message)
                score_adjustment: -20 to +20 percentage points
                iv_rank: 0-1 (percentile)
                status_message: 'low', 'neutral', 'high', 'extreme'
        """
        current_iv = _normalize_iv(current_iv)
        # Check cache first
        cache_entry = self._iv_cache.get(ticker)
        if self._is_cache_valid(cache_entry, self._cache_duration_hours):
            iv_rank = cache_entry.get("iv_rank", 0.5)
            iv_status_cached = cache_entry.get("iv_status", "normal")
        else:
            iv_rank, iv_status_cached = self._calculate_iv_rank(ticker, current_iv)
            self._iv_cache[ticker] = {
                "iv": current_iv,
                "timestamp": datetime.now(),
                "iv_rank": iv_rank,
                "iv_status": iv_status_cached,
            }

        # Unknown IV: return neutral score
        if iv_status_cached == "unknown":
            return (0, 0.5, "unknown")

        # Determine score adjustment and status
        if iv_rank < 0.20:
            return (-20, iv_rank, "extreme_low")  # Dangerous - very low IV
        elif iv_rank < 0.30:
            return (-10, iv_rank, "low")  # Low IV warning
        elif iv_rank < 0.40:
            return (-5, iv_rank, "below_avg")  # Slightly below average
        elif iv_rank < 0.60:
            return (0, iv_rank, "neutral")  # Normal range
        elif iv_rank < 0.70:
            return (5, iv_rank, "above_avg")  # Slightly above average
        elif iv_rank < 0.80:
            return (10, iv_rank, "high")  # Good premium environment
        else:
            return (20, iv_rank, "extreme_high")  # Excellent - very high IV

    def _strip_moomoo_prefix(self, ticker: str) -> str:
        return clean_yfinance_ticker(ticker)

    def _earnings_lookup_ticker(self, ticker: str) -> str:
        return earnings_underlying_ticker(ticker)

    def _earnings_data_freshness(self, last_updated: Optional[str]) -> Tuple[bool, Optional[float]]:
        if not last_updated:
            return False, None
        try:
            parsed = datetime.strptime(last_updated, "%Y-%m-%d %H:%M:%S")
            age_hours = (datetime.now() - parsed).total_seconds() / 3600
            return age_hours > self._earnings_cache_duration_hours, round(age_hours, 1)
        except Exception:
            return False, None

    def fetch_earnings_date(self, ticker: str) -> Dict:
        """
        Fetch event data (earnings + upcoming ex-dividend) for a ticker using
        the Alpha Vantage -> yfinance provider chain. Never raises; provider
        failures surface in ``provider_status`` / ``provider_error`` and unknown
        dates stay ``None``.

        Returns dict:
            success, earnings_date, ex_dividend_date, time_of_day,
            fiscal_date_ending, estimate, currency, earnings_source
            ("alpha_vantage"|"yfinance"), provider_status, provider_error, error
        """
        clean_ticker = self._earnings_lookup_ticker(ticker)
        result: Dict = {
            "success": False,
            "earnings_date": None,
            "ex_dividend_date": None,
            "time_of_day": None,
            "fiscal_date_ending": None,
            "estimate": None,
            "currency": None,
            "earnings_source": None,
            "provider_status": None,
            "provider_error": None,
            "error": None,
        }
        if not clean_ticker:
            result["error"] = "No valid ticker to look up"
            result["provider_status"] = self._build_provider_status()
            return result

        # --- Provider 1: Alpha Vantage (bulk calendar; earnings only) ---
        if self._alpha_vantage.available:
            try:
                av_data = self._alpha_vantage.get_earnings(clean_ticker)
                if av_data and av_data.get("reportDate"):
                    report_date = str(av_data["reportDate"])[:10]
                    if "-" in report_date:
                        result.update(
                            {
                                "earnings_date": report_date,
                                "time_of_day": av_data.get("timeOfDay", "") or None,
                                "fiscal_date_ending": av_data.get("fiscalDateEnding", "") or None,
                                "estimate": av_data.get("estimate"),
                                "currency": av_data.get("currency", "") or None,
                                "earnings_source": SOURCE_ALPHA_VANTAGE,
                            }
                        )
            except Exception as e:
                logger.warning(f"Alpha Vantage earnings lookup failed for {clean_ticker}: {e}")
                result["provider_error"] = f"Alpha Vantage lookup failed: {e}"

        # --- Provider 2: yfinance (earnings fallback; ex-dividend source) ---
        # Gentle pacing so yfinance network calls never burst; bounded upstream
        # by the caller (24h per-ticker cache, capped stale refresh).
        yf_ticker = None
        try:
            time.sleep(1)
            yf_ticker = get_yfinance_ticker(clean_ticker)
        except Exception as e:
            logger.warning(f"yfinance Ticker init failed for {clean_ticker}: {e}")
            result["provider_error"] = f"yfinance init failed: {e}"

        if not result["earnings_date"] and yf_ticker is not None:
            try:
                earnings_df = yf_ticker.get_earnings_dates(limit=1)
                ed, parse_err = self._parse_earnings_df_date(earnings_df)
                if ed:
                    result.update({"earnings_date": ed, "earnings_source": SOURCE_YFINANCE})
                elif parse_err:
                    result["provider_error"] = f"yfinance earnings parse failed: {parse_err}"
            except Exception as e:
                logger.warning(f"get_earnings_dates failed for {clean_ticker}: {e}")
                result["provider_error"] = f"yfinance earnings failed: {e}"

        if yf_ticker is not None:
            try:
                calendar = yf_ticker.calendar
                ex_div, _err = self._parse_calendar_ex_dividend(calendar)
                if ex_div:
                    result["ex_dividend_date"] = ex_div
            except Exception as e:
                logger.warning(f"yfinance calendar (ex-dividend) failed for {clean_ticker}: {e}")
                result["provider_error"] = f"yfinance calendar failed: {e}"

        result["provider_status"] = self._build_provider_status()
        result["success"] = bool(result["earnings_date"] or result["ex_dividend_date"])
        result["error"] = None if result["success"] else "No data from Alpha Vantage or yfinance"
        return result

    def update_earnings_data(self, ticker: str) -> bool:
        """
        Update earnings + ex-dividend data for a ticker

        Args:
            ticker: Stock ticker symbol

        Returns:
            bool: True if any event data was stored
        """
        if not self.db:
            logger.warning("No database connection for earnings update")
            return False

        try:
            normalized_ticker = self._earnings_lookup_ticker(ticker)
            if not normalized_ticker:
                logger.warning("Skipping empty earnings ticker input")
                return False

            result = self.fetch_earnings_date(normalized_ticker)

            if result["success"]:
                self.db.save_earnings_date(
                    normalized_ticker,
                    result["earnings_date"],
                    fetch_status="success",
                    time_of_day=result.get("time_of_day"),
                    fiscal_date_ending=result.get("fiscal_date_ending"),
                    estimate=result.get("estimate"),
                    currency=result.get("currency"),
                    earnings_source=result.get("earnings_source"),
                    ex_dividend_date=result.get("ex_dividend_date"),
                )

                # Update cache, including last_updated so freshness is honest
                # on later cache hits (regression: cache entries previously
                # omitted last_updated, making data_stale always False).
                now = datetime.now()
                self._earnings_cache[normalized_ticker] = {
                    "normalized_ticker": normalized_ticker,
                    "earnings_date": result["earnings_date"],
                    "ex_dividend_date": result.get("ex_dividend_date"),
                    "time_of_day": result.get("time_of_day"),
                    "fiscal_date_ending": result.get("fiscal_date_ending"),
                    "estimate": result.get("estimate"),
                    "currency": result.get("currency"),
                    "earnings_source": result.get("earnings_source"),
                    "provider_status": result.get("provider_status"),
                    "provider_error": result.get("provider_error"),
                    "last_updated": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "timestamp": now,
                }

                logger.info(
                    "Updated earnings for %s: %s (%s) ex-div=%s",
                    ticker,
                    result["earnings_date"],
                    result.get("earnings_source", "?"),
                    result.get("ex_dividend_date"),
                )
                return True
            else:
                self.db.mark_earnings_error(
                    normalized_ticker,
                    error_message=result.get("error") or "No data from Alpha Vantage or yfinance",
                    earnings_source=result.get("earnings_source"),
                )
                return False

        except Exception as e:
            logger.error(f"Error updating earnings for {ticker}: {e}")
            return False

    def get_earnings_info(self, ticker: str) -> Dict:
        """
        Get earnings information for a ticker

        Args:
            ticker: Stock ticker symbol

        Returns:
            dict: {
                'earnings_date': str or None,
                'ex_dividend_date': str or None,
                'days_to_earnings': int or None,
                'warning_level': str ('none', 'soon', 'very_soon', 'today', 'error'),
                'fetch_status': str,
                'error_message': str or None,
                'time_of_day': str or None,
                'fiscal_date_ending': str or None,
                'estimate': float or None,
                'currency': str or None,
                'earnings_source': 'alpha_vantage'|'yfinance'|None,
                'ex_dividend_source': 'yfinance'|None,
                'data_stale': bool,
                'data_age_hours': float or None,
                'provider_status': dict,
                'provider_error': str or None,
            }
        """
        normalized_ticker = self._earnings_lookup_ticker(ticker)
        cache_entry = self._earnings_cache.get(normalized_ticker)
        if self._is_cache_valid(cache_entry, self._earnings_cache_duration_hours):
            earnings_date = cache_entry.get("earnings_date")
            ex_dividend_date = cache_entry.get("ex_dividend_date")
            time_of_day = cache_entry.get("time_of_day")
            fiscal_date_ending = cache_entry.get("fiscal_date_ending")
            estimate = cache_entry.get("estimate")
            currency = cache_entry.get("currency")
            earnings_source = cache_entry.get("earnings_source")
            provider_error = cache_entry.get("provider_error")
            last_updated = cache_entry.get("last_updated")
        elif self.db:
            record = self.db.get_earnings_date(normalized_ticker)
            if record:
                earnings_date = record.get("earnings_date")
                ex_dividend_date = record.get("ex_dividend_date")
                time_of_day = record.get("time_of_day")
                fiscal_date_ending = record.get("fiscal_date_ending")
                estimate = record.get("estimate")
                currency = record.get("currency")
                earnings_source = self._normalize_source(record.get("earnings_source"))
                provider_error = record.get("error_message")
                last_updated = record.get("last_updated")
                self._earnings_cache[normalized_ticker] = {
                    "earnings_date": earnings_date,
                    "ex_dividend_date": ex_dividend_date,
                    "time_of_day": time_of_day,
                    "fiscal_date_ending": fiscal_date_ending,
                    "estimate": estimate,
                    "currency": currency,
                    "earnings_source": earnings_source,
                    "provider_error": provider_error,
                    "last_updated": last_updated,
                    "timestamp": datetime.now(),
                }
            else:
                (
                    earnings_date,
                    ex_dividend_date,
                    time_of_day,
                    fiscal_date_ending,
                    estimate,
                    currency,
                    earnings_source,
                    provider_error,
                    last_updated,
                ) = (None, None, None, None, None, None, None, None, None)
        else:
            (
                earnings_date,
                ex_dividend_date,
                time_of_day,
                fiscal_date_ending,
                estimate,
                currency,
                earnings_source,
                provider_error,
                last_updated,
            ) = (None, None, None, None, None, None, None, None, None)

        data_stale, data_age_hours = self._earnings_data_freshness(last_updated)

        if not earnings_date:
            return {
                "earnings_date": None,
                "ex_dividend_date": ex_dividend_date,
                "ex_dividend_source": SOURCE_YFINANCE if ex_dividend_date else None,
                "days_to_earnings": None,
                "warning_level": "none",
                "fetch_status": "pending" if not self.db else "unknown",
                "error_message": None,
                "time_of_day": None,
                "fiscal_date_ending": None,
                "estimate": None,
                "currency": None,
                "earnings_source": None,
                "data_stale": data_stale,
                "data_age_hours": data_age_hours,
                "provider_status": self._build_provider_status(),
                "provider_error": provider_error,
            }

        try:
            earnings_dt = datetime.strptime(earnings_date, "%Y-%m-%d")
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            days_to_earnings = (earnings_dt - today).days

            if days_to_earnings < 0:
                warning_level = "past"
            elif days_to_earnings == 0:
                warning_level = "today"
            elif days_to_earnings <= 3:
                warning_level = "very_soon"
            elif days_to_earnings <= 7:
                warning_level = "soon"
            elif days_to_earnings <= 30:
                warning_level = "upcoming"
            else:
                warning_level = "none"

            return {
                "earnings_date": earnings_date,
                "ex_dividend_date": ex_dividend_date,
                "ex_dividend_source": SOURCE_YFINANCE if ex_dividend_date else None,
                "days_to_earnings": days_to_earnings,
                "warning_level": warning_level,
                "fetch_status": "success",
                "error_message": None,
                "time_of_day": time_of_day,
                "fiscal_date_ending": fiscal_date_ending,
                "estimate": estimate,
                "currency": currency,
                "earnings_source": earnings_source,
                "data_stale": data_stale,
                "data_age_hours": data_age_hours,
                "provider_status": self._build_provider_status(),
                "provider_error": provider_error,
            }

        except Exception as e:
            return {
                "earnings_date": earnings_date,
                "ex_dividend_date": ex_dividend_date,
                "ex_dividend_source": SOURCE_YFINANCE if ex_dividend_date else None,
                "days_to_earnings": None,
                "warning_level": "error",
                "fetch_status": "error",
                "error_message": str(e),
                "time_of_day": time_of_day,
                "fiscal_date_ending": fiscal_date_ending,
                "estimate": estimate,
                "currency": currency,
                "earnings_source": earnings_source,
                "data_stale": data_stale,
                "data_age_hours": data_age_hours,
                "provider_status": self._build_provider_status(),
                "provider_error": provider_error,
            }

    def get_earnings_score_impact(self, ticker: str) -> tuple:
        """
        Get earnings-based score adjustment

        Args:
            ticker: Stock ticker symbol

        Returns:
            tuple: (score_adjustment, warning_message)
                score_adjustment: -30 to 0
                warning_message: str or None
        """
        info = self.get_earnings_info(ticker)

        source_tag = ""
        if info.get("earnings_source") or info.get("time_of_day"):
            parts = [p for p in [info.get("time_of_day"), info.get("earnings_source")] if p]
            source_tag = f" [{' · '.join(parts)}]"

        if info["warning_level"] == "today":
            return (-30, f"Earnings today - extreme risk{source_tag}")
        elif info["warning_level"] == "very_soon":
            return (-15, f"Earnings in {info['days_to_earnings']} days - high risk{source_tag}")
        elif info["warning_level"] == "soon":
            return (-5, f"Earnings in {info['days_to_earnings']} days - caution{source_tag}")
        elif info["warning_level"] == "error":
            return (0, "Failed to fetch earnings data")
        else:
            return (0, None)

    def refresh_stale_event_context(
        self,
        tickers: List[str],
        max_tickers: Optional[int] = None,
        hours: Optional[int] = None,
        max_workers: int = 2,
    ) -> Dict:
        """Best-effort refresh of event context (earnings + ex-dividend) that is
        missing, errored, or older than ``hours`` (default: the 24h per-ticker
        cache window). Bounded to ``max_tickers`` (default 20) with low
        concurrency. Never raises — used ahead of a broker scan so freshness is
        improved without ever blocking or aborting it.
        """
        hours = self._earnings_cache_duration_hours if hours is None else int(hours)
        if max_tickers is None:
            max_tickers = self._max_stale_event_tickers
        empty = {"refreshed": 0, "stale": 0, "skipped": 0, "errors": 0, "provider": self._alpha_vantage.get_status()}
        if not tickers:
            return empty

        normalized: List[str] = []
        seen = set()
        for ticker in tickers:
            n = self._earnings_lookup_ticker(ticker)
            if n and n not in seen:
                seen.add(n)
                normalized.append(n)
        if not normalized:
            return empty

        existing = {}
        if self.db:
            try:
                for row in self.db.get_all_earnings_dates() or []:
                    existing[row["ticker"]] = row
            except Exception as exc:
                # Best-effort contract: a failing DB read must never block or
                # abort the broker scan that called this refresher.
                logger.warning("Could not read stored earnings context: %s", exc)
                return empty

        stale: List[str] = []
        now = datetime.now()
        for n in normalized:
            row = existing.get(n)
            if row is None or (row.get("fetch_status") or "") in ("error", "pending"):
                stale.append(n)
                continue
            parsed = None
            try:
                parsed = datetime.strptime(str(row.get("last_updated") or ""), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                parsed = None
            if parsed is None or now - parsed > timedelta(hours=hours):
                stale.append(n)

        stale = stale[: int(max_tickers)]
        if not stale:
            return {
                "refreshed": 0,
                "stale": 0,
                "skipped": len(normalized),
                "errors": 0,
                "provider": self._alpha_vantage.get_status(),
            }

        refreshed = 0
        failed = 0
        workers = max(1, min(int(max_workers), len(stale)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {executor.submit(self.update_earnings_data, n): n for n in stale}
            for future in as_completed(future_map):
                try:
                    if future.result():
                        refreshed += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1

        logger.info(
            "Stale event context refreshed: %d updated, %d failed (of %d stale, %d skipped)",
            refreshed,
            failed,
            len(stale),
            len(normalized) - len(stale),
        )
        return {
            "refreshed": refreshed,
            "stale": len(stale),
            "skipped": len(normalized) - len(stale),
            "errors": failed,
            "provider": self._alpha_vantage.get_status(),
        }

    def batch_update_earnings(self, tickers: List[str], max_tickers: Optional[int] = None) -> Dict:
        """
        Update earnings + ex-dividend data for multiple tickers

        Args:
            tickers: List of ticker symbols
            max_tickers: Optional cap on how many are fetched this call

        Returns:
            dict: {'successful': int, 'failed': int, 'errors': List[str]}
        """
        successful = 0
        failed = 0
        errors = []
        normalized_tickers = []
        seen = set()

        if not tickers:
            return {
                "successful": 0,
                "failed": 0,
                "errors": [],
                "status": "empty",
            }

        for ticker in tickers:
            normalized = self._earnings_lookup_ticker(ticker)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            normalized_tickers.append(normalized)

        if not normalized_tickers:
            return {
                "successful": 0,
                "failed": 0,
                "errors": [],
                "status": "empty",
            }

        if max_tickers is not None:
            normalized_tickers = normalized_tickers[: int(max_tickers)]

        max_workers = min(8, max(1, len(normalized_tickers)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(self.update_earnings_data, ticker): ticker for ticker in normalized_tickers}
            for future in as_completed(future_map):
                ticker = future_map[future]
                try:
                    if future.result():
                        successful += 1
                    else:
                        failed += 1
                        errors.append(f"{ticker}: Failed to fetch earnings")
                except Exception as e:
                    failed += 1
                    errors.append(f"{ticker}: {e}")

        status = "success" if failed == 0 else "partial" if successful > 0 else "failed"
        if errors:
            logger.warning(
                "Batch earnings update completed with %d success, %d failures: %s",
                successful,
                failed,
                errors[:5],
            )
        else:
            logger.info(f"Batch earnings update complete: {successful} successful, {failed} failed")

        return {
            "successful": successful,
            "failed": failed,
            "errors": errors,
            "status": status,
        }

    def purge_old_data(self):
        """Purge old IV history data"""
        if self.db:
            deleted = self.db.purge_old_iv_data(days=45)
            logger.info(f"Purged {deleted} old IV history records")

    def get_cache_stats(self) -> Dict:
        """Get cache statistics for monitoring"""
        return {
            "iv_cache_entries": len(self._iv_cache),
            "earnings_cache_entries": len(self._earnings_cache),
            "iv_cache_valid": sum(
                1 for entry in self._iv_cache.values() if self._is_cache_valid(entry, self._cache_duration_hours)
            ),
            "earnings_cache_valid": sum(
                1
                for entry in self._earnings_cache.values()
                if self._is_cache_valid(entry, self._earnings_cache_duration_hours)
            ),
        }

    def get_provider_status(self) -> Dict:
        """UI-facing provider health: Alpha Vantage (incl. missing-key/quota/error)
        plus local cache dimensions."""
        av = self._alpha_vantage.get_status()
        return {
            "alpha_vantage": {
                "available": bool(av.get("available")),
                "status": av.get("status"),
                "error": av.get("error"),
                "daily_allowance": av.get("daily_allowance", ALPHA_VANTAGE_FREE_DAILY_ALLOWANCE),
                "requests_today": av.get("requests_today", 0),
                "last_attempt_at": av.get("last_attempt_at"),
                "last_success_at": av.get("last_success_at"),
                "cache_entries": av.get("cache_entries", 0),
                "cache_age_hours": av.get("cache_age_hours"),
            },
            "yfinance": {
                "available": True,
                "note": "per-ticker informational; cached 24h",
            },
            "cache": self.get_cache_stats(),
        }
