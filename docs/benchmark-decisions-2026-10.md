# Benchmark & strategy decisions — 2026-10-25

Goal: verified net annualized return > 50% p.a. on deployed capital,
measured by walk-forward replay (`tools/replay_signals.py`).
Baseline (task-1): all put rules lose to SPY/cash; published picks mean ROC
−8.7% (annualized ≈ −269%); account NAV −6.04% vs cash −0.67% over 61 days.
Every recommendation below is graded A/B/C, names an owner, and maps to this
codebase's contracts. The Bitter Lesson applies: expected gains come from
scalable measurement (replay over many candidate rules/features), not from
hand-tuning one more heuristic — so the doc prioritizes harness-driven
selection and exit search over new ad-hoc rules.

## 1. Deep dives (5 most relevant)

### 1.1 Kronos (shiyu-coder/Kronos) — vol/price foundation model
- Facts: pre-trained autoregressive TSFM on 12B K-line records, 45 exchanges;
  zero-shot price RankIC +93% over leading TSFM, volatility-forecast MAE −9%,
  synthetic K-line fidelity +22% (arXiv 2508.02739). Model is public.
- Transferable: a *realized-volatility forecast* as a decision-time feature
  for short-put selection (the exact task our pool loses on is assignment
  risk). We already persist Moomoo daily candles (replay uses them).
- Blocked by: watchlist-only data, quota budget, heavyweight local dependency,
  and proof that daily-equity vol beats a cheap baseline (IV rank,
  20d/60d realized-vol ratio) OOS.
- **Verdict: C — offline `_tmp/` experiment only, gated on OOS improvement
  over the deterministic baseline. Never runtime until proven.**
  Owner: tools/replay harness; user approves promotion.

### 1.2 Qlib (microsoft/qlib) — quant pipeline framework
- Facts: Alpha158/Alpha360 formulaic factor libraries (price, volume,
  volatility, liquidity, momentum groups), model zoo (LightGBM et al.),
  workflow automation, walk-forward backtests.
- Transferable: the *factor formulas* (not Qlib itself) as deterministic pure
  feature helpers — momentum, vol ratios, liquidity — computed from Moomoo
  candles at decision time; and the pattern of a downstream ranker consuming
  features.
- Blocked by: repo convention "prefer deterministic pure helpers"; Qlib as a
  dependency is heavy, its data layer conflicts with Moomoo-only source of
  truth, and our sample (83 candidates/11 cohorts) is tiny for training.
- **Verdict: A (feature set v1, hand-derived subset of Alpha158 logic) /
  B (LightGBM ranker, offline, gated on ≥8 OOS cohorts).**
  Owner: `core/scoring` (new pure helpers) + `tools/replay_signals.py`.

### 1.3 TimesFM (google-research/timesfm) — zero-shot TSFM
- Facts: pre-trained TSFM, strong zero-shot univariate forecasting; TimesFM-3
  adds multivariate in one forward pass (research.google blog);
  generalization is best on synthetic/structurally similar data.
- Transferable: same role as Kronos (vol/return forecast feature). Its
  sequential-econometrics framing is a good mental model for the 21-DTE
  horizon question, but no tested advantage over our data's simple baselines.
- **Verdict: C — optional offline experiment; Kronos or TimesFM is chosen
  only if one beats the EWMA/IV-rank baseline OOS by a predeclared margin.**
  Owner: `_tmp/` experiments; user approves promotion.

### 1.4 freqtrade (freqtrade/freqtrade) — exit framework + hyperopt
- Facts: mature exit framework — ROI tables, trailing stops, custom
  exit/stoploss callbacks — plus walk-forward analysis and hyperopt
  (freqtrade.io strategy-callbacks/bot-basics docs).
- Transferable: **the exit/roll policy is exactly the leak task-1 measured
  (replay is hold-to-expiry only; assignment leg is the loss engine)**.
  Pattern: credit-target exits (e.g., 50%/80% of max credit), loss stop
  (2× credit), roll at ≤21 DTE, and a share-stop after assignment. The
  stoploss+trailing structure maps directly to managing assigned positions.
- Blocked by: nothing technical — replay must model *manual* exits
  (signals-only), which is precisely what the tool can do.
- **Verdict: A — add an exit-policy model to the replay harness, then
  publish exit/roll copy suggestions in the UI only if replay shows edge.**
  Owner: `core/exit_playbook.py` (policy), `tools/replay_signals.py`
  (measurement), `frontend/` + `api/routes/` (suggestions, after proof).

### 1.5 ai-hedge-fund (virattt/ai-hedge-fund) — LLM agent pipeline
- Facts: educational PoC; does not place real trades (paper ledger,
  hash-chained NAV history, approval step before each simulated decision,
  kill switch); investor agents (Buffett/Druckenmiller/Graham/Lynch/Munger
  signals), incl. a **PEAD (post-earnings drift) signal**, risk-limits
  module, scorecard, strategy YAMLs; needs external data API keys.
- Transferable: (a) the hard-gate → risk-limit → ranking decision flow is
  what the repo already implements (`risk_gate`, hard gates, `rank_key`);
  (b) the **PEAD signal is a concrete new signal type**: we already persist
  the earnings calendar and `event_safe` logic — a post-earnings drift
  screen built from Moomoo candles is diagnostics-free and replay-testable;
  (c) paper-ledger/approval-step/kill-switch = our signals-only + immutable
  snapshot contract, already stronger.
- Blocked by: runtime LLM advisory is contract-forbidden; external data
  cannot create picks.
- **Verdict: B (earnings-drift signal screen) / C (LLM agents — reject at
  runtime; keep only as a pattern reference in docs).**
  Owner: `api/services/earnings` + replay; user approves any signal
  publishing.

## 2. Lighter coverage — explicit relevance verdicts

| Repo | Relevance to this desk | Verdict |
|---|---|---|
| TradingAgents (TauricResearch) | LLM multi-agent advisory/trading; video+API plumbing | C-reject: runtime LLM + external data banned; no code transfer |
| daily_stock_analysis (ZhuLinsen) | LLM daily briefing reports | C: briefing UX idea only, LLM runtime banned; no adoption |
| RD-Agent (microsoft) | Agents that generate strategies and backtest them in a loop | C-concept: our goal loop + replay already embody it; no dependency |
| OpenBB | Market-data plumbing platform | C-reject: Moomoo is sole data source; at most diagnostics |
| ccxt (ccxt/ccxt) | Exchange connectivity/execution | Reject: execution surface; prohibited |
| hummingbot (hummingbot) | Market-making bots | Reject: execution; nothing transferable to weekly CSP scanning |
| NautilusTrader (nautechsystems) | Backtest/execution engine | C: event-driven backtest ideas already present in bespoke replay; no rewrite |
| Lightweight Charts (tradingview) | Charting library | C: display-only diagnostics if a chart view is ever wanted; zero strategic value |
| Alpaca MCP (alpacahq) | Broker MCP for agents | Reject: execution surface + tradeable data path; prohibited |

## 3. Recommendation ledger (A/B/C)

| # | Recommendation | Grade | Owner (code / approver) | Constraint map |
|---|---|---|---|---|
| R1 | Replay exit-policy model: credit targets 50/80%, stop 2× credit, roll ≤21 DTE, post-assignment share stop; per-rule P&L series | A | `tools/replay_signals.py` + `core/exit_playbook.py` / user (strategy owner) | Signals-only: replay models manual exits; no order surface |
| R2 | Decision-time feature set v1 (Alpha158-subset): 20/60d realized-vol ratio, IV rank (exists), 20d momentum, gap, spread %, OI/liquidity | A | `core/scoring` pure helpers / user | Moomoo candles only; deterministic; no extra live quota |
| R3 | Rank variants consuming R2 features, all measured by the same replay; promote only variants beating POOL OOS (≥8 cohorts) | A | `tools/replay_signals.py`, `api/services/recommendation_ranking.py` / user | `rank_key` precedence doc updated only on promotion |
| R4 | LightGBM ranker trained on decision-time features only | B | replay extension + `core/scoring` exports / user | Offline; OOS-gated; no runtime dep until it beats R2 |
| R5 | Earnings-drift (PEAD) signal screen on watchlist, replay-measured | B | `api/services/earnings`, replay / user | Uses existing calendar + `event_safe`; Moomoo-only |
| R6 | Kronos/TimesFM vol-forecast feature | C | `_tmp/` experiments / user approves promotion | Offline; must beat IV-rank baseline OOS by predeclared margin |
| R7 | Frontend briefing/chart view (Lightweight Charts) | C | `frontend/` / user | Display-only; last priority |
| R8 | LLM agents (ai-hedge-fund/TradingAgents), execution engines (ccxt/hummingbot/Nautilus/Alpaca MCP), external data picks (OpenBB) | C-reject | — | Contract: no LLM advisory in runtime, no execution, Moomoo-only actionability |

## 4. Implied task-3 work plan (ordered)

1. Replay: exit-policy model (R1) with predeclared exit variants; keep
   hold-to-expiry as the reference series.
2. Replay: feature computation + alternative rank keys (R2/R3); verdicts
   predeclared (e.g., promote ⇔ OOS excess/cap-day > POOL AND 90% CI above
   the current rule).
3. Production only for survivors: ranking change, exit/roll copy suggestions
   per holding, earnings-drift screen — each with SCORING.md / README /
   CHANGELOG updates (contract changes recorded per repo rules).
4. Offline experiments (R4/R6) continue in `_tmp/` regardless, for the next
   improvement cycle.

Honesty note: with 11 cohorts / 83 candidates / 2 OOS cohorts, no single
lever can be "proven" yet; the plan stacks levers (exit policy, selection
features, event timing) and requires each promoted change to hold its own
OOS beat. The 50%/yr target is a sign change plus a multiple of the current
edge; both are far outside the measurement window today, which is why the
decision rule thresholds are predeclared and conservative from the start.
## Task-3 measurement results (2026-10-25)

Implemented in `tools/replay_signals.py` (measurement-only; no runtime-path change): an exit-policy model (hold / x50 / x80 / stop2 / roll21, Black-Scholes value paths at the entry-implied IV, per-policy P&L, second option fee on early closes) and decision-time feature variants (R5_calm: low 60d/20d realized-vol ratio; R5_mom: 20d momentum; R5_liq: bid/ask spread <= 3%, using closes strictly before the entry date). Replay: 11 cohorts, 3 resolved in-window for the series, 2 OOS folds.

### Exit policies — base case (0.25 slippage, 4% cash), in-sample

| Series | hold mean ROC | stop2 mean ROC | roll21 mean ROC | hold assigned | stop2 assigned |
|---|---|---|---|---|---|
| R0_published | -8.74% | +3.23% | +3.91% | 78% | 11% |
| R1_current | -10.03% | +2.37% | +2.37% | 67% | 0% |
| POOL | -6.56% | +1.98% | +1.99% | 60% | 0% |

stop2/roll21 flip mean ROC positive on every series; the 90% CIs for R0 stop2 (+0.0026..+0.0154) and roll21 (+0.0019..+0.0200) sit entirely above zero; assignment rate falls 78% -> <=11%. This matches the baseline diagnosis: the loss engine is the assignment/share leg, and both rules cut that leg while keeping most of the credit.

### R5 feature variants — verdict: NO EDGE, rejected

- R5_calm: 2 trades, mean ROC -11.9% (worse than POOL hold).
- R5_mom: 3 trades, mean ROC -9.9% (no improvement).
- R5_liq: 30 of 31 pool candidates exceed a 3% spread on the watchlist (penny/illiquid names); 1 trade, +2.9%, n=1 not meaningful.
- Feature coverage itself was complete: 307/307 pool contracts had pre-entry closes (75-day lookback).

Per the predeclared promotion rule (R3 in this doc), nothing is promoted from either workstream: exit policies stay measurement-only until an out-of-sample walk-forward over policies resolves (>= 8 OOS cohorts needed; 2 exist today), and R5 variants are rejected outright.

### Annualization honesty

The 50% p.a. target is NOT verified. In-sample stop2 excess is +0.0047 per deployed capital-day vs -0.0074 hold (R0, base case); naive daily compounding would be misleading at 9 trades across 3 cohorts and is not claimed. The verified statement today: current-system replay (hold) loses on every measure; stop2/roll21 flip the per-trade sign in-sample with CIs above zero; confirmation requires cohorts that accrue weekly.

### Verification gates (all run 2026-10-25)

ruff check/format: clean (182 files). architecture.py check: OK (73 modules, 406 imports); impact: no production capability affected (replay tooling only). ci_pytest: full suite passed (incl. tests/test_no_execution_surface.py — no execution surface or mock-data behavior added; new replay tests: 3). vitest: 15 files / 173 tests passed. e2e: 24 passed, 1 skipped (market-open-only), 1 flake class (`evening-staging.spec.js` copy-button "Copied" timeout) reproduced identically on unmodified HEAD via `git stash` — pre-existing, unrelated to this work.

No production contract changed, so CHANGELOG/SCORING/README were left untouched per the repo contract (docs update only on public-contract or user-facing behavior change); this doc is the durable record.

## Sizing and event-timing measurement (2026-10-25, second pass)

Closed two previously-absent dimensions with persisted decision-time data (rich `csp_picks` tape in every run snapshot):

- **Sizing (S_budget)**: greedy budget-constrained deployment in persisted rank order — a contract fits only when its persisted `capital_required` x `recommended_contracts` fits the `cash_available_for_csp` recorded at decision time. Result (base case): 2 trades, mean ROC **-1.60%** vs R1_current -10.03% and POOL hold -6.56%; excess/cap-day -0.00124 vs R1 -0.00241. In-sample improvement consistent with the diagnosis (binding cash cap forced the losing tail out). n=2; measurement-only, promotion still OOS-gated.
- **New signal type, earnings timing (R6_no_event)**: excludes contracts with a persisted `days_to_earnings` inside the holding window (decision-time value; unknown dates stay eligible). Result: identical to R1 (3 trades, -10.03%) — nearly all candidates already had no scheduled-earnings evidence at decision time, so the filter rarely fires. **Null result, not promoted.**
- **Event-gate audit (POOL trades, base case)**: candidates the tail gate flagged `research_only` realized mean ROC **-26.9%** (n=1) vs eligible **-1.5%** (n=4) — the existing research-only gate demonstrably avoided the worst realized candidate. Confirms the 2026-10-23 owner gate decision.

### Why OOS cannot resolve from persisted data (enumeration evidence)

The 50% p.a. verification needs >= 8 resolved OOS folds; 2 exist. Checked every persisted source: run_metadata snapshots span 2026-08-21..10-23 only (57 rows; older schema removed in the 2026-08-02 consolidation); the 226 pre-August `scan_ledger` rows carry only ticker/strike/option_type/score/annualized_return — **no bid/ask/expiration/delta/EV**, so executable candidates cannot be reconstructed and trades cannot be settled; option_chain data is retention-pruned (14d). The 11-cohort window is the complete reconstructable signal history; OOS folds accrue weekly from live runs.

## Policy walk-forward (2026-10-25, third pass)

Closed the mechanism gap that kept the OOS promotion gate data-stalled: hold-to-expiry settlement
required expiries that had not arrived, so OOS folds could only resolve at ~weekly cadence. Exit
policies resolve EARLY, and a trigger on a past bar is observable in persisted candles even when
expiry is future. New in `tools/replay_signals.py`:

- `policy_exit_early()`: first-trigger-wins resolution on observed bars only ("not_expired" when no
  trigger fired), same fees/P&L as `policy_trade`, baselines at the trigger-bar close.
- Walk-forward training purge switched from `expiration < entry` to resolution time
  (`entry + days < entry`): identical for hold trades, correct for early-exiting policy trades
  (a trade is knowable at its trigger date, not its expiry).
- `R1_stop2` / `R1_roll21` walk-forward candidates; per-policy OOS cells in the report.

Current state (all 11 cohorts): folds 0-4 skipped (insufficient resolved training), folds 5-6 choose
R2_no_tail_gate, folds 7-10 choose **R1_stop2** on training evidence. Policy OOS test trades: 0
resolvable — the chosen test cohorts' contracts fired no trigger within the observed bars, so their
OOS resolution accrues with new candles like everything else. Hold OOS unchanged: 2 cohorts, 6
trades, excess/cap-day -0.0250.

Predeclared promotion rule: a policy is OOS-confirmed when it is chosen by >= 8 resolved
walk-forward folds AND its pooled OOS excess per capital-day exceeds the hold path's pooled OOS
excess. Mechanism complete; evidence accrues weekly from live runs.

## Policy walk-forward feed bug found and fixed (2026-10-25, fourth pass)

Audit found the wf feed introduced on 2026-10-25 was dispatching `policy_exit_early` with the
feed-key names (`R1_stop2` / `R1_roll21`) instead of the bare policy names (`stop2` / `roll21`)
that `_policy_exit` dispatches on. Result: every unexpired contract resolved "not_expired" — the
policy OOS feed was dead from birth; policy OOS folds could never receive test trades. Verified by
reproduction (driver resolving the same contracts directly) and fixed (`policy[len("R1_"):]` at the
call site), with a regression test through `replay()` (`test_replay_wf_feed_resolves_roll21_for_
unexpired_contract`) proving roll21 resolves a real trigger while stop2 stays unresolved on the
same fixture.

Measured effect of the fix (final report.json, run 10):

- `R1_stop2`: 1 resolved wf trade (NFLX, cohort 7); chosen on training folds 7-9; **1 resolved OOS
  fold with pooled OOS excess/cap-day +0.00197 vs hold path -0.02504 on the same instrument.**
- `R1_roll21`: 3 resolved wf trades (NFLX k7, ASTS k9, IREN k9); chosen on fold 10; 0 resolved OOS
  folds so far (cohort 10 has no post-entry bars).
- Hold path unchanged: 2 OOS cohorts, 6 trades, excess/cap-day -0.02504.

The predeclared promotion rule (>= 8 resolved folds with pooled OOS excess above the hold path)
is still not met: 1 resolved policy-OOS fold exists; the remainder accrues weekly as triggers and
expiries resolve from the live desk. The measured sign of the available OOS evidence is positive
for stop2, but n=1 fold is not promotable evidence.

## TFT Finance (user-surfaced, zadescoxp/Temporal-Fusion-Transformer-Price-Prediction) — verdict: rejected

Reviewed 2026-10-25 (repo cloned and README/backend inspected). One Temporal Fusion Transformer per
ticker, daily closes 2010..2023 cutoff, frozen-weights OOS 2024+, 252-day quantile path (0.1/0.5/0.9),
long-or-flat signal after ~30 bp round-trip costs, PyTorch, 30-90 min/ticker training, causal-discipline
methodology (trailing indicators, no backfill, scalers on train slice only).

Not transferred. Reasons: (a) it addresses long-equity direction, not the desk's measured failure mode
(78% assignment, negative premium capture vs SPY/cash); (b) its OOS evidence needs years of price
history, but the gate needs years of persisted desk picks with bid/ask/EV — the 11-cohort window is
the complete reconstructable decision history, so a TFT overlay cannot clear the predeclared >=8-fold
promotion rule any sooner than the incumbent mechanisms; (c) the cheap analogues were already measured
and rejected (R5 momentum/calm/liq features: no edge; exit-early is stop2/roll21, measured best but
OOS-gated); (d) PyTorch + per-ticker training conflicts with the lean Windows-loopback, offline-testable
desk and with watchlist-only, Moomoo-only actionability (external models may diagnose, never create
picks). Methodology (causal features, frozen-weights OOS, cost-aware evaluation) already matches
replay_signals.py; nothing to import.
