# Shortlist scoring contract

The dashboard is a watchlist-only, signals-only shortlist. Moomoo/OpenD is the
only source that can create an actionable candidate. External earnings data can
classify risk or demote a Moomoo candidate; it cannot create one. Signals rank
by executable return on deployed capital so the shortlist reflects the 5x goal.

## Qualification before ranking

`core/wheel_decision.py` applies the selected immutable preset and hard-blocks:

- invalid strike, expiry, DTE, option type, or source;
- non-positive/one-sided quotes and crossed markets (`ask < bid`);
- missing, invalid, or stale Moomoo `update_time` while the US market is open;
- spread, bid premium, liquidity, IV/Greeks, DTE/OTM, cash, or share failures;
- absolute option delta outside the selected preset's inclusive target ± tolerance band;
- CSPs that do not fit true available cash after reserved short-put collateral;
- CSPs that exceed the preset's per-underlying account exposure cap (stock market value plus existing and proposed short-put collateral; the current cap is 25% for every preset);
- covered calls without unencumbered 100-share lots.

A locked market (`ask == bid`) is valid. Margin buying power is displayed only;
it never establishes CSP capacity. When account value or current underlying
exposure cannot be verified, the CSP cap fails closed. Multi-contract
recommendations are reduced to the remaining per-underlying room.

A hard-gate-passing candidate receives:

- `quality_tier=qualified` when spread, open interest, and volume meet the
  preset's existing ideal values; otherwise `marginal`;
- an event tier: `event_safe`, `event_not_applicable` for broker-verified ETFs
  and indexes, `earnings_before_expiry`, or `event_unknown`;
- a safe backend `recommended_contracts` quantity. Zero cash/share capacity
  produces zero, never an invented one-contract ticket;
- machine-readable blockers, rationale, source, broker timestamp, and UTC
  fetch timestamp.

## Canonical math

The executable bid is the only ranking premium. DTE is whole calendar days from
the current `America/New_York` market date to the contract expiry, independent
of the host machine's timezone:

```text
bid_premium_per_contract = bid * 100
premium_velocity_per_day = bid_premium_per_contract / DTE
```

The midpoint is carried separately:

```text
limit_target_per_contract = ((bid + ask) / 2) * 100
```

It is labelled **limit target — not guaranteed** and is never used to outrank a
candidate. Capital-normalized executable return is:

```text
capital_velocity_per_day = bid_premium_per_contract / (capital_base * DTE)
annualized_return = capital_velocity_per_day * 365 * 100
```

For CSPs, `capital_base = strike * 100` (secured cash). For covered calls,
`capital_base = stock_price * 100` (covered underlying value). The legacy
per-contract `premium_velocity_per_day = bid_premium_per_contract / DTE`
remains visible and breaks capital-velocity ties.

Delta-based POP, expected-value proxy, IV adjustment, and Greeks diagnostics
are heuristics, not calibrated probabilities, expectancy, or profitability
evidence, and none of them can outrank a capital-velocity result.

The compact composite score (and the sub-scores that fed it) was **removed on
2026-09-20**: it could never gate or reorder a candidate, and a risk-flavoured
number that cannot affect the outcome only implies influence it does not have.
Delta is a qualification gate, but does not affect ranking among candidates that
pass it. Quality/event tiers, IV rank, and the midpoint remain display-only; the
ordering contract above is unchanged.

Preset delta qualification uses `abs(delta)` for puts and calls and accepts the
inclusive interval `[max(0, target_delta - delta_tolerance),
min(1, target_delta + delta_tolerance)]`. Candidates outside it receive the
`outside_delta_band` blocker. All immutable preset versions are v7; the delta
band and per-underlying exposure-cap changes were versioned rather than silently
changing v5/v6 behavior.

## Deterministic ordering

After hard gates, the backend sorts candidates by:

```text
descending executable return on deployed capital per day
→ descending executable-bid premium velocity per day (tie-break)
→ canonical ticker
→ expiration
→ strike
→ option type
```

Quality and event tiers are carried as display-only risk information (visible
labels/warnings on the surfaced signal); they never gate or reorder the
shortlist. Delta controls eligibility only and does not rank qualifying signals.
The midpoint and composite score are never part of the sort key.

The existing underlying-diversity safeguard is applied after this ordering,
extended with a portfolio-aware concentration guard: an underlying you already
have open short options on receives at most ONE new pick (not the standard
cap), and each candidate carries `existing_exposure_contracts` for display.
The browser displays the backend fields and performs no ranking or premium math.

### Authoritative order of precedence (as-implemented)

The single authoritative sort is `api/services/recommendation_ranking.py::rank_key`.
After hard gates it orders purely by capital return:

```text
1. capital velocity — executable return on deployed capital / day
   (capital_velocity_per_day: bid premium / (strike × 100 × DTE) for CSPs;
    bid premium / (stock_price × 100 × DTE) for covered calls)
2. premium velocity — executable-bid premium / day (tie-break only)
3. canonical ticker, then expiration, then strike, then option type
```

Quality/event tiers are display-only risk information and never gate or
influence ordering. The midpoint and composite score are intentionally absent
from the key: they may qualify or explain a candidate, never break a velocity
tie. Review item S03 is RESOLVED on both halves: the ranking decision (owner
decision: capital return primary, as implemented in `rank_key`) and the
actionability/state-table half (implemented as the read-time session/coverage
resolution in `core/run_model.py` plus the copy revalidation endpoint
`/api/run/copy-check` described below).

### State / action table

The run-state and copy (manual ticket) contract in one table. “Copy” always
means a read-only clipboard draft — no order is ever placed by the app.

| Run state | market_state | Copy? | Ticket meaning |
|-----------|--------------|-------|----------------|
| `ready` + tradeable (fresh, complete) | open | Yes | live explicit limit draft on the current broker quote |
| `ready` + tradeable (fresh, complete) | closed | Yes | staged for US market open; premium labelled last broker quote, “verify live quote at open” note |
| `ready` but not tradeable (coverage incomplete or quotes stale while open) | open | No | review-only; blocked by missing/stale quote gate |
| session unknown (no broker quote evidence in the run) | any | No | review-only; cannot even confirm the market session |
| holiday-shortened (scheduled-open by wall clock but quotes not fresh) | open-expected | No live | live blocked; staged only after a successful copy-time OpenD confirmation |
| `planning` (preflight infeasible / persisted broker snapshot fallback) | any | No | review-only; verify then re-refresh — do not stage |
| `partial` or `stale` | any | No | review-only; missing/stale evidence or cross-market |
| any state with yfinance fallback | any | No | review-only (non-Moomoo provenance) |
| any state, insufficient capacity | any | No | review-only (zero capacity → zero recommended contracts) |
| any state, research-only mode | any | No | signals only |

This table is the implemented read-time copy gate. `core/run_model.py`
classifies the session (`open` / `closed` / `holiday_shortened` / `unknown`)
and coverage truth (`complete` / `partial` / `planning_quota` / `unknown`) on
every read, `compute_signal_eligibility` labels each signal `live` / `staged` /
`review_only`, and `api/routes/run.py::evaluate_copy_check` revalidates against
the current snapshot plus live OpenD evidence immediately before any clipboard
write — a run that changed between load and click requires a second click, and
a stale/persisted-fallback run can never stage. Only a `ready` run can copy,
and staging happens only when the market is closed (`SESSION_STAGED_STATES`).

Each allowed ticket surfaces event risk (`earnings_before_expiry`, unknown
event) as a warning in both the dashboard card and clipboard text — never
silently dropped. The same display-only warning applies to all presets;
earnings overlap does not hard-block CSPs or affect shortlist ordering.

## Capital-aware sizing on every pick

CSP capacity is computed from true available cash after reserved short-put
collateral (`cash_available_for_csp`). Each pick displays: cash required,
income at the recommended size (recommended contracts × executable bid),
cash remaining after the trade, and — when portfolio history exists — what
percentage of the daily pace to the preset's growth target this trade covers.

## IV rank and percentile (display-only)

Both read the same history series, and that series is the point: `iv_history`
receives one row per scored contract, so a single scan writes dozens of
same-day observations carrying each strike's own IV. A rank taken over that pile
measures the strike skew and how often the user refreshed, not the underlying's
IV regime. `IVEarningsService._daily_atm_series` therefore collapses raw rows to
one sample per calendar day — the observation whose strike is closest to the
underlying price, falling back to that day's median IV when no row carries
usable strike/price (legacy pre-v12 rows). Rows without a timestamp each count
as their own sample so older history still contributes.

```text
iv_rank       = (current - min) / (max - min)   over the daily series + current
iv_percentile = share of daily samples strictly below current
window        = IV_RANK_WINDOW_DAYS (365)      minimum = IV_RANK_MIN_DAYS (10)
```

`iv_status` carries `insufficient_history` when fewer than 10 daily samples
exist (or there is no database): the score adjustment is then 0 and the UI shows
"IV n/a — insufficient history" rather than a fabricated neutral 50% rank. That
is deliberately distinct from `normal` with a 0.5 rank, which means history *was*
observed and its range was flat — real information, namely "no information".
The rank and percentile are display-only: neither feeds
`capital_velocity_per_day`, `quality_tier`, `event_tier`, or `confidence_score`,
so they cannot reorder the shortlist. IV history retention is 400 days
(`DEFAULT_RETENTION_DAYS`) — enough headroom for the 1-year window, which the
previous 45-day cap silently truncated.

## Exit playbook (open positions)

`core/exit_playbook.py::evaluate_exit` assigns each open short option one
deterministic verdict — HOLD, TAKE_PROFIT, ROTATE, ROLL, or CLOSE — with ranked
reasons. First matching rule wins:

1. CLOSE — earnings land before expiry while ITM or within 5% OTM.
1b. CLOSE — ex-dividend lands before expiry on an ITM short CALL: assignment
   before ex-div is likely, so the shares are called away before the dividend
   is captured (roll past ex-div or close). Dividends drive early exercise of
   calls only, so a short PUT never triggers this rule; a call within 5% OTM
   gets a context note and no verdict.
2. CLOSE — deeply ITM beyond the preset threshold (default 15%).
3. CLOSE — |delta| breaches the exit level (default 0.65).
4. TAKE_PROFIT — ≥ 50% of entry credit captured (entry credit from Moomoo
   `avg_cost`; unknown credit disables this rule and the loss stop, never
   fakes either).
4b. CLOSE — loss stop: captured ≤ −100% of the entry credit, i.e. the mark
   reached 2× the credit taken in. Fires *before* the roll window so a losing
   position cannot be quietly rolled instead of closed.
4c. ROTATE — a fresh eligible same-underlying/same-side contract's net return/day
   is at least 2× the held contract's mark-based return/day, after pricing the
   close at ask, the new sale at bid, and subtracting known close/open fees.
   Unknown fees never count as zero: the comparison is reported unavailable and
   ROTATE is not emitted. The current app has no prospective fee schedule, so
   this verdict remains unavailable until both per-contract fees are explicitly
   supplied.
5. ROLL — DTE entered the roll window (default ≤ 21) while safely OTM, and only
   when a fresh eligible same-side target exists. The target contract is named;
   without one, ROLL is suppressed and the position remains HOLD.
6. HOLD — otherwise; proximity/decay notes ride along.

Verdicts are computed in `score_existing_position`, serialized on the wheel
decision, exposed via `/api/portfolio/roll-pressure`, and rendered on the
position monitor with reasons as tooltips. ROLL/ROTATE targets are also visible
beside the verdict and included in the manual copy ticket. Fresh candidates come
only from the latest published run; stale, ineligible, or different-side quotes
cannot justify a transition. No automatic orders are sent.

Held-position bid/ask/last and Greeks are copied from the Moomoo option market
snapshot already fetched with the portfolio; no extra option-chain request or
synthetic Greek estimate is used. The API includes fetch/update timestamps.
When Moomoo omits delta, it remains `null`, `greeks_source` is `missing`, and
the position carries an explicit warning; the delta-based close threshold is
skipped rather than treating missing data as a measured zero.

Two deliberate scope notes. "Captured" is **signed**: a negative value is a loss
on the short, which is what makes the loss stop arithmetically possible (a zero
floor would make it unreachable). Ex-dividend modelling covers the *date*
(yfinance, through the earnings enrichment) but not the *amount*, so the rule
keys on "ITM with ex-div before expiry" rather than the exact
extrinsic-value-vs-dividend comparison that decides early exercise; fabricating
a payout figure would violate the broker-truth contract.

Exit thresholds (`exit_profit_take_pct`, `exit_roll_dte`, `exit_delta`,
`exit_deep_itm_pct`, `exit_stop_loss_pct`) are read from the portfolio context
when present, but nothing populates those keys today and presets carry no exit
fields, so the defaults above always apply. Preset-driving them is a known open
gap, not implemented behaviour.

## Entry timing guidance

Each scan payload carries server-computed intraday advice
(`core/utils.entry_window_advice`): avoid the first 15 minutes (spread
blowout) and final 30 minutes (MOC/pinning), midday is fair, mid-session is
good, outside hours the app directs you to stage limit tickets. Earnings risk
before entry is already enforced by event tiers.

## Growth pace (path to target)

One portfolio snapshot is persisted per completed run (NAV, cash, reserved
collateral, full position book). `GET /api/portfolio/history` serves the
series plus a `pace` payload from `core.growth_mode.growth_pace`: progress
toward the active preset's 5x `target_account_multiple`, annualized pace from
realized NAV change, ETA to target, required premium/day, and an on-track
verdict derived from realized pace — never a promised date.

## Expected trajectory (Monte Carlo, Moomoo-calibrated)

`GET /api/portfolio/projection` extends the same snapshot history into a
forward fan chart. `core/monte_carlo.py::project_nav` collapses bursts to one
point per calendar day, drops zero/negative-NAV rows from failed broker reads
(counted, not hidden), fits a geometric Brownian motion with exact
inter-observation gaps (MLE drift/vol), and simulates daily log increments
under a deterministic default seed — identical inputs always draw identical
bands. The payload reports P10/P50/P90 bands, end-horizon and ever-hit
probabilities for the 5x target, median first-hit ETA, calibration
(mu/vol, sample size, window, seed), broker-fill corroboration as option-leg
totals only (gross collected vs. buybacks vs. known fees; zero-price
assignment/expiry movements counted separately), and warnings. Attributed
per-trade P&L with unknowns stays on `/api/options/analytics/outcomes`.
Below 4 daily points spanning 7 days the endpoint returns `insufficient`
with the reason instead of a fabricated curve. Like pace, this is NAV
change — market moves plus any deposits/withdrawals — not strategy return
alone, and a projection is never a promise.

## Actionability

One manual refresh creates one immutable `WheelRunSnapshot`. `/api/run` returns
the last successful snapshot and recomputes effective freshness on every read;
it never rewrites stored history. A run is tradeable (live, executable-now) only
when it is `ready`, complete across the watchlist union, current, and
error-free.

Any candidate that is `qualified` **or `marginal`**, has Moomoo provenance (not a
yfinance fallback), and a positive backend `recommended_contracts` is
`copy_eligible`. Copy is allowed regardless of market state so an Australia-based
trader can prepare tickets during US overnight hours:

- **Live run** (tradeable): the copied ticket is an explicit limit draft on the
  current broker quote.
- **Closed / stale run**: the ticket is *staged for US market open* — the premium
  is labelled as the last broker quote (not live) with a "verify live quote at
  open" note. Event risk (`event_unknown`, `earnings_before_expiry`) is surfaced
  as a warning in the ticket, never silently dropped.

Hard trust gates still block copy: crossed markets, missing/stale quotes while
open, yfinance fallback, insufficient capacity, and research-only mode all keep
a signal review-only. Copy text uses the bid credit and the midpoint only as a
labelled non-guaranteed limit target.

During closed-market review, both CSP and covered-call lanes request the freshest
available last-session chain from OpenD first, then fall back to a persisted broker
snapshot only if OpenD fails. The resulting run remains `planning`; its staged ticket
must be verified against the live quote before a manual Moomoo order is placed.

## Evidence and freshness

Every refresh assesses the complete supported watchlist union, even with zero
free CSP cash. Option-contract codes are unsupported underlying symbols and do
not enter the coverage denominator. Successful discovery with no contracts in
the expiry/OTM window counts as assessed (`no_contracts_in_window`); broker
failures keep coverage incomplete.

Contract discovery requests both option types in inclusive ranges of at most
30 days, once per symbol/range per US market date. Memory and SQLite reuse only
that exact date and covered window, including empty successful discoveries.
The preflight charges only missing ranges against `scan_discovery_budget_sec`
(default 900 seconds); quote freshness does not constrain static discovery.
Configured chain quota, spacing, single-flight gate, and adaptive back-off are
unchanged. Estimates do not guarantee latency. Fresh option snapshots follow
all discovery, in batches of at most 400 codes.

CSP scoring considers every expiry and every strike inside the preset DTE/OTM
window, replacing three-expiry sampling and the 20-strike target slice. Cash fit
labels candidates instead of pruning discovery or scoring: unaffordable picks
show required cash versus available cash, zero contracts, and review-only
eligibility. Cash-fitting CSPs form the first lane partition; unaffordable CSPs
follow. Each partition uses the unchanged capital-return-first `rank_key`.
Quality/event tiers, score, and midpoint never influence ordering. Portfolio
recovery comparisons use the best qualifying CSP across the full ranked set.
Net broker cash fields are already reduced for collateral and are not reduced
again; gross cash still subtracts open short-put collateral.

If the remaining CSP work cannot fit, the run retains covered-call analysis and
an explicit `scan_infeasible` rejection. Unassessed CSP symbols stay outside
coverage, the run is `planning`, and every signal remains review-only. There is
no partial CSP scan presented as the global top three.

Moomoo's `update_time` is preserved verbatim and parsed as
`America/New_York`. The adapter records a separate UTC fetch time for the
snapshot. Missing or invalid evidence fails closed while the market is open.
Aged snapshots remain visible for diagnosis but cannot copy.

This methodology does not claim positive expectancy. The scan ledger records
versioned inputs, tiers, bid basis, blockers, and top-signal evidence for
observability only.
