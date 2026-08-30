# API Reference — All You Need Is Wheel (as built 2026-08-02)

Single-user loopback app; all endpoints are read-only queries except
`POST /api/settings/preset` (persist the selected preset) and
`POST /api/run/refresh` (start one bounded background refresh). There is no
order, unlock, or trading-password endpoint.

## Endpoints

### System
- `GET /health` — top-level `healthy`/`degraded` status plus database and OpenD dependency states
- `GET /api/system/opend-status` — OpenD probe

### Wheel run
- `GET /api/run` — latest refresh attempt plus immutable last-good snapshot; response recomputes effective `tradeable`, `effective_status`, and stale symbols at read time. Closed-market snapshots may contain broker last-session chains and remain planning-only/staged.
- `POST /api/run/refresh` — start one background refresh (202; 409 if running); failed attempts never overwrite the last-good snapshot

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

### Portfolio / positions
- `GET /api/portfolio/` — portfolio summary (broker truth)
- `GET /api/portfolio/positions?type=STK|OPT`
- `GET /api/portfolio/weekly-income`
- `GET /api/portfolio/roll-pressure` — roll/hold/close diagnostics for option positions
- `GET /api/portfolio/alerts`

### Earnings calendar (risk metadata for the earnings gate)
- `GET /api/earnings/status`, `/pending`, `/locked-tickers`, `/lock-status`
- `POST /api/earnings/refresh`, `GET /api/earnings/update/<ticker>`

### Ledger
- `GET /api/ledger/*` — scan ledger diagnostics

## Retired endpoints (removed 2026-08-02)

`/api/options/catalyst-watch`, `/api/options/vix-regime`,
`/api/earnings/vol-signals`, `/api/risk/*`, `/api/signals/*`, `/api/macro/*`,
`/api/llm/*`, `/api/options/prefilled-close`. See docs/migration-ledger.md.

