# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

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
- **VIX Market Regime** — Fetches VIX from OpenBB (primary) or yfinance (fallback), adjusts delta targets by regime
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
- **yfinance VIX Fallback** — VIX data fetched from yfinance (`^VIX`) when OpenBB fails
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
- **Error Logging:** OpenBB VIX failures now logged as DEBUG (not WARNING) for graceful degradation

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

Current version: **2.0.0** — Phases 1–4 complete (Risk-Adjusted Scoring, IV/Earnings, Macro Regime, Pipeline Restoration)
