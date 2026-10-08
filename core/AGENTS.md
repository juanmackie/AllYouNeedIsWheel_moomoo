<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# Core DOX

## Purpose

`core/` owns broker connection helpers, scoring primitives, option decision logic, risk/evidence gates, the wheel runner and its immutable run model, caches, logging, and shared pure utilities.

## Ownership

- Own business-critical trading logic that should not depend on Flask.
- `historical_volatility.py` owns pure annualized log-return volatility math. `MoomooConnection.get_historical_volatility` uses the existing query-only historical-K-line wrapper, verifies quota first, excludes an incomplete current-session bar, and caches unavailable results briefly and successful results for 24 hours.
- The pure `falling_knife_diagnostic` uses 20-session return and the 50-session simple average; `get_historical_volatility` includes it from the same completed Moomoo close window. It is display-only and unavailable until all required bars exist.
- Own reusable decision functions for Wheel strategy signals, scoring factors, Greeks, and evidence-gated advice.
- Own executable-bid capital-return and premium-velocity calculations plus quote-evidence gates; quality/event tiers classify risk for display, while ranking is capital return per day first with bid velocity as the tie-break.
- Own OpenD connection lifecycle helpers and shared runtime utilities. `MoomooConnection` additionally exposes the read-only deal/fee/cash-flow queries (`get_history_deals`, `get_order_fees`, `get_cash_flow`, plus `resolve_portfolio_identity` for account-scoped persistence); these remain query-only members of `broker_protocol.QUERY_SDK_MEMBERS`.
- Own the growth cockpit pure logic: `exit_playbook` (HOLD/TAKE_PROFIT/ROLL/CLOSE verdicts, including the 2x loss stop and ex-dividend early-assignment rule for ITM short calls; `captured_profit_pct_for_short` is signed — negative is a loss — because a zero floor makes a stop unreachable), `position_diff` + `portfolio_snapshot` (per-run snapshots and trade-event inference), `sizing` (exposure/concentration arithmetic), and `goal_pace` (flow-adjusted 5x path math). `risk_metrics` owns pure risk, confidence, and covered-call-intent calculations. All are broker-free.
- Own pure broker-verified outcome attribution in `outcome_attribution.py`: combined per-recommendation net P&L (option premium P&L + underlying share P&L − fees), slippage/leakage, capital-days accounting, and the owner-efficiency summary (net P&L / capital-days, net dollars, open losses, drawdown). Missing cost basis ⇒ explicitly UNKNOWN (never fabricated); deposits/withdrawals never enter trading P&L; the capital-days denominator uses per-tranche arithmetic so partial closes and assignments cannot flatter results (rules documented in the module docstring).
- `measured_fee_model.py` owns pure BUY/SELL per-contract fee estimates from complete, fee-resolved US option orders. Estimates are account-scoped upstream, use a median per order, and remain unknown when either side lacks evidence; they are measurements from ingested fills, never a prospective broker schedule.
- `wheel_cycle.py::realized_cycle_outcome` combines net option cash flows with the assigned-share round trip only from complete paired-fill evidence; open, expired-unverified, provisional, mismatched, or fee-incomplete cycles stay unknown.
- `connection_manager.py` preserves Moomoo stock `can_sell_qty` on broker position rows; `portfolio_snapshot.py` retains it for the local holdings view as display-only evidence, never as covered-call eligibility or actionability.
- `MoomooConnection.get_open_orders()` and `get_max_sellable_info()` are account-scoped, rate-limited query-only diagnostics. They return only active-order contract/status/remaining quantity and sell-capacity fields, preserve unknown as `None`, and never affect eligibility or sizing.

## Local Contracts

- Keep `core` free of `api` imports entirely (any direction): api-layer composition is injected into `WheelRunner` as callables (see `api/services/roll_diagnostics.py`).
- Preserve live-trading safety assumptions and source-of-truth boundaries.
- `WheelRunner` may publish and position-diff only a complete broker portfolio read. An account/position query failure fails the refresh and retains the last successful snapshot; never turn unavailable positions into an empty book.
- Covered-call candidates require a usable stock basis: use `gate_basis` only when its source is `ledger_adjusted` or `moomoo_avg_cost`, otherwise fall back to a positive Moomoo `avg_cost`; if neither is available, hard-block the call with `missing_cost_basis`. Never interpret a missing or zero basis as permission to pass.
- Use `core.utils.market_now()` for US market dates in `api/`, `core/`, and `db/`; keep host-local date calls out of market calculations.
- Use `core.session_clock.SessionCalendar` for session boundaries. OpenD trading-day data is preferred; weekday-only fallback must remain labeled and must not turn missing calendar data into a confirmed holiday/session.
- During an open session, require fresh Moomoo quote evidence on each actionable candidate. Missing or stale timestamps on unrelated scanned symbols must not demote that candidate; complete-union coverage remains a separate hard gate.
- Scoring changes must preserve or deliberately update the methodology documented in `SCORING.md`; midpoint is display-only and never a ranking basis.
- Connection logic must avoid leaking handles and should be safe under repeated route/service calls. Closed-session option cache entries are reusable only when captured after the last regular close; range-chain snapshots must fail closed rather than truncate more than 400 option codes.

## Work Guidance

- Prefer pure functions and small data transformations for scoring/risk logic.
- Keep thresholds, weights, and profile choices explicit and covered by regression tests.
- Decision helpers for read-only panels should prefer plain-English blockers/rationale and preserve the ability to surface research-only outcomes.
- Avoid import-time network calls, thread starts, or DB writes.
- Reuse `ticker_utils`, `rate_limiter`, and logging helpers instead of local one-off versions.
- Cross-layer composition (e.g. roll diagnostics needing registered services) is injected as a provider callable at factory time (`api/__init__.py` → `WheelRunner(roll_diagnostics_provider=...)`); never import `api` from `core`.
- Wheel-cycle materialization is another optional API-layer callback injected into `WheelRunner`; it is best-effort and cannot prevent an immutable run snapshot from publishing.

## Verification

- Scoring/decision changes: run `pytest tests/test_wheel_decision.py tests/test_score_regression.py`.
- Connection changes: run `pytest tests/test_connection.py tests/test_import_side_effects.py`.
- Cache/rate-limit changes: run the matching focused tests such as `tests/test_rate_limiter.py` or `tests/test_scan_ledger.py`.
- Before significant edits: question requirements, delete dead weight, then simplify (V10 §3); compatibility needs concrete consumer evidence (V10 §3); structural readonly (`core/broker_protocol.py`) is non-negotiable (V10 §6). After edits, apply the brief changed/verified/assumptions/risks closeout (V10 §9) and note any DOX files intentionally unchanged.

## Child DOX Index

No child DOX files yet.
