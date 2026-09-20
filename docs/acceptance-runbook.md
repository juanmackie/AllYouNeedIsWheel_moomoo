# Windows/OpenD Acceptance Runbook — Staging → Copy → Reconcile

## Status: Sessions 1–3 UNEXECUTED · two measurement read-rounds executed 2026-09-20

> **Sessions 1–3 below are still UNEXECUTED.** They require a live Moomoo/OpenD login and a
> REAL account; they cannot be executed from CI, a sandbox, or any machine without OpenD.
> No S1–S3 result cell is filled in by anyone but the owner during a real session. Any
> number in this document is a config default, a recorded broker result, or prior
> historical data clearly labeled `[Historical]` — never a fabricated session result.
>
> **Executed instead, and recorded below:** two measurement read-rounds on 2026-09-20 —
> the outcome-ingestion verification ("First real read") and the attribution round
> ("Second read"). Both ran against the owner's live REAL OpenD using query-only broker
> calls. They cover the outcome/attribution half of acceptance; staging, copying, latency,
> and position/collateral reconciliation remain unexercised.

Purpose: accept the daily wheel workflow on the owner's Windows + local OpenD setup,
measure real scan latency and OpenD limiter behavior, and record honest, broker-verified
outcomes — in exactly three sessions:

1. **Australian-evening staging session** — complete watchlist-union scan, staged tickets
   with visible blockers.
2. **US-open copying session** — live copy, revalidation, quantity/collateral checks.
3. **Next-morning reconciliation** — positions and fills vs Moomoo, outcomes honest/unknown
   where unsupported.

## Hard boundaries (apply to every session)

- **Signals-only, broker-read-only.** The app never places, modifies, unlocks, or cancels
  orders; `core/broker_protocol.py`, `readonly=false` rejection, and
  `tests/test_no_execution_surface.py` are not weakened. The owner places every order
  manually in the Moomoo UI. `SIMULATE` vs `REAL`: validity runs may use `SIMULATE`, but a
  copy/reconciliation session that compares against real Moomoo positions and fills must be
  on `REAL` with a configured `account_id`.
- **No fabrication.** If Moomoo is unreachable or a value cannot be compared, record
  `unknown` / `blocked` instead of substituting simulated data or app-internal values.
- **Measured performance precedes any quota change or speed promise.** No
  `connection.json` `chain_*` value is raised and no "~110 s → ~20–40 s" style claim is
  made until Sessions 1–2 log the metrics in the Decision gate (§"Decision gate").
- **No code changes in this runbook.** If a session exposes a bug, record it and fix it as
  a separate tested change (own scope, tests, ruff, then a follow-up note here).
- **Immutable run snapshots.** A refresh publishes one new `WheelRunSnapshot` atomically;
  it never rewrites or relabels a previous run. A failed attempt never erases the last
  good snapshot.

## Prerequisites (owner's machine, each session)

1. Windows 10/11, `.venv` created via `start_local.ps1` (`uv sync --locked --all-groups`).
2. Moomoo OpenD running and logged in (loopback `127.0.0.1:11111`).
3. `connection.json` points at the intended environment. Local tuning keys (defaults in
   `config.py` / `connection.json.example`): `chain_rate_limit_max_requests` (10),
   `chain_rate_limit_window_sec` (30), `chain_min_request_spacing_sec` (3.0),
   `broker_cache_after_hours` (true); plus `portfolio_env`, `account_id`, `security_firm`.
   Record every value **before** starting a session in the session log.
4. App started via `start_local.ps1` on `127.0.0.1:8000`. A restart is required between
   tuning steps so the chain limiter re-reads config.
5. Watchlist: confirm the effective union and its size with `GET /api/watchlist`
   (`union`, `count`, per-ticker origin labels). The scan universe is the **complete
   canonical union** — never a broad market scan, never silently truncated.
6. Market clock: US RTH is 09:30–16:00 ET Mon–Fri (`is_market_open()`). The Australian
   evening can land either side of it depending on season — run whatever the clock gives,
   and record it; don't force a branch.

## Metrics vocabulary (what to log and where it comes from)

| Metric | Source |
|---|---|
| Complete-coverage % | `GET /api/run` → `run.coverage_scanned / run.coverage_total`; 100 % + no errors is required for a staged/live copy ticket. Run strip shows coverage live. |
| Cold vs warm scan duration | `[TIMING]` lines in console/log: `Get connection`, `Portfolio context`, `Watchlist CSP scan`, `Covered call scan`, `Scoring & ranking`, `Total`. Cold = first refresh after app/OpenD start; warm = immediate subsequent refresh reusing ticker cache + broker cache. |
| Per-lane attribution | `[TIMING] …CSP scan` vs `[TIMING] …Covered call scan`. Closed-market CC timings are artificially low when `broker_cache_after_hours=true` serves persisted chains — attribute, don't extrapolate. |
| OpenD request counts | `GET /api/options/connection-status` → `rate_limit_stats.api_calls_count`, `rate_limit_events` (quote limiter) and `option_chain_rate_limit_stats.api_calls_count`, `rate_limit_events` (chain limiter). |
| Limiter waits | `option_chain_rate_limit_stats.rate_limit_waits`, `current_queue_length`, `min_request_spacing`, `adapted`; effective values in `option_chain_rate_limit_config` (`max_requests_per_window`, `rate_limit_window`, `min_request_spacing`). |
| Run state | `GET /api/run` → `state` / `effective_status` (`ready` | `planning` | `stale` | `partial`), `tradeable`, `market_state`, `errors`. |
| Blocker / ticket branch | Per card: `copy_eligible`, `recommended_contracts`, quality tier, event tier; dashboard button text (live / staged / blocked + reason). |
| Outcomes (Session 3) | `GET /api/options/analytics/outcomes`; ingest via `POST /api/options/analytics/outcomes/ingest` (query-only, rate-limited, `502` on broker failure). |

Ticket semantics (from the app, not this runbook's choices):
- **Live copy** — current complete coverage + fresh broker evidence → explicit limit draft
  on the current quote.
- **Staged copy** — US-market-closed workflow on a complete-coverage run (premium is the
  last broker quote, **NOT live**; text says "verify the live quote at open"). Timer window
  is real: a persisted-`ready` run stays fresh only `max_tradeable_age_sec` (default 300 s).
- **Blocked / review-only** — partial coverage, stale quotes, crossed markets, yfinance
  fallback, insufficient capacity, research-only mode, or an infeasible union
  (`planning` with `recommended_max_size`). Blockers must show a visible reason, never a
  silent pass.

---

## Session 1 — Australian-evening staging session

Goal: one complete union scan in the owner's evening (US market likely closed), producing
the full shortlist where every card is either a staged ticket or a visible blocker; record
cold/warm latency and limiter metrics at the **current** `connection.json` values.

**Steps**

1. Prerequisites (§ above): OpenD logged in, app running, `connection.json` values and
   union size recorded first.
2. Cold refresh (stop-and-restart the app so the limiter reads config, then
   `Refresh run`): capture the `[TIMING]` lines and the `RefreshAttempt.stage/progress`
   advance on the run strip.
3. Immediately repeat → warm refresh; capture the same `[TIMING]` lines.
4. After each: `GET /api/run` (state, coverage %, freshness, errors) and
   `GET /api/options/connection-status` (request counts, limiter waits) into the log.
5. For the top-3 cards, confirm ordering is descending executable return on deployed
   capital per day, then executable-bid premium velocity per day tie-break, then
   ticker/expiry/strike/option-type; quality/event tiers, midpoint, and composite score
   must not reorder cards.
6. For every card record the ticket branch: **staged** (complete coverage, persisted-`ready`
   market closes in-window) or **visible blocker** with its exact reason text. A
   closed-market run that persists `planning` yields blocked/review-only tickets — that is
   an expected branch to record, not a failure (unless a card shows a blocker that shouldn't
   apply, e.g. coverage < 100 % on a complete scan).
7. Do **not** present any closed-market timing as full-scanner latency: CSP lanes may be
   skipped or served last-session chains when closed. Full CSP + tradeable-path timing is
   Session 2's job.

**Compare against Moomoo** (per card, against the Moomoo app / chain): ticker, expiry,
strike; executable bid premium per contract vs Moomoo's last quote; midpoint limit target
(label "not guaranteed"); DTE; spread; OI/volume; chain source (must be Moomoo); broker
`update_time` (America/New_York) and UTC fetch time. Also account value, cash available for
CSP, reserved short-put collateral, and buying power vs Moomoo's account; union count vs
watchlist panel.

**Metrics to log** — see vocabulary table: coverage % (must be 100 %), cold total s and
per-lane s, warm total s and per-lane s, OpenD quote + chain request counts,
`rate_limit_waits` / queue depth / effective spacing / `adapted`, run state, and the
staged-vs-blocked branch per card.

**Session-1 log (UNEXECUTED — owner fills)**

| # | connection.json values | union size | market_state | run state | cold total (s) | warm total (s) | CSP lane (s) | CC lane (s) | coverage % | quote reqs | chain reqs | limiter waits | adapted | staged | blocked (reason) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |

---

## Session 2 — US-open copying session

Goal: at/around the US open with fresh Moomoo quotes, run on the Session-1-verified values,
revalidate each staged candidate, and execute the manual copy-to-ticket flow with
quantity/collateral checks. This is **the only session that may exercise a live ticket**
(owner places it manually in Moomoo; the app only copies text).

**Steps**

1. Prerequisites; record `connection.json` values (should match Session 1's final clean row,
   or Session 1's decision-gate result).
2. Cold refresh at open. Acceptance: quotes now fresh → run must be `ready` with
   complete-union coverage; if not, record the honest reason (stale quote, partial coverage,
   planning preflight, OpenD error) and stop.
3. **Revalidate before every copy** (recomputed at the point of use, per card):
   - Live quote on Moomoo ≈ card's executable bid; midpoint is a limit target, not guaranteed.
   - Event tier visible (event_safe / not_applicable / earnings-before-expiry /
     event_unknown); a card with `event_unknown` or earnings in window carries a visible
     warning line on the ticket.
   - `recommended_contracts` quantity vs max affordable by cash (100 × strike per contract)
     and vs 100-share owned for covered calls.
   - Cash required vs `cash_available_for_csp` minus reserved short-put collateral; margin
     buying power is display-only.
4. Copy the live draft and paste into Moomoo order entry; verify action (SELL TO OPEN
   CSP / SELL TO OPEN COVERED CALL), ticker, expiry, strike, x qty, limit. If Session 1's
   staging branch is in effect at this moment (closed market), the ticket is staged and must
   say "verify the live quote before placing" — place with a verified quote.
5. Confirm no UI or API path places, unlocks, cancels, or modifies an order.
6. **Tuning (only with owner's go and built on Session-1 data)** — see Decision gate below.
   If tuning, one step at a time with a restart each step; stop rules apply; revert to last
   clean step. A fresh-open validation cannot be truthfully completed while
   `is_market_open()` is false.

**Compare against Moomoo**: live quote per card at copy time vs card premium; quantity vs
affordable contracts; collateral reserved for open short puts vs Moomoo positions; account
cash after reflecting the planned orders; per-symbol chain freshness; union coverage at
copy time must still be 100 %.

**Metrics to log**: Session-1 metrics plus, per card, ticket type (live vs staged), quote
age per symbol (from `quote_fetched_at` vs fetch time), every blocker reason hit, and
whether the order of cards changed vs Session 1 (it must not change from tiers).

**Session-2 log (UNEXECUTED — owner fills)**

| # | market_state | run state | tradeable | coverage % | cold total (s) | per-card: live/staged/blocked | quantity vs affordable OK | collateral vs Moomoo OK | costBasis cash check OK |
|---|---|---|---|---|---|---|---|---|---|
| S2 | — | — | — | — | — | — | — | — | — |

---

## Session 3 — Next-morning reconciliation

Goal: after orders have had a chance to fill, compare the app's positions/fills/outcomes
against Moomoo and record results — **measured where broker-verified, unknown where not**.

**Steps**

1. Refresh again for fresh Moomoo quotes; open the position monitor (open short options with
   HOLD / TAKE_PROFIT / ROLL / CLOSE verdicts).
2. `Pull broker fills` (query-only; rate-limited; `502` on broker failure). Open the
   Outcomes panel and compare each quoted-vs-filled row; expand the drill-down to its
   supporting fill transactions (credit, fee, time, qty).
3. Compare positions vs Moomoo: every open short put / covered call — ticker, expiry,
   strike, qty, status; reserved collateral vs Moomoo; assignments; cash movement from
   premium and from any assignment.
4. **Outcome honesty rule:** only broker-verified fills count as **measured**. Contracts
   whose fills could not be pulled (broker down, out of window, never filled) are
   **unknown** or **pending** — never inferred, never fabricated. With no fills the panel
   shows empty states. Report each Session-2 ticket's outcome exactly once: filled at what
   price vs limit (slippage), still open, expired, or assigned/unknown.
5. Record which staged/live tickets filled, and update the Session-1/2 tick rows with the
   eventual outcome.

**Compare against Moomoo**: positions (qty × strike/expiry), fills (price, time, qty, fees),
cash balance reflecting premium/assignment, closed positions, and the outcome panel
aggregates (sample size, coverage %, measured/unknown/pending counts, net outcome,
capital-days, owner $/day, avg slippage).

**Metrics to log**: measured / unknown / pending counts, sample size, net outcome,
capital-days, owner $/day, avg slippage, verdict distribution per position, and which
Session-2 tickets are measured vs unsupported.

**Session-3 log (UNEXECUTED — owner fills)**

| # | fills pulled | measured | unknown / pending | net outcome | slippage avg | open positions ok | collateral ok | assignments |
|---|---|---|---|---|---|---|---|---|
| S3 | — | — | — | — | — | — | — | — |

---

## First real read — 2026-09-20 (ingest verification; **NOT** Session 3)

This is not the three-session acceptance run. Sessions 1–3 are still UNEXECUTED. What was
executed is the outcome-ingestion path against a live REAL OpenD connection, to find out
whether the measurement loop produces honest numbers at all.

**What it fixed first.** `MoomooConnection.resolve_portfolio_identity()` resolved the
account before connecting, so a cold connection had no trade context, the account list came
back empty, and the call raised `ValueError: Configured REAL account_id … is not available
in OpenD.` Because `FillsService._identity()` is its only caller,
`POST /api/options/analytics/outcomes/ingest` returned **502** until some other broker read
happened to connect the shared instance first. Fixed in `core/connection_manager.py`
(connect on demand, and a `ConnectionError` that names OpenD instead of blaming the
account); regression tests in `tests/test_connection.py`. No log in `logs/` contains any
prior ingest request, so this was the first successful ingest on this machine.

**Command executed** (app served by `run_api.py`, loopback, read-only broker surface):

```
POST http://127.0.0.1:8000/api/options/analytics/outcomes/ingest?days=90&cash_flow_days=7
→ 200 {"fills": {"ok": true, "seen": 45, "ingested": 45, "fees_updated": 45},
         "cash_flows": {"ok": true, "seen": 0, "ingested": 0}}
```

Re-POST immediately: `seen 45, ingested 0, fees_updated 0` — idempotent against real data.

**Stored evidence** (all broker-verified, account-scoped `REAL` / opaque id):

| Item | Value | Evidence quality |
|---|---|---|
| `option_fills` rows | 45 (20 OPT SELL, 21 OPT BUY, 4 STK BUY) | broker-verified deals |
| Fill window | 2026-06-23 → 2026-09-17 (89 days, SDK window) | broker-verified |
| Fees resolved | 45 / 45 rows, $22.80 total known | broker fee query |
| `account_cash_flows` rows | 0 (nothing in the last 7 clearing days) | broker-verified (empty) |
| `GET /api/options/analytics/outcomes` | sample 74, coverage 33.8 % | local SQLite join |
| — signals awaiting fills | 49 | no fill evidence |
| — trades with fill evidence | 25 (15 realized, 10 open/unknown) | broker-verified fills |
| Realized net (closed legs) | **+$2,547.03** gross premium P&L incl. known fees | broker-verified |
| Worst / best closed trade | −$228.97 (ORCL 185C 2026-09-18) / +$661.77 (ORCL 185C 2026-10-16) | broker-verified |
| Drawdown (closed legs) | $228.97 | broker-verified |
| Avg slippage vs quoted | **null** | not computable — see below |

**The load-bearing finding: signal attribution of realized trades is 0 %.**

47 distinct contracts were recommended across the 26 recorded runs; 25 distinct contracts
were actually traded in the same 89-day window; the **intersection is empty**. Every one of
the 15 realized outcomes has `signal_type: unmatched`, `attribution: unattributed`,
`preset_key: ""` and `event_tier: untiered`. Consequences:

- `avg_slippage_per_contract` is null because slippage needs a quoted credit from a matched
  signal; no measured trade has one.
- The realized +$2,547.03 describes **the trades that were made**, not **the app's picks**.
  It is not evidence for or against the ranking contract.
- Some of the window predates the first recorded run (2026-08-21), but that does not explain
  the empty intersection on its own: e.g. SOXL 20260828 P106 was sold 2026-08-10 and closed
  at 0.11 on 2026-08-28, while the 2026-08-30 run recommended SOXL 100P 20260918.

**Decision-gate conclusion.** The measurement loop is now mechanically closed (fills →
fees → realized outcomes), but the ranking still has **no measured basis**: zero realized
trades carry a signal attribution. Therefore the composite score keeps no demonstrated
claim to influence ordering, and the `aggressive` preset keeps no demonstrated basis — both
remain open questions, not resolved ones. To attribute anything, tickets from a run have to
be the tickets that get executed (same ticker/expiry/strike), or the app must record which
recommendation the owner actually acted on.

**Still unverified by this section:** Sessions 1–3 (staging, copy, position/collateral
reconciliation), complete-coverage and latency metrics, and anything requiring a browser
check of the Outcomes panel. `cash_flow_days=7` returned no rows; a wider cash-flow probe
remains unrun.

---

## Second read — 2026-09-20 (attribution round: ingest re-run + owner trade-history cross-check)

Purpose: re-run the broker ingest (idempotency), attempt the first owner-recorded
"taken" link, and record the before/after attribution state. The taken link is the
feature that makes a manual trade attributable to a recommendation even when the traded
contract differs from the suggested one.

### Ingest re-run (REAL, read-only, idempotent)

```
POST /api/options/analytics/outcomes/ingest?days=90&cash_flow_days=7
→ 200 {"fills": {"ok": true, "seen": 45, "ingested": 0, "fees_updated": 0},
       "cash_flows": {"ok": true, "seen": 0, "ingested": 0}}
```

45 rows seen, 0 new — idempotent against real data. The route is also no longer limited
to the newest run: `POST /api/run/taken` validates the run id against stored published
history (a link recorded the morning after the run it came from is the normal case).

### Attribution before / after

| Metric | Before | After | Note |
|---|---|---|---|
| `taken_link_count` | 0 | **0** | no link recorded — reason below |
| attributed + measured trades (owner-linked, realized) | 0 | **0** | unchanged |
| measured trades (realized, attributed or not) | 15 | 15 | broker-verified |
| pending / unknown | 49 / 10 | 49 / 10 | signals awaiting fills / open or fee-unknown |
| realized net across measured trades | +$2,547.03 | +$2,547.03 | the trades that were made, none attributable |
| `option_fills` / fees known | 45 / 45 | 45 / 45 | unchanged |

### Why no link exists (owner-supplied evidence)

The owner provided their full account trade history
(`History-Universal Account(<redacted>)-<date>.csv`, 156 rows, export generated
2026-09-20) and stated: *"i need signals, but the app lacks and didnt provide any"*.

Cross-check performed locally (read-only, contracts parsed from symbols, ticker
normalization verified after an initial `lstrip("US.")` bug in the check itself — SOFI
parsed as "OFI", UBER as "BER" — which was corrected before recording anything):

- owner history: 75 filled rows, 71 option fills, **38 sell entries**, Apr 13 → Sep 9 2026;
- app recommendations on record: **208** contract recommendations across 26 runs;
- **exact recommendation → trade matches: 0** — not one trade was the contract the app
  suggested, so no fill can ever self-attribute;
- 14 distinct same-ticker/different-contract pairs, for example:
  - run 2026-08-30 recommended **UBER 20260918 C80** → owner sold UBER 20261002 C78 @ 0.91 (11.2 d later)
  - run 2026-08-30 recommended **SPCX 20260918 C142** → owner sold SPCX 20261002 C162.5 @ 2.87 (11.2 d later)
  - run 2026-09-10 recommended **ORCL 20260918 C162.5** → owner sold ORCL 20261009 C150 @ 5.30 (3.9 d later)
- **no put (CSP) entry** in the owner's history corresponds to any CSP pick; the ranked
  capital-velocity lane (SOXL 100P, NFLX 76/77P, INTC 83P) was never traded.

The owner's CSV corroborates the app's broker view: its NOK 20261002 P9.5 buyback
(4 @ 0.11) is the same fill the app ingested, so the app is reading the account the
owner actually trades (the export labels it "Universal Account(<redacted>)"; the app's
configured `acc_id` is a different number for the same activity).

**Conclusion recorded, not glossed:** the 0 % attribution is not a plumbing defect. The
app's ranked picks are not the contracts the owner trades — the owner trades the same
underlyings at their own strikes and expiries. The taken link is exactly the right
instrument for this (it exists so a different strike can still attribute), but it can
only record what actually happened, and nothing did. The first real link will be
recorded the first time a pick is traded; the capability itself is verified by route,
unit, integration and browser-render tests, and by the second read above.

**Verification limits of this section:** the link was never exercised against real
broker data because no qualifying trade exists; Sessions 1–3 remain unexecuted;
`cash_flow_days=7` again returned no rows.

---

## Decision gate — measured performance precedes any quota change or speed promise

- **No quota/speed change** is made from Sessions 1–2 evidence alone until at least one
  clean Session-1 and one clean Session-2 run are logged. The promise "~110 s cold →
  ~20–40 s" remains **unverified guesswork** until measured (Bitter Lesson Law 4).
- Tuning ladder (each step: restart app, cold refresh, capture all metrics):
  - Step A: `chain_min_request_spacing_sec` 3.0 → 1.5, quota unchanged (10/30 s).
  - Step B: spacing → 1.0; if clean, quota → 20/30 s.
  - **Stop rules** — never proceed past a step where: OpenD returns rate-limit errors,
    adaptive backoff engages on consecutive refreshes, or error/cancel storms appear in
    `logs/`. Revert to the last clean step and log it.
- Only a **second clean session** (any of the three, re-run) may justify promoting tuned
  values from `connection.json` into `config.py` defaults — that promotion is a follow-up,
  not part of this runbook.
- If a session still emits no `[TIMING]` lines, fix the logging sink as a separate tested
  change; never infer phase timings from the total alone.

## Acceptance criteria (pass = all true)

- Session 1: complete-union coverage 100 %; every top card staged or blocked with a visible
  reason; cold/warm and limiter metrics logged.
- Session 2: `ready` + complete coverage at open; ordering matches descending
  capital-return-per-day; copy branches behave per spec; quantity/collateral checks OK
  against Moomoo; zero unexplained rate-limit errors on the clean step.
- Session 3: fills pulled and every outcome measured, unknown, or pending — never invented;
  positions/collateral reconcile against Moomoo.
- No execution-capable controls exercised; broker-read-only intact; no run snapshot
  rewritten.

## Historical reference — prior closed-market tuning (executed 2026-08-30, NOT part of this runbook)

Kept verbatim for continuity. These were one-off closed-market covered-call samples on the
owner's machine; they predate this runbook and **do not** satisfy any acceptance criterion
(CSP skipped, market closed, pre-tuned config).

| Step | spacing | quota | Union size | Cold refresh total (s) | CSP scan (s) | Rate-limit errors | Backoff engaged | Run state |
|---|---|---|---|---|---|---|---|---|
| Baseline (after-hours cache) | 3.0 | 10/30s | 27 (CSP skipped; 4 CC chain calls served from cache) | 12.9 | N/A — market closed | 0 | no | planning |
| Controlled baseline (cache off) | 3.0 | 10/30s | 27 (CSP skipped; 8 CC chain calls) | 24.3 | N/A — market closed | 0 | no | planning |
| A | 1.5 | 10/30s | 27 (CSP skipped; 8 CC chain calls) | 14.0 | N/A — market closed | 0 | no | planning |
| B (selected) | 1.0 | 10/30s | 27 (CSP skipped; 8 CC chain calls) | 11.0 | N/A — market closed | 0 | no | planning |
| C | 1.0 | 20/30s | 27 (CSP skipped; 8 CC chain calls) | 11.0 | N/A — market closed | 0 | no | planning |

Prior-fix context: `OptionsService.portfolio_service` provider contract was restored
(regression covered in `tests/test_options_service.py`); selected local values at the time
were `chain_rate_limit_max_requests=10`, `chain_rate_limit_window_sec=30`,
`chain_min_request_spacing_sec=1.0`, `broker_cache_after_hours=true`; auto-refresh-at-open
was removed (manual refresh is the deliberate workflow). `[TIMING]` lines were not emitted
that day; timings came from `RefreshAttempt` timestamps — an observability gap rechecked in
Session 1.

## Follow-ups (not this runbook's scope)

- Promote tuned `chain_*` defaults to `config.py` only after a second clean session.
- If a session exposes a bug, fix it as its own tested change (regression test, ruff,
  `scripts/ci_pytest.py tests/ -q` for the narrow file, then `npm test` only if frontend).
- When Sessions 1–3 pass, tick the live-verification box in `plans/repo-review-todo-2026-08-26.md`.
---
> **Status note (2026-09, P2b reconciliation):** This runbook remains the required
> owner-run acceptance procedure referenced by `PLAN.md` step 11 and “Remaining
> open work”. It is not itself a code checklist; track its completion in `PLAN.md`.
