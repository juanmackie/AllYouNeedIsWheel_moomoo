"""
Catalyst Flow Service - scans watchlist options chains for unusual activity.

Read-only service that surfaces research signals only.
Reuses Moomoo connection for option chain data.
"""

import logging
import time
from datetime import datetime

from core.catalyst_flow_decision import ACTION_BUCKETS, classify_catalyst_flow
from core.ticker_utils import canonical_underlying
from api.services.signal_overlay_service import apply_signal_overlay, fetch_signal_overlay_map

logger = logging.getLogger("api.services.catalyst_flow")


class CatalystFlowService:
    """Self-contained scanner for anomalous options flow."""

    _shared_cache = {}
    _shared_cache_ttl = 1800  # 30 minutes

    def __init__(self, config_provider=None, watchlist_provider=None):
        self._config_provider = config_provider
        self._watchlist_provider = watchlist_provider
        self.connection = None
        self._apewisdom_service = None

    @property
    def config(self):
        if hasattr(self._config_provider, "config"):
            return self._config_provider.config
        return self._config_provider or {}

    def _get_catalyst_config(self):
        return self.config.get("catalyst_flow", {})

    def _ensure_connection(self):
        if self.connection is not None and self.connection.is_connected():
            return self.connection
        from core.connection import MoomooConnection
        host = str(self.config.get("host", "127.0.0.1"))
        port = int(self.config.get("port", 11111))
        self.connection = MoomooConnection(
            host=host,
            port=port,
            readonly=bool(self.config.get("readonly", True)),
            account_id=self.config.get("account_id"),
            portfolio_env=self.config.get("portfolio_env"),
            security_firm=self.config.get("security_firm"),
            broker_cache_after_hours=self.config.get("broker_cache_after_hours", True),
        )
        if self.connection.connect():
            return self.connection
        logger.error("CatalystFlowService: failed to connect to Moomoo")
        return None

    def _get_effective_watchlist(self):
        manager = self._watchlist_provider
        if manager is None:
            from api.services.watchlist_manager import WatchlistManager
            manager = WatchlistManager(config_provider=self._config_provider)
        return manager.get_effective_watchlist(
            growth_mode_config=self.config.get("growth_mode", {})
        )

    def _get_apewisdom_service(self):
        if self._apewisdom_service is None:
            from api.services.apewisdom_service import ApeWisdomService
            self._apewisdom_service = ApeWisdomService(
                config=self._get_catalyst_config().get("apewisdom", {})
            )
        return self._apewisdom_service

    def get_signals(
        self,
        tickers=None,
        limit=6,
        refresh=False,
        min_premium_notional=None,
        min_volume=None,
        min_fresh_volume_ratio=None,
        max_scan_tickers=None,
        max_expirations=None,
    ):
        """
        Scan watchlist for anomalous options flow.

        Args:
            tickers: Optional list to override watchlist.
            limit: Max signals to return.
            refresh: Force bypass cache.
            min_premium_notional: Override config premium threshold.
            min_volume: Override config volume threshold.
            min_fresh_volume_ratio: Override config fresh volume/OI threshold.
            max_scan_tickers: Override config ticker scan cap.
            max_expirations: Override config expiration scan cap.

        Returns:
            dict: { success, generated_at, count, signals, scanned, elapsed_seconds, thresholds }
        """
        start_ts = time.time()
        conn = self._ensure_connection()
        if not conn:
            return {"success": False, "error": "Failed to connect to Moomoo", "signals": [], "count": 0}

        cfg = self._get_catalyst_config()
        if min_premium_notional is not None:
            scan_config = dict(cfg)
            scan_config["min_premium_notional"] = min_premium_notional
        else:
            scan_config = dict(cfg)
        if min_volume is not None:
            scan_config["min_volume"] = min_volume
        if min_fresh_volume_ratio is not None:
            scan_config["min_fresh_volume_ratio"] = min_fresh_volume_ratio
        if max_scan_tickers is not None:
            scan_config["max_scan_tickers"] = max_scan_tickers
        if max_expirations is not None:
            scan_config["max_expirations"] = max_expirations

        watchlist = tickers or self._get_effective_watchlist()
        limit = max(1, min(int(limit or 6), 20))
        scan_limit = int(scan_config.get("max_scan_tickers", max(limit * 4, 25)))
        scan_tickers = watchlist[: max(1, min(len(watchlist), scan_limit))]

        # -- Expand candidates with Ape Wisdom social momentum --#
        aw_cfg = cfg.get("apewisdom", {})
        aw_metadata = {"enabled": False, "candidates_fetched": 0, "boost_tickers_applied": []}
        social_index: dict[str, dict] = {}
        if aw_cfg.get("enabled", True):
            try:
                aw_svc = self._get_apewisdom_service()
                aw_candidates = aw_svc.get_momentum_candidates()
                aw_metadata["enabled"] = True
                aw_metadata["candidates_fetched"] = len(aw_candidates)
                seen = {canonical_underlying(t) for t in scan_tickers}
                scan_cap = scan_limit + int(aw_cfg.get("max_boost_tickers", 8))
                boost_tickers_applied = []
                for c in aw_candidates:
                    canon = canonical_underlying(c["ticker"])
                    social_index[canon] = c
                    if canon not in seen and len(scan_tickers) < scan_cap:
                        scan_tickers.append(c["ticker"])
                        seen.add(canon)
                        boost_tickers_applied.append(c["ticker"])
                aw_metadata["boost_tickers_applied"] = boost_tickers_applied
            except Exception as exc:
                logger.debug("ApeWisdom expansion failed (non-fatal): %s", exc)

        all_signals = []
        scanned = 0
        errors = []
        cache_hits = 0
        tickers_scanned = []
        candidate_count = 0
        rejected_by_threshold_count = 0

        for ticker in scan_tickers:
            cached = CatalystFlowService._shared_cache.get(ticker)
            if cached:
                cache_age = time.time() - cached["ts"]
                if cache_age < CatalystFlowService._shared_cache_ttl:
                    if refresh:
                        logger.debug("Catalyst refresh kept fresh cache for %s (age %.0fs)", ticker, cache_age)
                    if cached["signals"]:
                        all_signals.extend(cached["signals"])
                    cache_hits += 1
                    continue

            try:
                result = self._scan_ticker(conn, ticker, scan_config)
                scanned += 1
                tickers_scanned.append(ticker)
                if not result:
                    rejected_by_threshold_count += 1
            except Exception as exc:
                logger.debug("Catalyst scan error %s: %s", ticker, exc)
                errors.append(ticker)
                result = None

            CatalystFlowService._shared_cache[ticker] = {"ts": time.time(), "signals": result or []}
            if result:
                all_signals.extend(result)

        # -- Attach social context to confirmed signals --#
        if social_index:
            for item in all_signals:
                canon = canonical_underlying(item.get("ticker", ""))
                if canon in social_index:
                    se = social_index[canon]
                    item["social"] = {
                        "source": "apewisdom",
                        "rank": se.get("rank", 0),
                        "rank_24h_ago": se.get("rank_24h_ago", 0),
                        "mentions": se.get("mentions", 0),
                        "mentions_24h_ago": se.get("mentions_24h_ago", 0),
                        "upvotes": se.get("upvotes", 0),
                        "momentum_score": se.get("momentum_score", 0),
                    }
                    item.setdefault("rationale", []).append(
                        f"Scan source: social momentum ({se.get('mentions', 0)} mentions)"
                    )

        # -- Ticker-level conflict detection --#
        # Group by canonical underlying, then check for meaningful opposite sides.
        # Meaningful = not REJECT, not SPECULATIVE_ONLY, score >= 30.
        MEANINGFUL_BUCKETS = {"CALL_RESEARCH", "PUT_RESEARCH"}
        ticker_sides: dict[str, dict[str, list]] = {}
        for item in all_signals:
            canon = canonical_underlying(item.get("ticker", ""))
            side = item.get("side", "")
            ticker_sides.setdefault(canon, {}).setdefault(side, []).append(item)

        # Snapshot meaningful sides per canonical ticker before any mutation.
        ticker_meaningful: dict[str, set[str]] = {}
        for canon, sides in ticker_sides.items():
            meaningful = set()
            for side_key, side_signals in sides.items():
                for s in side_signals:
                    if s.get("action_bucket") in MEANINGFUL_BUCKETS:
                        meaningful.add(side_key)
                        break
            ticker_meaningful[canon] = meaningful

        for item in all_signals:
            canon = canonical_underlying(item.get("ticker", ""))
            sides = ticker_sides.get(canon, {})
            meaningful = ticker_meaningful.get(canon, set())
            if len(sides) <= 1 or len(meaningful) <= 1:
                continue

            # Both sides have meaningful signals — check dominance
            call_top = max(
                (s.get("score", 0) for s in sides.get("CALL", [])),
                default=0,
            )
            put_top = max(
                (s.get("score", 0) for s in sides.get("PUT", [])),
                default=0,
            )
            dominant = max(call_top, put_top)
            recessive = min(call_top, put_top)
            is_dominant = dominant >= recessive * 1.5 and dominant >= 50

            bucket = item.get("action_bucket")
            if is_dominant and bucket in MEANINGFUL_BUCKETS:
                dominant_side = "CALL" if call_top >= put_top else "PUT"
                if item.get("side") == dominant_side:
                    continue  # dominant side keeps its bucket
                else:
                    item["action_bucket"] = "WATCH"
                    item["action_label"] = ACTION_BUCKETS["WATCH"]
                    item["action_reason"] = (
                        f"Opposing {item.get('side', '').lower()} flow exists but "
                        f"{dominant_side.lower()} dominates on {canon}"
                    )
            elif bucket in MEANINGFUL_BUCKETS:
                item["action_bucket"] = "CONFLICT_WATCH"
                item["action_label"] = ACTION_BUCKETS["CONFLICT_WATCH"]
                item["action_reason"] = (
                    f"Both bullish and bearish flow on {canon} — "
                    "conflicting signals, watch only"
                )
                item["blockers"] = list(set(
                    item.get("blockers", []) + ["Conflicting directional flow on same ticker"]
                ))

        # Attach the shared overlay after catalyst classification so the
        # card can show capital/technical/derivatives context without
        # mutating the underlying flow score.
        try:
            overlay_tickers = []
            seen_overlay_tickers = set()
            for item in all_signals:
                ticker = canonical_underlying(item.get("ticker", ""))
                if ticker and ticker not in seen_overlay_tickers:
                    seen_overlay_tickers.add(ticker)
                    overlay_tickers.append(ticker)
            overlay_map = fetch_signal_overlay_map(overlay_tickers)
            all_signals = [apply_signal_overlay(item, overlay_map.get(canonical_underlying(item.get("ticker", "")), {})) for item in all_signals]
        except Exception as exc:
            logger.debug("Catalyst overlay attachment skipped: %s", exc)

        all_signals.sort(key=lambda s: s.get("score", 0), reverse=True)
        candidate_count = len(all_signals)

        return {
            "success": True,
            "generated_at": datetime.now().isoformat(),
            "count": min(len(all_signals), limit),
            "signals": all_signals[:limit],
            "scanned": scanned,
            "cache_hits": cache_hits,
            "errors": errors,
            "tickers_scanned": tickers_scanned,
            "candidate_count": candidate_count,
            "rejected_by_threshold_count": rejected_by_threshold_count,
            "research_only": True,
            "elapsed_seconds": round(time.time() - start_ts, 1),
            "thresholds": {
                "min_premium_notional": scan_config.get("min_premium_notional", 1_000_000),
                "min_fresh_volume_ratio": scan_config.get("min_fresh_volume_ratio", 5),
                "min_volume": scan_config.get("min_volume", 500),
                "max_expirations": scan_config.get("max_expirations", 3),
                "max_dte": scan_config.get("max_dte", 60),
                "max_scan_tickers": scan_config.get("max_scan_tickers", max(limit * 4, 25)),
            },
            "apewisdom": aw_metadata,
        }

    def _scan_ticker(self, conn, ticker, config):
        """Fetch option chains and classify catalyst flow for one ticker."""
        from moomoo import RET_OK

        stock_price = conn.get_stock_price(ticker)
        stock_price_source = "moomoo"
        if stock_price is None or stock_price <= 0:
            price = self._get_fallback_price(ticker)
            if not price:
                return None
            stock_price = price
            stock_price_source = "yfinance"

        ret, data = conn.get_option_expiration_dates(ticker)
        if ret != RET_OK or data is None:
            return None

        exp_column = "expiration_date"
        if exp_column not in data.columns:
            if "strike_time" in data.columns:
                exp_column = "strike_time"
            elif "option_expiry_date" in data.columns:
                exp_column = "option_expiry_date"
            else:
                return None

        today = datetime.now().date()
        max_dte = int(config.get("max_dte", 60))
        valid_exps = []
        for raw in data[exp_column].tolist():
            exp_str = str(raw).replace("-", "")
            try:
                exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
                dte = (exp_date - today).days
                if dte <= 0:
                    continue
                if dte > max_dte:
                    continue
                valid_exps.append((dte, exp_str))
            except ValueError:
                continue

        valid_exps.sort(key=lambda x: x[0])
        valid_exps = [v for _, v in valid_exps]

        if not valid_exps:
            return None

        max_exp = min(len(valid_exps), int(config.get("max_expirations", 3)))
        option_list = []

        for exp_str in valid_exps[:max_exp]:
            for right in ("C", "P"):
                chain = conn.get_option_chain(ticker, exp_str, right, target_strike=None)
                if chain and chain.get("options"):
                    side = "CALL" if right == "C" else "PUT"
                    for opt in chain["options"]:
                        normalized = dict(opt)
                        normalized["option_type"] = side
                        normalized["expiration"] = normalized.get("expiration") or exp_str
                        option_list.append(normalized)

        if not option_list:
            return None

        from db.database import OptionsDatabase
        db_path = self.config.get("db_path", "options.db")
        from api.services.iv_earnings_service import IVEarningsService
        try:
            db_earn = OptionsDatabase(db_path)
            iv_svc = IVEarningsService(db_earn)
            earnings_info = iv_svc.get_earnings_info(ticker)
        except Exception:
            earnings_info = {}

        scan_config = dict(config)
        scan_config["scanned_expirations"] = max_exp

        signals = classify_catalyst_flow(
            ticker=ticker,
            stock_price=stock_price,
            option_list=option_list,
            earnings_info=earnings_info,
            config=scan_config,
        )

        if not signals:
            return None

        normalized_signals = []
        for signal in signals:
            item = signal.to_dict()
            item["stock_price_source"] = stock_price_source
            normalized_signals.append(item)
        return normalized_signals

    def get_ticker_warnings(self, ticker: str) -> list[str]:
        """Return defensive warnings from shared cache only — never triggers broker calls.

        Only warns for clean directional signals (CALL_RESEARCH / PUT_RESEARCH).
        Conflict, speculative, rejected, and watch signals do not generate
        lane-specific warnings.
        """
        CLEAN_BUCKETS = {"CALL_RESEARCH", "PUT_RESEARCH"}
        cached = CatalystFlowService._shared_cache.get(ticker)
        if not cached or not cached.get("signals"):
            return []
        if time.time() - cached.get("ts", 0) > CatalystFlowService._shared_cache_ttl:
            return []
        warnings = []
        for sig in cached["signals"]:
            if sig.get("action_bucket") not in CLEAN_BUCKETS:
                continue
            side = sig.get("side")
            if side == "CALL" and not sig.get("is_hedged"):
                warnings.append("Bullish call flow detected \u2014 upside cap risk for covered calls")
            elif side == "PUT" and not sig.get("is_hedged"):
                warnings.append("Bearish put flow detected \u2014 tail risk for CSPs")
        return warnings

    def _get_fallback_price(self, ticker):
        """Get stock price from yfinance as fallback."""
        try:
            from api.services.utils import clean_yfinance_ticker, get_yfinance_ticker
            bare = clean_yfinance_ticker(ticker)
            hist = get_yfinance_ticker(bare).history(period="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            logger.warning("YFinance price fallback failed for %s", ticker, exc_info=True)
        return None
