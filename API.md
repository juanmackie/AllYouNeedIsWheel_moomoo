# API Reference — All You Need Is Wheel (as built 2026-08-02)

Single-user loopback app. Read-only queries are the default; the state-changing
endpoints are signals-only (nothing places, modifies, or cancels an order) and
cover run control, settings, the app-managed watchlist, and earnings-gate
metadata:

- `POST /api/run/refresh` — start one bounded background refresh
- `POST /api/settings/preset` — persist the selected preset
- `POST /api/watchlist` / `DELETE /api/watchlist/<symbol>` — add/remove an app-managed symbol
- `POST /api/earnings/refresh` / `POST /api/earnings/update/<ticker>` — update earnings-gate metadata

There is no order, unlock, or trading-password endpoint.

## Endpoints

### System
- `GET /health` — top-level `healthy`/`degraded` status plus database and OpenD dependency states
- `GET /api/system/opend-status` — OpenD probe

### Wheel run
- `GET /api/run` — latest refresh attempt plus immutable last-good snapshot; response recomputes effective `tradeable`, `effective_status`, and stale symbols at read time. Closed-market snapshots may contain broker last-session chains and remain planning-only/staged. The snapshot's `capital_recovery` list is read-only portfolio context: it compares fetched covered-call scenarios against the best qualifying CSP return/day and uses Moomoo average cost conservatively; historical option credits are excluded until fill-history completeness can be proven. Call comparisons are limited to the broker strike slice fetched by the existing scan, not an exhaustive chain search. `watchlist_cash_fit` summarizes watchlist names with no affordable OTM CSP strike at current CSP cash and the maximum affordable strike; the dashboard uses it as a warning only. Recommendation cards omit the unused `iv_adjusted_return` and `remaining_gap_to_target` metrics; covered-call expected value is unavailable (`null`) until assignment-aware modeling exists.
- `POST /api/run/refresh` — start one background refresh (202; 409 if running); failed attempts never overwrite the last-good snapshot
- `POST /api/run/taken` — validate and persist an owner-confirmed recommendation/trade link for a published run; never places or modifies an order.

A quota-infeasible CSP lane retains covered-call diagnostics in the published
planning snapshot. Its `rejected` list includes `scan_infeasible`, coverage stays
incomplete, and every signal is review-only. CSP feasibility charges uncached contract-directory ranges against a separate
900-second discovery budget, independently of quote freshness and free cash.
`GET /api/run` snapshot `preflight` includes `chain_calls`, `chain_symbol_count`,
`ranges_per_symbol`, `estimated_scan_sec`, `discovery_budget_sec`, quota/spacing
configuration, feasibility, and estimated capacity. `freshness_window_sec` is
retired. `RefreshAttempt.stage="discover"` reports discovery progress.
Unaffordable names appear in `watchlist_cash_fit`, but still get discovery and
scoring; their published picks have zero contracts and review-only eligibility.
Successful empty contract windows count as assessed (`no_contracts_in_window`).
Option-contract watchlist entries are listed as unsupported and excluded from
the underlying coverage total.

### Settings
- `GET /api/settings` — presets, active key, effective read-only values
- `POST /api/settings/preset` — persist `{preset: conservative|balanced|aggressive}`

### Watchlist
- `GET /api/watchlist` — sources (moomoo/app/config), canonical union, origins
- `POST /api/watchlist` — add app-managed symbol `{symbol}`
- `DELETE /api/watchlist/<symbol>` — remove app-managed symbol

### Options / research
- `GET /api/options/otm`, `/api/options/expirations`, `/api/options/stock-price`,
  `/api/options/cash-status`, `/api/options/analytics/lifecycle` — broker-only research views. Shortlist cards are served only by `/api/run`; there is no parallel recommendation cache endpoint.
- `GET /api/options/analytics/outcomes` — broker-verified outcome journal (local SQLite; no OpenD gate). Joins published recommendation signals to ingested option fills: quoted vs. filled credit, slippage, net-of-fee outcomes (unknown fees ⇒ unknown outcome), capital-days, owner $/day, and groups by preset / DTE bucket / ticker / event tier (display-only). Also returns `link_suggestions` for short fills sharing ticker and option type within 7 days before/after a published run; these are suggestions only and persist only after owner confirmation through `POST /api/run/taken`. `dte_comparisons` compares matching 21–45 DTE owner fills with candidates retained in that run's saved shortlist; missing candidates are marked unavailable. This is comparison-only, never fetches extra chains, and is not a full-chain ranking. Filter params: `ticker`, `preset`, `event_tier`, `dte_bucket`, `limit`.
- `POST /api/options/analytics/outcomes/ingest?days=90&cash_flow_days=7` — query-only pull of broker fills, order fees, and cash movements from OpenD (OpenD-gated, 6/min, `days` capped at the SDK 90-day window). Ingestion only ever reads; nothing is placed, modified, or cancelled.

### Portfolio / positions
- `GET /api/portfolio/` — portfolio summary (broker truth)
- `GET /api/portfolio/positions?type=STK|OPT` — open option rows include Moomoo snapshot bid/ask/last, IV, delta and theta, plus local fetch and broker update timestamps. Missing delta stays `null` rather than being represented as a measured zero.
- `GET /api/portfolio/weekly-income`
- `GET /api/portfolio/history` — persisted portfolio snapshot history (one per completed run) plus 5x growth pace
- `GET /api/portfolio/roll-pressure` — roll/hold/close/rotate diagnostics for option positions. Held-option Greeks and bid/ask/last come from the existing Moomoo position market snapshot; missing delta is returned as `null` and accompanied by a warning that delta-based close protection is unavailable. `ROLL` and `ROTATE` include a named `roll_target` only from a fresh, eligible same-underlying/same-side candidate in the latest published run; `ROLL` is suppressed when no target exists. `ROTATE` requires a 2× net return/day hurdle after close-at-ask, open-at-bid, and known per-contract fees; unknown fees explicitly suppress it.
- `GET /api/portfolio/alerts`

### Earnings calendar (risk metadata for the earnings gate)
- `GET /api/earnings/status`, `/pending`, `/locked-tickers`, `/lock-status`
- `POST /api/earnings/refresh`, `POST /api/earnings/update/<ticker>` — update a validated ticker

### Ledger
- `GET /api/ledger/*` — scan ledger diagnostics

## Retired endpoints (removed 2026-08-02)

`/api/options/catalyst-watch`, `/api/options/vix-regime`,
`/api/earnings/vol-signals`, `/api/risk/*`, `/api/signals/*`, `/api/macro/*`,
`/api/llm/*`, `/api/options/prefilled-close`. See docs/migration-ledger.md.

