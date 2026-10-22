# Signals, return persistence, and display investigation

Scope: source at `b28f070`, local changes described below. No live broker was
contacted and no trades were placed. All new accounting/restart checks use
synthetic data in temporary databases. This is **not a profitability backtest**.

## Findings and disposition

| Finding | Evidence | Disposition |
|---|---|---|
| Ranking rewards gross premium yield, not expected net profit | `core/scoring_factors.py::capital_velocity_per_day` computes premium / (capital × DTE); `api/services/recommendation_ranking.py::rank_key` orders this first, premium/day second. Neither deducts expected buybacks, assignment/share losses or fees. Quality/event tiers do not reorder picks. | Ranking/presets unchanged. UI explicitly calls yield gross premium and distinguishes data confidence from win probability. A strategy change needs out-of-sample evidence. |
| Historical financial evidence was deleted at startup | `OptionsDatabase.__init__` calls `prune_retained_data`; the original one-year policies deleted published runs, NAV snapshots, trade events/owner links and cash flows, while keeping fills. Reopen reproduced lost attribution. | Removed those financial tables from automatic retention. Cache/discovery/scan diagnostics still expire. Old rows already deleted are not recovered. |
| Return ratio displayed as dollar income | `owner_efficiency = net_pnl / capital_days` in `core/outcome_attribution.py`; totals, groups and contract rows formatted it as currency to two decimals. | Display as basis points/day, ratio × 10,000. $73 / ($7,000 × 31 days) is **3.36 bp/day**, not $0.00/day. Unknown stays unknown; negative and measured zero remain distinct. API units unchanged. |
| Stale pace/ETA/journal counts survived failed reads | `growth-panel.js` returned early without clearing previous metrics when history/journal disappeared. A DOM regression reproduced mixed unavailable/current and old positive values. | Clear stale numbers, colour and progress; distinguish history unavailable from empty. |
| NAV growth is not strategy return | `growth_pace` and `project_nav` consume NAV without cash-flow adjustment. A deposit changes their growth estimates. Monte Carlo extrapolates this history; it is not a signal backtest. | Prominent NAV/cash-flow caveat, NAV RISING rather than COMPOUNDING, illustrative rather than expected projection. No claim of cash-flow-adjusted returns. |
| Outcome view is not lifetime or full-account performance | Service reads the latest 500 runs and 5,000 fills per security type by default. Filters apply after reads. Realized option-leg P&L omits stock P&L/open marks and leaves unsupported lifecycles unknown. | Disclosed in UI/API docs. Read-window truncation is **not fixed** here: older entry legs or owner-link targets can fall outside the view even when persisted. Do not use this view as an all-time performance ledger. |

These reproduce code defects and limitations, not proof of the cause of the
owner's actual losses or missing live records. The private deployment's database,
configuration, ledger and broker statements were not supplied. Different DB
paths or account/environment settings can also make history appear absent.

## Data lineage

1. **Broker holdings and capital:** `MoomooConnection.get_portfolio` calls
   `accinfo_query` and `position_list_query`, adds option snapshots, and selects
   account/cash fields. `PortfolioService` caches this for 30 seconds;
   `PortfolioContext` derives true CSP cash, reserved collateral, holdings and
   exposure. Currency/account-field selection still needs reconciliation against
   the owner's actual statement, not assumptions about SDK fields.
2. **Quotes and discovery:** `options_data.get_contract_directory` and the
   recommendation engine's batch evidence collection discover expiry windows;
   `get_option_quotes` fetches broker snapshots. Preset gates, capacity, liquidity,
   source, event and freshness checks determine eligibility. Failed/over-quota
   coverage remains planning, never a fabricated complete scan. The complete
   watchlist union is the scan universe, not the entire market.
3. **Ranking and publication:** the engine ranks hard-gate survivors by quoted
   capital yield. `WheelRunner.refresh` resolves identity, requests fill evidence,
   reads portfolio context, scans, builds immutable `WheelRunSnapshot`, and saves
   it in `run_metadata`. `_persist_portfolio_state` separately saves NAV/positions
   and inferred events atomically with each other. That second step is
   best-effort: a successful published signal does not prove NAV persistence
   succeeded; failures currently log warnings. This is an outstanding failure
   visibility/atomicity limitation, not repaired by changing retention.
4. **Fills and fees:** `FillsService` requests query-only history on each refresh,
   normalizes deal IDs, side, quantity and timestamps, and inserts idempotently.
   Order fees are queried separately (40-order budget per refresh), allocated to
   fills, and remain NULL when unavailable. Incremental pulls are capped at 90
   days. Long downtime can create unrecoverable gaps without statement imports.
5. **Cash movements:** explicit outcome ingestion calls `ingest_cash_flows` for
   selected clearing dates (default last seven days). These are stored separately;
   normal per-refresh fill pulls do not establish complete cash-flow coverage.
   Simply subtracting the available deposits would therefore give false precision.
6. **Outcome arithmetic:** `OutcomeService` joins persisted recommendations,
   owner links and fills. Option prices convert per-share to per-contract once
   (×100). Closed FIFO tranches produce premium in minus buyback cost minus known
   fees; open premium is not realized profit. Ambiguous matching stays
   unattributed. Assigned/unresolved expiry legs remain unknown. Capital-days
   track quantity and actual holding duration, not just the signal's DTE.
7. **Dashboard:** `/api/run` supplies saved signals with read-time eligibility;
   `/api/portfolio/history` supplies persisted NAV and pace;
   `/api/options/analytics/outcomes` supplies local option-leg accounting without
   a live broker gate. Copy checks remain query-only and server-revalidated.

The earlier `docs/csp-signal-diagnosis.md` records a historical capture with 67
put candidates and many marginal/unknown-event labels. That is context, not a
measurement of the owner's present scan. Low count, low premium, poor liquidity,
and low net return are different problems; loosening gates would not establish
better expected performance.

## Reconciliation and restart result

The regression in `tests/test_outcome_integration.py::TestReturnPersistence`:

- stores a deliberately old synthetic recommendation and owner link;
- ingests a sell at $1.05/share and buyback at $0.30/share, one contract each,
  with $1 fee per order: realized option-leg P&L is $105 − $30 − $2 = **$73**;
- stores a separate $5,000 deposit and NAV snapshots; the deposit does not enter
  option P&L (it still affects unadjusted NAV);
- closes all SQLite handles and reopens the database;
- starts a new Python interpreter with the real application factory, same DB
  and configured identity, and reads history and outcome HTTP routes;
- verifies the NAV baseline and owner-linked outcome, quoted/filled credits,
  fees and slippage survive. Cash flows and trade links survive reopen too.

The original code failed after reopen because attribution history was pruned.
The fixed code passes. This establishes synthetic accounting/persistence
correctness, **not reconciliation of the owner's real broker account**.

## Performance evaluation: blocked, not fabricated

Available: source, synthetic scenario/replay fixtures, and one redacted saved run
with no candidate signals. The local checkout does not contain a complete
broker ledger, point-in-time chain/quote archive, or comparable benchmark series.
Old committed WAL fragments are not a complete or suitable research dataset.
`test_raw_scan_replay.py` verifies scanning/publication/copy behavior across
presets; it has no longitudinal market outcomes and cannot demonstrate an edge.

No walk-forward returns, Sharpe ratio, drawdown, or excess-return result is
reported. There is no support here for either a profitable edge or a definitive
claim that the strategy has none.

### Evidence needed privately

- Same-account statements and complete dated fills with order-level fees,
  stock/option assignments, expiries, corporate actions, dividends, cash flows,
  currency conversions and daily marked NAV; retain anonymous join keys.
- Point-in-time watchlist membership, settings/preset versions, all candidate
  quotes (including rejected/not-traded choices), bid/ask sizes and timestamps,
  underlying prices, earnings information as known then, and later exit marks.
- A timestamp-aligned cash/short-Treasury return series and an investable passive
  underlying baseline using membership known at the time, with comparable capital
  and cash-flow treatment. Owner-selected fills alone cannot evaluate untraded
  signals or remove selection bias.

Keep these outside Git and public reports. A full private database copy/backups
may contain raw deal/account information. Never publish it as a test fixture.

### Predeclared evaluation before changing ranking

1. Reconcile the ledger and daily NAV against broker statements first. Match
   currency and timing; unexplained differences larger than statement rounding,
   missing fees or unresolved lifecycle events block a confident result.
2. Freeze the current ranking, entry/exit/capital rules and fee schedule before
   evaluation. Use chronological expanding training windows and subsequent
   untouched test windows; purge overlapping trade labels at boundaries. Never
   tune on test outcomes. Record all trials and coverage gaps.
3. Replay only information available at decision time. Enter shorts no better
   than contemporaneous bid and buy back no better than ask; apply recorded broker
   fees and execution delay. Calibrate adverse slippage/fill probability from
   timestamp-matched actual fills, and stress worse execution rather than assume
   every signal fills. Account for shares, assignment, dividends, exercise,
   overlapping collateral, idle cash and failed/ineligible signals.
4. Build cash-flow-adjusted total account returns, including unrealized holdings,
   not premium collected. Compare net excess return, volatility, drawdown,
   risk-adjusted performance and turnover to the predeclared cash and passive
   baselines on identical dates/capital. Use time-block confidence intervals;
   avoid treating correlated contracts as independent observations.
5. Report each held-out fold and combined out-of-sample result, costs, coverage,
   assignment/tail losses and sensitivity. A claim of an edge requires positive
   net excess performance against the relevant baseline with uncertainty and
   drawdown within pre-agreed risk limits; inadequate data or unstable results
   mean inconclusive, not a pass. Do not use a larger premium quote or a green
   test suite as the success criterion.

## Validation and limits

- Python: **1,128 tests + 55 subtests passed** with the locked environment,
  including ranking replay, accounting, real-process persistence, account
  isolation, query-only broker and no-execution-surface tests.
- Frontend: **156 tests passed**, including positive/negative/zero/unknown rate
  formatting and stale-value clearing.
- Ruff lint/format and architecture checks passed.
- Fixture-backed Playwright journeys: **22 passed, 1 market-open-only test skipped**
  with one worker. The dashboard smoke suite passed **3/3** against a temporary
  loopback app configured with a fresh synthetic database and no OpenD session.
  No live Windows/OpenD or owner usability acceptance was performed.
- Existing dependency audit reported nine npm vulnerabilities. Dependency
  upgrades were not bundled into this accounting/display change.

Retaining financial evidence increases local disk usage; keep private backups.
No schema migration, ranking/preset change, safety-gate relaxation, remote push,
or deployment is part of this change.
