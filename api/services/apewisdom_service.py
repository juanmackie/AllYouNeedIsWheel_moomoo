"""
Ape Wisdom social momentum service.

Fetches trending stock mentions from Reddit/4chan via Ape Wisdom API
and returns ranked candidates for catalyst scan expansion.
Social data alone never produces a signal — it only widens which
tickers get scanned for unusual options flow.
"""

import logging
import threading
import time

logger = logging.getLogger("api.services.apewisdom")

_APEWISEDOM_BASE = "https://apewisdom.io/api/v1.0/filter"
_DEFAULT_TIMEOUT = 10


class ApeWisdomService:
    """Lightweight client for Ape Wisdom social momentum data."""

    def __init__(self, config=None):
        self._cfg = config or {}
        self._cache = None
        self._cache_ts = 0.0
        self._lock = threading.Lock()

    @property
    def _cache_ttl(self) -> int:
        return int(self._cfg.get("cache_ttl", 300))

    @property
    def _filter_name(self) -> str:
        return str(self._cfg.get("filter", "all-stocks"))

    @property
    def _max_boost_tickers(self) -> int:
        return int(self._cfg.get("max_boost_tickers", 8))

    @property
    def _min_mentions(self) -> int:
        return int(self._cfg.get("min_mentions", 5))

    @property
    def _exclude_tickers(self) -> set:
        raw = self._cfg.get("exclude_tickers", [])
        return {t.upper().strip() for t in raw if isinstance(t, str)}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_momentum_candidates(self) -> list[dict]:
        """
        Return top momentum candidates sorted by momentum_score descending.

        Returns an empty list if the API is unavailable, disabled, or returns
        no qualifying results.  Never raises.
        """
        if not self._cfg.get("enabled", True):
            return []

        now = time.time()
        with self._lock:
            if self._cache is not None and (now - self._cache_ts) < self._cache_ttl:
                return list(self._cache)

        raw = self.fetch_all_stocks(page=1)
        if raw is None:
            return []

        try:
            entries = self._parse_results(raw)
            filtered = self._apply_filters(entries)
            scored = self._score_and_rank(filtered)
            result = scored[: self._max_boost_tickers]
        except Exception as exc:
            logger.warning("ApeWisdom momentum fetch failed: %s", exc)
            result = []

        with self._lock:
            self._cache = result
            self._cache_ts = time.time()

        return list(result)

    def fetch_all_stocks(self, page: int = 1) -> dict | None:
        """
        Fetch the Ape Wisdom all-stocks list for a given page.

        Returns the parsed JSON dict or ``{}`` on failure.
        """
        import requests

        url = f"{_APEWISEDOM_BASE}/{self._filter_name}/page/{page}"
        try:
            resp = requests.get(
                url,
                timeout=_DEFAULT_TIMEOUT,
                headers={"User-Agent": "AllYouNeedIsWheel/1.0 (catalyst-watch)"},
            )
            if resp.status_code == 200:
                return resp.json() if resp.text else {}
            logger.warning(
                "ApeWisdom HTTP %d from %s", resp.status_code, url
            )
            return None
        except Exception as exc:
            logger.warning("ApeWisdom request failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_entry(raw: dict) -> dict:
        """Normalize a single Ape Wisdom result entry."""
        return {
            "ticker": str(raw.get("ticker", "")).strip().upper(),
            "name": str(raw.get("name", "")).strip(),
            "rank": ApeWisdomService._safe_int(raw.get("rank")),
            "mentions": ApeWisdomService._safe_int(raw.get("mentions")),
            "upvotes": ApeWisdomService._safe_int(raw.get("upvotes")),
            "rank_24h_ago": ApeWisdomService._safe_int(raw.get("rank_24h_ago")),
            "mentions_24h_ago": ApeWisdomService._safe_int(
                raw.get("mentions_24h_ago")
            ),
        }

    def _parse_results(self, raw: dict) -> list[dict]:
        """Parse the API response into a list of normalized entries."""
        results = raw.get("results") or []
        if not isinstance(results, list):
            return []
        return [self._parse_entry(r) for r in results if isinstance(r, dict)]

    # ------------------------------------------------------------------
    # Filtering & scoring
    # ------------------------------------------------------------------

    def _apply_filters(self, entries: list[dict]) -> list[dict]:
        """Apply min_mentions and exclude_tickers filters."""
        excluded = self._exclude_tickers
        min_mentions = self._min_mentions
        return [
            e
            for e in entries
            if e["ticker"]
            and e["mentions"] >= min_mentions
            and e["ticker"] not in excluded
        ]

    @staticmethod
    def _compute_momentum(entry: dict) -> float:
        """
        Simple momentum score: current mentions weighted by growth ratio.

        ``mentions * (1 + mentions / max(mentions_24h_ago, 1))``

        A ticker with 100 mentions today and 10 yesterday scores higher
        than one with 100 mentions today and 100 yesterday.
        """
        mentions = entry["mentions"]
        mentions_prev = max(entry["mentions_24h_ago"], 1)
        return round(mentions * (1 + mentions / mentions_prev), 2)

    def _score_and_rank(self, entries: list[dict]) -> list[dict]:
        """Attach momentum_score and sort descending."""
        for e in entries:
            e["momentum_score"] = self._compute_momentum(e)
        entries.sort(key=lambda e: e["momentum_score"], reverse=True)
        return entries
