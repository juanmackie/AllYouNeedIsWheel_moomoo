# Plan: Apply Wheel Strategy Options Findings to the Watchlist Scanner

## Context

The external review of Wheel Strategy Options shows a useful pattern—broker/market data, explicit quality gates, derived metrics, and workflow UI—but no proven profitability engine. This repository already has the stricter product contract: rank only the user's watchlist, use Moomoo/OpenD as the sole actionable source, keep premium velocity primary, and remain signals-only/read-only.

The intended outcome is a lean improvement to the top-three CSP and call shortlist: reject or demote contracts that look attractive only because of stale/thin/wide-spread quotes, expose decision math clearly, and avoid importing broad-screening or opaque composite-score complexity that would move the product sideways.

The plan must also follow `production-ready-vibe-coding-rules V6.0.md`: question each requirement, delete duplication before adding code, use minimal cohesive changes, preserve safety boundaries, validate trust boundaries, and verify non-trivial behavior with focused and broad checks. `PLAN.md` is a planning artifact and must not be committed.

## Initial recommendation

Adopt only findings that strengthen qualification and explainability around premium velocity:

- preserve raw premium velocity as the final ranking key within risk/metadata tiers;
- evaluate spread quality, liquidity, DTE, the existing premium floor, cushion/breakeven, and event risk as explicit gates or transparent tiering—not as a replacement black-box rank;
- use only fresh Moomoo quotes/Greeks/portfolio facts for actionable picks;
- reuse existing presets, recommendation lanes, immutable run snapshots, and rejection diagnostics rather than creating a parallel screener;
- explicitly reject broad-market discovery, external actionable quotes, autonomous execution, and claims that a heuristic score proves expectancy.

The user selected a **single comprehensive** implementation plan, **tier-then-rank** behavior for marginal quote quality, **quality-first** ordering, **review-only** treatment for every lower-tier signal (visible but no copy-to-ticket), and **executable bid** as the premium basis for final ranking.

Current-code audit findings already narrow the change and exposed several pre-existing safety-contract violations that must be included because they directly affect the shortlist:

- `score_contract()` currently hard-rejects one-sided quotes, low mids/premiums, spreads above the preset maximum, and contracts where both OI and volume are below floors (`core/wheel_decision.py`). It then emits softer warnings for spread above the ideal and OI below ideal.
- `RecommendationEngine.get_top_recommendations()` already sorts known earnings metadata above unknown metadata and then by `premium_velocity_per_day()`, with an underlying-diversity safeguard (`api/services/recommendations.py`).
- Existing presets are intended to own DTE, delta/OTM, mid, premium, spread, OI, cash-fit, and position-size thresholds (`core/presets.py`), so a new preset system would be duplication. However, the CSP scorer currently passes the preset's flat profile into `WatchlistManager.get_screening_profile()` as `growth_mode_config`, while that function looks for a nested `screener_profile`; most preset thresholds therefore do **not** reach `score_contract()`. Fetch-time DTE/OTM filtering uses the preset, but scoring-time quality thresholds fall back to legacy defaults.
- The UI already exposes Moomoo provenance/freshness, unknown-risk demotion, breakeven buffer, expected-move buffer, preset, score drivers, and warnings (`frontend/static/js/dashboard/top-recommendations.js`). **Safety-contract surprise:** it loads a separate cached `/api/options/top-recommendations` response and always binds copy-to-ticket; it does not consume `WheelRunSnapshot.tradeable`. The operational strip polls `/api/run` separately, so the documented “only a ready immutable run can copy” rule is not actually enforced on recommendation cards.
- `SCORING.md` contains stale pre-consolidation material (Catalyst/social expansion, yfinance VIX, removed service paths) before an appended current ranking contract. Durable documentation needs consolidation rather than another addendum.
- Complete-union scanning can still truncate when a mocked/inconsistent preflight says `feasible=true` but recommends fewer symbols; a regression test explicitly requires the cap. Replace the cap with an invariant: scan all symbols or publish `planning`, never a silently partial “top three.”
- `PortfolioContext` comments distinguish true cash from margin, but then selects `broker_buying_power` for `cash_available_for_csp`; `/api/options/cash-status` duplicates the same mistake. This violates the explicit cash-secured-put contract. CSP affordability must use true withdrawable/available cash minus existing short-put collateral; margin buying power remains display-only context.

The likely core gap is therefore not missing formulas; it is a coherent **quality/risk-tier contract** between hard-invalid quotes and fully qualified quotes, plus visible premium-velocity/rank math and removal of stale/duplicate scoring narratives.

Additional evidence from the scoring and UI paths:

- Crossed markets (`ask < bid`) currently produce a negative spread and can pass every quote gate; the external live sample specifically observed this failure mode. This is invalid broker evidence and must hard-block, not merely demote.
- The installed Moomoo snapshot API exposes `update_time`, but `MoomooConnection.get_option_chain()` drops it. `score_contract()` then stamps `quote_timestamp` with local scoring time, while the run stamps every symbol with the overall generated time. `_is_quote_stale()` treats missing/invalid timestamps as fresh. The freshness gate therefore proves recent processing, not a fresh broker quote. Preserve both broker update time and local fetch time, parse the broker timestamp for the US market explicitly, and fail closed for actionable candidates when freshness evidence is missing/invalid/stale.
- `WheelRunSnapshot.to_dict()` persists the computed `tradeable` boolean. SQLite returns that JSON directly, so a snapshot saved as tradeable can remain `tradeable: true` after its quotes age out. Recompute effective `tradeable`/`stale` state on every `/api/run` read using a pure run-model helper; do not rewrite or relabel the persisted last-good snapshot.
- Imminent earnings currently reduce the secondary composite and add a warning, but the final recommendation sort ignores the composite; a high-velocity earnings contract can still lead the shortlist. Event state therefore belongs in explicit rank tiering.
- Marginal spread/OI already create warnings, but those warnings do not affect final velocity order. These are the natural inputs to the user-selected lower quality tier.
- The backend computes canonical premium velocity from midpoint premium, while the browser recomputes `premium_per_contract / dte` in multiple display/rationale paths. Change the canonical rank basis to `bid × 100 ÷ DTE`; carry `premium_velocity_per_day`, `bid_premium_per_contract`, and midpoint only as a separate non-guaranteed limit target so display and rank evidence cannot drift.
- Quote trust is represented twice: `quote_quality` in `WheelDecision` and a separate numeric confidence deduction in `core/growth_mode.py`. The comprehensive change should consolidate candidate qualification around one explicit classifier rather than add a third score.
- The frontend still labels the shortlist as “growth impact”/“2x portfolio impact” and emphasizes the secondary composite despite the current premium-velocity contract. It also builds ticket quantity from `max_contracts` before `recommended_contracts` and defaults missing quantity to one, while `_format_recommendation()` drops `recommended_contracts`. This copy and ticket sizing obscure or bypass the actual decision rule. `tests/README.md` likewise documents removed Catalyst/social/VIX/growth workflows, so its manual smoke contract must be replaced with the as-built one-screen run flow.
- The existing delta-based `pop` and fixed-10%-drop “expected value” are heuristic diagnostics, not calibrated probabilities/expectancy. They must be labelled as proxies/scenarios wherever retained; the plan will not replace them with Wheel Strategy Options' unvalidated POP model.

## Findings disposition

| External finding | Decision for this repository |
|---|---|
| Separate raw data, filters, rank, and workflow | Keep and tighten the existing Moomoo → decision → run snapshot → dashboard path. |
| DTE, delta/OTM, spread, liquidity, premium, breakeven/cushion, IV, earnings | Reuse existing calculations; fix preset delivery, quote validity, tier semantics, and labels instead of cloning Contract Score. |
| Broker confirmation before entry | Strengthen: bid-based rank, midpoint labelled as a limit target, and copy only from a fresh ready run plus a top-tier candidate. |
| Trade tracker / realised expectancy | Do not add here. Existing production data cannot support a backtest or profitability claim; scan-ledger evidence is retained for observability only. |
| Broad discovery, highest-yield/IV feeds, unusual volume, GEX | Reject: extra calls and feature breadth do not improve the watchlist-only top-three workflow. |
| Published Contract Score / POP model | Reject as a ranking replacement. Keep secondary diagnostics clearly labelled as heuristic proxies, not calibrated probabilities or expected profit. |
| Wheel Strategy Options API/REA/token behavior | Reject: no external actionable quote path, scraping dependency, copied gating token, or vendor coupling. |
| Dividend/ex-dividend context | Defer: the current broker adapter exposes no verified fresh ex-dividend field. Do not add another external hot-path provider without source/freshness evidence. |

## Exact behavior contract

- **Premium math:** canonical `premium_per_contract = bid × 100`; `premium_velocity_per_day = premium_per_contract ÷ DTE`. Midpoint is carried separately as `limit_target_per_contract = ((bid + ask) ÷ 2) × 100` and labelled “limit target—not guaranteed.” Cycle yield is bid credit divided by secured cash for CSPs (`strike × 100`) or underlying value for covered calls (`stock_price × 100`); annualized yield is cycle yield × `365 ÷ DTE`.
- **Hard invalid/block:** non-Moomoo actionable chain, invalid strike/expiry/DTE, non-positive or one-sided quote, crossed market (`ask < bid`), market-open quote with missing/invalid/stale broker update time, spread above the selected preset maximum, premium below the selected preset floor, both OI and volume below hard floors, missing IV/Greeks after the existing broker/derived enrichment, CSP outside preset DTE/OTM/cash constraints, or call without unencumbered 100-share lots. A locked market (`ask == bid`) is valid.
- **Quality tier:** `qualified` only when spread/OI/volume meet the existing ideal profile values; otherwise a hard-gate-passing contract is `marginal`. No hidden premium multiplier or fill model is introduced.
- **Event tier:** `event_safe` when fresh earnings metadata proves the next event is after expiry; broker-verified ETF/index underlyings are `event_not_applicable` at the same tier; `event_unknown` covers missing/stale/error/past metadata or unknown underlying type; `earnings_before_expiry` applies when a stock contract spans the next earnings date. External event data can only demote/restrict a Moomoo-created candidate.
- **Ordering:** `(quality tier, event tier, -bid velocity, canonical ticker, expiration, strike)`, followed by the existing underlying-diversity selection. Composite score never breaks a premium-velocity tie.
- **Actionability:** only `qualified + event_safe` candidates may copy, and only when the read-time `/api/run` snapshot is `tradeable`. Every other surfaced candidate is visibly review-only. Ticket quantity must be the backend `recommended_contracts`; `max_contracts` is context only, and missing/zero recommended quantity disables copy instead of defaulting to one. The sizing helper returns zero when cash/share capacity is zero and otherwise never exceeds capacity; one contract may remain the atomic minimum when it fits true cash.
- **Freshness:** carry Moomoo `update_time` (US quotes interpreted in `America/New_York`) and UTC fetch time separately. Missing/invalid broker time fails closed while the market is open. `/api/run` recomputes effective stale/tradeable state against current time on every read without rewriting stored history.

## Approach

1. Consolidate profile, cash, quote-quality, and freshness logic at their existing owners before adding tier fields.
2. Implement the exact validity → quality/event tier → bid-velocity ordering contract as deterministic backend data, with versioned presets/scoring evidence.
3. Consolidate the dashboard onto the persisted `/api/run` snapshot. Cards remain visible from the last successful snapshot during refresh/failure, but copy requires both read-time run tradeability and candidate eligibility.
4. Delete the parallel recommendation cache/background worker, legacy response adapters, browser-side rank math, and retired growth/screener compatibility paths after updating all in-repo consumers atomically.
5. Surface only decision-useful math, tier rationale, source, freshness, and blockers in the existing cards; keep the secondary composite visually subordinate and correctly caveated.
6. Verify the cheapest pure and service contracts first, then run/API/UI integrations, full gates, and a manual Windows/OpenD smoke.

## Files to modify

### Core and services

- `core/connection_manager.py` — retain broker `update_time`/UTC fetch time and batch broker security type (stock/ETF/index) from Moomoo.
- `core/scoring_factors.py` — canonical bid-velocity, cycle-yield, and quality-classification helpers.
- `core/wheel_decision.py` — quote validity, canonical premium fields, tier inputs, caveated diagnostics, and serialization.
- `core/presets.py` — selected hard thresholds and version bump; no new runtime knobs.
- `core/run_model.py`, `core/wheel_runner.py` — quote evidence and pure read-time stale/tradeable evaluation.
- `core/scan_ledger.py` — scoring-version bump; existing JSON captures bid basis and tiers without schema work.
- `api/services/watchlist_manager.py` — explicit base profile + selected preset merge; delete retired growth/VIX overlay behavior.
- `api/services/options_data.py`, `api/services/recommendations.py` — propagate evidence, enforce complete-union/tier/rank contracts, and format one signal payload.
- `api/services/portfolio_context.py`, `api/services/options_service.py` — true-cash CSP capacity and one live preset propagation method.
- `api/routes/settings.py` — call the canonical service preset update rather than mutating only the recommendation engine.

### API and frontend

- `api/routes/run.py` — sole dashboard snapshot/refresh contract with read-time effective state.
- `api/routes/options.py` — remove the parallel top-recommendations worker/cache/legacy adapters; delegate retained cash/config research views to canonical owners.
- `core/cache_manager.py` — delete after route removal; symbol search found no other runtime consumer.
- `frontend/static/js/dashboard/api-run.js` (new), `api-options.js`, `api.js`, `run-strip.js`, `top-recommendations.js` — consume `/api/run`, remove duplicate recommendation generation/polling math, and gate copy.
- `frontend/templates/partials/dashboard/top_recommendations.html` — qualified/review-only labels, bid velocity, limit-target wording, and corrected premium-velocity copy.

### Tests and durable docs

- Focused Python: `tests/test_connection.py`, `test_wheel_decision.py`, `test_score_regression.py`, `test_presets.py`, `test_portfolio_context.py`, `test_recommendations.py`, `test_run_model.py`, `test_wheel_parity.py`, `test_scan_ledger.py`, `test_routes_options.py`, plus new `tests/test_routes_run.py`.
- Frontend: `tests/frontend/top-recommendations.test.js` and a focused run-strip/run-client test if behavior is not fully covered there.
- Delete obsolete recommendation-cache route tests and deduplicate repeated recommendation test blocks touched by this work.
- `SCORING.md`, `README.md`, `API.md`, `CHANGELOG.md`, `tests/README.md` — update only the changed methodology, endpoint/workflow, and manual verification contracts.
- Owning DOX: `api/routes/AGENTS.md`, `api/services/AGENTS.md`, `frontend/static/js/AGENTS.md`, `frontend/templates/AGENTS.md`, `tests/AGENTS.md`; root/core docs only if their already-correct boundaries change.

No database schema or migration is planned. Do not alter/delete local trade-event data, and do not commit `PLAN.md`.

## Reuse

Confirmed reusable architecture:

- immutable refresh publication: `core/run_model.py`, `core/wheel_runner.py`
- CSP/call lane and risk-tier ranking: `api/services/recommendations.py`
- immutable effective risk settings: `core/presets.py`
- query-only Moomoo boundary: `core/broker_protocol.py`
- existing source/freshness/actionability gates described in `README.md`; implementation must wire cards to `WheelRunSnapshot.tradeable` instead of inferring from independent cache metadata

Specific reusable functions and fields found:

- `core.scoring_factors.premium_velocity_per_day()` — canonical final rank metric
- `core.scoring_factors._calculate_mid_price()` and `_compute_shared_subscores()` — current quote/liquidity calculations
- `core.wheel_decision.score_contract()` / `WheelDecision.to_dict()` — canonical decision and serialized evidence
- `core.presets.WheelPreset.to_screener_profile()` / `get_preset()` — immutable threshold delivery; fix the current adapter mismatch rather than creating another profile layer
- `api.services.watchlist_manager.WatchlistManager.get_screening_profile()` — legacy dynamic defaults currently merged incorrectly with presets; simplify to an explicit base + selected-preset merge
- `api.services.recommendations._format_decision_to_candidate()` and `_format_recommendation()` — existing response adapters
- `RecommendationEngine.get_top_recommendations()` — existing complete-union filtering, `_risk_tier`, velocity ordering, and diversity selection
- `frontend/static/js/dashboard/top-recommendations.js` existing source/freshness, warning, risk, breakeven, expected-move, and score-driver renderers

## Steps

- [ ] **Unify strategy configuration.** Make selected-preset propagation an `OptionsService` responsibility; merge preset values explicitly into the existing call/CSP base profiles, remove retired growth/VIX overlays and stale screener overrides, delete unused preset claims/fields such as the non-operative per-CSP buying-power percentage, and bump preset versions.
- [ ] **Fix capital and coverage invariants.** Calculate CSP capacity from true cash minus reserved short-put collateral, delegate the cash-status view to that result, and replace cold-scan slicing with all-symbol scan or `planning`.
- [ ] **Make broker evidence truthful.** Preserve Moomoo `update_time` and UTC fetch time, parse US timestamps with `zoneinfo`, hard-block open-market quotes with invalid/stale evidence, add crossed-market validation while accepting locked markets, and batch/carry Moomoo underlying type so ETFs do not require irrelevant earnings dates.
- [ ] **Centralize qualification and math.** Reuse the existing profile thresholds to classify hard-invalid/qualified/marginal; compute bid credit, bid velocity, midpoint limit target, cycle yield, annualized yield, event tier, `review_only`, safe recommended quantity, and machine-readable rationale once in backend code.
- [ ] **Apply deterministic ranking.** Sort by quality tier, event tier, descending bid velocity, then stable identity fields; preserve the existing diversity safeguard and prove composite score cannot influence order.
- [ ] **Correct run actionability.** Build per-symbol quote evidence from candidate data and recompute effective stale/tradeable state on each `/api/run` read without mutating the saved snapshot.
- [ ] **Collapse to one runtime workflow.** Move cards and refresh controls to `/api/run` and `/api/run/refresh`; keep last-good cards visible during attempts/failures and disable copy unless `snapshot.tradeable && signal.copy_eligible`.
- [ ] **Delete superseded code.** Remove `/api/options/top-recommendations`, `RecommendationCache`, background/in-flight/stale-cache logic, legacy response normalizers, browser-side premium/rank calculations, retired growth/Best-Plays code, and their obsolete/duplicated tests.
- [ ] **Expose only decision evidence.** Show bid velocity as the headline, midpoint as a non-guaranteed limit target, spread/OI/volume, cycle/annualized yield, source/freshness, quality/event tier, recommended versus maximum quantity, and review-only rationale; keep heuristic composite/POP/EV diagnostics secondary and explicitly caveated.
- [ ] **Version and document.** Bump preset, scoring, and run-payload schema versions; include premium basis/tiers in existing scan-ledger JSON; rewrite `SCORING.md`; update endpoint/workflow/manual-smoke docs and owning DOX; leave the dormant trade-event schema/data untouched.
- [ ] **Verify narrow-to-broad.** Run focused unit/service/route/UI checks, then full Python/frontend/hygiene gates and the manual Windows/OpenD flow.

## Verification

Planned verification layers:

1. Pure unit tests for formulas and threshold boundaries, including zero/negative bid, crossed versus locked markets, missing/invalid/stale broker timestamps, timezone conversion, stock/ETF event classification, missing Greeks, and DTE edge cases.
2. Recommendation-service tests proving executable-bid premium velocity ranks within the applicable tier, midpoint inflation cannot win the rank, deterministic tie-breaks hold, and poor-quality quotes cannot leapfrog qualified candidates.
3. Run/API serialization tests proving immutable snapshots preserve visible math and rejection reasons, read-time actionability flips to stale after the freshness deadline without mutating stored state, CSP sizing never uses margin buying power, and actionable coverage is all-or-planning (never truncated).
4. Frontend tests proving the top-three display remains decisive, copy actions remain disabled unless both candidate-level top-tier eligibility and existing Moomoo coverage/freshness/market-state gates pass, and ticket text uses `recommended_contracts` without silently defaulting to one/max.
5. Focused commands before broad gates:
   - `uv run python scripts/ci_pytest.py tests/test_connection.py tests/test_wheel_decision.py tests/test_score_regression.py tests/test_presets.py tests/test_portfolio_context.py tests/test_recommendations.py tests/test_run_model.py tests/test_wheel_parity.py tests/test_scan_ledger.py tests/test_routes_options.py tests/test_routes_run.py -q`
   - `npm test -- tests/frontend/top-recommendations.test.js`
6. Full gates: `uv run ruff check .`, `uv run ruff format --check .`, `uv run python scripts/ci_pytest.py tests/ -q`, and `npm test --silent`, followed by the repository hygiene scans/no-execution-surface tests.
7. Manual Windows/OpenD smoke: refresh the complete watchlist union; compare displayed bid velocity/cycle yield to Moomoo; verify qualified/event-safe cards alone can copy; verify marginal, unknown-event, earnings-before-expiry, market-closed, partial, and aged snapshots remain visible but cannot copy; confirm true CSP cash excludes margin and reserved collateral; confirm no order-capable action exists.

## Bitter Lesson review (required format)

### A. Systemic violations

- The external site's broad universe, numerous data products, and composite Contract Score would duplicate or obscure this repo's focused watchlist decision flow.
- Treating modelled POP or score as proven profitability would create an ownerless assumption unsupported by realised expectancy evidence.
- The repository currently has two signal state machines (immutable runs and a separate recommendation cache), two cash calculations, multiple profile layers, and backend/browser rank math. This duplication already causes broken copy safety, preset drift, and conflicting user copy.
- Stamping processing time as quote time, persisting a dynamic tradeable boolean, and allowing a mocked “feasible” scan to truncate all protect implementation convenience over the explicit freshness/complete-union contracts.

### B. What will be deleted / simplified

- Exclude broad-market discovery, external actionable quote paths, copied request-token/API behavior, autonomous execution, and a second opaque score.
- Delete the parallel recommendation cache/background worker and legacy response-shape adapters; the persisted immutable run already provides the needed freshness/failure behavior without a second state machine. The consumer inventory proves this is repository-owned compatibility only, not a demonstrated external dependency.
- Consolidate duplicated quality calculations and preset defaults discovered in the current pipeline before adding logic.
- Do not resurrect the dormant trade-event analytics as a “backtest”: there is no production write path or fill/assignment dataset, so it cannot validate expectancy. Preserve local tables/data unless the user separately authorizes destructive cleanup.
- Keep the Moomoo source boundary, immutable run publication, cash/share reservation, and copy-action safety gates intact: these may look costly but are load-bearing correctness and safety controls.

### C. Systemic impact + the Bitter Lesson

The target leverage is fewer false-attractive candidates and faster human review without weakening capability or safety. One persisted run, one cash model, one profile merge, one quality classifier, and one backend rank formula prevent whack-a-mole drift across API/UI/cache paths. This moves the product forward: the top three remain premium-velocity-ranked, watchlist-scoped, Moomoo-grounded, and visibly explainable; adding feature breadth would be sideways motion.

## Final deliverable

One lean watchlist workflow: a manual refresh publishes an immutable top-three snapshot; invalid contracts are rejected, qualified contracts outrank review-only marginal/event-risk contracts by executable-bid premium velocity, every number is traceable to Moomoo plus clearly labelled context, and copy-to-ticket is impossible unless both the candidate and the current read-time run state are actionable.
