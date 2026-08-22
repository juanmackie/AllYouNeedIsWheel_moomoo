# Shortlist scoring contract

The dashboard is a watchlist-only, signals-only shortlist. Moomoo/OpenD is the
only source that can create an actionable candidate. External earnings data can
classify risk or demote a Moomoo candidate; it cannot create one.

## Qualification before ranking

`core/wheel_decision.py` applies the selected immutable preset and hard-blocks:

- invalid strike, expiry, DTE, option type, or source;
- non-positive/one-sided quotes and crossed markets (`ask < bid`);
- missing, invalid, or stale Moomoo `update_time` while the US market is open;
- spread, bid premium, liquidity, IV/Greeks, DTE/OTM, cash, or share failures;
- CSPs that do not fit true available cash after reserved short-put collateral;
- covered calls without unencumbered 100-share lots.

A locked market (`ask == bid`) is valid. Margin buying power is displayed only;
it never establishes CSP capacity.

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

The executable bid is the only ranking premium:

```text
bid_premium_per_contract = bid * 100
premium_velocity_per_day = bid_premium_per_contract / DTE
```

The midpoint is carried separately:

```text
limit_target_per_contract = ((bid + ask) / 2) * 100
```

It is labelled **limit target — not guaranteed** and is never used to outrank a
candidate. CSP cycle yield uses bid credit divided by `strike * 100`; covered
call cycle yield uses bid credit divided by `stock_price * 100`. Annualized
return is cycle yield times `365 / DTE`.

The existing composite, delta-based POP, expected-value proxy, IV adjustment,
and Greeks diagnostics remain secondary explanations/gates. They are heuristics,
not calibrated probabilities, expectancy, or profitability evidence, and they
cannot break a premium-velocity tie.

## Deterministic ordering

The backend sorts candidates by:

```text
quality tier
→ event tier
→ descending executable-bid premium velocity
→ canonical ticker
→ expiration
→ strike
```

The existing underlying-diversity safeguard is applied after this ordering,
extended with a portfolio-aware concentration guard: an underlying you already
have open short options on receives at most ONE new pick (not the standard
cap), and each candidate carries `existing_exposure_contracts` for display.
The browser displays the backend fields and performs no ranking or premium math.

## Capital-aware sizing on every pick

CSP capacity is computed from true available cash after reserved short-put
collateral (`cash_available_for_csp`). Each pick displays: cash required,
income at the recommended size (recommended contracts × executable bid),
cash remaining after the trade, and — when portfolio history exists — what
percentage of the daily pace to the preset's growth target this trade covers.

## Exit playbook (open positions)

`core/exit_playbook.py::evaluate_exit` assigns each open short option one
deterministic verdict — HOLD, TAKE_PROFIT, ROLL, or CLOSE — with ranked
reasons. First matching rule wins:

1. CLOSE — earnings land before expiry while ITM or within 5% OTM.
2. CLOSE — deeply ITM beyond the preset threshold (default 15%).
3. CLOSE — |delta| breaches the exit level (default 0.65).
4. TAKE_PROFIT — ≥ 50% of entry credit captured (entry credit from Moomoo
   `avg_cost`; unknown credit disables the rule, never fakes it).
5. ROLL — DTE entered the roll window (default ≤ 21) while safely OTM.
6. HOLD — otherwise; proximity/decay notes ride along.

Verdicts are computed in `score_existing_position`, serialized on the wheel
decision, exposed via `/api/portfolio/roll-pressure`, and rendered on the
position monitor with reasons as tooltips. Early-assignment/dividend risk for
ITM short calls is intentionally not modeled: no free-tier dividend feed
exists, and inventing one would violate the broker-truth contract.

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
toward the active preset's `target_account_multiple`, annualized pace from
realized NAV change, ETA to target, required premium/day, and an on-track
verdict derived from realized pace — never a promised date.

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

## Evidence and freshness

Moomoo's `update_time` is preserved verbatim and parsed as
`America/New_York`. The adapter records a separate UTC fetch time for the
snapshot. Missing or invalid evidence fails closed while the market is open.
Aged snapshots remain visible for diagnosis but cannot copy.

This methodology does not claim positive expectancy. The scan ledger records
versioned inputs, tiers, bid basis, blockers, and top-signal evidence for
observability only.
