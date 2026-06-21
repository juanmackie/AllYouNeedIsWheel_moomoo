# Tests

## Python Environment

Use the canonical `.venv` created by `start_local.ps1`. Do not run tests from a stale top-level `venv/`; remove it if present so missing dependencies fail loudly.

## Unit Tests

Run all unit tests:

```bash
pytest tests/
```

### Test Files

| File | Description |
|------|-------------|
| `test_rate_limiter.py` | Tests for `core/rate_limiter.py` — virtual scheduling, burst detection, thread safety (uses `FakeClock` — no real wall-clock waits) |
| `test_tvscreener_service.py` | Tests for TradingView screener integration |
| `test_options_service_tvscreener.py` | Tests for options service with tvscreener |
| `test_apewisdom_service.py` | Ape Wisdom social momentum client, filtering, scoring, and cache behavior |
| `test_catalyst_flow.py` | Catalyst Watch scan expansion, social context, and ranking behavior |
| `test_catalyst_watch_route.py` | Catalyst Watch freshness metadata and Ape Wisdom source policy wiring |
| `test_routes_validation.py` | Pydantic validation for risk sizing and PoP routes |
| `test_risk_sizing.py` | Risk sizing service (ATR calculation, TTL cache behavior) |
| Optional enrichment service | VIX fetch, macro data, TTL cache |
| `test_technical_regime.py` | Technical regime service (EMA, ADX, TTL cache) |
| `test_pop_service.py` | Probability-of-profit service (delta, Monte Carlo) |

## Smoke Testing

Manual end-to-end checklist for verifying UI and pipeline functionality:

### Dashboard UI
- [ ] Page loads without JavaScript errors
- [ ] Navigation works (Dashboard, Portfolio, Rollover)
- [ ] Theme toggle (light/dark) works and persists
- [ ] Account summary shows correct cash, positions, weekly income
- [ ] Earnings status badge visible in header ("RUNNING"/"STOPPED" — reflects APScheduler state, not old BackgroundTaskManager)
- [ ] "Refresh All" (earnings) button works with spinner feedback

### Options Table
- [ ] Calls/Puts tabs switch correctly
- [ ] OTM% auto-refreshes after 800ms debounce (no manual button needed)
- [ ] Rapid typing in OTM% only triggers one refresh (debounce works)
- [ ] Refresh All / individual ticker refresh buttons work
- [ ] Expiration dropdown works
- [ ] Signal actions update the visible candidate rows
- [ ] Custom ticker add/delete works (Puts tab)
- [ ] IV rank badges show with correct colors (🔴 🟡 ⚫ 🟢)
- [ ] Earnings `(e)` badges show with date tooltip and per-ticker refresh
- [ ] **Watchlist CSP**: A watchlist-only ticker (not held, not custom) appears in the Cash-Secured Puts table when it has valid put data and passes cash-fit filters
- [ ] **Watchlist CC exclusion**: The same watchlist-only ticker does NOT appear in the Covered Calls table
- [ ] **Refresh All Puts includes watchlist**: Clicking "Refresh All Puts" fetches data for watchlist-only tickers (not just held 100+ share positions)
- [ ] **Excluded ticker isolation**: Excluding a held ticker does not hide a valid watchlist or custom CSP candidate with the same ticker
- [ ] **CSP empty-state varies**: When no watchlist or custom tickers exist, the empty message says "No watchlist or custom tickers" rather than a generic message
- [ ] **Concentration warning**: A held ticker with a CSP that ties up >30% of cash shows a `"Held + CSP: XX% of cash"` warning badge in the ticker cell

### Top Recommendations
- [ ] Cards load automatically with score/ranking
- [ ] Signal cards show source, confidence, and warnings
- [ ] Refresh button updates recommendations

### Catalyst Watch
- [ ] Catalyst Watch expands the scan list with Ape Wisdom names when enabled
- [ ] Confirmed flow cards show a short social-rising note when Ape Wisdom is present
- [ ] Disabling Ape Wisdom falls back to the watchlist-only scan path

### Rollover Signals
- [ ] Roll pressure positions display correctly
- [ ] Rollover suggestions show paired close/open legs
- [ ] Rollover review modal opens with signal-only copy
- [ ] Weekly income and filled-position summaries still load correctly

### Earnings Pipeline
- [ ] `/api/portfolio/roll-pressure` loads without 500 error
- [ ] `/api/portfolio/alerts` loads without 500 error
- [ ] Scheduler shuts down cleanly on server kill (APScheduler `atexit` handler)
- [ ] Global "Refresh All" earnings updates all active symbols
- [ ] Per-ticker refresh only updates that row

### Error Handling
- [ ] Graceful handling when OpenD is disconnected
- [ ] Invalid ticker input shows validation error
- [ ] OTM% outside 1-50 range doesn't trigger refresh

### Data Persistence (localStorage)
- [ ] OTM% settings persist per ticker
- [ ] Put quantities persist
- [ ] Custom tickers persist
- [ ] Theme preference persists
- [ ] Selected expiration dates persist per ticker

### Performance
- [ ] Page loads within 3 seconds
- [ ] Table updates don't freeze UI
- [ ] Large tables (20+ tickers) render smoothly
