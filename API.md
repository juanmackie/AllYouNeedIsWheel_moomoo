# API Reference — All You Need Is Wheel (as built 2026-09-30)

Single-user loopback app. Read-only queries are the default; the state-changing
endpoints are signals-only (nothing places, modifies, or cancels an order) and
cover run control, settings, the app-managed watchlist, and earnings-gate
metadata:

- `POST /api/run/refresh` — start one bounded background refresh
- `POST /api/settings/preset` — persist the selected preset
- `POST /api/settings/watchlist-group` — persist the selected Moomoo watchlist group
- `POST /api/watchlist` / `DELETE /api/watchlist/<symbol>` — add/remove an app-managed symbol
- `POST /api/earnings/refresh` / `POST /api/earnings/update/<ticker>` — update earnings-gate metadata

There is no order, unlock, or trading-password endpoint.

## Endpoints

### System
- `GET /health` — top-level `healthy`/`degraded` status plus database and OpenD dependency states
- `GET /api/system/opend-status` — OpenD probe

### Wheel run
- `GET /api/run` — latest refresh attempt plus immutable last-good snapshot; response recomputes effective `tradeable`, `effective_status`, and stale symbols at read time. Closed-market snapshots may contain broker last-session chains and remain planning-only/staged. `active_watchlist.tickers` includes an optional Moomoo-only `price_context` with current price, 52-week range, and percent below the 52-week high. `active_watchlist.market_session` carries the next scheduled US open and its calendar source for the operational-strip countdown. These display-only fields never affect screening, eligibility, or ranking. The snapshot's `capital_recovery` list is read-only portfolio context: it compares fetched covered-call scenarios against the best qualifying CSP return/day and uses Moomoo average cost conservatively; historical option credits are excluded until fill-history completeness can be proven. Call comparisons are limited to the broker strike slice fetched by the existing scan, not an exhaustive chain search. `watchlist_cash_fit` summarizes watchlist names with no affordable OTM CSP strike at current CSP cash and the maximum affordable strike; the dashboard uses it as a warning only. Recommendation cards omit the unused `iv_adjusted_return` and `remaining_gap_to_target` metrics; covered-call expected value is unavailable (`null`) until assignment-aware modeling exists.
- Top-three cards may include `working_order_check`, `has_working_order`, `working_order_statuses`, and `broker_capacity_check` plus `broker_max_*` fields. These are account-scoped OpenD diagnostics; exact working-order matches render a `RESTING ORDER` badge. They are display-only and do not alter rank, copy eligibility, or recommended quantity. A failed query reports unavailable/null, not zero.
- The combined top three may include `iv_vs_hv`: broker-sourced option IV, annualized 20-session historical volatility from Moomoo adjusted daily closes, their percentage-point spread, sample count, and last close date. This optional diagnostic is computed after selection, cached for 24 hours, and never affects rank or copy eligibility; quota or history gaps remain unavailable.
- Broker-IV cards may also include `falling_knife`: a display-only flag when the 20-session close-to-close decline is at least 15% and the latest close is below its 50-session simple average. It uses the same quota-checked Moomoo close request as `iv_vs_hv`; insufficient history is unavailable and the flag never affects rank or copy eligibility.
- `POST /api/run/refresh` — start one background refresh (202; 409 if running); failed attempts never overwrite the last-good snapshot
- `POST /api/run/taken` — validate and persist an owner-confirmed recommendation/trade link for a published run; never places or modifies an order.
- `GET /api/run/copy-check?ticker=...&option_type=CALL|PUT&expiration=...&strike=...&run_id=...` — revalidate the current snapshot and contract fingerprint at copy time; classifies live, staged, or review-only mode and rechecks a staged quote through OpenD. Read-only and rate-limited; it never creates or submits an order.

### Settings
- `GET /api/settings` — presets, active key, effective read-only values
- `POST /api/settings/preset` — persist `{preset: conservative|balanced|aggressive}`
- `POST /api/settings/watchlist-group` — persist `{group}` for the next scan

### Watchlist
- `GET /api/watchlist` — sources (moomoo/app/config), canonical union, origins
- `POST /api/watchlist` — add app-managed symbol `{symbol}`
- `DELETE /api/watchlist/<symbol>` — remove app-managed symbol

### Options / research
- `GET /api/options/otm`, `/api/options/expirations`, `/api/options/stock-price`,
  `/api/options/cash-status`, `/api/options/analytics/lifecycle` — broker-only research views. Shortlist cards are served only by `/api/run`; there is no parallel recommendation cache endpoint.
- `GET /api/options/analytics/outcomes` — broker-verified outcome journal (local SQLite; no OpenD gate). Joins published recommendation signals to ingested option fills: quoted vs. filled credit, slippage, net-of-fee outcomes (unknown fees ⇒ unknown outcome), capital-days, owner $/day, and groups by preset / DTE bucket / ticker / event tier (display-only). Also returns persisted `wheel_cycles` with combined option/share P&L only when paired fill prices, share quantities, all fees, and non-provisional called-away evidence are complete; open, provisional, expired-unverified, or incomplete cycles show unknown, never zero. Paired assignment fills and consecutive Moomoo snapshots showing an expired short contract absent stop option capital-days; expiry payoff remains unknown without proof. Also returns `link_suggestions` for short fills sharing ticker and option type within 7 days before/after a published run; these are suggestions only and persist only after owner confirmation through `POST /api/run/taken`. `dte_comparisons` compares matching 21–45 DTE owner fills with candidates retained in that run's saved shortlist; missing candidates are marked unavailable. This is comparison-only, never fetches extra chains, and is not a full-chain ranking. Filter params: `ticker`, `preset`, `event_tier`, `dte_bucket`, `limit`.
- `POST /api/options/analytics/outcomes/ingest?days=90&cash_flow_days=7` — query-only pull of broker fills, order fees, and cash movements from OpenD (OpenD-gated, 6/min, `days` capped at the SDK 90-day window). Ingestion only ever reads; nothing is placed, modified, or cancelled.
- `GET /api/options/analytics/shadow?alternative_score_key=capital_velocity_per_day` — local-only archive and rank-checkpoint summary. Readiness is reported per DTE bucket and requires the settled-sample, expiry-week, and ticker-week thresholds; a ready result is evidence for an owner decision, never an automatic rank change.
- `POST /api/options/analytics/shadow/settle?limit=500` — rate-limited, OpenD-gated settlement of expired archived candidates from Moomoo unadjusted daily closes. The route verifies available historical-K-line quota before fetching; insufficient quota or missing closes leave candidates pending. PUT outcomes are net of intrinsic value; call outcomes label intrinsic value as upside forgone. These observations are diagnostic and do not affect picks.

### Portfolio / positions
- `GET /api/portfolio/` — portfolio summary (broker truth)
- `GET /api/portfolio/positions?type=STK|OPT` — open option rows include Moomoo snapshot bid/ask/last, IV, delta and theta, plus local fetch and broker update timestamps. Missing delta stays `null` rather than being represented as a measured zero.
- `GET /api/portfolio/weekly-income`
- `GET /api/portfolio/history` — persisted portfolio snapshot history plus the account's flow-adjusted 5x goal pace and `flow_completeness`. While cash-flow history is incomplete, the pace reports `confidence_state: flows_incomplete` and withholds goal index, progress, CAGR, gap, ETA, and on-track metrics.
- `GET /api/portfolio/goal-flows` — account-scoped owner-declared deposits/withdrawals and flow-completeness status.
- `POST /api/portfolio/goal-flows` — add `{date, direction: IN|OUT, amount, currency?, remark?}`; amount is positive USD, matching NAV, and the active configured account determines storage scope.
- `DELETE /api/portfolio/goal-flows/<cashflow_id>` — remove one `owner:<uuid>` flow from the active account only.
- `PUT /api/portfolio/goal-flows/completeness` — confirm `{complete: true, through: YYYY-MM-DD}` through the latest snapshot after reviewing all deposits/withdrawals, or revoke with `{complete: false}`. Adding/removing an owner flow clears this confirmation.
- `GET /api/portfolio/holdings?since=<ISO-8601>` — local persisted stock book, wheel-cycle/basis provenance, open option legs, stored call candidates at/above basis, Moomoo `can_sell_qty` as a display-only covered-call capacity cross-check, changes since the supplied time, near expiries, dead-money share of NAV, and fill-history age. It never changes covered-call eligibility. No live OpenD request.
- `GET /api/portfolio/roll-pressure` — roll/hold/close/rotate diagnostics for option positions. Held-option Greeks and bid/ask/last come from the existing Moomoo position market snapshot; missing delta is returned as `null` and accompanied by a warning that delta-based close protection is unavailable. `ROLL` and `ROTATE` include a named `roll_target` only from a fresh, eligible same-underlying/same-side candidate in the latest published run; `ROLL` is suppressed when no target exists. Prospective roll/rotate fees are side-specific medians from the account's own fee-resolved US option fills in the last 90 days, labelled with their BUY/SELL sample counts. Unknown fees are never zero; `ROTATE` and `ROLL_OUT_DOWN` remain unavailable if either side lacks evidence.

### Earnings calendar (risk metadata for the earnings gate)
- `GET /api/earnings/status`, `/pending`, `/locked-tickers`, `/lock-status`
- `POST /api/earnings/refresh`, `POST /api/earnings/update/<ticker>` — update a validated ticker

### Ledger
- `GET /api/ledger/*` — scan ledger diagnostics

## Retired endpoints (removed 2026-08-02)

`/api/options/catalyst-watch`, `/api/options/vix-regime`,
`/api/earnings/vol-signals`, `/api/risk/*`, `/api/signals/*`, `/api/macro/*`,
`/api/llm/*`, `/api/options/prefilled-close`. See docs/migration-ledger.md.

