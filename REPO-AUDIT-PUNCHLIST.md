# Repository audit and execution punchlist

**Repository:** AllYouNeedIsWheel_moomoo  
**Review posture:** Private, local Windows tool; signals-only; structurally read-only; Moomoo/OpenD actionable truth; canonical watchlist union; premium velocity primary rank.  
**Review date:** 2026-08-20  
**Scope:** Read-only static review, non-destructive checks, local browser observation, and compatible benchmark research.  
**Application changes made:** None. The existing user-owned deletion of `TECH_DEBT_AUDIT.md` was preserved. `REPO-REVIEW-PLAN.md`, `research.md`, and this punchlist are review artifacts.

> This is an engineering/product audit, not investment advice. Any analytics recommendation must be validated against the user's broker data, fees, assignment rules, and risk limits before use.

## Executive summary

The repository has a strong safety foundation: the broker surface is query-only, `readonly=False` is rejected, trade contexts use `TrdMarket.NONE`, REAL account resolution is explicit in the refresh runner, snapshots are immutable, and premium velocity is the final ranking axis within risk tiers. Those are load-bearing capabilities and should not be simplified away.

The most important finding is that the **copy gate currently does not prove what it claims to prove**. Snapshot freshness is synthesized from run generation time rather than observed per-symbol broker timestamps; failed symbols can still count toward complete coverage; normal portfolio paths can select the first account by environment; missing provenance defaults toward broker trust; and copied quantities prefer `max_contracts` over `recommended_contracts`. These issues can produce a manual ticket that looks ready without proving fresh, complete, correctly scoped broker data or the intended conservative quantity.

The second major problem is **consolidation residue**. Current README/API/root contracts describe a focused watchlist scanner, while scoring docs, environment examples, DOX files, tests, frontend copy, legacy code, and dependencies still advertise or implement pieces of growth mode, yfinance fallback, VIX/macro, Catalyst/Ape Wisdom, LLM, scheduler, Docker, broad screening, execution language, and old tabbed workflows. This increases the chance that a future change reintroduces an explicitly rejected capability or silently trusts the wrong source.

The third problem is **feedback and operability**. The documented quality gate is not green: Ruff reports three errors, the connection-cache subset fails, direct pytest runs hang in broader execution, and `npm ci` rejects the lockfile. The legacy Playwright E2E suite targets port 5000 and removed selectors but is not run by CI. In the browser, disconnected 503 responses showed `$0.00` financial placeholders, `/health` still reported `healthy`, and a 390px viewport produced 2021px document width. Offline reload failed completely because runtime assets are CDN-dependent.

### Recommended execution order

1. **P0 safety gate:** observed freshness, honest coverage, centralized account resolution, provenance fail-closed, and copy quantity correctness.
2. **P1 trust/reliability:** privacy redaction, roll failure visibility, preset propagation, migration/readiness correctness, fake/legacy UI removal, and app/database dependency ownership.
3. **P2 feedback loop:** fix CI/lockfile/E2E, mobile/accessibility/state correctness, XSS audit, local asset strategy, request duplication/deadlock, retention, and operational shutdown.
4. **Only then add compatible analytics:** a compact qualify → compare → manually copy workflow using Moomoo-native data, not a larger market scanner.

## Triage rubric

- **P0:** Safety, data-integrity, or money-risk blocker. Fix before trusting copy actions.
- **P1:** Core workflow, privacy/security, or major trust failure. Fix before broad daily use.
- **P2:** Important resilience, maintainability, observability, accessibility, or UX work.
- **P3:** Optional polish or product opportunity after the contracts are stable.

Classification is intentionally separate from priority:

- **Confirmed defect:** directly demonstrated by code, command output, or runtime behavior.
- **Confirmed debt:** directly present and inconsistent with the current architecture, but not necessarily harmful on every path.
- **Likely risk:** a credible unsafe/failure path requiring a regression test or reachability check before implementation.
- **Opportunity:** compatible improvement, not a defect claim.

## Strengths to preserve

1. **Structural read-only boundary:** `core/broker_protocol.py:16-59`, `core/connection_manager.py:210-216`, `core/context_factory.py:65-73`, `tests/test_query_only_broker.py`, and `tests/test_no_execution_surface.py` make execution absence explicit and testable.
2. **Explicit runner account resolution:** `core/wheel_runner.py:39-76` rejects missing REAL identity and ambiguous SIMULATE accounts for refresh runs.
3. **Immutable run model:** `core/run_model.py:27-114` separates completed snapshots from refresh attempts and centralizes tradeability checks.
4. **Premium velocity contract:** `api/services/recommendations.py:1080-1102` qualifies with gates and ranks within risk tiers by `premium_per_contract / dte`.
5. **Canonical watchlist union:** `api/services/watchlist_manager.py` merges Moomoo, app, and config origins and labels them.
6. **Cash/share gates:** CSP affordability and covered-call share capacity are explicitly calculated in recommendation and decision paths.
7. **Failure-preserving refresh lifecycle:** `core/wheel_runner.py:100-198` does not replace the last snapshot when an attempt fails.
8. **Useful diagnostics direction:** run strip, source labels, blocker counts, freshness fields, and the scan ledger are the right observability primitives even where their current values need correction.
9. **Private local defaults:** loopback serving, parameterized SQLite access, WAL/busy timeout, generated request IDs, and rate-limited routes are appropriate foundations.
10. **Frontend modularity and safety helpers:** feature-oriented JS modules, `StateModel`, `escapeHtml`, and explicit manual copy feedback are worth retaining.

# A. Systemic violations

## A1. The repository has more than one product contract

The current contract says watchlist-only, Moomoo-only actionable data, signals-only, premium-velocity-first. Legacy files still describe a broad multi-factor screener with VIX/macro, social discovery, LLM advice, growth mode, dynamic screening, scheduler, Docker, and execution. Examples: `SCORING.md:1-30,161-209,353-376,524`; `.env.example:19-44`; `tests/README.md:14-105`; `api/routes/AGENTS.md:7,20`; `api/services/AGENTS.md:7,17,36`; `core/AGENTS.md:7,31`; `frontend/static/js/AGENTS.md:11,23`.

**Systemic effect:** contributors cannot tell which code is authoritative. The app can regress by “restoring” a feature that the root contract explicitly removed.

## A2. Trust defaults fail open

Several fields that should be evidence requirements default to trusted-looking values: `api/services/recommendations.py:1026-1044` uses broker-like defaults for missing source and confidence; `_format_recommendation()` at `api/services/recommendations.py:650-668` defaults feasibility/source fields; `core/wheel_decision.py:470-482` normalizes absent provenance toward broker. The copy gate then relies on the resulting payload.

**Systemic effect:** source policy is metadata decoration rather than a hard invariant. Unknown must be represented as unknown and must not enable actionability.

## A3. “Freshness” is a label, not an observation

`core/wheel_runner.py:267-285` assigns `generated_at` to every watchlist symbol and leaves partial/stale symbol lists empty. `core/run_model.py:66-87` trusts those timestamps. The broker cache uses a separate 180-second chain TTL in `core/connection_manager.py:251-279`, while the run model uses a 120-second tradeability window.

**Systemic effect:** a long-running scan, cached chain, skipped symbol, or failed symbol can be represented as freshly quoted.

## A4. Complexity was retained after consolidation

The codebase contains duplicate config/database ownership, global service registries, legacy route caches, growth-mode adapters, external fallback plumbing, unused background-manager behavior, old frontend scanner paths, and stale tests/docs. This is not harmless historical clutter: it expands the number of paths that can bypass the current source and safety contracts.

## A5. The feedback loop is weaker than the risk surface

The app makes money-risk recommendations but has no reliable green end-to-end gate: `npm ci` is broken, the E2E suite is stale and not run, Ruff fails, targeted broker-cache tests fail, and broader pytest runs can hang. A safety boundary without a fast regression loop will decay.

# B. What should be deleted or simplified

## Delete or retire after reachability confirmation

1. Removed Catalyst/Ape Wisdom, VIX/macro, LLM, dynamic screener, Docker, scheduler, and growth-mode production paths that are no longer reachable from the consolidated workflow.
2. `sizing_modal.html` and its inline ATR calculator unless a real broker-sourced review workflow is explicitly redefined; it currently contains fake financial defaults and an execution-oriented “Apply to Order” label.
3. Legacy `customTickers` scanner expansion from localStorage; canonical app-watchlist mutation must go through `/api/watchlist`.
4. Stale E2E scenarios for Catalyst Watch, removed tabs, port 5000, and legacy IDs; replace with current dashboard smoke tests rather than maintaining both suites.
5. Duplicate global service/database/config paths after dependency injection is in place.
6. Obsolete `.env.example` secret prompts and removed-provider variables.

## Simplify, do not delete

1. Keep the immutable run/attempt model. It is load-bearing, not duplication.
2. Keep canonical watchlist origin labels; they support auditability and operator trust.
3. Keep query-only protocol and AST/hygiene scans; do not replace structural enforcement with a UI warning or config flag.
4. Keep risk presets, but make them the one read-only strategy contract instead of layering editable overrides and growth mode over them.
5. Keep source/freshness/blocker metadata, but make it evidence-backed and fail-closed.
6. Keep manual copy-to-ticket, but make it a review artifact with explicit quantity, quote age, source, and actionability guards.
7. Keep rate limiting and cache layers; consolidate them around one ownership model instead of bypassing them.

## Bitter lesson

The right simplification is not “fewer fields” or “fewer tests.” It is fewer competing owners and fewer implicit defaults while retaining every safety gate, provenance field, and failure diagnostic that protects a manual trading decision.

# C. Prioritized execution backlog

## P0 — fix before trusting copy actions

### AUD-001 — Replace synthetic quote freshness with observed broker timestamps

- **Classification:** Confirmed defect
- **Subsystem:** Run model / recommendations / broker adapter
- **Evidence:** `api/services/recommendations.py:522-543` does not carry an observed broker quote timestamp through the candidate; `core/wheel_runner.py:267-285` maps every symbol to `generated_at`; `core/run_model.py:66-87` uses those values for `tradeable`; `core/connection_manager.py:251-279` caches chains for 180 seconds while the run threshold is 120 seconds.
- **Impact:** A ready run can enable manual copy for data that is stale, cached beyond policy, early in a slow scan, or never successfully fetched.
- **Root cause:** Run publication fabricates one run-level timestamp instead of preserving per-symbol/contract broker observation metadata.
- **Recommended action:** Carry separate observed timestamps and cache age for stock quote, option chain, and selected contract. Require explicit Moomoo source plus fresh timestamps for every selected pick. Unknown age must disable copy.
- **Likely files:** `core/connection_manager.py`, `api/services/options_data.py`, `api/services/recommendations.py`, `core/run_model.py`, `core/wheel_runner.py`, `tests/test_run_model.py`, `tests/test_recommendations.py`.
- **Reuse:** Existing `_freshness` payload, `RunMetadata.quote_fetched_at`, `WheelRunSnapshot.tradeable`, and `StateModel.showStale`.
- **Acceptance criteria:** Mixed-age candidates make the run non-tradeable; a 121+ second cached chain cannot produce `ready`; missing/conflicting timestamps are visible blockers; selected pick freshness is tested for both CSP and CC.
- **Verification:** Add unit tests for fresh, stale, cached, missing, invalid, and mixed timestamps; run run-model/recommendation tests; render a stale snapshot in the browser.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** First. Blocks AUD-002 and AUD-005.

### AUD-002 — Make coverage honest and lane-aware

- **Classification:** Confirmed defect
- **Subsystem:** Recommendation scan / run publication
- **Evidence:** `api/services/recommendations.py:876-914` increments `watchlist_errors` and continues; response coverage at `api/services/recommendations.py:1238-1268` reports `scanned=len(scan_watchlist)` regardless of success and does not expose failed symbols in coverage; `core/wheel_runner.py:246-282` reads only `result["errors"]` and leaves `partial_symbols` empty.
- **Impact:** A non-global shortlist can be labeled complete/ready after symbol failures.
- **Root cause:** Attempted, successful, blocked, cached, and failed symbols are not distinct coverage states.
- **Recommended action:** Persist `attempted`, `evaluated`, `cached`, `blocked`, and `failed` sets. Any unexplained failure in the canonical union must produce `partial`, not `ready`; make CC-only/no-watchlist semantics explicit.
- **Likely files:** `api/services/recommendations.py`, `core/wheel_runner.py`, `core/run_model.py`, `tests/test_recommendations.py`, `tests/test_run_model.py`.
- **Reuse:** Existing `_diagnostics`, `scan_coverage`, `partial_symbols`, `stale_symbols`, and blocker counts.
- **Acceptance criteria:** One failed ticker among successful symbols produces partial status and lists the ticker; zero-symbol/CC-only runs have status and tradeability that agree; a capped scan is planning, never falsely complete.
- **Verification:** Add failure/capped/empty-union/CC-only fixtures and assert API/run status plus copy gate.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** After AUD-001; before E2E state coverage.

### AUD-003 — Centralize explicit account identity for every portfolio path

- **Classification:** Confirmed safety defect
- **Subsystem:** Broker connection / portfolio service
- **Evidence:** `core/wheel_runner.py:39-76` correctly rejects missing/ambiguous REAL identity, but `core/connection_manager.py:370-383` uses `_find_account_by_env()` and `_resolve_portfolio_account()` to select the first matching account when `account_id` is absent; `core/connection_manager.py:889-893` calls that path for portfolio retrieval. Existing tests explicitly cover `test_resolve_portfolio_account_no_id_finds_by_env` at `tests/test_connection.py:1221-1228`.
- **Impact:** Dashboard portfolio/cash/positions can query a different REAL account from the one required by the runner contract.
- **Root cause:** Account resolution is implemented twice with different policies.
- **Recommended action:** Inject/use one strict resolver for refresh, portfolio, cash, weekly income, roll pressure, and direct connection calls. REAL requires configured matching ID; SIMULATE requires explicit ID or exactly one account.
- **Likely files:** `core/connection_manager.py`, `core/wheel_runner.py`, `api/services/portfolio_service.py`, route tests, connection tests.
- **Reuse:** `resolve_account()` and `opaque_account_id()`.
- **Acceptance criteria:** No portfolio API path calls an implicit first-account fallback; ambiguous accounts return a safe 503; selected account is opaque in all output.
- **Verification:** Add mocked multi-account REAL/SIMULATE tests for every portfolio/cash path and run query-only tests.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** Before trusting any connected-state UI.

### AUD-004 — Copy the recommended quantity, never the maximum by default

- **Classification:** Confirmed defect
- **Subsystem:** Frontend manual ticket generation
- **Evidence:** `frontend/static/js/dashboard/top-recommendations.js:233-243` sets `qty` from `rec.max_contracts || rec.recommended_contracts || 1`; `tests/test_score_regression.py:172-185` proves a decision can have `max_contracts=20` and `recommended_contracts=2`.
- **Impact:** A conservative recommendation can produce a maximum-size manual ticket.
- **Root cause:** Maximum affordability is treated as the intended quantity.
- **Recommended action:** Prefer `recommended_contracts`; if absent/zero, disable copy and require explicit review rather than falling back to max or one.
- **Likely files:** `frontend/static/js/dashboard/top-recommendations.js`, `tests/frontend/top-recommendations.test.js`.
- **Reuse:** Existing `copyTicket()` and clipboard feedback.
- **Acceptance criteria:** Fixture `max_contracts=5`, `recommended_contracts=1` copies `x1`; missing recommendation disables copy with a visible reason; all run/source/freshness gates remain required.
- **Verification:** Add a frontend regression test and manually inspect copied text in a safe fixture.
- **Effort / confidence:** Small / High
- **Dependencies / order:** Independent, immediate quick win.

### AUD-005 — Make provenance fail closed

- **Classification:** Confirmed safety risk
- **Subsystem:** Source policy / recommendation formatting
- **Evidence:** `api/services/recommendations.py:1026-1044` defaults missing source/confidence toward broker/100; `api/services/recommendations.py:650-668` defaults broker feasibility and source; `core/wheel_decision.py:470-482` normalizes absent source toward broker.
- **Impact:** Malformed or future candidates can bypass the Moomoo-only actionability boundary.
- **Root cause:** Missing evidence is normalized into trust instead of unknown.
- **Recommended action:** Require explicit `price_source`, `chain_source`, quote timestamp, confidence, and actionable flag. Unknown, blank, conflicting, cached-external, or mixed source must be blocked/research-only.
- **Likely files:** `api/services/recommendations.py`, `core/wheel_decision.py`, `api/routes/source_policy.py`, tests.
- **Reuse:** `detect_external_sources`, `build_account_source_policy`, `WheelRunSnapshot.tradeable`.
- **Acceptance criteria:** Missing/blank/conflicting provenance never yields copyable; only explicit Moomoo/Moomoo with fresh evidence can pass; external data can diagnose but cannot create/promote a pick.
- **Verification:** Add absent/blank/conflicting source fixtures and AST/source-policy regression tests.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** With AUD-001.

## P1 — core trust, privacy, and reliability

### AUD-006 — Redact account IDs, balances, and credentials from logs/errors

- **Classification:** Confirmed privacy defect
- **Subsystem:** Logging / errors / persisted attempts
- **Evidence:** `app.py:84-86` logs the full connection config; `core/wheel_runner.py:47-57` includes raw account IDs in errors later persisted by `core/wheel_runner.py:176-195`; `core/connection_manager.py:388-406,891-900` formats available account IDs and cash diagnostics; `core/logging_config.py:91-115` writes DEBUG logs to disk.
- **Impact:** Local logs, SQLite refresh attempts, and API diagnostics can retain raw broker identifiers and account values.
- **Root cause:** Diagnostic detail is not separated from sensitive identity/account data.
- **Recommended action:** Use opaque IDs/counts and allowlisted diagnostic fields. Redact before logging and before persistence; never log full config or raw balance dictionaries.
- **Likely files:** `app.py`, `core/wheel_runner.py`, `core/connection_manager.py`, `core/logging_config.py`, tests.
- **Reuse:** `opaque_account_id()` and existing sanitized `error_response()`.
- **Acceptance criteria:** Secret/account-ID/balance capture tests prove raw values never appear in log records, persisted attempts, or HTTP payloads.
- **Verification:** Capture logs and inspect SQLite/API responses with sentinel identifiers.
- **Effort / confidence:** Small / High
- **Dependencies / order:** P1 before sharing logs or adding diagnostics.

### AUD-007 — Reserve capital and shares across the shortlist, or label picks as alternatives

- **Classification:** Confirmed portfolio-risk gap
- **Subsystem:** Recommendation selection / sizing
- **Evidence:** Candidate scoring uses unchanged portfolio context at `api/services/recommendations.py:306-356,878-914`; CC candidates use the same `available_calls` at `api/services/recommendations.py:961-1009`; final selection at `api/services/recommendations.py:1104-1137` allows multiple picks without sequential reservation.
- **Impact:** Multiple displayed tickets can collectively exceed cash or share capacity even though each is individually feasible.
- **Root cause:** Ranking and basket allocation are conflated.
- **Recommended action:** Either explicitly label the three as mutually exclusive alternatives or perform sequential allocation and show remaining capacity.
- **Likely files:** `api/services/recommendations.py`, `core/wheel_decision.py`, frontend cards/tests.
- **Reuse:** Existing `cash_available_for_csp`, `short_puts`, `short_calls`, and `recommended_contracts`.
- **Acceptance criteria:** UI says “alternatives” if no basket guarantee; otherwise aggregate cash/share reservation is tested and displayed.
- **Verification:** Add two-CSP and two-CC reservation fixtures.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** After P0 gates.

### AUD-008 — Surface roll-analysis failures instead of returning an empty success lane

- **Classification:** Confirmed defect
- **Subsystem:** Roll diagnostics / run status
- **Evidence:** `core/wheel_runner.py:205-244` catches every roll exception and returns `[]`; `_build_snapshot()` at `core/wheel_runner.py:246-282` does not retain a roll error.
- **Impact:** A run can appear ready while actual short-option roll decisions were unavailable; empty can mean “none needed” or “failed.”
- **Root cause:** Exception fallback has no structured partial state.
- **Recommended action:** Return roll status, affected symbols, and error codes; distinguish no decisions from unavailable diagnostics.
- **Likely files:** `core/wheel_runner.py`, `core/run_model.py`, roll routes/frontend/tests.
- **Reuse:** Existing `errors`, `partial_symbols`, and attempt stage model.
- **Acceptance criteria:** A roll failure is visible and cannot be mistaken for an empty healthy lane; CSP/CC run state remains independently interpretable.
- **Verification:** Inject scorer/connection failures and assert UI/API diagnostics.
- **Effort / confidence:** Small–Medium / High
- **Dependencies / order:** After coverage model.

### AUD-009 — Make preset persistence and live engine propagation atomic

- **Classification:** Confirmed defect
- **Subsystem:** Settings / recommendation engine
- **Evidence:** `api/routes/settings.py:57-80` writes the setting, attempts to mutate the engine, swallows every exception, and returns success.
- **Impact:** UI/database can report one preset while the next recommendation run uses another.
- **Root cause:** Durable setting and in-memory engine are updated independently with silent failure.
- **Recommended action:** Use an app-scoped settings provider read at run time, or update engine/config under a lock and return failure if propagation fails.
- **Likely files:** `api/routes/settings.py`, `api/services/options_service.py`, `api/__init__.py`, preset tests.
- **Reuse:** Versioned `WheelPreset` and DB settings repository.
- **Acceptance criteria:** Successful response and next snapshot share key/version; failed propagation retains/reports prior state; no broad `except: pass`.
- **Verification:** Inject propagation failure and run settings/recommendation tests.
- **Effort / confidence:** Small / High
- **Dependencies / order:** Before adding analytics tied to presets.

### AUD-010 — Remove fake sizing defaults and execution-oriented UI residue

- **Classification:** Confirmed defect / consolidation debt
- **Subsystem:** Frontend templates and legacy routes
- **Evidence:** `frontend/templates/partials/components/sizing_modal.html:26-27,53-76,89,126` ships `$45,000`, illustrative ATR/results, and “Apply to Order”; `frontend/templates/base.html:74` includes it globally. `frontend/static/js/dashboard/dashboard-init.js:19` still says “Positions & orders.”
- **Impact:** A local REAL-account operator can mistake synthetic numbers or execution language for broker-derived advice.
- **Root cause:** Old feature survived consolidation and is globally mounted.
- **Recommended action:** Remove the modal and dead endpoints, or rebuild it as an explicitly broker-sourced review-only calculator with no fake values.
- **Likely files:** sizing modal, `base.html`, `dashboard-init.js`, old route modules, tests/docs.
- **Reuse:** Current account summary and recommendation data only.
- **Acceptance criteria:** No fake financial numbers or “Apply to Order” controls in production UI; signals-only copy is consistent everywhere.
- **Verification:** Browser load with disconnected state and text grep for execution/fake defaults.
- **Effort / confidence:** Small–Medium / High
- **Dependencies / order:** P1 cleanup.

### AUD-011 — Fail startup/readiness on migration failure

- **Classification:** Confirmed reliability defect
- **Subsystem:** SQLite migration/startup
- **Evidence:** `db/schema.py:346-350` catches migration exceptions and logs only; `db/database.py:26-37` continues initialization.
- **Impact:** A partially migrated database can serve requests with undefined schema behavior.
- **Root cause:** Migration failure is treated as recoverable without an explicit degraded state.
- **Recommended action:** Propagate migration failures, prevent readiness, and provide a backup/restore message.
- **Likely files:** `db/schema.py`, `db/database.py`, `api/__init__.py`, database tests.
- **Reuse:** Existing schema version and transaction context.
- **Acceptance criteria:** Deliberately failing migration prevents healthy readiness and preserves the previous database; migration remains idempotent.
- **Verification:** Migration fault-injection test and startup/health test.
- **Effort / confidence:** Small / High
- **Dependencies / order:** Before health contract changes.

### AUD-012 — Separate liveness from readiness and distinguish TCP from broker session state

- **Classification:** Confirmed observability defect
- **Subsystem:** Health / OpenD status / launcher
- **Evidence:** `core/context_factory.py:14-51` calls any reachable TCP port connected; `api/__init__.py:213-244` always returns `status: healthy`; `start_local.ps1` uses that response for readiness.
- **Runtime evidence:** Disconnected browser observation returned HTTP 200 `{"status":"healthy","database":"available","opend":"unavailable"}` and rendered zero-valued account widgets.
- **Impact:** Launcher/operator can believe the app is ready while broker/account access is unavailable.
- **Root cause:** Port reachability, authenticated OpenD, account readiness, and process liveness are collapsed.
- **Recommended action:** Keep `/health` as liveness and add readiness/dependency fields or endpoint; classify database failure as not ready; classify OpenD states as reachable/login/account-ready.
- **Likely files:** `api/__init__.py`, `core/context_factory.py`, launch scripts, API tests.
- **Reuse:** Existing `probe_opend_status` payload shape and route error codes.
- **Acceptance criteria:** Health status and HTTP code match documented semantics; launcher waits on readiness; OpenD-unavailable UI remains a supported state without pretending to be ready.
- **Verification:** Probe unavailable, TCP-reachable-but-not-logged-in, and healthy states.
- **Effort / confidence:** Small–Medium / High
- **Dependencies / order:** P1.

### AUD-013 — Unify app-scoped configuration, services, and database ownership

- **Classification:** Confirmed architecture debt with correctness risk
- **Subsystem:** Flask factory / service container / persistence
- **Evidence:** `app.py:48-85` builds connection config and `current_app.config["database"]`; `api/services/config.py:10-34` owns a global config singleton; `api/services/options_service.py:23-31` and `portfolio_service.py:22-38` create separate databases; `api/__init__.py:25-101` uses a global unsynchronized service registry; `core/wheel_runner.py:215-220` imports API services.
- **Impact:** Multiple app instances/configurations can share stale service state or different DB paths; core/API dependency direction is violated.
- **Root cause:** Incremental extraction left several ownership models alive.
- **Recommended action:** Construct services once in `app.extensions` with injected config/database/providers; inject roll scorer into `WheelRunner`; remove service locator use from core.
- **Likely files:** `api/__init__.py`, `app.py`, service constructors, `core/wheel_runner.py`, tests.
- **Reuse:** Existing lazy factory pattern as a transition point.
- **Acceptance criteria:** Two app factories cannot share service/config/DB state; all services use the configured DB; core imports no API module.
- **Verification:** App-factory isolation and dependency-direction tests.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** Before broad cleanup.

### AUD-014 — Make local watchlist scope authoritative in the frontend

- **Classification:** Confirmed product-boundary defect
- **Subsystem:** Frontend scanner state
- **Evidence:** `frontend/static/js/dashboard/options-table.js:72-74,228-230`, `options-table-events.js:543`, and `options-table-state.js:164-191` load/store `customTickers` in localStorage; scanner paths use those values.
- **Impact:** Browser-local symbols can bypass the canonical Moomoo/app/config watchlist union.
- **Root cause:** Legacy custom ticker state survived while the backend introduced a canonical union.
- **Recommended action:** Add/remove app symbols only via `/api/watchlist`; scanner reads only server-returned union; migrate/discard legacy localStorage symbols.
- **Likely files:** options-table JS/state/events, watchlist route/manager, frontend tests.
- **Reuse:** `watchlist-panel.js` already calls/represents app-owned symbols and uses `escapeHtml`.
- **Acceptance criteria:** Injecting localStorage `customTickers` cannot expand scan scope; API add/delete updates canonical union and origins.
- **Verification:** Browser/jsdom regression test with hostile/out-of-union localStorage.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** Before E2E replacement.

## P2 — feedback loop, API quality, and frontend trust

### AUD-015 — Remove API-fed raw `innerHTML` insertion or escape every external field

- **Classification:** Confirmed security risk
- **Subsystem:** Frontend rendering
- **Evidence:** Raw/template-string insertion exists in `frontend/static/js/dashboard/account.js:170-172,245-246,368-370`, `dashboard-init.js:176-192`, `options-table-rendering.js:698`, `utils/alerts.js:15-17`, and many options-table paths. `frontend/AGENTS.md` requires `escapeHtml` for API-fed content.
- **Impact:** Broker/error/ticker text containing markup can create DOM XSS in a local UI.
- **Root cause:** Some modules use `textContent`/`escapeHtml`, others interpolate API data into HTML.
- **Recommended action:** Use DOM nodes/textContent or central escaping; allow only static trusted markup. Remove dead rendering paths after reachability is confirmed.
- **Likely files:** account/options-table/alerts/rendering JS and `tests/frontend/dashboard-safety.test.js`.
- **Reuse:** `frontend/static/js/utils/formatters.js:escapeHtml`.
- **Acceptance criteria:** API-fed ticker, error, warning, source, and position fields are escaped; hostile fixture renders text, not nodes.
- **Verification:** Expand safety tests and run npm test.
- **Effort / confidence:** Medium / Medium-High
- **Dependencies / order:** After AUD-014 reachability map.

### AUD-016 — Replace zero-valued loading/error placeholders with unknown state

- **Classification:** Confirmed UX/trust defect
- **Subsystem:** Account/weekly-income UI
- **Evidence:** `frontend/templates/partials/dashboard/account_summary.html:25-46,59-72` initializes financial values to `$0.00`/`0.00%`; `weekly_income.html:47-69` also initializes zero values.
- **Runtime evidence:** When portfolio endpoints returned 503 in the disconnected browser run, the page visibly showed `$0.00`, `0.00%`, and zero counts while a “loading data” status was present.
- **Impact:** Slow or unavailable REAL data can be mistaken for a zero-balance account.
- **Recommended action:** Use skeleton/`—` until successful broker data; preserve last valid snapshot with age; show unavailable/error separately.
- **Likely files:** account templates, `account.js`, weekly income JS, StateModel/tests.
- **Reuse:** `StateModel.showLoading/showError/showStale`.
- **Acceptance criteria:** No financial zero is rendered as a placeholder; disconnected, malformed, stale, and successful fixtures each have distinct states.
- **Verification:** Browser fixture and frontend tests.
- **Effort / confidence:** Small / High
- **Dependencies / order:** With AUD-012.

### AUD-017 — Make the dashboard responsive and keyboard/screen-reader complete

- **Classification:** Confirmed UX/accessibility defect
- **Subsystem:** Frontend layout and semantics
- **Evidence:** Mobile runtime at 390px reported `document.documentElement.scrollWidth=2021` and `clientWidth=375`; first Tab landed on Dashboard and no skip link exists in `base.html:20-49`; refresh/watchlist icon buttons lack accessible names in templates; leverage progressbar at `account_summary.html:50-52` lacks a useful label; weekly table lacks caption/scope.
- **Impact:** Mobile users must horizontally navigate a desktop-width page; keyboard/screen-reader users cannot orient or interpret controls reliably.
- **Recommended action:** Fix width/min-width rules and watchlist overflow; add skip link, button labels, table caption/scope, progress labels, live region for copy/loading status, and modal focus restoration.
- **Likely files:** `frontend/templates/base.html`, account/watchlist/weekly/top-recommendations templates, `frontend/static/css/ft.css`, JS tests.
- **Reuse:** Existing focus-visible CSS and semantic run-strip landmark.
- **Acceptance criteria:** No horizontal overflow at 390/768/1440; full keyboard path works; focus is visible; axe/manual checks pass for changed surfaces.
- **Verification:** Browser screenshots, computed dimensions, keyboard walk, and accessibility scan.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** Before considering UI finished.

### AUD-018 — Remove third-party runtime CDN dependency or provide a fully functional local fallback

- **Classification:** Confirmed local-first resilience/security debt
- **Subsystem:** HTML asset loading / CSP
- **Evidence:** `frontend/templates/base.html:7-11,73` loads Google Fonts, Bootstrap Icons, and Bootstrap JS from CDNs without integrity metadata.
- **Runtime evidence:** Offline reload produced Chrome `ERR_INTERNET_DISCONNECTED`; the dashboard did not render from local assets.
- **Impact:** The private local tool fails when offline, leaks requests to third parties, and has a larger supply-chain surface.
- **Recommended action:** Vendor required fonts/icons/JS or ensure core dashboard works without them; add CSP and document optional enhancement behavior.
- **Likely files:** `base.html`, static assets, build/package config, CI/browser tests.
- **Reuse:** Existing vanilla CSS/JS and local package installation.
- **Acceptance criteria:** Dashboard renders core state offline; no third-party network is required for signals/status UI; CSP is explicit.
- **Verification:** Browser with network blocked and console/network capture.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** After UI state semantics.

### AUD-019 — Replace stuck background generation with bounded, recoverable work

- **Classification:** Confirmed concurrency risk
- **Subsystem:** Recommendation route background jobs
- **Evidence:** `api/routes/options.py:35-90` records `_generation_in_flight` and clears it only in the worker `finally`; `api/routes/options.py:509-550` reports a timeout diagnostic but does not evict/recover a permanently hung worker.
- **Impact:** A single hung broker call can block future recommendation refreshes indefinitely.
- **Recommended action:** Use bounded worker/executor operations with cancellation/timeout, explicit job state, and safe replacement after stale lease expiry.
- **Likely files:** `api/routes/options.py`, connection calls, run model, route tests.
- **Reuse:** `start_background_refresh()` serialization and refresh attempt model.
- **Acceptance criteria:** A permanently blocked fake provider does not permanently block later refresh; state reports running/stale/failed distinctly.
- **Verification:** Fault-injection concurrency test with bounded test timeout.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** After service ownership cleanup.

### AUD-020 — Define consistent API envelopes, validation, and same-origin protection

- **Classification:** Confirmed API debt / local security risk
- **Subsystem:** Flask routes
- **Evidence:** Standard helpers exist in `api/routes/utils.py:143-190`, but `api/routes/run.py:27-42`, `watchlist.py:27-67`, `settings.py:41-80`, and earnings/portfolio routes return mixed shapes. `POST /api/run/refresh` has no body/origin/CSRF protection.
- **Impact:** Frontend error handling is inconsistent; a hostile local webpage can repeatedly trigger expensive scans through a loopback browser context.
- **Recommended action:** Define route response/error codes and validation policy; add same-origin/CSRF token for state-changing routes while retaining loopback support; keep refresh serialization/rate limiting.
- **Likely files:** route modules, JS API clients, tests.
- **Reuse:** `success_response`, `error_response`, `opend_unavailable_response`, route rate limiter.
- **Acceptance criteria:** All registered endpoints have documented envelope/status/error codes; invalid input and foreign-origin POSTs are tested.
- **Verification:** Route contract test matrix.
- **Effort / confidence:** Medium / Medium-High
- **Dependencies / order:** After auth/identity decision; before exposing more mutators.

### AUD-021 — Enforce SQLite pool capacity or replace the pool with a bounded lease model

- **Classification:** Confirmed performance/concurrency debt
- **Subsystem:** SQLite pooling
- **Evidence:** `db/sqlite_pool.py:31-42` creates a new connection whenever the idle queue is empty; `maxsize` only limits returned idle connections, not concurrent connections.
- **Impact:** Concurrent route/background work can exceed the configured capacity and amplify SQLite contention.
- **Recommended action:** Track created/leased capacity under a condition variable, wait with timeout, and test concurrent leases.
- **Likely files:** `db/sqlite_pool.py`, database tests.
- **Reuse:** Existing `_created`, `_borrowed`, and `maxsize` stats.
- **Acceptance criteria:** Concurrent leases never exceed the configured maximum; timeout produces a clear degraded response; no connection leak.
- **Verification:** Thread stress test and Windows SQLite smoke.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** Independent P2.

### AUD-022 — Add retention/minimization for snapshot and diagnostic history

- **Classification:** Confirmed privacy/storage debt
- **Subsystem:** SQLite persistence
- **Evidence:** `db/database.py:109-149` stores full serialized snapshots including portfolio contents; `db/schema.py` defines run, attempt, ledger, chain, IV, and earnings history; no run/attempt retention path is exposed.
- **Impact:** Sensitive account history grows indefinitely and backups become larger than necessary.
- **Recommended action:** Minimize snapshot fields, document purpose/retention, prune old records while preserving latest valid snapshot and audit summary, and provide a safe backup/restore procedure.
- **Likely files:** schema/database/repositories, settings/run diagnostics, docs/tests.
- **Reuse:** existing published/latest query and schema version migrations.
- **Acceptance criteria:** Retention is configurable/documented; latest snapshot survives pruning; no raw account IDs are retained.
- **Verification:** Migration/pruning/restore test.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** After AUD-006 and AUD-011.

## P2/P3 — quality gates, maintenance, and product opportunities

### AUD-023 — Restore a green, current CI contract

- **Classification:** Confirmed test/automation defects
- **Subsystem:** CI/dependencies/tests
- **Evidence:** `ruff check .` fails three issues in `tests/test_wheel_parity.py`; `npm ci --dry-run --ignore-scripts` fails because `@playwright/test`, `playwright`, `fsevents`, and `playwright-core` are missing from `package-lock.json`; Vitest passes 5 files/28 tests; `.github/workflows/ci.yml` does not run Playwright, Gitleaks, or Codecov despite `.github/AGENTS.md:11` claiming them.
- **Impact:** Fresh clone cannot reliably reproduce the tested frontend environment; security/coverage gates are assumed but absent.
- **Recommended action:** Decide the supported E2E stack, regenerate lockfile intentionally, fix Ruff, add Node setup/version, add actual secret scan/coverage or correct DOX claims, and set CI timeout/artifacts.
- **Likely files:** `tests/test_wheel_parity.py`, `package.json`, `package-lock.json`, `.github/workflows/ci.yml`, `.github/AGENTS.md`, `pyproject.toml`.
- **Reuse:** Existing Vitest and safety tests; existing `scripts/ci_pytest.py` only as a temporary SDK workaround.
- **Acceptance criteria:** Clean clone: `uv sync --locked`, Ruff check/format, CI pytest, `npm ci`, npm test all pass; E2E is either runnable and current or removed from the supported contract.
- **Verification:** Fresh-clone CI-equivalent run with exact artifacts.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** First P2 item; unlocks reliable future fixes.

### AUD-024 — Replace the obsolete E2E suite with consolidated dashboard coverage

- **Classification:** Confirmed test debt
- **Subsystem:** Browser automation
- **Evidence:** `tests/e2e/smoke.spec.js:10-150` targets `http://localhost:5000`, `#dashboard-container`, `#portfolio-summary`, Catalyst Watch, theme toggle, old options tabs, and removed selectors; `package.json` has no E2E script and CI does not execute Playwright.
- **Impact:** The highest-risk user workflows have no browser regression gate.
- **Recommended action:** Replace tests with current selectors and fixtures for disconnected, planning, partial, stale, ready, copy-disabled, watchlist-origin, and mobile states; run without credentials.
- **Likely files:** `tests/e2e`, `package.json`, Playwright config, CI workflow, test fixtures.
- **Reuse:** Existing manual checklist and frontend state model.
- **Acceptance criteria:** Current dashboard smoke runs on the documented port via configured web server; no live broker credentials; core copy gate and state matrix covered.
- **Verification:** CI Playwright run using route fixtures/no mutation.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** After AUD-002, AUD-012, AUD-014, AUD-017.

### AUD-025 — Reconcile docs, environment examples, dependencies, and release metadata

- **Classification:** Confirmed consolidation debt
- **Subsystem:** Documentation/repository hygiene
- **Evidence:** `README.md` and `API.md` describe the 2026-08-02 consolidated app, but `SCORING.md`, `CHANGELOG.md`, `.env.example`, `requirements.txt`, `tests/README.md`, and DOX files retain removed features. `pyproject.toml:3,5` reports version 1.0.0/Python >=3.10 while README requires Python 3.11+ and changelog describes 3.0.0. `docs/migration-ledger.md:27` references nonexistent `scripts/backup_db.py`; `README.md:125-130` contains a private absolute archive path.
- **Impact:** New work follows contradictory instructions; stale dependencies/secrets are installed or requested; recovery guidance is not portable.
- **Recommended action:** Rewrite current contracts; keep historical detail only in changelog/migration ledger; remove unused dependencies/env vars; align version/Python metadata; add portable backup/restore procedure.
- **Likely files:** `SCORING.md`, `CHANGELOG.md`, `README.md`, `API.md`, all affected `AGENTS.md`, `.env.example`, `requirements.txt`, `pyproject.toml`, `tests/README.md`, `docs/migration-ledger.md`.
- **Reuse:** Root AGENTS and `docs/intent/application-purpose.md` as authority.
- **Acceptance criteria:** A new contributor can identify one current architecture/product contract; no removed feature appears in operational instructions; backup/restore works without private paths.
- **Verification:** Repository-wide grep for removed terms plus doc review.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** After safety decisions, before feature work.

### AUD-026 — Remove credential prompts and provider secrets from `.env.example`

- **Classification:** Confirmed security/documentation defect
- **Subsystem:** Configuration examples
- **Evidence:** `.env.example:1-8` requests Moomoo login/password/trading password even though authentication occurs in OpenD and runtime is query-only; `.env.example:23-44` advertises FRED, LLM, Catalyst, and Alpha Vantage variables that are removed/out of scope.
- **Impact:** Encourages unnecessary secret storage and misstates the security boundary.
- **Recommended action:** Keep only consumed safe configuration; state that the app does not accept a trading password and OpenD owns login.
- **Likely files:** `.env.example`, README/config docs, config tests.
- **Reuse:** `connection.json.example` and `core/broker_protocol.py` contract.
- **Acceptance criteria:** No example asks for credentials the app does not consume; secret scan tests cover examples.
- **Verification:** Compare env keys to code reads and run hygiene scan.
- **Effort / confidence:** Small / High
- **Dependencies / order:** With AUD-025.

### AUD-027 — Fix Windows launcher, shutdown, and logging lifecycle

- **Classification:** Confirmed operational debt
- **Subsystem:** Windows runtime
- **Evidence:** `run_api.py:72-79` honors `PORT`, while `start_local.ps1:163-164,201-204` hardcodes 8000; `core/logging_config.py:91-115` replaces handlers without closing them; `db/database.py:293-308` relies on destructor cleanup; background health thread uses sleep loops in `core/background_manager.py:190-214`.
- **Impact:** Custom-port launches can fail falsely; repeated startup/reload can leak Windows file handles; shutdown behavior is less deterministic.
- **Recommended action:** Derive all URLs from one validated port; close handlers explicitly; add app shutdown hooks for broker/services/DB; use event waits for stoppable loops.
- **Likely files:** `start_local.ps1`, `run_api.py`, `app.py`, logging/background/database modules, scripts/tests.
- **Reuse:** Existing `atexit` registration and pool close APIs.
- **Acceptance criteria:** Non-default port smoke works; repeated startup does not duplicate handlers/hold logs; shutdown closes resources within timeout.
- **Verification:** PowerShell smoke and repeated app-factory lifecycle test.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** After CI contract.

### AUD-028 — Calibrate liquidity and executable-credit policy from Moomoo data

- **Classification:** Opportunity / likely ranking risk
- **Subsystem:** Option analytics / presets
- **Evidence:** `core/presets.py` allows max spreads of 45%, 60%, and 70%, and min OI 25/10/5; `core/wheel_decision.py` treats contracts under those ceilings as tradable and ranks by mid-derived premium. `research.md` records that competitor patterns support liquidity cleanup but their marketing thresholds are not evidence.
- **Impact:** A high mid-price/premium-velocity candidate may not be fillable.
- **Recommended action:** Measure watchlist quote distributions and fill proxies; rank using a documented conservative sell-credit estimate while continuing to display mid/bid/ask/spread.
- **Likely files:** `core/presets.py`, `core/wheel_decision.py`, recommendation/UI/tests, optional Moomoo quote adapter.
- **Reuse:** Existing bid/ask/spread/open-interest/volume fields and preset versioning.
- **Acceptance criteria:** One-sided/wide-spread fixtures never become copyable; inflated ask cannot improve rank; thresholds are empirical and versioned.
- **Verification:** Analyze real watchlist samples without storing account secrets; regression-test synthetic quote cases.
- **Effort / confidence:** Medium / Medium
- **Dependencies / order:** After P0 provenance/freshness; do not copy competitor thresholds.

### AUD-029 — Label heuristic EV/PoP honestly and distinguish broker model fields

- **Classification:** Confirmed analytics-trust risk
- **Subsystem:** Scoring/docs/frontend
- **Evidence:** `core/wheel_decision.py` uses `pop = 1 - abs(delta)` and fixed CSP loss estimate; covered-call EV is premium retained; `SCORING.md` presents these as Expected Value and Probability of Profit formulas.
- **External evidence:** OIC describes delta as probability context, not a guaranteed probability; assignment can occur on any business day for American-style short options.
- **Impact:** Users can read heuristic outputs as calibrated probability or expected return.
- **Recommended action:** Rename to “scenario EV heuristic”/“delta-based approximation,” show assumptions adjacent, and keep any Moomoo PoP/ROI as separately sourced broker model fields.
- **Likely files:** `core/wheel_decision.py`, `SCORING.md`, recommendation card/formatter, tests.
- **Reuse:** Existing `score_details`, rationale, source labels.
- **Acceptance criteria:** No UI/API label claims calibrated certainty; unknown broker model stays unknown; formulas/docs/tests agree.
- **Verification:** Golden fixtures and copy-review of card text.
- **Effort / confidence:** Small / High
- **Dependencies / order:** With analytics documentation cleanup.

### AUD-030 — Add compact decision-table context, not a second broad scanner

- **Classification:** Compatible product opportunity
- **Subsystem:** Recommendation cards / option analytics
- **Evidence:** Current cards already expose velocity, annualized return, breakeven buffer, expected move, delta, IV, warnings, source, and freshness in `frontend/templates/partials/dashboard/top_recommendations.html` and `top-recommendations.js`. Wheel Strategy Options publicly demonstrates a manual filter → compare → execute workflow with yield beside cushion, DTE, IV, earnings, and liquidity context.
- **Recommended action:** Reorganize each of the three cards around four questions: income speed, assignment/call-away trade-off, execution quality, and event/data risk. Keep raw premium velocity as rank.
- **Likely files:** recommendation template/JS/CSS and frontend tests.
- **Reuse:** Existing fields and card template; no new scanner or provider required.
- **Acceptance criteria:** Operator can compare top three without opening a second table; definitions are progressive; rank remains velocity within risk tier; all source/freshness warnings remain visible.
- **Verification:** Render desktop/mobile cards and test malformed/unknown fields.
- **Effort / confidence:** Medium / High
- **Dependencies / order:** After P0/P1 data correctness.

### AUD-031 — Add Moomoo-native shortlist volatility/assignment context selectively

- **Classification:** Compatible product opportunity
- **Subsystem:** Broker adapter / shortlist enrichment
- **Evidence:** `research.md` records official Moomoo documentation for option-chain Greeks/IV/OI/volume, volatility history, and option quote analysis fields; local OpenD version/entitlements/signatures were not verified.
- **Recommended action:** Probe capability/version first; fetch IV-vs-HV or broker model fields only for already-qualified top candidates behind cache/rate limits. External data must never promote a pick.
- **Likely files:** `core/broker_protocol.py`, connection adapter, recommendations, run model, frontend card/tests.
- **Reuse:** Existing query-only protocol, rate limiter, TTL cache, source policy.
- **Acceptance criteria:** Unsupported/unauthorized fields degrade to unknown; old OpenD still completes; calls are shortlist-only; disabling enrichment does not change pick/rank.
- **Verification:** Capability probe with fake/old SDK, quota test, and ranking-invariance test.
- **Effort / confidence:** Medium / Medium
- **Dependencies / order:** After P0 and only after local capability verification.

## Rejected ideas

- Broad-market “2M+ contracts” discovery: violates canonical watchlist union and free-tier OpenD quota.
- PVE options-flow, dark-pool, congress, insider, prediction-market, OSINT, or social feeds as ranking/actionability inputs: not Moomoo account/quote truth and expands operational scope.
- Autonomous execution, broker unlock, order-prefill submission, or any hidden execution call: violates structural read-only contract.
- Editable 25+ filter panels: conflicts with versioned immutable presets and the one-screen daily workflow.
- Competitor score formulas, claimed thresholds, testimonials, or returns as evidence: marketing is not calibration/fill/fee data.
- “High IV is good” or “delta equals guaranteed assignment probability”: unsupported simplifications.

## Verification record

### Commands and observations

- `./.venv/Scripts/python.exe -m ruff check .` — **failed**, 3 errors in `tests/test_wheel_parity.py` (import ordering, unused `WheelRunner`, local import ordering).
- `./.venv/Scripts/python.exe -m ruff format --check .` — **passed**, 118 files formatted.
- `./.venv/Scripts/python.exe -m pytest tests/test_no_execution_surface.py tests/test_query_only_broker.py tests/test_connection.py::TestMoomooConnectionInit tests/test_connection.py::TestMoomooConnectionMethods -q` — **passed**, 18 tests.
- `./.venv/Scripts/python.exe -m pytest tests/test_api_config.py tests/test_api_health.py tests/test_database.py tests/test_response_consistency.py -q` — **passed**, 36 tests, 1 skipped.
- `./.venv/Scripts/python.exe scripts/ci_pytest.py tests/test_connection.py::TestMoomooConnectionCaching -q` — **failed**, `FF.F.` and exit 1; direct verbose run identified `MoomooConnection.__new__()` rejecting `broker_cache_after_hours` in `test_broker_cache_after_hours_disabled`; broader direct pytest runs timed out after partial progress.
- `npm test -- --silent` — **passed**, 5 files / 28 tests.
- `npm ci --dry-run --ignore-scripts` — **failed**, package and lockfile out of sync; missing `@playwright/test`, `playwright`, `fsevents`, and `playwright-core` entries.
- Browser disconnected run on loopback port 8765 — `/health` returned database available/OpenD unavailable/status healthy; account/weekly endpoints returned 503; UI showed zero placeholders and duplicate API requests.
- Browser connected non-sensitive run after OpenD was started — health reported connected and REAL-time account status loaded; recommendation refresh was blocked to avoid a new live scan. Within the bounded page observation, `/api/run` was requested 5 times, `/api/settings` 6 times, and `/api/system/opend-status` 3 times.
- Mobile browser observation at 390px — `scrollWidth=2021`, `clientWidth=375`; first Tab focused Dashboard; no skip link was present.
- Offline reload — browser displayed `ERR_INTERNET_DISCONNECTED`; local core UI did not render without network assets.
- Query-only broker test suite passed; no unlock/order/cancel/modify calls were observed or introduced.

### Could not verify safely

1. A connected, ready, fresh, complete REAL snapshot with live recommendation cards was not generated during this audit; doing so would create a broker scan and alter local run/ledger history. The P0 findings are proven from code paths and deterministic tests instead.
2. No raw account identifier, balance, position, or screenshot was retained in this artifact.
3. PVE authenticated dashboard behavior, latency, data accuracy, and accessibility were not observed; only first-party published capability claims were used.
4. Local OpenD version, API entitlements, and support/signatures for newer option quote/volatility endpoints were not probed.
5. Liquidity thresholds were not calibrated from a stored Moomoo watchlist sample; AUD-028 is an opportunity/risk hypothesis, not a confirmed threshold defect.
6. Full axe/accessibility tooling was not installed or run; markup/runtime findings are evidence-backed but need the proposed browser automation check.

### External benchmark sources

- Wheel Strategy Options CSP screener: https://wheelstrategyoptions.com/about-cash-secured-put-screener
- Wheel Strategy Options covered-call screener: https://wheelstrategyoptions.com/about-covered-call-screener
- Wheel Strategy Options screener guide: https://wheelstrategyoptions.com/learn/screener-guide
- PVE product: https://pve.trade/ and https://pve.trade/option-flow
- OIC assignment FAQ: https://www.optionseducation.org/referencelibrary/faq/options-assignment
- OIC delta/probability guidance: https://www.optionseducation.org/videolibrary/probability-i-delta-as-a-probability-metric
- OCC options risk disclosure: https://www.theocc.com/getmedia/a151a9ae-d784-4a15-bdeb-23a029f50b70/riskstoc.pdf
- Moomoo option chain: https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-chain.html
- Moomoo option volatility: https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-volatility.html
- Moomoo option quote: https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-quote.html

## Final hostile-review checklist

- [x] No application source files were modified.
- [x] User-owned `TECH_DEBT_AUDIT.md` deletion was not reverted.
- [x] P0 items are backed by source evidence and are not based only on competitor claims.
- [x] Competitor-derived ideas are explicitly constrained to manual review, watchlist scope, Moomoo source, and premium-velocity ranking.
- [x] Confirmed defects are separated from risks/debt/opportunities.
- [x] Duplicate findings were merged: freshness/coverage/provenance are separate gates; service ownership and import direction are one architecture item; CI/lockfile/E2E are grouped where they share the feedback-loop contract.
- [x] Load-bearing safety features are explicitly listed as retained.
- [x] Unsupported live-state claims and unobserved connected-ready behavior are listed under could-not-verify.
- [x] The recommended order moves from safety and truth to reliability, then UX/analytics; it does not add feature scope before fixing the trust boundary.
