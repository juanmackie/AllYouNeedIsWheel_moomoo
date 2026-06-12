# AllYouNeedIsWheel (Moomoo Edition)

A financial options signal desk for the "Wheel Strategy" powered by the [Moomoo OpenAPI](https://openapi.moomoo.com/moomoo-api-doc/en/intro/intro.html). View your portfolio, analyze options chains for cash-secured puts and covered calls, and review signals through a local web dashboard.

**Risk-Adjusted Scoring, IV Environment Analysis & Macro Regime Detection** â€” Intelligent option ranking with IV rank tracking, earnings warnings, dynamic expiration profiles, and FRED-powered macro economic context.

<img width="1680" alt="Dashboard screenshot" src="https://github.com/user-attachments/assets/d27d525e-1fb4-4494-b5be-eba17e774322" />
<img width="1321" alt="Portfolio screenshot" src="https://github.com/user-attachments/assets/24634bbf-3110-46fa-85c4-b05301e11a88" />
<img width="1311" alt="Options screenshot" src="https://github.com/user-attachments/assets/0688ca0a-7fca-41fc-83b4-91881a2e9848" />
<img width="1309" alt="Rollover screenshot" src="https://github.com/user-attachments/assets/3e029e78-406c-44d4-b557-39b55c691f8a" />
<img width="1500" alt="Dashboard screenshot 2" src="https://github.com/user-attachments/assets/12a6539c-f74a-4d18-b868-ac7bef766dc8" />
<img width="1357" alt="Dashboard screenshot 3" src="https://github.com/user-attachments/assets/d9b2f57f-606d-4f4f-9d83-08b933ba71da" />

## Features

### Core Features
- **Portfolio Dashboard** â€” positions, cash balance, margin metrics, and weekly option income
- **Wheel Strategy Focus** â€” cash-secured puts and covered calls with OTM analysis
- **Options Rollover** â€” roll positions approaching strike price to later expirations
- **Signal-Only Workflow** â€” review opportunities in-app, then place trades in your broker
- **OpenD Connection Status** â€” the web UI shows real-time OpenD connection and login state
- **Auto Launch** â€” optional one-click start that can open OpenD for you on Windows
- **Dynamic Watchlist** â€” optional TradingView-powered stock screening for wheel strategy candidates
- **Catalyst Watch** â€” unusual options flow scans that can widen with Ape Wisdom social momentum, then confirm with broker data

### Intelligent Option Scoring (Phase 1 & 2)
- **Risk-Adjusted Scoring** â€” IV-adjusted returns, theta/delta risk ratio, expected value calculations
- **IV Environment Awareness** â€” 30-day rolling IV rank tracking with color-coded badges (ðŸ”´ low, ðŸŸ¢ high)
- **Dynamic Screening Profiles** â€” Auto-detects weekly/monthly/quarterly expirations with optimized parameters
- **Earnings Integration** â€” Automatic earnings warnings with multi-source fallback and manual refresh UI
- **APScheduler Earnings Job** â€” Earnings data refreshed every 6 hours via central APScheduler with one-shot initialization on startup
- **Macro Regime Detection** â€” FRED-powered economic context (rates, credit stress, growth, inflation) influencing scores and recommendations

### Macro Regime Detection (Phase 3)
- **Interest Rate Environment** â€” Detects rising/falling/stable rate regimes from Fed funds data
- **Credit Stress Monitoring** â€” Tracks high-yield corporate bond spreads for market stress signals
- **Economic Growth Regime** â€” Uses yield curve slope (10y-2y) to detect expansion/slowdown risks
- **Inflation Trends** â€” Monitors CPI trends for inflation context
- **Score Impact** â€” Macro multiplier (0.80x to 1.05x) adjusts all option scores based on economic conditions
- **Dashboard Integration** â€” Real-time macro regime card with actionable strategy advice

### Scoring Methodology

The system uses a sophisticated multi-factor scoring algorithm to rank option plays:

**Risk-Adjusted Metrics (Phase 1):**
- **IV-Adjusted Return** â€” Annualized return normalized by implied volatility (filters low IV danger)
- **Theta/Delta Ratio** â€” Daily income per unit of directional risk
- **Expected Value** â€” Probability-weighted outcome accounting for win rate and loss magnitude
- **Capital Efficiency** â€” CSP optimization based on capital usage vs account size

**Weight Distribution (Fixed):**
- **CALLs:** IV-Adjusted (25%), Theta/Delta (20%), Liquidity (18%), Expected Value (15%), Upside (12%), OTM Fit (10%)
- **PUTs:** IV-Adjusted (25%), Theta/Delta (20%), Expected Value (18%), Liquidity (15%), Buffer (12%), Capital Efficiency (10%)

**IV Environment Impact (Phase 2):**
| IV Rank | Score Impact | Status |
|---------|--------------|--------|
| < 20% | -20% penalty | ðŸ”´ Extreme low - dangerous |
| 20-30% | -10% penalty | ðŸŸ¡ Low IV warning |
| 30-40% | -5% penalty | Slightly below average |
| 40-60% | Neutral | âœ“ Normal range |
| 60-70% | +5% bonus | Slightly above average |
| 70-80% | +10% bonus | ðŸŸ¢ Good premium environment |
| > 80% | +20% bonus | ðŸŸ¢ Excellent IV |

**Earnings Impact (Phase 2):**
| Days to Earnings | Score Impact | Warning |
|------------------|--------------|---------|
| Today | -30% | ðŸš¨ EARNINGS TODAY |
| 1-3 days | -15% | âš ï¸ High assignment risk |
| 4-7 days | -5% | Caution advised |
| > 7 days | No impact | â€” |

**Dynamic Profiles (Auto-Detected by DTE):**
- **Weeklies (0-14 DTE):** Tighter delta targeting (0.16-0.22), higher liquidity weight (35%), lower premium threshold
- **Monthlies (15-45 DTE):** Standard delta (0.20-0.30), balanced approach, moderate premiums
- **Quarterlies (46-90 DTE):** Wider delta (0.25-0.35), lower liquidity weight (15%), higher premium requirements

## Prerequisites

| Requirement | Notes |
|---|---|
| Windows 10/11 | Required for the one-click launcher (`start_local.cmd`) |
| Python 3.10+ | The launcher creates a venv automatically |
| [Moomoo OpenD](https://www.moomoo.com/download/OpenAPI) | Runs locally alongside the app |
| Moomoo account | With US options market data subscriptions |
| OPRA Options Real-time Quote card | Free if total assets > $3,000 |
| FRED API Key (optional) | Free key for macro regime detection: [Get here](https://fred.stlouisfed.org/docs/api/api_key.html) |

## Quick Start (Windows)

This is the recommended daily-use flow.

### 1. Clone

```bash
git clone https://github.com/juanmackie/AllYouNeedIsWheel_moomoo.git
cd AllYouNeedIsWheel_moomoo
```

### 2. Create connection.json

```bash
copy connection.json.example connection.json
```

The launcher will also create this file for you if it is missing.

### 3. Create .env

```bash
copy .env.example .env
```

Edit `.env` with your Moomoo credentials:

```env
MOOMOO_LOGIN=your-email@example.com
MOOMOO_PASSWORD=your-moomoo-password
MOOMOO_TRADING_PASSWORD=your-trading-password
MOOMOO_LANG=en
```

### 4. Start the app

Double-click `start_local.cmd`.

The launcher will:

1. Create a Python virtual environment (`.venv`) if it does not exist
2. Install Python dependencies when requirements change
3. Create `connection.json` from the example if it is missing
4. Optionally open OpenD (see below)
5. Start the Flask app on `http://127.0.0.1:8000/`
6. Open your browser automatically

### 5. Log in to OpenD

If OpenD is not already signed in, sign in there manually and complete any verification or captcha step. The web app stays running and shows a banner explaining the current OpenD state.

### 6. Open the app

If the launcher did not open a browser, visit `http://127.0.0.1:8000/`.

## OpenD Auto Launch

To have the launcher open OpenD for you, edit `connection.json`:

```json
{
  "host": "127.0.0.1",
  "port": 11111,
  "readonly": true,
  "auto_launch_opend": true,
  "opend_path": "C:\\Path\\To\\OpenD.exe",
  "db_path": "options.db"
}
```

- `auto_launch_opend` â€” set to `true` to start OpenD when you run the launcher
- `opend_path` â€” path to `OpenD.exe` or `FutuOpenD.exe` on your machine
- If `opend_path` is empty the launcher searches common install locations automatically

## Catalyst Watch Social Expansion

Catalyst Watch can widen its scan list with Ape Wisdom trending tickers before broker-side flow confirmation. This remains research-only and does not change the core options scoring model.

```json
{
  "catalyst_flow": {
    "enabled": true,
    "min_premium_notional": 1000000,
    "min_fresh_volume_ratio": 5,
    "min_volume": 500,
    "max_expirations": 1,
    "max_dte": 60,
    "max_scan_tickers": 2,
    "apewisdom": {
      "enabled": true,
      "filter": "all-stocks",
      "max_boost_tickers": 8,
      "min_mentions": 5,
      "exclude_tickers": ["SPY", "QQQ", "VOO", "VTI", "VT", "TQQQ", "SQQQ"]
    }
  }
}
```

- `max_boost_tickers` caps how many social names can be added to the scan
- `min_mentions` filters out one-off chatter
- `exclude_tickers` keeps broad index funds out of the scan list

## OpenD Login

OpenD requires manual login and may show a graphic verification step (captcha). The app cannot automate this. When OpenD is running but not logged in, the dashboard shows a "LOGIN REQUIRED" banner and keeps signal actions review-only.

## Configuration

### connection.json fields

| Field | Default | Description |
|---|---|---|
| `host` | `127.0.0.1` | OpenD hostname |
| `port` | `11111` | OpenD port |
| `readonly` | `true` | Use `true` for paper trading (SIMULATE), `false` for live |
| `db_path` | `options.db` | Path to SQLite database |
| `auto_launch_opend` | `false` | Open OpenD when the launcher starts |
| `opend_path` | `""` | Path to the OpenD executable |

### connection_docker.json

Used only by `docker-compose.yml`. Points to `opend:11111` via the Docker network.

### connection_real.json (gitignored)

Live trading config. Not committed to version control.

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `MOOMOO_LOGIN` | Yes | Your Moomoo email or phone number |
| `MOOMOO_PASSWORD` | Yes | Your Moomoo login password |
| `MOOMOO_TRADING_PASSWORD` | For live | Your trading password |
| `MOOMOO_LANG` | No | Language: `en` (default) or `ch` |
| `MOOMOO_LOG_LEVEL` | No | OpenD log level: `no`, `debug`, `info` (default: `info`) |
| `CONNECTION_CONFIG` | No | Override config file (default: `connection.json`) |
| `PORT` | No | App port (default: `8000`) |
| `OPEND_PORT` | No | OpenD port override |
| `FRED_API_KEY` | No | FRED API key for macro regime detection (free: https://fred.stlouisfed.org/docs/api/api_key.html) |
  - *Uncomment the FRED_API_KEY line in your `.env` file after obtaining a free key* |
| `FRED_ENABLED` | No | Enable/disable FRED integration (default: `true`) |
| `OPENBB_ENABLED` | No | Enable/disable optional enrichment (default: `false`) |
| `WATCHLIST_MODE` | No | Watchlist mode: `static`, `dynamic`, or `hybrid` (default: `hybrid`) |
| `WATCHLIST` | No | Comma-separated static watchlist tickers used as the fallback/static list from `.env` |
| `SCREENING_MIN_IV_RANK` | No | Min IV rank for dynamic screening (default: `30`) |
| `SCREENING_MIN_VOLUME` | No | Min volume for dynamic screening (default: `1000000`) |
| `SCREENING_MAX_STOCKS` | No | Max stocks for dynamic screening (default: `50`) |
| `LLM_ENABLED` | No | Enable AI trade advisor (**opt-in, defaults to `false`**) |
| `LLM_PROVIDER` | No | LLM provider: `openai`, `anthropic`, `openrouter`, `ollama`, `custom` |
| `LLM_API_KEY` | For LLM | API key for the LLM provider |
| `LLM_MODEL` | No | Model name (default: `gpt-4o`) |
| `LLM_BASE_URL` | No | Override base URL for OpenAI-compatible APIs |
| `LLM_TEMPERATURE` | No | LLM temperature (0.0-1.0, default: `0.3`) |
| `LLM_MAX_TOKENS` | No | Max tokens in response (default: `2000`) |

## API Endpoints

The app exposes a full REST API for system status, portfolio data, options analysis, earnings, and macro regime detection.

**Base URL:** `http://127.0.0.1:8000`

| Category | Key Endpoints |
|---|---|
| **System** | `GET /health`, `GET /api/system/opend-status` |
| **Portfolio** | `GET /api/portfolio/`, `GET /api/portfolio/positions`, `GET /api/portfolio/weekly-income`, `GET /api/portfolio/roll-pressure`, `GET /api/portfolio/alerts` |
| **Options** | `GET /api/options/otm`, `GET /api/options/stock-price`, `GET /api/options/expirations`, `GET /api/options/catalyst-watch`, `GET /api/options/top-recommendations`, `GET /api/options/cash-status` |
| **Orders** | Retired. Review signals in the dashboard and place trades in Moomoo manually. |
| **Earnings & IV** | `GET /api/earnings/status`, `POST /api/earnings/refresh`, `GET /api/earnings/pending` |
| **Macro Regime** | `GET /api/macro/regime`, `GET /api/macro/cache/status` |
| **VIX Regime** | `GET /api/options/vix-regime` |
| **Analytics** | `GET /api/options/analytics/lifecycle`, `GET /api/options/analytics/leakage`, `POST /api/options/prefilled-close` |
| **LLM** | `POST /api/llm/suggestions` |

> ðŸ“– **Full API reference with request/response examples:** see [API.md](API.md)

## Web Pages

| URL | Description |
|---|---|
| `http://127.0.0.1:8000/` | Dashboard (options analysis, positions, signals) |
| `http://127.0.0.1:8000/portfolio` | Detailed portfolio view |
| `http://127.0.0.1:8000/rollover` | Option rollover signal review |

---

## Documentation

| Document | Purpose |
|---|---|
| [README.md](README.md) | This file â€” features, quick start, configuration, troubleshooting |
| [FREE_ONLY_REPO_SHORTLIST.md](FREE_ONLY_REPO_SHORTLIST.md) | Canonical free-only external repo shortlist and selection rules |
| [API.md](API.md) | Complete API reference with request/response examples |
| [SCORING.md](SCORING.md) | Detailed scoring algorithm, weights, and IV/earnings methodology |
| [CHANGELOG.md](CHANGELOG.md) | Version history and release notes |
| [tests/README.md](tests/README.md) | Unit tests and smoke test checklist |

## Dynamic Watchlist (tvscreener Integration)

**TradingView-Powered Stock Screening** â€” Automatically discover optimal wheel strategy candidates using free TradingView data.

### Overview
The dynamic watchlist feature uses [tvscreener](https://github.com/deepentropy/tvscreener) to screen stocks based on:
- **High Volatility** (TradingView's volatility metric as IV proxy)
- **High Liquidity** (average daily volume)
- **Configurable Criteria** (customize for your strategy)

### Watchlist Modes
| Mode | Description |
|---|---|
| `static` | Uses the static watchlist from `WATCHLIST` (default fallback) |
| `dynamic` | Uses TradingView screening exclusively |
| `hybrid` | Combines dynamic screening + static watchlist (default) |

### Configuration
Edit `connection.json` or set environment variables:

```json
{
  "watchlist_mode": "hybrid",
  "screening_criteria": {
    "min_iv_rank": 30,
    "min_volume": 1000000,
    "max_stocks": 50
  }
}
```

### Environment Variables
```bash
WATCHLIST_MODE=hybrid
WATCHLIST=AAPL,MSFT,GOOGL,AMZN,TSLA,NVDA,META,AMD,NFLX,UBER,SOFI,PLTR,BABA,DIS,BA,JPM,V,MA,KO,PEP,WMT,COST,HD,INTC,F
SCREENING_MIN_IV_RANK=30
SCREENING_MIN_VOLUME=1000000
SCREENING_MAX_STOCKS=50
```

### How It Works
1. **When enabled**: `get_effective_watchlist()` checks the mode and fetches candidates
2. **Dynamic mode**: Calls TradingView API via tvscreener library
3. **Hybrid mode**: Combines dynamic results with your static watchlist
4. **Fallback**: If TradingView API is unavailable, falls back to static watchlist
5. **Caching**: Results cached for 5 minutes to avoid rate limits

### Benefits
- âœ… **No API key required** â€” tvscreener uses free TradingView data
- âœ… **Automatic discovery** â€” find new opportunities automatically
- âœ… **Graceful degradation** â€” always works, even if API fails
- âœ… **Single-click start** â€” automatic installation via requirements.txt

## Project Structure

```
AllYouNeedIsWheel_moomoo/
â”œâ”€â”€ api/
â”‚   â”œâ”€â”€ routes/                  # Flask route modules (options, portfolio, system)
â”‚   â””â”€â”€ services/
â”‚       â”œâ”€â”€ options_service.py   # Option scoring, screening, VIX regime
â”‚       â”œâ”€â”€ iv_earnings_service.py  # IV tracking, earnings (Yahoo Finance)
â”‚       â”œâ”€â”€ portfolio_service.py # Portfolio operations
â”‚       â”œâ”€â”€ enrichment adapter      # optional and disabled by default
â”‚       â””â”€â”€ tvscreener_service.py   # TradingView stock screener
â”œâ”€â”€ core/
â”‚   â”œâ”€â”€ connection_manager.py    # Moomoo OpenD connection + singleton cache
â”‚   â”œâ”€â”€ connection_constants.py  # Connection utility functions
â”‚   â”œâ”€â”€ context_factory.py       # Context creation helpers
â”‚   â”œâ”€â”€ ticker_utils.py          # Ticker formatting and caching
â”‚   â”œâ”€â”€ rate_limiter.py          # Thread-safe rate limiting (virtual scheduling)
â”‚   â”œâ”€â”€ ttl_cache.py             # Shared TTL cache helper (cachetools + fallback)
â”‚   â”œâ”€â”€ scheduler.py             # APScheduler: earnings updater
â”‚   â”œâ”€â”€ background_manager.py    # Health monitor for background tasks
â”‚   â”œâ”€â”€ rate_limiter.py          # Shared request throttling
â”‚   â”œâ”€â”€ wheel_decision.py        # Scoring engine and decision logic
â”‚   â”œâ”€â”€ scoring_factors.py       # Pure scoring sub-functions
â”‚   â”œâ”€â”€ ttl_cache.py             # Shared TTL cache helper
â”‚   â”œâ”€â”€ currency.py              # Currency conversion
â”‚   â”œâ”€â”€ logging_config.py        # Logging setup
â”‚   â””â”€â”€ utils.py                 # Utility functions
â”œâ”€â”€ db/
â”‚   â””â”€â”€ database.py              # SQLite: orders, iv_history, earnings_calendar
â”œâ”€â”€ frontend/
â”‚   â”œâ”€â”€ static/
â”‚   â”‚   â”œâ”€â”€ css/                 # Stylesheets
â”‚   â”‚   â””â”€â”€ js/dashboard/        # Dashboard JS modules
â”‚   â””â”€â”€ templates/
â”‚       â”œâ”€â”€ partials/             # Reusable Jinja2 components
â”‚       â””â”€â”€ base.html
â”œâ”€â”€ tests/
â”‚   â”œâ”€â”€ test_rate_limiter.py
â”‚   â”œâ”€â”€ test_tvscreener_service.py
â”‚   â””â”€â”€ README.md                # Smoke test checklist
â”œâ”€â”€ app.py                       # Flask app factory + background threads
â”œâ”€â”€ run_api.py                   # WSGI server launcher
â”œâ”€â”€ config.py                    # Config loader
â”œâ”€â”€ API.md                       # Complete API reference
â”œâ”€â”€ SCORING.md                   # Scoring algorithm documentation
â”œâ”€â”€ CHANGELOG.md                 # Version history
â”œâ”€â”€ start_local.cmd              # Windows one-click launcher
â”œâ”€â”€ start_local.ps1              # PowerShell launcher logic
â”œâ”€â”€ connection.json.example      # Example local config
â”œâ”€â”€ connection_docker.json       # Docker Compose config
â”œâ”€â”€ docker-compose.yml           # Optional containerized setup
â”œâ”€â”€ Dockerfile                   # Web app container image
â”œâ”€â”€ requirements.txt             # Python dependencies
â””â”€â”€ .env.example                 # Example env file
```

## Database Schema

The application uses SQLite (`options.db`) with automatic migrations. Key tables:

| Table | Purpose |
|---|---|
| `orders` | Legacy table retained for historical data only |
| `iv_history` | IV data over time for 30-day rolling IV rank (purged after 45 days) |
| `earnings_calendar` | Earnings dates from Yahoo Finance (refreshed every 6 hours) |

Caching: in-memory â€” 4 hours (IV), 24 hours (earnings). See [SCORING.md](SCORING.md) for full schema.

## Scoring Methodology

The system uses a multi-factor scoring algorithm (0-100) to rank option plays.

**Core factors:** IV-adjusted return (25%), Theta/Delta risk ratio (20%), Expected Value (15-18%), Liquidity (15-18%), plus CALL/PUT-specific metrics.

**IV Environment:** 30-day rolling IV rank applies -20% to +20% score adjustments, shown as color-coded badges (ðŸ”´ ðŸŸ¡ âš« ðŸŸ¢).

**Earnings Impact:** Background thread fetches earnings dates (Yahoo Finance, no API key). Score penalties: -30% (today), -15% (1-3 days), -5% (4-7 days).

**Dynamic Profiles (auto-detected by DTE):**
| Profile | DTE | Target Delta | Liquidity Weight |
|---------|-----|--------------|------------------|
| Weeklies | 0-14 | 0.16-0.22 | 35% |
| Monthlies | 15-45 | 0.20-0.30 | 18% |
| Quarterlies | 46-90 | 0.25-0.35 | 15% |

> ðŸ“– **Full scoring algorithm, formulas, weights, and data models:** see [SCORING.md](SCORING.md)

## Docker (Optional)

The Docker Compose setup runs everything in containers. It is optional and best suited for experimentation rather than daily use because OpenD requires interactive login that is difficult in containers.

```bash
docker-compose up -d
```

This starts:
- `moomoo-opend` â€” OpenD gateway on port 11111
- `all-you-need-is-wheel` â€” web app on port 8000

## Troubleshooting

### OpenD not running

The dashboard shows an "OPEN OPEND" or "OpenD is not running" banner. Open the OpenD application and log in.

### OpenD login required

The dashboard shows "LOGIN REQUIRED". Complete the login or captcha step inside the OpenD window.

### Port 8000 already in use

Stop the existing process or start the app on a different port:

```bash
PORT=8001 python run_api.py
```

### No market data permissions

You need the OPRA Options Real-time Quote card (free if total assets > $3,000).

### Failed to unlock trade

Set `MOOMOO_TRADING_PASSWORD` in your `.env` file for live trading.

### App starts but pages show no data

Check the OpenD connection banner at the top of every page. Data only loads when OpenD is connected and logged in.

## Docker Commands (Reference)

```bash
docker-compose logs -f
docker-compose restart
docker-compose down
docker-compose up -d --build
docker-compose logs opend
```

## Security Notes

- Never commit `connection_real.json` to version control (it is in `.gitignore`)
- `.env` credentials are visible to anyone with access to the machine
- Store `MOOMOO_TRADING_PASSWORD` in your environment, not in config files
- Use `readonly: true` in `connection.json` unless you intentionally want live trading

## License

[Apache License 2.0](LICENSE)

## Acknowledgments

- [Moomoo OpenAPI](https://openapi.moomoo.com/moomoo-api-doc/en/intro/intro.html) â€” market data and trading API
- [Flask](https://flask.palletsprojects.com/) â€” web framework
