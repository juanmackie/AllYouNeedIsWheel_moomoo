<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# Core DOX

## Purpose

`core/` owns broker connection helpers, scoring primitives, option decision logic, risk/evidence gates, the wheel runner and its immutable run model, caches, logging, and shared pure utilities.

## Ownership

- Own business-critical trading logic that should not depend on Flask.
- Own reusable decision functions for Wheel strategy signals, scoring factors, Greeks, and evidence-gated advice.
- Own executable-bid capital-return and premium-velocity calculations plus quote-evidence gates; quality/event tiers classify risk for display, while ranking is capital return per day first with bid velocity as the tie-break.
- Own OpenD connection lifecycle helpers and shared runtime utilities. `MoomooConnection` additionally exposes the read-only deal/fee/cash-flow queries (`get_history_deals`, `get_order_fees`, `get_cash_flow`, plus `resolve_portfolio_identity` for account-scoped persistence); these remain query-only members of `broker_protocol.QUERY_SDK_MEMBERS`.
- Own the growth cockpit pure logic: `exit_playbook` (HOLD/TAKE_PROFIT/ROLL/CLOSE verdicts, including the 2x loss stop and ex-dividend early-assignment rule for ITM short calls; `captured_profit_pct_for_short` is signed — negative is a loss — because a zero floor makes a stop unreachable), `position_diff` + `portfolio_snapshot` (per-run snapshots and trade-event inference), `sizing` (exposure/concentration arithmetic), `growth_mode.growth_pace` (path-to-target math), and `monte_carlo` (Moomoo-snapshot-calibrated GBM NAV fan chart: daily collapse, irregular-gap MLE drift/vol, deterministic seed; fills corroboration is option-leg totals only). All are pure and broker-free.
- Own pure broker-verified outcome attribution in `outcome_attribution.py`: combined per-recommendation net P&L (option premium P&L + underlying share P&L − fees), slippage/leakage, capital-days accounting, and the owner-efficiency summary (net P&L / capital-days, net dollars, open losses, drawdown). Missing cost basis ⇒ explicitly UNKNOWN (never fabricated); deposits/withdrawals never enter trading P&L; the capital-days denominator uses per-tranche arithmetic so partial closes and assignments cannot flatter results (rules documented in the module docstring).

## Local Contracts

- Keep `core` free of `api` imports entirely (any direction): api-layer composition is injected into `WheelRunner` as callables (see `api/services/roll_diagnostics.py`).
- Preserve live-trading safety assumptions and source-of-truth boundaries.
- Scoring changes must preserve or deliberately update the methodology documented in `SCORING.md`; midpoint is display-only and never a ranking basis.
- Connection logic must avoid leaking handles and should be safe under repeated route/service calls. `MoomooConnection.get_option_chain(..., force_refresh=True)` is the explicit seam for closed-market last-session reads; it must remain query-only.

## Work Guidance

- Prefer pure functions and small data transformations for scoring/risk logic.
- Keep thresholds, weights, and profile choices explicit and covered by regression tests.
- Decision helpers for read-only panels should prefer plain-English blockers/rationale and preserve the ability to surface research-only outcomes.
- CSP `max_contracts` is bounded by net broker cash, the preset cash budget, and remaining per-underlying exposure. It is distinct from the recommended quantity; buying power must not enlarge cash-secured capacity.
- Avoid import-time network calls, thread starts, or DB writes.
- Reuse `ticker_utils`, `rate_limiter`, and logging helpers instead of local one-off versions.
- `MoomooConnection.get_option_contracts` discovers both option rights in inclusive ranges of at most 30 days through the chain limiter and gate. `OptionChainCache` reuses covered ranges only on the exact US market date; quote force-refresh never invalidates this metadata. `get_option_quotes` batches fresh snapshots at 400 codes and preserves broker timestamps. Core never persists directories; services supply SQLite read-through. A failed discovery stores the broker reason by code in `_contract_discovery_errors` for service diagnostics.
- `WheelRunner` persists discovery progress separately from its immutable published snapshot; snapshot `preflight` reports discovery-budget arithmetic independently of quote freshness.
- `WheelRunSnapshot` preserves recommendation-result `cash_available_for_csp`, `broker_buying_power`, and `cash_reserved_for_csp` for published-run display. Missing legacy fields stay `None`; measured zero remains zero.
- `recover_interrupted_refresh` runs only at explicit application startup. It fails persisted queued/refreshing attempts when no process-local worker owns the refresh lock, leaves published snapshots unchanged, and never infers failure from elapsed time alone. The app is single-process; do not reuse this process-local ownership check for a multi-worker deployment.
- `start_background_refresh` persists the accepted queued attempt before starting the worker. The worker continues that same attempt ID without writing a second queued row; if worker startup fails, the attempt is marked failed and the process-local lock is released.
- Cross-layer composition (e.g. roll diagnostics needing registered services) is injected as a provider callable at factory time (`api/__init__.py` → `WheelRunner(roll_diagnostics_provider=...)`); never import `api` from `core`.

## Verification

- Scoring/decision changes: run `pytest tests/test_wheel_decision.py tests/test_score_regression.py`.
- Connection changes: run `pytest tests/test_connection.py tests/test_import_side_effects.py`.
- Cache/rate-limit changes: run the matching focused tests such as `tests/test_rate_limiter.py` or `tests/test_scan_ledger.py`.
- Before significant edits: question requirements, delete duplication, then simplify; compatibility needs concrete consumer evidence. Structural readonly (`core/broker_protocol.py`) is non-negotiable. After edits, report changes, verification, assumptions, and risks; note any owning contracts intentionally unchanged.

## Child DOX Index

No child DOX files yet.
