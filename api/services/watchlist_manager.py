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

# Group states surfaced by get_scan_universe(). Distinct and stable string
# tokens so the UI can badge each failure mode separately.
GROUP_STATUS_OK = "ok"
GROUP_STATUS_MISSING = "missing_group"
GROUP_STATUS_EMPTY = "empty_group"
GROUP_STATUS_CONNECTION = "connection_failed"

# Known non-US market prefixes on Moomoo codes. A code carrying one of these
# is never silently converted into a US ticker — it is reported as unsupported
# with an explicit reason. Bare codes and US.-prefixed codes are US listings.
KNOWN_NON_US_PREFIXES = {"HK", "SZ", "SH", "SS", "SG", "JP", "TW", "UK", "DE", "FR", "IT", "CA", "AU", "NZ"}


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
                from core.connection_manager import MoomooConnection

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

    # -- scan universe (signed-in OpenD session's My Watchlist group) ---------

    @staticmethod
    def _empty_status(status: str, explanation: str, groups_available=None, group_name="", fetched_at=""):
        return {
            "status": status,
            "group_name": group_name or "",
            "explanation": explanation or "",
            "groups_available": list(groups_available or []),
            "tickers": [],
            "raw_codes": {},
            "unsupported": [],
            "fetched_at": fetched_at or "",
        }

    def _classify_symbol(self, code: str):
        """Classify a raw Moomoo watchlist code into a scanner symbol entry.

        Never silently converts a non-US listing into a US ticker. Returns
        (kind, canonical, entry) where kind is "us" or "unsupported".
        ``canonical`` is the bare US ticker for kind "us" and ``None``
        otherwise, and ``entry`` is the per-ticker dict to merge into the
        scan universe (``raw_codes`` / ``unsupported``).
        """
        from core.ticker_utils import canonical_underlying

        raw = str(code or "").strip()
        if not raw:
            return None, None, None
        if "." in raw:
            market, _, rest = raw.partition(".")
            market = market.upper()
            if market == "US":
                canonical = canonical_underlying(raw)
                if not canonical:
                    return "skip", None, None
                return "us", canonical, {"raw": raw, "canonical": canonical}
            if market in KNOWN_NON_US_PREFIXES:
                return (
                    "unsupported",
                    None,
                    {
                        "symbol": raw,
                        "reason": f"non-US listing {raw} is not scanned — only US-listed securities are eligible for US cash-secured put / covered-call scanning",
                    },
                )
            return (
                "unsupported",
                None,
                {
                    "symbol": raw,
                    "reason": f"unrecognized market prefix '{market}' on {raw} — not scanned; add a US listing (US.<ticker>) or report it as unsupported",
                },
            )
        canonical = canonical_underlying(raw)
        if not canonical:
            return "skip", None, None
        return "us", canonical, {"raw": raw, "canonical": canonical}

    def _list_moomoo_groups(self, conn):
        """Return group names from the signed-in OpenD session, or None if the
        enumeration call fails (unavailable SDK / unsupported API).

        Group enumeration is the reliable way to tell a missing group apart
        from an empty one: both degrade to a generic RET_ERROR on
        get_user_security, but the group list tells us whether the configured
        group exists at all before we read its securities.
        """
        try:
            ret, data = conn.get_user_security_group()
            if ret != (RET_OK or 0) or data is None:
                return None
            if hasattr(data, "to_dict"):
                records = data.to_dict("records")
            else:
                records = list(data)
            names = []
            for rec in records:
                if isinstance(rec, dict):
                    name = str(rec.get("group_name") or "").strip()
                    if name and name not in names:
                        names.append(name)
            return names or None
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"Moomoo watchlist group enumeration unavailable: {exc}")
            return None

    def get_scan_universe(self, growth_mode_config=None, portfolio_context=None):
        """Return the active CSP scan universe: the configured OpenD watchlist
        group of the signed-in session, US-listed securities only.

        Legacy config/app additions are archived (settings preserved, never
        deleted) and are NOT part of the scan universe; owned shares are
        scanned separately for covered calls from Moomoo positions.

        Returns a structured dict:
          - status: ok | missing_group | empty_group | connection_failed
          - group_name: the configured group name
          - explanation: a distinct, human-readable reason for each status
          - groups_available: group names enumerated from the session
          - tickers: canonical US tickers in group order
          - raw_codes: {canonical ticker: raw Moomoo code}
          - unsupported: [{symbol, reason}] never scanned, with explicit reasons
          - fetched_at: ISO timestamp of the successful group sync, or ""
        """
        from datetime import datetime, timezone

        conn = self._get_moomoo_connection()
        if conn is None:
            return self._empty_status(
                GROUP_STATUS_CONNECTION,
                "Moomoo/OpenD connection could not be initialized — check host, port, and OpenD configuration.",
            )
        # Fail fast: TCP probe before any SDK connect attempt (SDK connect can
        # block with reconnect retries when OpenD is absent).
        try:
            from core.context_factory import probe_opend_status

            probe = probe_opend_status(
                host=str(self.config.get("host", "127.0.0.1")), port=int(self.config.get("port", 11111))
            )
            if probe.get("status") != "connected":
                return self._empty_status(
                    GROUP_STATUS_CONNECTION,
                    "OpenD is not reachable at host:port (status probe returned %s) — start OpenD and confirm it is signed into the watchlist account."
                    % probe.get("status"),
                )
        except Exception as exc:
            return self._empty_status(
                GROUP_STATUS_CONNECTION,
                f"OpenD status probe failed: {exc}. Start OpenD and retry.",
            )
        if not conn.is_connected() and not conn.connect():
            return self._empty_status(
                GROUP_STATUS_CONNECTION,
                "Moomoo/OpenD quote session failed to connect — check credentials and that the watchlist account is signed in.",
            )

        group_name = str(self.config.get("moomoo_watchlist_group", "My Watchlist") or "My Watchlist")
        groups_available = self._list_moomoo_groups(conn)
        if groups_available and group_name not in groups_available:
            available = ", ".join(sorted(groups_available)) or "(none)"
            return self._empty_status(
                GROUP_STATUS_MISSING,
                f"Watchlist group '{group_name}' was not found in the signed-in OpenD session. Available groups: {available}. Configure moomoo_watchlist_group to a group that exists in Moomoo, or create '{group_name}' in the Moomoo app.",
                groups_available=groups_available,
                group_name=group_name,
            )

        try:
            ret, data = conn.get_user_security(group_name)
        except Exception as exc:
            return self._empty_status(
                GROUP_STATUS_CONNECTION,
                f"Moomoo watchlist read failed: {exc}. Check the connection and retry.",
                groups_available=groups_available,
                group_name=group_name,
            )

        if ret != (RET_OK or 0) or data is None or (hasattr(data, "empty") and data.empty):
            message = None
            if isinstance(data, str) and data.strip():
                message = data.strip()
            # The group is confirmed present but returned no securities, or the
            # enumeration was unavailable so we cannot distinguish — either way
            # the explanation states the group matched, never a config fallback.
            return self._empty_status(
                GROUP_STATUS_EMPTY,
                f"Watchlist group '{group_name}' is empty or could not return its securities (SDK: {message or 'empty data'}) — add US-listed securities to this group in the Moomoo app.",
                groups_available=groups_available,
                group_name=group_name,
            )

        if hasattr(data, "to_dict"):
            records = data.to_dict("records")
        else:
            records = list(data)

        tickers = []
        raw_codes = {}
        unsupported = []
        for record in records:
            if not isinstance(record, dict):
                continue
            kind, canonical, entry = self._classify_symbol(record.get("code", ""))
            if kind == "us" and canonical:
                if canonical not in tickers:
                    tickers.append(canonical)
                raw_codes[canonical] = entry["raw"]
            elif kind == "unsupported" and entry:
                unsupported.append(entry)
            # kind == "skip": blank/unparseable codes are ignored silently.

        fetched_at = datetime.now(timezone.utc).isoformat() if tickers else ""
        logger.info(
            f"Moomoo watchlist: scanned group '{group_name}' -> {len(tickers)} US tickers, "
            f"{len(unsupported)} unsupported skipped"
        )
        return {
            "status": GROUP_STATUS_OK,
            "group_name": group_name,
            "explanation": "",
            "groups_available": groups_available or [],
            "tickers": tickers,
            "raw_codes": raw_codes,
            "unsupported": unsupported,
            "fetched_at": fetched_at,
        }

    def _fetch_moomoo_watchlist(self):
        """Legacy list-only form: the canonical US tickers of the scan universe.

        Kept for callers and tests that only need the ticker list. Never
        substitutes the legacy config watchlist for a failed Moomoo read.
        """
        try:
            status = self.get_scan_universe()
            return list(status.get("tickers", []) or [])
        except Exception as exc:  # defensively match legacy contract
            logger.warning(f"Moomoo watchlist fetch failed: {exc}")
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
            # The moomoo source is the signed-in OpenD watchlist group only.
            # A failed/unavailable group yields an empty list — the legacy
            # config watchlist is NEVER substituted as the moomoo source.
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

        Returns a list of dicts: {"ticker": str, "origins": [str, ...],
        "scanned": bool}. Tickers are canonicalized (UBER vs US.UBER) and
        deduplicated. ``scanned`` is True only for symbols that came from the
        Moomoo group (the active scan universe); app/config entries are
        preserved for display but flagged as not scanned.
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
        return [
            {"ticker": ticker, "origins": sorted(origins), "scanned": "moomoo" in origins}
            for ticker, origins in sorted(merged.items())
        ]

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
        Return the canonical merged watchlist for display (Moomoo group + app
        SQLite + config). Tickers are canonicalized and deduplicated. This is
        NOT the scan universe — scans use get_scan_universe() (Moomoo group
        only); app/config entries are preserved here for the settings panel
        and flagged as not scanned in get_effective_watchlist_with_origins().
        """
        return [item["ticker"] for item in self.get_effective_watchlist_with_origins()]

    def get_screening_profile(self, option_type, dte=None, profile_type=None, growth_mode_config=None):
        """Return the active preset's screener profile for one option lane.

        The preset (``WheelPreset.to_screener_profile()``) is the single source of
        screening thresholds; nothing here overrides its values. ``option_type``
        only projects the side-specific delta/OTM target, and ``profile_type`` is
        display metadata derived from DTE (weekly <= 14, monthly <= 45, else
        quarterly). Every key a reader subscripts is always present, so no reader
        can hit a missing-key path.

        Args:
            option_type: 'CALL' or 'PUT'
            dte: Days to expiration (labels ``profile_type`` when not given)
            profile_type: 'weekly', 'monthly', 'quarterly', or None (from dte)
            growth_mode_config: The active preset's flat screener profile. When
                omitted, the manager resolves the active preset itself.

        Returns:
            dict: Screening profile parameters
        """
        preset_profile = (
            growth_mode_config
            if isinstance(growth_mode_config, dict) and growth_mode_config
            else self._active_preset_profile()
        )
        profile = dict(preset_profile)
        profile["profile_type"] = profile_type or self._profile_type_for_dte(dte)
        # Project the neutral reader keys from the preset's side-specific
        # originals. core reads these by name (one of them by direct subscript),
        # so a profile without them would silently skip a gate or raise.
        neutral = {
            "min_dte": preset_profile.get("csp_min_dte"),
            "max_dte": preset_profile.get("csp_max_dte"),
            "preferred_dte": preset_profile.get("csp_preferred_dte"),
            "min_otm_pct": preset_profile.get("csp_min_otm_pct"),
            "max_otm_pct": preset_profile.get("csp_max_otm_pct"),
        }
        for key, value in neutral.items():
            if value is not None and profile.get(key) is None:
                profile[key] = value
        if str(option_type or "").upper() == "CALL":
            profile["target_delta"] = preset_profile.get("call_target_delta", profile.get("target_delta", 0.30))
            profile["delta_tolerance"] = preset_profile.get(
                "call_delta_tolerance", profile.get("delta_tolerance", 0.12)
            )
            profile["default_otm_pct"] = preset_profile.get("call_default_otm_pct", profile.get("default_otm_pct", 10))
        else:
            profile["target_delta"] = preset_profile.get("csp_target_delta", profile.get("target_delta", 0.30))
            profile["delta_tolerance"] = preset_profile.get("csp_delta_tolerance", profile.get("delta_tolerance", 0.12))
            profile["default_otm_pct"] = preset_profile.get("csp_default_otm_pct", profile.get("default_otm_pct", 10))
        return profile

    @staticmethod
    def _profile_type_for_dte(dte) -> str:
        """Expiry-class label for display; thresholds are preset-driven."""
        if dte is None:
            return "monthly"
        try:
            days = float(dte)
        except (TypeError, ValueError):
            return "monthly"
        if days <= 14:
            return "weekly"
        if days <= 45:
            return "monthly"
        return "quarterly"

    def _active_preset_profile(self) -> dict:
        """The active preset's flat screener profile (the single threshold source)."""
        from core.presets import DEFAULT_PRESET_KEY, WHEEL_PRESETS, get_preset

        key = ""
        if self._db is not None:
            try:
                key = str(self._db.get_setting("wheel_preset") or "")
            except Exception:
                key = ""
        if key not in WHEEL_PRESETS:
            config = self.config
            configured = config.get("wheel_preset", "") if isinstance(config, dict) else ""
            key = str(configured or "")
        return get_preset(key if key in WHEEL_PRESETS else DEFAULT_PRESET_KEY).to_screener_profile()
