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

Primary sort: **executable return on deployed capital per day**
(`capital_velocity_per_day`): bid premium / (strike × 100 × DTE) for CSPs;
bid premium / (stock price × 100 × DTE) for covered calls. Higher return per
dollar of deployed capital per day = better rank. Per-contract executable-bid
premium velocity breaks ties; remaining ties resolve on canonical ticker,
expiration, strike, option type. Quality/event tiers are visible risk
information and never influence ordering; midpoint and composite score never
influence ordering. See `api/services/recommendation_ranking.py::rank_key` and
SCORING.md “Authoritative order of precedence” (owner decision, S03 ranking
half resolved).

## Data Scope

Scan universe is your user-defined watchlist only. The app does not discover new tickers — it ranks the ones you already care about.
