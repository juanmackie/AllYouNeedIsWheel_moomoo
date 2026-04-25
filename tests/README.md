# Tests

## Unit Tests

Run all unit tests:

```bash
pytest tests/
```

### Test Files

| File | Description |
|------|-------------|
| `test_rate_limiter.py` | Tests for `core/rate_limiter.py` — basic rate limiting, burst detection, thread safety |
| `test_tvscreener_service.py` | Tests for TradingView screener integration |
| `test_options_service_tvscreener.py` | Tests for options service with tvscreener |

## Smoke Testing

Manual end-to-end checklist for verifying UI and pipeline functionality:

### Dashboard UI
- [ ] Page loads without JavaScript errors
- [ ] Navigation works (Dashboard, Portfolio, Rollover)
- [ ] Theme toggle (light/dark) works and persists
- [ ] Account summary shows correct cash, positions, weekly income
- [ ] Earnings status badge visible in header ("RUNNING", "STOPPED", "REFRESHING")
- [ ] "Refresh All" (earnings) button works with spinner feedback

### Options Table
- [ ] Calls/Puts tabs switch correctly
- [ ] OTM% auto-refreshes after 800ms debounce (no manual button needed)
- [ ] Rapid typing in OTM% only triggers one refresh (debounce works)
- [ ] Refresh All / individual ticker refresh buttons work
- [ ] Expiration dropdown works
- [ ] Sell/Add buttons add orders to pending
- [ ] Custom ticker add/delete works (Puts tab)
- [ ] IV rank badges show with correct colors (🔴 🟡 ⚫ 🟢)
- [ ] Earnings `(e)` badges show with date tooltip and per-ticker refresh

### Top Recommendations
- [ ] Cards load automatically with score/ranking
- [ ] Add Order / Execute Now buttons work
- [ ] Refresh button updates recommendations

### Pending Orders
- [ ] Pending orders display correctly
- [ ] Execute / Cancel / Quantity edit work
- [ ] Cancel All works with confirmation
- [ ] Filled orders display with timestamps and weekly summary

### Earnings Pipeline
- [ ] `/api/portfolio/roll-pressure` loads without 500 error
- [ ] `/api/portfolio/alerts` loads without 500 error
- [ ] Background threads stop gracefully on server kill
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
