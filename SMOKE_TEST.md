# Smoke Test Checklist - AllYouNeedIsWheel

## Pre-Test Setup
1. Start the application: `start_local.cmd` or `python run_api.py`
2. Open browser to: `http://127.0.0.1:8000/`
3. Ensure OpenD connection is established (check status indicator)

---

## Phase 1: Basic UI Elements

### 1.1 Page Load & Navigation
- [ ] Dashboard page loads without JavaScript errors (check console)
- [ ] Navigation menu is visible and clickable
- [ ] Theme toggle (light/dark) works and persists on refresh
- [ ] Page title shows "Dashboard - All You Need Is Wheel"
- [ ] Footer is visible with version info

### 1.2 Account Summary Section
- [ ] Cash balance displays correctly
- [ ] Stock positions count shows accurate number
- [ ] Option positions count shows accurate number
- [ ] Weekly option income displays (or shows "No data available" if none)
- [ ] Data status indicator shows connection status

---

## Phase 2: Options Table (Primary Testing)

### 2.1 Tab Navigation
- [ ] **Covered Calls** tab is active by default
- [ ] Clicking **Cash-Secured Puts** tab switches view
- [ ] Active tab state is visually distinct
- [ ] Table headers are correct for each tab

### 2.2 OTM Percentage Auto-Refresh (NEW FEATURE)
- [ ] **CRITICAL TEST**: Change OTM% value in the input field
- [ ] Spinner appears on refresh button after 800ms debounce
- [ ] Options data refreshes automatically
- [ ] Success toast notification appears
- [ ] New options data reflects the updated OTM percentage
- [ ] Values outside 1-50 range don't trigger refresh
- [ ] Multiple rapid changes only trigger one refresh (debounce works)
- [ ] **NO manual refresh button needed anymore**

### 2.3 Table Interactions
- [ ] Refresh All Calls button works
- [ ] Refresh All Puts button works
- [ ] Individual ticker refresh button works
- [ ] Expiration dropdown shows available dates
- [ ] Selecting different expiration refreshes options
- [ ] Sell/Add buttons add orders to pending
- [ ] Delete ticker button removes ticker from table (custom tickers)

### 2.4 Table Data Display
- [ ] Ticker symbols display correctly
- [ ] Stock prices are formatted with $ and 2 decimals
- [ ] OTM% shows current value
- [ ] Strike prices display correctly
- [ ] Expiration dates are formatted (YYYY-MM-DD)
- [ ] Mid prices show bid/ask midpoint
- [ ] Delta values display (0.00 to 1.00)
- [ ] IV% shows implied volatility
- [ ] Quantity fields are editable
- [ ] Total Premium calculates correctly (qty × price × 100)
- [ ] Cash Required shows for puts (strike × qty × 100)

### 2.5 Custom Tickers (Puts Tab Only)
- [ ] Add ticker input accepts valid symbols (AAPL, TSLA, etc.)
- [ ] Add button validates ticker exists
- [ ] Custom ticker appears in puts table
- [ ] Custom ticker persists after page refresh
- [ ] Delete button removes custom ticker

---

## Phase 3: Top Recommendations Section

- [ ] Top recommendations load automatically
- [ ] Cards show ticker, strike, expiration, premium
- [ ] OTM percentage displays correctly
- [ ] Score/ranking is visible
- [ ] Add Order button works
- [ ] Execute Now button works
- [ ] Refresh button updates recommendations
- [ ] Empty state shows when no recommendations available

---

## Phase 4: Pending Orders Section

### 4.1 Pending Orders Table
- [ ] Pending orders display in table
- [ ] Execute button sends order to OpenD
- [ ] Cancel button removes pending order
- [ ] Quantity is editable inline
- [ ] Order details show correctly (ticker, type, strike, expiration)
- [ ] Refresh button updates orders status
- [ ] Cancel All button removes all pending orders (with confirmation)

### 4.2 Filled Orders Table
- [ ] Filled orders display with timestamps
- [ ] Order details are accurate
- [ ] Weekly earnings summary calculates correctly
- [ ] Refresh button updates filled orders

---

## Phase 5: Error Handling & Edge Cases

### 5.1 Network/Connection Issues
- [ ] Graceful handling when OpenD is disconnected
- [ ] Appropriate error messages display
- [ ] Retry mechanisms work
- [ ] Data status indicator reflects connection state

### 5.2 Invalid Inputs
- [ ] Empty ticker input shows validation error
- [ ] Invalid ticker symbol shows error
- [ ] Negative quantities are rejected
- [ ] OTM% outside 1-50 range doesn't auto-refresh
- [ ] Form validation prevents submission of invalid data

### 5.3 Concurrent Actions
- [ ] Multiple rapid clicks don't duplicate orders
- [ ] Buttons disable during processing
- [ ] Loading spinners appear during async operations
- [ ] Race conditions handled properly

---

## Phase 6: Data Persistence

- [ ] OTM% settings persist in localStorage per ticker
- [ ] Put quantities persist in localStorage
- [ ] Custom tickers persist in localStorage
- [ ] Theme preference persists
- [ ] Selected expiration dates persist per ticker

---

## Phase 7: Performance Testing

- [ ] Page loads within 3 seconds
- [ ] Table updates don't cause UI freezing
- [ ] Auto-refresh doesn't trigger excessive API calls
- [ ] Large tables (20+ tickers) render smoothly
- [ ] No memory leaks after extended use

---

## Specific Tests for Changes Made

### Test 1: Heatmap Removal Verification
- [ ] **CONFIRM**: No heatmap toggle switch visible in Calls tab
- [ ] **CONFIRM**: No heatmap toggle switch visible in Puts tab
- [ ] **CONFIRM**: No color-coding on score cells
- [ ] **CONFIRM**: No "Best Play" gold border styling
- [ ] **CONFIRM**: Tables look normal without heatmap styling

### Test 2: OTM Auto-Refresh Verification
- [ ] **CRITICAL**: Open browser DevTools (F12) → Console
- [ ] Navigate to Covered Calls tab
- [ ] Click on any OTM% input field
- [ ] Type a new value (e.g., change from 10 to 15)
- [ ] Wait 800ms without clicking anything else
- [ ] **VERIFY**: Spinner appears on refresh button
- [ ] **VERIFY**: Console shows: "Auto-refreshing [TICKER] CALL options with OTM 15%"
- [ ] **VERIFY**: Toast notification appears: "OTM Updated"
- [ ] **VERIFY**: Table data updates with new OTM options
- [ ] **VERIFY**: No manual refresh button click needed

### Test 3: OTM Debounce Verification
- [ ] Focus on OTM% input field
- [ ] Type "1" → wait 200ms → type "2" → wait 200ms → type "0" (to make "10")
- [ ] **VERIFY**: Only ONE refresh occurs after you stop typing (not three)
- [ ] Check console - should only see one "Auto-refreshing" message

---

## Known Issues to Watch For

1. **OpenD Connection**: If OpenD is not running, data won't load
2. **Rate Limiting**: Too many rapid refreshes may hit API limits
3. **Market Hours**: Some features work best during market hours
4. **Portfolio Data**: Requires real account connection to OpenD

---

## Testing Commands

```bash
# Start the application
python run_api.py

# Or on Windows
start_local.cmd
```

---

## Success Criteria

- All Phase 1 tests pass: ✅ **BASIC UI FUNCTIONAL**
- All Phase 2 tests pass: ✅ **OPTIONS TABLE FUNCTIONAL**
- All Phase 3 tests pass: ✅ **RECOMMENDATIONS FUNCTIONAL**
- All Phase 4 tests pass: ✅ **ORDERS SYSTEM FUNCTIONAL**
- All Phase 5 tests pass: ✅ **ERROR HANDLING FUNCTIONAL**
- All Phase 6 tests pass: ✅ **DATA PERSISTENCE FUNCTIONAL**
- Test 1 (Heatmap) passes: ✅ **HEATMAP REMOVED**
- Test 2 (OTM Auto-refresh) passes: ✅ **OTM AUTO-REFRESH WORKING**
- Test 3 (Debounce) passes: ✅ **DEBOUNCE WORKING**

**Overall Status**: All systems operational ✅

---

## Reporting Issues

If any test fails:
1. Note the specific test number
2. Copy any error messages from browser console (F12)
3. Check network tab for failed API calls
4. Note steps to reproduce
5. Report findings for investigation

---

*Last Updated: March 29, 2026*
*Version: Post-Heatmap-Removal & OTM-Auto-Refresh*
