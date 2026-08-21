## 2026-08-21 — Night-staged copy tickets

- Broadened copy eligibility to any qualified or marginal Moomoo-sourced signal with
  positive capacity (dropped the event_safe/ETF-only restriction). Event risk is now a
  ticket warning, not a copy blocker.
- The dashboard copies a manual ticket for any `copy_eligible` candidate regardless of
  market state: live runs get an explicit limit draft; US-closed/stale runs get a ticket
  *staged for US open* with the premium labelled as the last broker quote and a
  "verify live quote at open" note. Hard trust gates (crossed market, stale quote while
  open, yfinance fallback, zero capacity, research-only) still block copy.

## 2026-08-21 — Watchlist shortlist contract

- Canonicalized shortlist ranking to quality tier, event tier, and executable-bid
  premium velocity with stable tie-breakers; midpoint is a non-guaranteed limit target.
- Preserved Moomoo quote update time and UTC fetch evidence; crossed/stale quotes
  fail closed while open; CSP capacity uses true cash after reserved collateral.
- Consolidated dashboard cards onto immutable `/api/run` snapshots with read-time
  stale/tradeable evaluation and candidate/run copy gates.
- Removed the parallel `/api/options/top-recommendations` cache workflow.

## 2026-08-02 — Consolidation (wheel app)

- One-screen dashboard with operational strip, watchlist union, presets,
  CSP/CC/roll actions, diagnostics, copy-to-ticket.
- Atomic wheel run model (immutable snapshots + refresh attempts), explicit
  REAL account identity, Moomoo-only actionability, complete-union coverage.
- Removed: growth mode, LLM, macro/FRED, catalyst/social, dynamic screening,
  long-option lanes, scheduler, Docker, multi-worker serving, yfinance
  quote/chain fallbacks, granular screener overrides.
- Structural read-only enforcement (broker protocol + scans); loopback-only
  single process; locked env + Windows CI.
- See docs/migration-ledger.md.

# Changelog

All notable changes to this project will be documented in this file.

---

## [3.0.0] - 2026-06-17

### Changed — Application Purpose (Intent Refinement)

The application's core purpose was re-derived via structured intent extraction (`interview-me` skill). What was originally a broad Wheel Strategy signals dashboard with multi-factor scoring, macro regime detection, catalyst flow, and broad-market screening is now a **focused premium velocity scanner** scoped to the user's watchlist.

Key changes:
- **Premium velocity (premium / DTE)** is now the primary ranking axis. Multi-factor scoring qualifies but does not rank.
- **Scan scope is the user's watchlist only.** Broad-market screening, social sentiment, and ticker discovery are out of scope for the core scan path.
- **Top 3 shortlist** replaces a flat list of maybes. The app surfaces the highest-conviction plays, not everything that qualifies.
- **Runtime defaults** now favor the static watchlist (`watchlist_mode=static`) unless an operator explicitly opts into dynamic/hybrid screening.
- **All secondary features** (FRED macro regime, Ape Wisdom catalyst expansion, LLM advisor, dynamic TradingView screening) are de-prioritized relative to the core scan path. They remain in the codebase but are not the focus.
- The binding constraint is **free-tier OpenD API rate limits**. Everything is scoped to work within that bottleneck.

### Added
- `docs/intent/application-purpose.md` — confirmed intent statement for downstream consumers (dev team, future agents)

### Documentation
- `AGENTS.md` — Purpose, project-wide rules (premium velocity primacy, watchlist scope)
- `OVERARCHING GOAL.txt` — Refined to match the narrowed, watchlist-centric framing
- `README.md` — Updated header, description, and feature list to reflect premium velocity focus

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [2.3.0] - 2026-05-31

### Added
- **Ape Wisdom Catalyst Expansion** ? Catalyst Watch can now widen its scan universe with Ape Wisdom social momentum from `all-stocks`, then confirm candidates with broker-side options flow before surfacing signals.
- **Social Context on Confirmed Signals** ? Matching catalyst signals now include compact Ape Wisdom metadata (`rank`, `mentions`, `upvotes`, and momentum score) when social momentum was part of the scan.
- **Catalyst Watch Docs** ? README, API reference, scoring notes, test README, and local env examples now describe the social expansion path and its research-only guardrails.

### Fixed
- **Ape Wisdom Cache Lifetime** ? The social client now stays attached to `CatalystFlowService`, so the 5-minute cache actually survives repeated route calls instead of resetting every request.
- **Failure Semantics** ? Ape Wisdom fetch failures no longer poison the cache with an empty result; future requests keep retrying normally.

### Changed
- **Social Boost Ordering** ? Ape Wisdom context is attached and the light score bump is applied before the final sort, so boosted signals are ranked correctly.
- **Config Cleanup** ? Reverted an unrelated flag flip while documenting the new `catalyst_flow.apewisdom` config block.

## [2.1.0] - 2026-05-24

### Added
- **Rate Limiter Virtual Scheduling** — `core/rate_limiter.py` now computes the next-allowed timestamp under the lock then sleeps outside it, eliminating the deadlock window that blocked the entire API. Enforces `0.1s` minimum spacing between consecutive requests to prevent API collisions.
- **Earnings Updater via APScheduler** — Moved from `BackgroundTaskManager` (60-second restart loop) to the central APScheduler with a 6-hour `IntervalTrigger`. A one-shot background initializer runs on first startup when the `earnings_calendar` table is empty, without blocking Flask.
- **Connection Cache Key Normalization** — `MoomooConnection.__new__` now normalizes `account_id`, `portfolio_env`, and `security_firm` before computing the singleton key, so `OptionsService` and `PortfolioService` reuse the same connection instance.
- **FRED CPI Series Fix** — Changed CPI series code from `CPIALLMINMEI` (non-existent, triggered "Bad Request" warnings) to `CPIAUCSL` (All Urban Consumers).
- **CSP Gate Removed** — Removed the top-level `cash_available_for_csp < min_csp_buying_power` blocker so watchlist scanning can find cheap puts even when total available cash is below $5k. Individual per-contract capital checks remain.
- **Portfolio Context Query Reduction** — `_calculate_cash_reserved` now accepts an `option_positions` parameter, avoiding a second `get_positions('OPT')` call in `get_portfolio_context`.
- **Earnings Status Endpoint** — `/api/earnings/status` now reports the APScheduler state instead of the removed `BackgroundTaskManager` task.

### Changed
- `API.md`: earnings status response now includes `scheduler` object. System Tasks section updated — earnings no longer runs under `BackgroundTaskManager`.
- `README.md`: scheduler section mentions earnings updater every 6h; project structure updated.
- `TECH_DEBT_AUDIT.md`: open question #5 resolved; architectural mental model updated.
- `tests/README.md`: smoke test checklist updated to reflect APScheduler ownership.
- `tests/test_rate_limiter.py`: switched to `FakeClock` so all tests run without real wall-clock delays.

## [2.2.0] - 2026-05-24

### Added
- **Shared TTL Cache Utility** — `core/ttl_cache.py` provides `make_ttl_cache(maxsize, ttl)`, wrapping `cachetools.TTLCache` with a built-in `OrderedDict` fallback so the rest of the codebase can use one-liner caches without duplicating expiry/eviction logic.
- **Pydantic Request Validation** — `api/routes/risk.py` and `api/routes/pop.py` now define `BaseModel` schemas with `field_validator` normalizers (case-stripped tickers, type coercion, invalid-option-type rejection), replacing hand-parsed `request.args` and `request.get_json` calls.
- **Test Coverage for Cache & Validation** — `test_routes_validation.py`, `test_risk_sizing.py`, `test_technical_regime.py`, `test_pop_service.py`.

### Changed
- optional enrichment, `risk_sizing_service`, `tvscreener_service`, `technical_regime_service` — Replaced hand-rolled timestamp-dict caches with `make_ttl_cache()` from `core/ttl_cache.py`. Each service now declares `maxsize` and `ttl` in its constructor, removing ~15 lines of repeated expiry plumbing per file.
- `requirements.txt`, `pyproject.toml` — Added `cachetools>=5.3.0` and `pydantic>=2.0.0`.
- `README.md` — Project structure updated.
- `tests/README.md` — Test file table updated.

### Fixed
- **Risk Sizing Cache Staleness** — The per-entry timestamp-dict in `risk_sizing_service` used a lazy-expiry scheme that never actually evicted old entries. The new TTL cache enforces global expiration on every access, so stale ATR values no longer persist indefinitely.

## [2.0.0] - 2026-04-19

### Added - Phase 1: Risk-Adjusted Scoring
- **IV-Adjusted Return** — Annualized return normalized by implied volatility to filter dangerous low-IV scenarios
- **Theta/Delta Risk Ratio** — Daily income per unit of directional risk
- **Expected Value Calculation** — Probability-weighted outcomes using delta as PoP approximation
- **Capital Efficiency Score** — CSP optimization based on capital usage vs account size
- Enhanced scoring weights for CALLs and PUTs (see SCORING.md for details)

### Added - Phase 2: IV Environment & Earnings Integration
- **30-day Rolling IV Rank** — Tracks implied volatility history per ticker with color-coded badges (🔴 🟡 ⚫ 🟢)
- **Dynamic Screening Profiles** — Auto-detects weekly/monthly/quarterly by DTE with optimized parameters
- **Yahoo Finance Earnings Integration** — Free earnings data via yfinance (no API key), background refresh every 6 hours
- **Earnings Warnings** — Visual badges with score penalties (-30% today, -15% 1-3 days, -5% 4-7 days)
- **New API Endpoints:** `/api/earnings/status`, `/api/earnings/refresh`, `/api/earnings/update/<ticker>`, `/api/earnings/pending`
- **New Database Tables:** `iv_history` (45-day rolling IV), `earnings_calendar` (earnings dates)
- **New Service Module:** `api/services/iv_earnings_service.py`

### Added - Phase 3: Macro Regime & VIX Detection
- **VIX Market Regime** — Uses yfinance by default, with optional enrichment when explicitly enabled; adjusts delta targets by regime
- **Macro Regime Detection** — FRED-powered economic context (rates, credit stress, growth, inflation) with score multiplier (0.80x–1.05x)
- **New API Endpoints:** `/api/options/vix-regime`, `/api/macro/regime`, `/api/macro/cache/status`, `/api/macro/cache/clear`

### Added - Phase 4: Earnings Pipeline Restoration & UX Enhancement
- **Multi-Source Earnings Fetching** — Four-layer fallback (get_earnings_dates → stock.info → Calendars → earnings_dates)
- **Underlyer Extraction** — Resolves option contract names to stock symbols (e.g., `AAPL260508C195` → `AAPL`)
- **Shared DB Instance** — All services use `app.config` database, eliminating connection leaks
- **Global Refresh Button** — Dashboard header button triggers background update for all active symbols
- **Ticker-Level Refresh** — Per-ticker refresh icons next to earnings badges in positions table
- **Earnings Status Indicator** — Real-time badge showing background worker state (RUNNING/STOPPED/REFRESHING)
- **Premium Summary Renaming** — Renamed `displayEarningsSummary` to `displayPremiumSummary` to avoid confusion

### Added
- **FRED API Key Configuration** — Added `FRED_API_KEY` to `.env.example` with setup instructions
- **yfinance VIX Fallback** — VIX data fetched from yfinance (`^VIX`) when optional enrichment is unavailable
- **Thread Optimization** — Replaced CPU sleep loops with `threading.Event.wait()`
- **Graceful Shutdown** — `atexit` handlers stop background threads cleanly
- **Path Resolution** — Database path always resolves relative to project root

### Fixed - TVScreener Integration
- Removed invalid `StockField.SYMBOL` (symbols auto-returned by TradingView API)
- Changed `IV_PERCENTILE` to `VOLATILITY` (field doesn't exist in tvscreener)
- Updated filtering to use `StockField.VOLATILITY >= min_iv_rank / 100`

### Fixed - Earnings Data Fetch (yfinance)
- Added `_strip_moomoo_prefix()` helper — Converts moomoo format (`US.UBER`) to plain format (`UBER`)
- Fixed UBER and similar tickers failing yfinance lookups

### Fixed - Stability & Reliability
- **Return Type Standardization** — Fixed list vs dict return types crashing `/roll-pressure` and `/alerts` routes
- **Credential Cleanup** — Removed hardcoded FRED API key from `.env`
- **Repo Hygiene** — Cleaned up stray files (`0.2.28`, `README.md.bak`, etc.)

### Added (Minor)
- **Connection Status Endpoint** — `/api/options/connection-status` for debugging connection cycling issues
- **Top Recommendations Endpoint** — `/api/options/top-recommendations` for multi-threaded ranked option analysis
- **Cash Status Endpoint** — `/api/options/cash-status` for CSP buying power calculation
- **Analytics Endpoints** — `/api/options/analytics/lifecycle` and `/api/options/analytics/leakage` for position performance analysis
- **Prefilled Close Endpoint** — `/api/options/prefilled-close` for rollover data prefill
- **Portfolio Roll Pressure & Alerts** — `/api/portfolio/roll-pressure` and `/api/portfolio/alerts` for position management
- **System Tasks Endpoints** — `/api/system/tasks`, `/api/system/tasks/<name>/restart`, `/api/system/tasks/<name>/status` for background task management
- **LLM Suggestions Endpoint** — `/api/llm/suggestions` for AI-driven strategy recommendations

### Changed
- **Dependencies:** Added `yfinance>=0.2.28` to requirements.txt
- **Error Logging:** Optional enrichment VIX failures now logged as DEBUG (not WARNING) for graceful degradation

---

## [1.0.0] - Initial Release

### Features
- Portfolio Dashboard with positions, cash balance, and margin metrics
- Wheel Strategy focus with OTM analysis for CSPs and CCs
- Options Rollover manager for rolling positions approaching strike
- Order Management (create, execute, cancel from browser)
- Real-time OpenD connection status
- Auto-launch capability for Windows
- SQLite database for order tracking
- Docker Compose setup for containerized deployment

### API Endpoints
- System health and OpenD status
- Portfolio summary and positions
- Options analysis (OTM, stock prices, expirations)
- Order management (CRUD operations, execution, rollover)

### Technical Stack
- Python 3.10+ with Flask
- Moomoo OpenAPI for market data
- SQLite database
- Bootstrap 5 frontend
- Docker support

---

## Migration Guide

### From 1.0.0 to 2.0.0

1. **Install yfinance:**
   ```bash
   pip install yfinance>=0.2.28
   ```

2. **Database Migration** — Automatic on first run:
   - `iv_history` table created automatically
   - `earnings_calendar` table created automatically
   - Indexes created for performance

3. **Restart Application** — Background earnings updater starts automatically

4. **Verify Earnings Status:**
   ```
   GET http://localhost:8000/api/earnings/status
   ```

### No Breaking Changes
All existing functionality remains unchanged. New features are additive and backward compatible.

---

## Version Numbering

This project follows [Semantic Versioning](https://semver.org/):
- **MAJOR** — Breaking changes to API or database schema
- **MINOR** — New features, backward compatible
- **PATCH** — Bug fixes, documentation updates

Current version: **3.0.0** — Intent refinement: watchlist-focused premium velocity scanner
