# Tests

## Environment and gates

Use the canonical `.venv` and a local SQLite test database. Unit tests must not
require live OpenD, credentials, or paid data; the final manual smoke is the
only broker-dependent check.

```bash
uv run ruff check .
uv run ruff format --check .
uv run python scripts/ci_pytest.py tests/ -q
npm test --silent
```

Browser-level smoke (requires the app running on 127.0.0.1:8000 and Playwright
chromium: `npx playwright install chromium`):

```bash
npm run test:e2e
```

The e2e suite (`tests/e2e/smoke.spec.js`) asserts the one-screen dashboard
scaffolds render (run strip, options table, position monitor, top
recommendations), the read-only signal is visible, and no execution-capable
controls exist. It does not require broker data — panels may show empty/error
states when OpenD is unavailable. Set `E2E_BASE_URL` to target another port.

Focused shortlist checks:

```bash
uv run python scripts/ci_pytest.py tests/test_connection.py tests/test_wheel_decision.py tests/test_score_regression.py tests/test_presets.py tests/test_portfolio_context.py tests/test_recommendations.py tests/test_run_model.py tests/test_wheel_parity.py tests/test_scan_ledger.py tests/test_routes_options.py tests/test_routes_run.py -q
npm test -- tests/frontend/top-recommendations.test.js
```

## Manual Windows/OpenD smoke

1. Start OpenD, log in, launch the app, and confirm the operational strip shows
   the environment, read-only state, market state, run status, coverage, and
   quote age.
2. Toggle dark and light modes and confirm text, signals, warnings, and the
   OpenD connection indicator remain readable.
3. Confirm the effective watchlist is the complete canonical union. A feasible
   refresh scans every symbol; an infeasible union publishes `planning` rather
   than a silently partial top three.
4. Refresh `/api/run` from the dashboard. Confirm one immutable last-good
   snapshot remains visible while a refresh is in flight or fails. When the US
   market is closed, confirm the scan still produces CSP and covered-call
   candidates from fresh OpenD last-session chains (or persisted broker fallback)
   and labels the run `planning`.
5. Compare each card's executable bid, bid premium velocity, midpoint
   **limit target—not guaranteed**, DTE, spread, OI/volume, cycle/annualized
   yield, source, broker timestamp, and UTC fetch time against Moomoo.
6. Confirm ordering is qualified quality tier, event tier, descending executable
   capital return per deployed dollar per day, then bid premium velocity and
   ticker/expiry/strike; composite score cannot reorder cards.
7. Confirm any copy_eligible card (qualified or marginal, Moomoo-sourced, positive
   `recommended_contracts`) can copy. When the run is live-tradeable the ticket is an
   explicit limit draft on the current quote; when US markets are closed or the quote
   is stale the ticket is staged for US open (premium labelled as the last broker quote,
   "verify live quote at open", and event-risk warnings). Hard gates still block copy:
   crossed markets, missing/stale quotes while open, yfinance fallback, insufficient
   capacity, and research-only mode. Copied quantity is the backend
   `recommended_contracts`. A closed-market ticket is staged for manual placement
   before/at the US open; verify the live quote before placing it in Moomoo.
8. Confirm true available cash minus reserved short-put collateral controls CSP
   affordability; margin buying power is display-only.
9. Confirm no UI or API path places, unlocks, cancels, or modifies an order.

If OpenD is unavailable, record the blocked manual-smoke result rather than
substituting simulated production portfolio or quote data.
