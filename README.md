# All You Need Is Wheel (Moomoo Edition)

A focused capital-return scanner for the Wheel Strategy, powered by
[Moomoo OpenAPI](https://openapi.moomoo.com/moomoo-api-doc/en/intro/intro.html)
via local OpenD. One screen, one broker truth boundary, one ranked wheel
decision flow.

**The app is signals-only and structurally read-only.** It never places,
modifies, or cancels orders and never unlocks a trading context. Outputs are
manual copy-to-ticket suggestions for your broker UI.

## The one daily workflow

1. Start OpenD and log in, then launch the app.
2. Open the dashboard: operational strip (env, read-only, market state, run
   status, coverage, quote freshness) → portfolio summary → growth panel
   (path to 5x: equity curve, pace, ETA) → watchlist union → top-three CSP
   picks with contract sizing and entry-timing advice → position monitor
   (open short options with exit-playbook verdicts) → covered-call/roll
   actions → diagnostics.
3. Choose a risk preset (Conservative / Balanced / Aggressive; **Balanced by
   default**; effective values are read-only). Each preset carries a growth
   objective (`target_account_multiple`; all presets target **5x**) that drives
   pace math.
4. Refresh before bed in the Australian evening. The closed-market scan fetches
   the freshest available last-session Moomoo chains and publishes a `planning`
   snapshot with the full CSP/covered-call shortlist, visible math, and stale-data
   labels. A completed `WheelRunSnapshot` is persisted atomically alongside one
   portfolio snapshot per run and inferred trade events when positions change.
5. Copy the staged explicit ticket, verify the live quote and event risk at the
   US open when practical, then place the limit order manually in Moomoo. Nothing
   in this app places, modifies, or cancels orders; the resting order may fill
   while you sleep.
6. When awake around the US session, refresh again for fresh Moomoo quotes. At
   the next Australian morning, review fills and open-position verdicts.

## Data and actionability contract

- **Moomoo/OpenD is the only actionable source.** Account, cash, positions,
  quotes, and option chains must come from the broker. External data can
  explain gaps but can never create a pick.
- The scan universe is the **complete canonical union** of the named Moomoo
  watchlist group + app-managed SQLite symbols (+ legacy config list),
  canonicalized and source-labelled. If the union cannot fit the OpenD quota
  and freshness window, the run is **planning** and directs you to reduce a
  source list — it never silently truncates and claims a global top three.
- Each hard-gate-passing candidate is classified as `qualified` or `marginal`,
  then receives an event tier (`event_safe`, `event_not_applicable`,
  `earnings_before_expiry`, or `event_unknown`). Ordering is quality tier,
  event tier, executable annualized return on deployed capital, then executable
  bid premium velocity as a tie-break, then stable ticker/expiry/strike keys.
  Composite score cannot override that order.
- Moomoo `update_time` is preserved verbatim and interpreted in
  `America/New_York`; UTC fetch time is carried separately. Missing/invalid/
  stale broker time blocks actionable candidates while the market is open.
  Midpoint is shown only as a non-guaranteed limit target.
- Market-closed results are planning previews: visible, read-only, and staged for
  manual review. Their last-session Moomoo quotes are explicitly not live; verify
  the quote before placing a resting limit order.

## Architecture

```
moomoo servers <-> OpenD (127.0.0.1:11111)
                        |
                 query-only adapter (TrdMarket.NONE; readonly=False rejected)
                        |  one serialized connection + rate-limit boundary
        WheelRunner: RefreshAttempt -> resolve account -> portfolio once ->
                     engine (CSP/CC lanes) -> roll diagnostics ->
                     immutable WheelRunSnapshot -> one SQLite transaction ->
                     atomic publish
                        |  /api/run, /api/settings, /api/watchlist, /health
              Flask dashboard (Jinja + vanilla JS, loopback only)
```

Key pieces:

- `core/run_model.py` — immutable `WheelRunSnapshot` (run id, timestamps,
  env, opaque account identity, preset, market state, per-symbol broker fetch
  freshness, complete-union coverage, picks, rejections) and separate
  `RefreshAttempt` state. `/api/run` recomputes effective stale/tradeable state
  at read time without rewriting history; a failed attempt never relabels or
  erases the last successful snapshot.
- `core/wheel_runner.py` — one bounded background refresh worker; explicit
  account identity resolution (REAL requires a configured `account_id`;
  never "the first account").
- `core/presets.py` — versioned Conservative/Balanced/Aggressive presets,
  each carrying its `target_account_multiple` growth objective.
- `core/exit_playbook.py` — deterministic HOLD/TAKE_PROFIT/ROLL/CLOSE verdicts
  for open short options (profit-capture %, roll DTE window, delta breach,
  deep-ITM, earnings-in-window), surfaced on the position monitor.
- `core/position_diff.py` + `core/portfolio_snapshot.py` — per-run portfolio
  snapshots and trade-event inference (entry/exit/roll/assignment) feeding the
  persisted journal and win-rate analytics.
- `core/growth_mode.py::growth_pace` — path-to-target math from persisted
  snapshots: progress, annualized pace, ETA, required premium/day. No fixed
  deadline; verdicts derive from realized pace.
- `db/portfolio_snapshots_repository.py` — one snapshot row per completed run;
  served by `GET /api/portfolio/history` (equity curve + pace payload).
- `core/broker_protocol.py` — the query-only surface; forbidden SDK members
  are enforced by tests and an AST/repository scan.
- `api/services/recommendations.py` — complete watchlist CSP/CC lanes,
  true-cash/share reservation gates, explicit quality/event tiers, and
  deterministic executable-bid premium-velocity ranking.
- `api/routes/` — `run`, `settings`, `watchlist`, `options`, `portfolio`,
  `roll_pressure`, `alerts`, `earnings`, `ledger`, `source_policy`.

## Prerequisites

- Windows 10/11 (loopback-only single process; no Docker needed)
- Python 3.11+ and [uv](https://docs.astral.sh/uv/) (locked environment)
- Node.js 20.19+ / 22.12+ (frontend tests only)
- Moomoo OpenD running and logged in

## Quick start (Windows)

```powershell
# 1) Clone + install
uv sync --locked --all-groups
npm ci          # frontend tests only

# 2) Configure
copy connection.json.example connection.json   # set host/port/portfolio_env/account_id
copy .env.example .env

# 3) Run (one loopback process on :8000)
.\start_local.cmd     # or: .\start_local.ps1
```

The launcher health-checks `http://127.0.0.1:8000/health` and opens the
dashboard. For a REAL account view you must set `portfolio_env=REAL` and an
explicit `account_id` in `connection.json`; missing or ambiguous accounts
hard-fail with a clear message. Dashboard assets are served locally, so the
screen does not depend on a font/icon/Bootstrap CDN. Option-chain quota
defaults are conservative; tune them only after observing connection
diagnostics. Closed-market scans remain planning-only but still fetch broker
last-session chains for the Australian-evening review.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `MOOMOO_OPEND_HOST` / `PORT` | `127.0.0.1` / `11111` | OpenD connection |
| `MOOMOO_PORTFOLIO_ENV` | `SIMULATE` | `REAL` or `SIMULATE` (read-only view of the paper account) |
| `MOOMOO_ACCOUNT_ID` | — | Explicit account id; REQUIRED for REAL |
| `PORT` | `8000` | App port (loopback only) |
| `CONNECTION_CONFIG` | `connection.json` | Config file |
| `WATCHLIST` | — | Legacy static tickers (merged as the `config` source) |
| `MOOMOO_WATCHLIST_GROUP` | `My Watchlist` | Named Moomoo group (merged as the `moomoo` source) |
| `MOOMOO_CHAIN_RATE_LIMIT_MAX_REQUESTS` | `10` | Conservative option-chain requests per window |
| `MOOMOO_CHAIN_RATE_LIMIT_WINDOW_SEC` | `30` | Option-chain quota window in seconds |
| `MOOMOO_CHAIN_MIN_REQUEST_SPACING_SEC` | `3.0` | Minimum spacing between chain calls |
| `wheel_preset` (in config/DB) | `balanced` | Selected risk preset; persisted via `/api/settings/preset` |

## Tests and quality gates

```bash
uv run ruff check . && uv run ruff format --check .
uv run python scripts/ci_pytest.py tests/ -q    # os._exit avoids SDK thread-join hang
npm test --silent
```

CI (`.github/workflows/ci.yml`) runs the same gates on Windows plus hygiene
scans (no order-capable runtime code, no tracked generated artifacts) and a
fresh-clone import smoke.

## Data, backup, and reset

- SQLite database: `options.db` (git-ignored; WAL mode). Watchlist symbols,
  selected preset, run snapshots, and refresh attempts persist there.
- Backup: stop the app, copy `options.db` (use SQLite backup API for
  consistency). The Phase-0 verified backup lives in
  `Documents/TradingProjectArchive/step1-manifests/options.db-verified-backup-20260802.db`.
- Reset: stop the app, back up first, delete `options.db`.

## Out of scope (removed in consolidation)

Directional long options, spreads, LLM advisory, social/catalyst scans,
FRED/macro, TradingView dynamic screening, background notifications,
autonomous execution, Docker/multi-worker serving. The archived donor
`Moomoo Signal v2` and research repos `Swingtrade-Signals` / `PEAD Options`
are preserved read-only; see the migration ledger in `docs/migration-ledger.md`.
