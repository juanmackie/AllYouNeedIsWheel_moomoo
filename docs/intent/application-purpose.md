# Application Purpose — Confirmed Intent

Derived via `interview-me` skill on 2026-06-17.

## Outcome

Open the app 1-2x during your day, see the top 3 CSP and call plays ranked by premium-per-day from your watchlist, decide quickly, trade when the US session opens.

## User

You — a Wheel Strategy trader based in Australia, planning for the night's US session.

## Why Now

The current app is too slow (rate-limited free-tier OpenD API) and returns a flat list of maybes with no conviction ranking — so you don't trust or use it. The gap between "open dashboard" and "execute a good trade" is too wide.

## Success

You open the app, wait ≤ reasonable scan time for a watchlist-sized universe, see 3 ranked picks with premium velocity math visible, and walk away with a trade decision. The app becomes your pre-session checklist instead of your broker's option chain.

## Constraint

Free-tier OpenD API rate limits. The fix is scoping scans to your watchlist (not a broad universe) so the pipeline completes before you lose patience.

## Out of Scope

- Background alerts and push notifications
- Autonomous order placement or execution
- Broad market scans beyond watchlist
- Fundamental analysis
- Real-time streaming data
- Social sentiment / Ape Wisdom integration
- FRED macro regime detection
- LLM trade advisor
- Anything that requires a paid data tier or additional API subscriptions

## Ranking Axis

Primary sort: **premium velocity** (premium / days to expiration). Higher return per day = better rank. This replaces the existing multi-factor scoring as the dominant ranking signal.

## Data Scope

Scan universe is your user-defined watchlist only. The app does not discover new tickers — it ranks the ones you already care about.
