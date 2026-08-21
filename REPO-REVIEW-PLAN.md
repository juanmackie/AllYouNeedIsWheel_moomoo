# Repository review and improvement punchlist plan

## Context

All You Need Is Wheel needs an evidence-backed, repository-wide review that identifies defects, risks, UX gaps, maintainability issues, and high-value product improvements without weakening its established contracts: signals-only, structurally read-only, Moomoo/OpenD as the actionable source, watchlist-only scope, and premium velocity as the primary ranking axis.

The deliverable will be a prioritized punchlist rather than code changes. Competitors such as `wheelstrategyoptions.com` and `pve.trade` will be used as inspiration and comparison points, not copied blindly and not allowed to override this repository's safety and product boundaries.

## Approach

1. Optimize for the confirmed **private, local Windows operator** posture. Benchmark decision workflow and option analytics most deeply, with UI and trust reviewed as supporting surfaces.
2. Inventory the repository, its DOX contracts, public documentation, architecture, dependencies, tests, and current working-tree state.
3. Review each subsystem for correctness, safety, source-policy compliance, architecture, resilience, performance, observability, maintainability, and test coverage.
4. Run non-destructive static checks and the documented test/quality gates; distinguish confirmed failures from inferred risks.
5. Launch and inspect the local dashboard where the environment permits, covering responsive behavior, accessibility, loading/empty/error/stale/planning/ready states, and core operator workflows.
6. Inspect the named reference products and relevant first-party option-scanner/broker guidance. Extract reusable product patterns while explicitly rejecting incompatible scope (order execution, broad-market discovery, or non-Moomoo actionability).
7. Trace every proposed item to repository or runtime evidence, remove duplicates, and rank the final backlog by urgency, user impact, confidence, effort, and dependency.
8. Apply one triage rubric: **P0** safety/data-integrity or money-risk blocker; **P1** broken core workflow, privacy/security, or major trust failure; **P2** important resilience/maintainability/UX work; **P3** optional polish or validated product opportunity. Each item will be tagged `confirmed defect`, `confirmed debt`, `likely risk`, or `opportunity` so ideas never masquerade as bugs.
9. Structure the final document to satisfy the repository's Bitter Lesson contract: systemic violations; deletions/simplifications (including load-bearing parts explicitly retained); systemic impact; then the dependency-ordered execution backlog.

## Files to modify

- `REPO-REVIEW-PLAN.md` — iterative planning artifact only.
- `REPO-AUDIT-PUNCHLIST.md` — proposed final evidence-backed execution backlog; no application source files will be modified during this assessment.
- `research.md` — benchmark research already produced during planning; retain as source notes and fold only verified, compatible insights into the final punchlist.

Likely evidence surfaces (read-only):

- `README.md`, `API.md`, `SCORING.md`, `CHANGELOG.md`, `OVERARCHING GOAL.txt`, `docs/**`
- `app.py`, `config.py`, `run_api.py`, launch/container/CI configuration
- `api/**`, `core/**`, `db/**`, `frontend/**`, `tests/**`, `scripts/**`
- Dependency and environment manifests (`pyproject.toml`, `uv.lock`, `requirements.txt`, `package*.json`, examples)

## Early evidence shaping the review

- The working tree already contains a user-owned deletion of `TECH_DEBT_AUDIT.md`; the review will preserve it and will not infer that the deletion is ours.
- Durable docs disagree materially: `README.md`, `API.md`, and the 2026-08-02 changelog describe the consolidated watchlist-only app, while most of `SCORING.md`, older changelog entries, `requirements.txt` comments, and several DOX ownership statements still describe removed scheduler, macro/VIX, catalyst, LLM, dynamic-screening, and Docker behavior. Documentation drift is therefore a first-class audit track, not cosmetic cleanup.
- The repository has only 174 tracked files but carries broad historical contracts; recommendations must delete stale descriptions/requirements before proposing more abstractions.
- Static review has already identified safety-critical hypotheses that must be independently traced and tested in the final audit: copied ticket quantity appears to prefer `max_contracts` over `recommended_contracts`; snapshot freshness is synthesized from run generation time; failed symbols may still count toward complete coverage; some portfolio paths may select the first REAL account; raw account IDs/config may reach logs and persisted errors; missing provenance may fail open as broker data.
- The code also retains substantial consolidation residue: localStorage-only scanner symbols, growth-mode/yfinance paths, removed signal tabs/copy, a fake `$45,000` sizing modal, stale DOX/testing/environment instructions, and conflicting app/config/service ownership. These will be reachability-tested before being labeled dead code or deleted.
- Quality gates are not currently green: Ruff reports three issues in `tests/test_wheel_parity.py`; Python collection finds 606 tests, but two `MoomooConnection` cache tests fail because `broker_cache_after_hours` is no longer accepted, and broader runs hang after partial progress; 28 Vitest tests pass. `package.json` also declares Playwright while the lockfile root does not, so clean `npm ci` must be checked without altering dependencies during the review.
- A disconnected-state browser observation on an alternate loopback port found `/health` reporting `healthy` while OpenD was unavailable, duplicate initial API requests, real-looking `$0.00` portfolio placeholders during 503s, stale ranking/execution/growth-mode copy, unlabeled icon buttons, third-party CDN requests, and severe mobile horizontal overflow (`390px` viewport vs `2021px` document width).
- After the user started OpenD, the same loopback server reported it connected and the REAL portfolio metrics loaded. The expensive recommendation endpoint was browser-blocked to avoid creating a new scan during planning, and no financial values/account identifiers were captured. Even in this bounded observation the page requested `/api/run` 5 times, `/api/settings` 6 times, and `/api/system/opend-status` 3 times in roughly 10 seconds, so polling/request ownership needs explicit audit.
- The confirmed REAL-account runtime review will remain query-only. No unlock, order, modification, cancellation, secrets inspection, account identifier capture, preset/watchlist mutation, or refresh-triggering local DB write is permitted; screenshots/evidence must redact financial/account-sensitive values.

## Reuse

The review will identify and cite existing helpers before recommending new abstractions. Initial reuse anchors already documented by the project include:

- Immutable run/attempt model: `core/run_model.py`
- Bounded refresh orchestration: `core/wheel_runner.py`
- Query-only broker boundary: `core/broker_protocol.py`
- Versioned risk presets: `core/presets.py`
- Recommendation lanes and premium-velocity ranking: `api/services/recommendations.py`
- Route validation/envelopes and source labeling: `api/routes/utils.py`, `api/routes/source_policy.py`
- Canonical ticker/watchlist logic: `core/ticker_utils.py`, `api/services/watchlist_manager.py`, `api/routes/watchlist.py`
- Deterministic decision/scoring primitives: `core/wheel_decision.py`, `core/scoring_factors.py`, `api/services/options_data.py`
- Frontend state/safety helpers: `frontend/static/js/utils/state-model.js`, `frontend/static/js/utils/formatters.js`, `frontend/static/js/dashboard/run-strip.js`, `frontend/static/js/dashboard/watchlist-panel.js`
- Existing Python, Vitest, safety/hygiene, import-smoke, parity, and manual smoke coverage under `tests/` and `.github/workflows/ci.yml`; extend these contracts in recommendations before suggesting new harnesses.

## Steps

- [x] Confirm posture and review format: private local tool; deepest benchmarks are decision workflow and option analytics; OpenD REAL may be inspected read-only; final output is an execution-ready backlog.
- [x] Read the full root/child `AGENTS.md` hierarchy and inventory tracked/untracked repository surfaces without exposing secrets.
- [ ] Reconcile durable docs with implementation, especially the confirmed consolidation-vs-legacy contradictions already found in `SCORING.md`, `CHANGELOG.md`, `requirements.txt`, and DOX ownership text.
- [ ] Audit architecture and dependency direction across application startup, routes, services, core logic, persistence, and frontend.
- [ ] Audit trading-domain correctness: universe completeness, premium velocity, risk tiers, cash/share reservation, quote freshness, earnings uncertainty, market state, roll actions, and copy gating.
- [ ] Audit broker/data safety: read-only enforcement, REAL-account identity, source truth, failure isolation, rate limits, caching, and stale-data behavior.
- [ ] Audit API contracts, validation, error semantics, concurrency, persistence/migrations, privacy, and local deployment security.
- [ ] Audit dashboard information architecture, visual hierarchy, responsiveness, accessibility, interaction feedback, and all operational states.
- [ ] Audit tests and automation; reproduce the Ruff failures, Python cache-test failures/hang, lockfile drift, obsolete E2E suite, and missing/contradictory CI gates, then map uncovered critical paths.
- [ ] Complete browser evidence for disconnected and connected REAL read-only states at desktop and mobile widths. Disconnected layout, mobile overflow, first-focus/no-skip-link, and connected non-sensitive status/request-count checks are done; remaining work is offline/CDN behavior, non-sensitive ready/planning/stale fixtures, and copy-gating reproduction without mutating the live watchlist, preset, or run history.
- [x] Benchmark the named reference sites and selected OCC/OIC/Moomoo primary sources. Carry forward only compatible patterns: qualify → compare → manual copy, yield beside cushion/breakeven/DTE/liquidity/event risk, explicit heuristic/source labels, and selective Moomoo-native shortlist analytics.
- [ ] Produce `REPO-AUDIT-PUNCHLIST.md` with one canonical record per item: ID/title, classification, priority, subsystem, evidence, user/risk impact, root cause, recommended action, files likely affected, reusable code, acceptance criteria, verification, effort, confidence, dependencies, and ordering.
- [ ] Add the Bitter Lesson sections and a short executive summary: strengths to preserve, systemic violations, deletion/simplification candidates, load-bearing parts not to cut, top risks, quick wins, strategic opportunities, rejected out-of-scope ideas, and could-not-verify items.
- [ ] Perform a final hostile review against every tracked subsystem and the original request; merge twins, remove unsupported threshold advice, and ensure no priority depends solely on competitor marketing.

## Verification

- Every finding cites a file/line, command result, runtime observation, or external source; speculative ideas are labeled separately.
- All documented Python/frontend quality gates are attempted non-destructively, with exact pass/fail/hung/blocked status recorded; direct pytest hangs are not mislabeled as test failures, and no install/lockfile rewrite is performed during the assessment.
- Critical operator workflows and major responsive/accessibility states are observed in the running UI where possible; environment-blocked checks are explicit.
- Competitor-derived ideas include a compatibility check against the project's signals-only, read-only, Moomoo-only, watchlist-only contracts.
- The punchlist is audited for duplicates, conflicting recommendations, missing acceptance criteria, and priority inflation.
- The final document clearly separates: confirmed defects, maintainability debt, UX/accessibility gaps, test/ops gaps, and optional product opportunities.
