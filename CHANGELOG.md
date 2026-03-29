# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added - Phase 1: Risk-Adjusted Scoring

#### Risk-Adjusted Metrics
- **IV-Adjusted Return** — Annualized return normalized by implied volatility to filter dangerous low-IV scenarios
- **Theta/Delta Risk Ratio** — Daily income per unit of directional risk
- **Expected Value Calculation** — Probability-weighted outcomes using delta as PoP approximation
- **Capital Efficiency Score** — CSP optimization based on capital usage vs account size

#### Enhanced Scoring Weights
- **CALLs:** IV-Adjusted (25%), Theta/Delta (20%), Liquidity (18%), Expected Value (15%), Upside (12%), OTM Fit (10%)
- **PUTs:** IV-Adjusted (25%), Theta/Delta (20%), Expected Value (18%), Liquidity (15%), Buffer (12%), Capital Efficiency (10%)

#### Technical Changes
- Modified `api/services/options_service.py` to include new scoring calculations
- Updated `score_details` in candidate objects to include new metrics
- Added rationale messages showing IV-adjusted yields and theta/delta ratios

### Added - Phase 2: IV Environment & Earnings Integration

#### IV Environment Awareness
- **30-day Rolling IV Rank** — Tracks implied volatility history per ticker
- **IV Environment Scoring** — Automatic score adjustments (-20% to +20%) based on IV percentile
- **Color-Coded IV Badges** — UI badges showing IV status:
  - 🔴 Red (< 30%): Extremely low IV, dangerous
  - 🟡 Yellow (30-40%): Below average IV
  - ⚫ Gray (40-60%): Normal range
  - 🟢 Green (> 60%): Above average, good premiums

#### Dynamic Screening Profiles
- **Auto-Detection by DTE** — Automatically selects optimal parameters based on days to expiration
- **Weekly Profile (0-14 DTE)** — Tighter delta targeting (0.16-0.22), higher liquidity focus (35% weight), lower premium threshold ($8-10)
- **Monthly Profile (15-45 DTE)** — Balanced approach with standard delta (0.20-0.30), moderate premiums ($12-15)
- **Quarterly Profile (46-90 DTE)** — Wider delta targeting (0.25-0.35), lower liquidity focus (15%), higher premium requirements ($25-30)

#### Earnings Integration
- **Yahoo Finance Integration** — Free earnings data via yfinance library (no API key required)
- **Background Updater** — Automatic earnings refresh every 6 hours using simple threading
- **Earnings Warnings** — Visual badges for earnings proximity:
  - 🚨 "EARNINGS TODAY" — Extreme risk, -30% score penalty
  - ⚠️ "Earnings in X days" — High risk, -15% penalty (1-3 days), -5% penalty (4-7 days)
- **Manual Update API** — Endpoints to force earnings refresh for specific tickers

#### New API Endpoints
- `GET /api/earnings/status` — Check background updater status and cache statistics
- `GET /api/earnings/update/<ticker>` — Manually update earnings for a specific ticker
- `GET /api/earnings/pending` — Get all tickers with earnings in next 7 days

#### Database Changes
- **New Table: iv_history** — Stores 45 days of IV data per ticker
  - Fields: ticker, timestamp, implied_volatility, stock_price, option_type, expiration, dte
  - Indexed on (ticker, timestamp) for fast lookups
  - Auto-purged after 45 days
  
- **New Table: earnings_calendar** — Tracks earnings dates
  - Fields: ticker, earnings_date, last_updated, fetch_status, error_message
  - Unique constraint on ticker
  - Updated every 6 hours via background thread

#### New Service Module
- **IVEarningsService** (`api/services/iv_earnings_service.py`)
  - IV rank calculation over rolling windows
  - IV environment scoring (-20 to +20 adjustments)
  - Earnings fetching via yfinance with error handling
  - In-memory caching (4 hours IV, 24 hours earnings)
  - Batch update operations for multiple tickers
  - Cache statistics for monitoring

#### Frontend Updates
- **IV Rank Display** — Badge next to IV% showing rank with color coding
- **Profile Type Badges** — Small badges indicating detected strategy profile (weekly/monthly/quarterly)
- **Enhanced Warnings** — New warning types:
  - IV environment warnings ("IV extremely low (15%) - poor risk/reward")
  - Earnings warnings with day counts
  - Combined warnings in warning tooltips
- **Updated Rationale** — Now shows IV-adjusted yield, IV rank, theta/delta ratio, and profile type

### Technical Implementation Details

#### Dependencies
- Added `yfinance>=0.2.28` to requirements.txt

#### Background Threading
- Simple threading-based scheduler in app.py
- Runs every 6 hours (21600 seconds)
- Graceful shutdown support
- Updates tickers found in recent orders (last 100)

#### Caching Strategy
- IV data: 4-hour in-memory cache
- Earnings data: 24-hour in-memory cache
- Database persists data across restarts
- Automatic cache validation on read

#### Error Handling
- Graceful degradation if yfinance fails (shows warning to user)
- Earnings fetch failures tracked with error_message field
- Continues operating without earnings data if unavailable

### Documentation

#### Updated Files
- **README.md** — Comprehensive updates including:
  - New Features section with scoring methodology
  - Risk-adjusted metrics explanation
  - IV environment impact tables
  - Earnings impact tables
  - Dynamic profiles documentation
  - Database schema section
  - New API endpoints
  - Updated project structure

#### New Documentation Files
- **CHANGELOG.md** — This file tracking all changes
- **SCORING.md** — Detailed scoring algorithm documentation
- **API.md** — Complete API endpoint documentation with examples

## [Previous Versions]

### [1.0.0] - Initial Release

#### Features
- Portfolio Dashboard with positions, cash balance, and margin metrics
- Wheel Strategy focus with OTM analysis for CSPs and CCs
- Options Rollover manager for rolling positions approaching strike
- Order Management (create, execute, cancel from browser)
- Real-time OpenD connection status
- Auto-launch capability for Windows
- SQLite database for order tracking
- Docker Compose setup for containerized deployment

#### API Endpoints
- System health and OpenD status
- Portfolio summary and positions
- Options analysis (OTM, stock prices, expirations)
- Order management (CRUD operations, execution, rollover)

#### Technical Stack
- Python 3.10+ with Flask
- Moomoo OpenAPI for market data
- SQLite database
- Bootstrap 5 frontend
- Docker support

---

## Migration Guide

### From Previous Version

1. **Install yfinance**:
   ```bash
   pip install yfinance>=0.2.28
   ```

2. **Database Migration** — Automatic on first run:
   - `iv_history` table created automatically
   - `earnings_calendar` table created automatically
   - Indexes created for performance

3. **Restart Application** — Background earnings updater starts automatically

4. **Verify Earnings Status**:
   ```
   GET http://localhost:8000/api/earnings/status
   ```

### No Breaking Changes

All existing functionality remains unchanged. New features are additive and backward compatible.

## Version Numbering

This project follows [Semantic Versioning](https://semver.org/):
- **MAJOR** — Breaking changes to API or database schema
- **MINOR** — New features, backward compatible
- **PATCH** — Bug fixes, documentation updates

Current version strategy: Minor version increments for each phase release.
