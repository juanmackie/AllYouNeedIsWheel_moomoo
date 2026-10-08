 AllYouNeedIsWheel — dev-team roadmap: make the wheel compound toward 5x

 Context

 The app is a signals-only, structurally read-only Moomoo OpenD wheel scanner. Engineering is mature (304 commits, ~940 Python tests, clean tree), and the candidate-level engine is sound: executable-bid capital-velocity ranking, delta bands, a 25% per-underlying cap, fail-closed quote freshness, a below-basis call block.

 The product is not yet producing compounding. Evidence from the owner's own options.db:

 - Last acceptance run (2026-09-27): a 69-name Moomoo union → planning, coverage 0/69, zero signals.
 - 0 of 208 past recommendations matched any owner trade.
 - ~75% of the ~$60k account sits in assigned stock, 4 of 5 names underwater (about -$7.9k unrealized vs +$2.5k realized option P&L over 89 days, ~17%/yr).
 - The app cannot see the wheel cycle (assignment → covered call → called away), cannot realize expired-worthless puts, and its pace model would report "on track" for any positive return.

 Goal of this plan: fix what blocks signals today, then make the wheel a first-class cycle, measure honestly, and use Moomoo's own free analytics instead of flaky external feeds. Everything must serve OVERARCHING GOAL.txt (do not edit that file) and follow the repo's AGENTS.md contracts.

 Owner decisions (confirmed this session)

 1. Data policy: keep Moomoo-only picks. Use OpenD's built-in analytics endpoints for diagnostics. Free external APIs stay diagnostic-only. No change to the actionability rule.
 2. Horizon: 5x in 3 years from the 2026-08-22 baseline snapshot (needs ~71%/yr). Deposits are excluded from progress.
 3. Ranking is frozen (api/services/recommendation_ranking.py::rank_key). Add measurement first; revisit only on outcome evidence.

 Standing owner decisions to preserve: below-basis calls are hard-blocked using Moomoo average cost unless fill-history completeness is proven; unknown fees are never treated as zero (see O4); notifications/scheduler are out of scope.

 Feasibility — say this plainly in the UI and to the team

 - 5x in 3 years = 71%/yr. Measured option income ≈ 17%/yr (~$29/day); 5x at that pace takes ~10 years. Needed flat premium is ~8x the measured pace.
 - Nothing in the data supports a sustained 71% from an unlevered cash-secured wheel. The app's job is to show required vs realized CAGR, the gap in points, "at current pace: N years", and which levers move it. It must not imply the target is on track when it is not.
 - Levers inside a cash-secured wheel, largest first: (1) dead money in underwater stock; (2) loss drag from assignments into falling names; (3) realized capture vs quoted yield (quoted picks show 100%+ annualized, realized is ~17%); (4) utilisation (~98% deployed today, little headroom); (5) fee drag (~$0.55/leg on one-lot trades); (6) compounding size.
 - Leverage/margin is out of scope and needs the owner's explicit risk decision (O13).

 Verified findings (read by me in code / read-only DB)

 #: V1
 Finding: CSP cash double-count is live. _csp_cash_available compares the summary key (available_cash) to a set of broker field names, so the "already
 net of collateral" skip never fires. 2026-09-27 snapshot: cash 1,075.66, reserved 16,000 → cash_available_for_csp = 0 (should be ≈1,075.66). Twin bug
  in the cached path.
 Evidence: api/services/portfolio_context.py:15-22,53-57,145,321-330,389-391; core/connection_manager.py:865; portfolio_snapshots id 20-22
 ────────────────────────────────────────
 #: V2
 Finding: 69-name union cannot scan. Preflight charges 3 chain calls/symbol regardless of cash fit (207 calls vs cap 100).
 Evidence: api/services/watchlist_manager.py:355-386; pinned by tests/test_watchlist_manager.py:29-70
 ────────────────────────────────────────
 #: V3
 Finding: Expired/assigned shorts never realize. A short with no closing BUY stays open forever, capital-days accrue to now.
 Evidence: api/services/outcome_service.py:765-787
 ────────────────────────────────────────
 #: V4
 Finding: Goal baseline will drift. Snapshots, fills, cash flows pruned at 365 days on every startup; no backup script.
 Evidence: db/database.py:26-38,65

 Reported by exploration, verify while implementing

 - Assignment shows in fills as OPT BUY @0.0 + STK BUY qty=100n @strike within ~1s (4 real cases); worthless expiry leaves no row; assigned CSPs are currently booked as $0-buyback "measured" wins.
 - Fill history is capped at 90 days; the CSPs behind AA/ORCL/SPCX predate it, so the ledger will not unlock those underwater calls on day one (only SOFI is provable). position_list_query.diluted_cost may help — needs a probe and owner approval (O3).
- Resolved 2026-09-30: symbols with no candidate may have missing or stale quote timestamps. Run freshness now requires some fresh broker evidence for session confirmation and records stale symbols diagnostically; each actionable candidate independently needs fresh, parseable Moomoo quote evidence during an open session. Complete-union coverage remains a separate hard gate.
 - QUERY_SDK_MEMBERS is documentation only; the real gate is QueryOnlyTradeContext (trade side). The quote context is unrestricted; cancel_all_order/change_order are missing from FORBIDDEN_SDK_MEMBERS.
 - Below-basis block fails open when avg cost is missing (core/wheel_decision.py:587). A failed position_list_query yields an empty book that can be persisted and diffed into false exits (connection_manager.py:897-898, wheel_runner.py:387-405).
 - Exit playbook emits CLOSE for ITM calls even above basis; ITM puts have no roll path; exit thresholds are never populated from presets; ROTATE can never fire; ex-dividend feed fills 0 of 30 rows (rule inert); IV rank is insufficient_history everywhere; earnings refresh covers held tickers only.
- Resolved 2026-09-30: weekly option-income expiry, earnings warning/locked-ticker day counts, core Greeks dates, and IV-history sample timestamps now use the US market calendar. Roll-pressure `generated_at` timestamps are UTC; these results no longer inherit a naive host-local timestamp/date.
 - Installed moomoo-api 10.9.6908 has unused free endpoints: get_option_underlying_overview (IV rank/percentile/HV, 500 codes/call), get_option_underlying_his_volatility, get_earnings_calendar, get_dividend_calendar, get_rehab, request_trading_days, get_market_state, request_history_kline; trade-side order_list_query, acctradinginfo_query. accinfo_query is called without a currency (defaults HKD) while the payload hard-codes USD.

 ---

 Phase 0 — Correctness & unblock (start day 1; parallel; no schema change)

 Each item small; all lead with a failing test. Targeted pytest first.

 ID: 0a
 Change: Read-only probes (no code path change): tools/probe_opend_analytics.py modelled on tools/probe_broker_history.py; verify each candidate
 endpoint + entitlement, output docs/opend-analytics-capability.md
 Files: tools/, docs/
 Test: probe run, redacted output
 ────────────────────────────────────────
 ID: 0b
 Change: Fix CSP cash double-count in both context builders; carry the broker field name (available_cash_source) through the summary; pass currency=USD
 to accinfo_query; fix max_power_short_sell → max_power_short; label/remove HKD fallbacks. Prefer building context once (three builders today: two in
 portfolio_context.py, one in portfolio_scoring.py).
 Files: wheel_decision.py, connection_manager.py, wheel_runner.py, options_data.py:107-123
 Test: test_wheel_decision.py, test_connection.py, test_options_data.py
 ────────────────────────────────────────
 ID: 0e
 Change: Harden read-only surface: add cancel_all_order, change_order to FORBIDDEN_SDK_MEMBERS; AST test that every quote_ctx.X/trd_ctx.X in
 connection_manager.py is in QUERY_SDK_MEMBERS and QueryOnlyTradeContext exposes exactly its allowlist
 Files: core/broker_protocol.py, core/context_factory.py
 Test: test_no_execution_surface.py, test_query_only_broker.py
 ────────────────────────────────────────
 ID: 0f
 Change: Stop booking assignment as profit: exclude OPT BUY @0 paired with same-underlying stock BUY from close_fills
 Files: outcome_service.py:727-728
 Test: real-shape fixture (redacted tickers)
 ────────────────────────────────────────
 ID: 0g
 Change: Market-date everywhere via market_now(); AST guard forbidding date.today() / datetime.now().date() in api/, core/, db/
 Files: files listed above
 Test: tests/test_utils.py + guard test
 ────────────────────────────────────────
 ID: 0h
 Change: Fix UI unit mismatch: top-recommendations.js:763-766 divides total credit ($) by required premium/day
 Files: top-recommendations.js
 Test: tests/frontend/top-recommendations.test.js
 ────────────────────────────────────────
 loadPortfolioData → alerts
 Files: frontend/static/js/dashboard/, api/routes/options.py:331-364
 Test: dashboard-init.test.js

 Phase 1 — W1: cash-aware, session-aware scan (L, medium risk)

 Coverage semantics (no contract change): a symbol is covered when it has a terminal, evidenced disposition — scanned, skipped_no_cash_fit (needs a Moomoo snapshot price and a proven "no strike fits min(cash_for_csp, exposure room)"), or error (still a gap). Cash-skips already count as scanned in the loop (recommendations.py:1519-1524); the inconsistency is the preflight. Each skip carries {price, min_strike_needed, cash_at_scan, fetched_at} and a coverage_cash_skipped count, so nothing is silently truncated. resolve_coverage_truth stays scanned >= total.

 1. Pure core/scan_plan.py: csp_affordability(price, cash, exposure_room, preset) (one predicate replacing _has_any_affordable_otm_strike at recommendations.py:372-391 and the duplicate in wheel_decision), partition_universe, chain_budget, select_chain_rows.
 2. Batch price snapshot MoomooConnection.get_price_snapshot(symbols) — one get_market_snapshot per 400 codes, seeds TickerCache, keeps last_price, prev_close_price, 52-week hi/lo, dividend_ttm, update_time (all discarded today). Replaces get_security_types (recommendations.py:886-898).
 3. Preflight rewrite: calls_per_symbol = ceil(span_days/30); real time = max(calls*spacing, (ceil(calls/max_req)-1)*window); freshness-window feasibility only in an open session (closed sessions are quota-only; staged mode re-verifies at copy time); recommended_max_size phrased as affordable names.
 4. Range chain fetch get_option_chain_range(symbol, right, start, end, band): one chain call + one snapshot for band-selected codes; derive expirations from rows (drops get_option_expiration_date per symbol). Keep the 3-expiration rule (min/preferred/max) so ranking evidence does not drift. Do not use OptionDataFilter initially (bypasses the chain cache; strike band + cash cap already bound the snapshot). Select per-expiry, not head(20) (connection_manager.py:787-789). Balanced (14-45 = 32 days) needs 2 calls; Aggressive/Conservative need 1.
 5. Decouple lanes: split get_top_recommendations into _run_csp_lane / _run_cc_lane / _assemble; an infeasible CSP preflight no longer returns before the covered-call lane (recommendations.py:995-1037). Global copy gate unchanged (O1).
 6. Closed-session cache + core/session_clock.py (pure SessionCalendar seeded from request_trading_days, weekday fallback labelled as such): replace forced refresh on every closed-market call (options_data.py:65,74) with "reusable if captured after last regular close". Replaces three duplicate clocks (utils.is_market_open, run_model._scheduled_market_open, entry_window_advice).
 7. Per-pick funding: annotate top picks with funded_contracts / cash_conflict by sequential allocation (order and membership unchanged); enforce the 25% per-underlying cap jointly across picks on one underlying (_select_top(max_per=2) at recommendations.py:1351, silent clamp at ~1383-1393). Drop-vs-annotate is O12.

 Tests: tests/test_watchlist_manager.py:29-70 (all four flip: 243s→81s, etc.); adapt test_recommendations.py preflight mocks (~307, 347, 422, 1523), delete tests-only _can_afford_csp family; new tests/test_scan_plan.py (incl. property test old-vs-new affordability on a grid), tests/test_session_clock.py; new scenario: 69-name union at $1,075.66 → complete, cash_skipped>0, chain calls = affordable count, CC lane present when quota-infeasible.
 Docs: SCORING.md state table + coverage paragraph, api/services/AGENTS.md, core/AGENTS.md, CHANGELOG.

 Phase 2 — W2: the wheel as a cycle (L, medium-high risk; money-adjacent)

 Schema v13 (additive, idempotent, in both create_tables and migrate_database; bump SCHEMA_VERSION; update pins at tests/test_database.py:1078,1113). Land early so the Phase 3 candidate archive starts collecting immediately.
 - wheel_cycles(cycle_id UNIQUE, env, account_id, ticker, state, origin, opened_at, closed_at, shares, gross_basis_per_share, credits_net, open_credit, fees_known, completeness, reasons_json, adjusted_basis_per_share, events_json, updated_at) — materialized fold (fills are pruned/windowed, so a durable fold is required).
 - Retention: remove option_fills, account_cash_flows from DEFAULT_RETENTION_DAYS; raise portfolio_snapshots, trade_events, run_metadata to ~1,200 days (~10 MB total today). Fixes V4.

 Pure core/wheel_cycle.py: states csp_open → assigned_uncovered → cc_open → called_away → closed; classify_fills, fold_cycles, adjusted_basis, gate_basis(cycle, broker_avg_cost, diluted_cost=None) → (basis, source) with source ledger_adjusted | moomoo_avg_cost | missing. Adjusted basis = gross basis − realized credits net of fees (open short-call premium excluded — it is a liability until expiry; a missing opening credit is excluded and flagged, which is conservative).

 Detection priority: (1) fills — assigned put = OPT BUY @0 + same-underlying STK BUY 100n @strike ≤5s; called-away = mirror (not yet observed live: fixture-tested, marked provisional); expiry = contract in previous snapshot, absent now, expiry date before today ET, no pair/buyback (label expired_unverified if fills predate expiry+1d); ignore zero-price option SELLs; (2) book reconciliation → needs_review; (3) position_diff stays as the run-cadence journal and gains called-away parity.
 Completeness proven requires: window start ≤ cycle origin, all cycle fills have non-NULL fees, ledger shares == broker position, no unclassified zero-price option fills. Anything else → partial → falls back to broker average cost.

 Wiring: api/services/wheel_ledger.py + db/wheel_cycles_repository.py (same _identity_where pattern as fills_repository); recompute on fill ingest and run publish (best-effort, never fails the publish). Inject gate_basis/gate_basis_source into positions[ticker] via an optional basis_provider on PortfolioContext.get_portfolio_context (the engine builds its own context; a WheelRunner-side merge would be invisible). Reuse fills_service.normalize_deal_row, outcome_attribution.share_leg_pnl/combined_outcome (dead helpers gain their first production caller), capital_days_from_lots. Cross-check covered-call capacity with can_sell_qty (display-only first).

 Exit playbook + preset v8 (all three presets 7→8; tests/test_presets.py:42, tests/test_wheel_parity.py:261):
 - New verdict ACCEPT_ASSIGNMENT (ITM short call with strike >= gate_basis; today rules 1/1b/2/3/4b say CLOSE); new ROLL_OUT_DOWN for ITM short puts (later expiry, strike ≤ current, net credit > 0 after known fees); else the existing CLOSE stands.
 - evaluate_exit takes stock_basis/basis_source; score_position/build_roll_decisions pass the stock book.
 - Preset fields exit_profit_take_pct, exit_roll_dte, exit_delta, exit_deep_itm_pct, exit_stop_loss_pct, rotate_min_multiplier; WheelPreset.exit_thresholds() replaces loose context keys (position_scorer.py:130-136). Proposed roll_dte = max(2, csp_min_dte//3) (7/4/2) because the current 21 is ≥ Balanced/Aggressive entry minimums (O6).
 - Fee model for ROTATE / roll net credit: measured from the owner's ingested fills, disclosed as "measured from your fills" (O4).

 Holdings + "what changed since last visit": GET /api/portfolio/holdings?since=ISO (local SQLite + latest snapshot; no live OpenD). Rows: shares, broker avg cost, adjusted basis + completeness, cycle state, open legs + verdicts, best call ≥ basis, dead-money share of NAV; changes from trade_events/cycle events; expiries in next 7 days; fill-ingest age (90-day window warning). Client keeps lastVisitAt in localStorage; no push notifications. New holdings.html partial + dashboard/holdings.js (textContent/escapeHtml only).

 Tests: new test_wheel_cycle.py (4 real assignment shapes redacted to synthetic tickers + synthetic called-away), test_wheel_ledger.py, tests/frontend/holdings.test.js; extend test_exit_playbook.py, test_position_scorer.py, test_wheel_decision.py (missing basis blocks; adjusted basis only when proven), test_position_diff.py, test_routes_portfolio.py.
 Docs: db/AGENTS.md, core/AGENTS.md, api/services/AGENTS.md, frontend/AGENTS.md, SCORING.md (exit playbook, basis gate), API.md, CHANGELOG; annotate the P0-3 note in plans/5x-signal-review-2026-09-27.md.

 Phase 3 — W3: measure honestly (L, medium risk)

 1. Outcome realization (outcome_service.py): classify assigned / called-away / expired-worthless via wheel_cycle.classify_fills + book absence; synthesize the closing lot at expiry-day 16:00 ET so capital-days stop (_fifo_lots, aggregate_group, outcome_attribution.py:217-257); report cycle-level combined P&L (combined_outcome); share leg unknown ⇒ whole outcome unknown.
 2. Candidate archive (v13 run_candidates): archive hard-gate-passing candidates (cap ~10/ticker) from all_candidates before _select_top and the per-ticker cut, inside the save_run_snapshot transaction (atomic publish holds). Excluded from to_dict so snapshot_json doesn't grow. Rank stays frozen.
 3. Shadow scoring (v13 shadow_outcomes): settle archived candidates after expiry from unadjusted daily K-lines; PUT net/share = bid − max(0, K − S_exp), CALL = bid − max(0, S_exp − K) labelled upside-forgone. Probe get_history_kl_quota first; zero-quota fallback = harvest prev_close_price from the daily batch snapshot. Pure core/shadow_rank.py evaluates alternative ranks offline only. Routes: GET /api/options/analytics/shadow, POST .../shadow/settle (rate-limited, OpenD-gated).
 4. Decision checkpoint for rank_key: ready only when per DTE bucket there are ≥100 settled candidates across ≥8 expiry weeks and ≥40 (ticker, expiry-week) clusters; compare alternative vs frozen top-3 mean realized capital return/day with a block bootstrap by expiry week, 95% CI. Until then the app reports ready:false, n=…. The change itself stays the owner's decision. (Expect ~late Nov if the archive starts early Oct.)
 5. Goal/pace rewrite core/goal_pace.py (replaces growth_mode.growth_pace; delete estimate_target_gap): time-weighted return chain-linked over flow dates (first consumer of account_cash_flows/is_capital_movement_cash_flow); baseline NAV/date/target multiple/horizon end (2029-08-22) stored in settings JSON; ≥60-day observation before annualizing (O8); on_track = realized_cagr >= required_cagr; outputs required_cagr = (5/index)^(1/years_left)−1, realized_cagr, gap_pp, at_current_pace_years (or "never"), and levers (utilisation, realized yield per capital-day, loss drag, fee drag, dead-money fraction). Cash-flow API depth is limited (one clearing date per call, observed depth ~2026-09-02), so owner-declared rows use account_cash_flows IDs `owner:<uuid>`; pace is hidden as "flows incomplete" until the owner attests history complete (O7, implemented 2026-09-30).
 6. Backup: db/backup.py::backup_database via sqlite3.Connection.backup (WAL-safe, no new dependency), once/day after a successful publish, keep 14 daily + monthly; backups/ git-ignored; restore note in README. Fixes V4 (O14).

 Tests: replace test_growth_pace.py/test_growth_mode.py with test_goal_pace.py; extend test_outcome_service.py, test_outcome_attribution.py, test_outcome_integration.py, test_saved_run_regressions.py; backup test in test_database.py.
 Docs: SCORING.md "Growth pace" rewritten, API.md pace shape, db/AGENTS.md, CHANGELOG.

 Phase 4 — W4: OpenD-native data (M-L, medium risk; each adoption independently reversible)

 Adopt only after the Phase 0a probe shows the endpoint works on the owner's entitlement.

 ┌──────────────────────┬───────────────────────────────────────────────────────────────────────────────┬──────────────────────────────────────────┐
 │         Need         │                                     Adopt                                     │                 Replaces                 │
 ├──────────────────────┼───────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
 │ IV                   │ get_option_underlying_overview (one call, watchlist + held); optional         │ record_iv_data, _daily_atm_series,       │
 │ rank/percentile/HV   │ one-time his_volatility backfill. Display-only as today.                      │ iv_history accumulation                  │
 ├──────────────────────┼───────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
 │ Earnings, whole      │ weekly get_earnings_calendar sweep                                            │ portfolio-only refresher                 │
 │ watchlist            │                                                                               │ (api/__init__.py:404-413), Alpha Vantage │
 ├──────────────────────┼───────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
 │ Ex-dividend          │ get_rehab (held) + get_dividend_calendar — makes the ex-div exit rule live    │ yfinance (0/30 rows)                     │
 ├──────────────────────┼───────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
 │ Holidays / half-days │ request_trading_days → SessionCalendar                                        │ weekday-only clock                       │
 ├──────────────────────┼───────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
 │ Diagnostics          │ 52-week range / falling-knife flag, IV-vs-HV — display-only (any eligibility  │ nothing exists                           │
 │                      │ use waits for shadow evidence, O9)                                            │                                          │
 ├──────────────────────┼───────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────┤
 │                      │ order_list_query added to QueryOnlyTradeContext; "resting" badge; dedupe      │ nothing; matches the Australian          │
 │ Resting orders       │ picks that already have a working order; acctradinginfo_query max-sellable on │ resting-limit workflow                   │
 │                      │  top-3 only                                                                   │                                          │
 └──────────────────────┴───────────────────────────────────────────────────────────────────────────────┴──────────────────────────────────────────┘

 Read-only checklist for every new SDK member: wrapper in MoomooConnection with 
 Fate of Alpha Vantage / yfinance: keep as fallback during rollout; once ≥95% of watchlist names get earnings + ex-div from OpenD, delete alpha_vantage_provider.py (293 lines), the yfinance chain in iv_earnings_service.py, the from_yfinance/price_source/iv_source provenance duplicates (keep chain_source, which gates staging), and the yfinance dependency (O10).

 Phase 5 — Remaining Bitter-Lesson deletions (M, low risk)

 Delete: alerts.py (duplicate of roll_pressure.py) + test; ~350 lines of uncalled OpenD wrappers and matching QUERY_SDK_MEMBERS entries (get_capital_distribution, get_capital_flow, get_broker_queue, get_cur_kline, get_owner_plate, get_option_exercise_probability, get_option_screen, get_short_interest, the earnings price-move calls, create_option_contract, get_security_type, get_opend_diagnostics); tests-only _can_afford_csp/_find_affordable_csp_strike/_csp_target_strike_estimate; leakage_dollars, gross_premium_dollars; "Score:" label + help text, "Macro" row, sellAllOptions stub; requirements.txt + CI drift step (O10); phantom "volatility ≥ 4.5%" text and unused max_watchlist_tickers; duplicated DTE/OTM/cash-fit/exposure checks (including hardcoded 21-45 in outcome_service.py) collapsed to one place each; merge the two decision→dict converters; rename growth_mode.py remnants. Candidate only after confirming no consumer: core/scan_ledger.py + api/routes/ledger.py (superseded by run_candidates).
 Also: AU-time run strip ("US opens in N h", local time next to UTC), CI (coverage floor, flush pytest summary before os._exit, recorded redacted OpenD payload fixtures, route tests for /api/ledger+/api/watchlist).

Continuation record — 2026-09-30

- Completed: deleted the duplicate alerts route and UI fetch; removed unused SDK wrapper families and tests-only CSP helpers; removed dead premium/leakage fields, the Score label, Macro row, sell-all stub, phantom volatility threshold, and unused watchlist limit; consolidated the outcome DTE bounds; renamed the remaining risk helpers to `core/risk_metrics.py` and removed superseded snapshot-only growth math. Added Moomoo 52-week display context and an OpenD-calendar next-open display to the watchlist/run strip. Added watchlist and ledger route tests, pytest summary flushing, and a 75% CI coverage floor. Fixed the 5x growth-panel ETA renderer and added a frontend regression test for available, unavailable, and reached-target ETA states.
- Completed the display-only W4 diagnostics that do not depend on unsupported analytics protocols: combined top-three cards compare broker IV with annualized 20-session volatility and show a downtrend flag (20-session decline ≥15% plus latest close below the 50-session average), both from the same quota-checked Moomoo adjusted daily-close request and only after rank selection. The OpenD-native overview, earnings calendar, and forward dividend calendar remain gated on the owner's OpenD update and a passing probe.
- Kept intentionally: Alpha Vantage/yfinance and the dependency drift check because the required OpenD earnings/ex-dividend endpoints did not pass; `core/scan_ledger.py` and `/api/ledger` because recommendation refreshes still write this diagnostic ledger and the route remains a public API.
- Not added: captured live OpenD payload fixtures. `tests/fixtures/AGENTS.md` forbids retaining live broker responses; existing test data remains synthetic and redacted.
- REAL account evidence resolved in the read-only probe: `uniCardNum` is distinct from `accID`, and the account's log-reported security firm maps to `FUTUAU`; using that firm filter made the intended REAL row visible. Order, buying-power, and position queries returned `RET_OK`. The existing app config's REAL mode, `FUTUAU` firm, and explicit account ID were verified to match the log-derived row without displaying or changing the ID. `diluted_cost` is populated, but using it as basis still requires O3 approval. OpenD logs report server `10.2.6208` with an update to `10.10.7008` available; the four option/earnings/dividend analytics APIs return `Unknown protocol id`. Moomoo's release notes associate these analytics APIs with `10.8.6808`, so rerun the probe after OpenD is updated before considering adoption. The owner-run Windows/OpenD acceptance run is still outstanding.
- O11 cash-freeze check (read-only, REAL, 2026-09-30): the explicit-account active-order query returned no active orders, and the USD account summary reported no frozen cash. With no working short puts, this cannot establish whether their collateral is included in available cash. Keep O11 unresolved and do not add another cash deduction until a working sell-put order can be compared against broker-frozen cash.
- O7 flow-confidence gap closed: owner deposit/withdrawal rows are added, listed, and removed under active `env`/opaque-account scope; completeness is explicitly confirmed through the latest snapshot, and any owner-flow edit or newly ingested broker capital flow revokes that confirmation. Flow-adjusted goal metrics remain unavailable until complete. Added API/UI, account-scoped persistence tests, and growth-panel coverage.
- Covered-call basis gate completed: candidate scoring uses a `ledger_adjusted` basis only when the wheel ledger proves completeness, otherwise uses positive Moomoo average cost; missing basis now blocks the call. Capital-recovery cards use the same gated basis and disclose its source. The prior fixture runner signature mismatch and collapsed-options smoke assertion were fixed; the full E2E suite passes sequentially (14 passed, one market-gated skip).
- Position-read failure is fail-closed: `get_portfolio` returns unavailable on an unsuccessful position query, portfolio context preserves an error marker, and `WheelRunner` aborts before scanning/publishing/diffing. Existing complete position reads remain valid when the broker returns an empty table.

 Do NOT delete (load-bearing): core/broker_protocol.py, QueryOnlyTradeContext an

 ┌─────┬─────────────────────────────────────────────────────────────────────────┬────────────────────────────────────────────────────────────┐
 │ ID  │                                Question                                 │                          Default                           │
 ├─────┼─────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
 │ O1  │ Covered-call copy while CSP union is incomplete                         │ Keep review-only                                           │
 ├─────┼─────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
 │ O2  │ Balanced preset spans 32 days (2 range calls)                           │ Accept 2 calls                                             │
 ├─────┼─────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
 │ O3  │ Use diluted_cost as basis proof/floor (needs probe)                     │ Not until probed + approved                                │
 ├─────┼─────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
 │ O4  │ Measured per-contract fee model (relaxes "no prospective fee schedule") │ Measured from own fills, labelled                          │
 ├─────┼─────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
 │ O5  │ Auto-accept ITM-put assignment vs CLOSE                                 │ Keep CLOSE (assignment created the current stock book)     │
 ├─────┼─────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
 │ O6  │ Preset v8 exit numbers (roll_dte 7/4/2, etc.)                           │ Adopt proposed                                             │
 ├─────┼─────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
 │ O7  │ Owner-declared capital flows                                            │ Yes                                                        │
 ├─────┼─────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
 │ O8  │ 60-day annualization window                                             │ 60 days                                                    │
 ├─────┼─────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
 │ O9  │ 52-week/falling-knife data used for eligibility                         │ Display-only                                               │
 ├─────┼─────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
 │ O10 │ Remove Alpha Vantage, yfinance, requirements.txt                        │ After probe passes                                         │
 ├─────┼─────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
 │ O11 │ Deduct working-sell-put collateral from CSP cash                        │ Only if frozen_cash shows broker doesn't already freeze it │
 ├─────┼─────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
 │ O12 │ Unfunded picks: annotate vs drop                                        │ Annotate                                                   │
 ├─────┼─────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
 │ O13 │ Leverage/margin                                                         │ Out of scope                                               │
 ├─────┼─────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
 │ O14 │ Retention exemptions + backup location                                  │ As designed above                                          │
 └─────┴─────────────────────────────────────────────────────────────────────────┴────────────────────────────────────────────────────────────┘

 Constraints every change must respect (from AGENTS.md)

 Signals-only/structurally read-only; Moomoo-only actionability (no mock/simulated portfolio data; unknown stays unknown, never zero); rank_key frozen; immutable atomic WheelRunSnapshot and complete-union-or-planning; presets versioned/immutable (bump for any value change); core never imports api; use RLock in the service registry; expensive calls behind cache/rate-limit/source policy; idempotent migrations + SCHEMA_VERSION bump; import has no side effects; update the closest owning AGENTS.md when a contract changes; no push/deploy/merge without authorization; auth/trust-boundary changes state "human security review required" (none planned).

 Verification

 # Phase 0
 uv run pytest tests/test_portfolio_context.py tests/test_run_model.py -q
 uv run pytest tests/test_scan_plan.py tests/test_watchlist_manager.py tests/test_recommendations.py tests/test_connection.py tests/test_options_data.py -q
 # Phase 2 / schema v13
 uv run pytest tests/test_database.py tests/test_fills_repository.py tests/test_wheel_cycle.py tests/test_wheel_ledger.py tests/test_exit_playbook.py tests/test_position_scorer.py tests/test_presets.py tests/test_wheel_parity.py tests/test_wheel_decision.py tests/test_position_diff.py tests/test_score_regression.py -q
 # Phase 3
 uv run pytest tests/test_outcome_service.py tests/test_outcome_attribution.py tests/test_outcome_integration.py tests/test_goal_pace.py tests/test_saved_run_regressions.py -q
 npm test -- --run tests/frontend/top-recommendations.test.js tests/frontend/holdings.test.js tests/frontend/active-watchlist.test.js
 # Full gate before merge
 uv run ruff check . ; uv run ruff format --check .
 uv run python scripts/ci_pytest.py tests/ -q ; npm test

 scripts/ci_pytest.py suppresses the pytest summary count (it os._exits) — also run plain pytest on touched files. Run probes with .venv/Scripts/python tools/probe_*.py against live OpenD (read-only).

 A. Systemic violations
 - Three portfolio-context builders (the cash bug had to be fixed twice); three market clocks; two decision→dict converters; a read-only allowlist tuple that no test enforces; cash-fit/DTE/OTM/exposure checks duplicated across recommendations.py, wheel_decision.py, outcome_service.py.
 - Two overlapping outcome systems (snapshot-diff journal vs broker-fill outcomes) and a legacy per-ticker scanner beside the /api/run shortlist.
 - Complexity that serves itself: ~350 lines of never-called OpenD wrappers, dead dashboard panels that still cost broker calls per run publish, a from_yfinance provenance chain with no producer.
 - Ownerless assumptions: goal-critical retention (365 days), the 300s freshness window applied to closed-market runs, AGENTS.md's "no code-level gap remains open" (contradicted by the findings above).

 B. Deleted / simplified — see Phase 5 and Phase 4 for the delete list; load-bearing items listed under "Do NOT delete". Net: ~1,000+ lines removable against ~1,200 added, with the new code pure and testable.

 C. Systemic impact — Moves forward, not sideways: signals exist for a 69-name watchlist; the wheel cycle, adjusted basis and expired/assigned outcomes become visible; the goal metric excludes deposits and states the gap; ranking decisions get an evidence loop instead of opinion; free Moomoo-native data replaces feeds that fill 0 of 30 rows. No regression in the safety contract.

Housekeeping audit — 2026-09-30

 - Resolved: root `AGENTS.md` now scopes its no-gap statement to S03; preset v8 supplies exit thresholds and the changelog records fee-gated ROTATE behavior.
 - Resolved: this roadmap now lives at the non-ignored path `docs/ROADMAP-2026-10.md`.
 - Resolved: `API.md` now documents `POST /api/settings/watchlist-group`. The outcomes ingest endpoint was already documented at `POST /api/options/analytics/outcomes/ingest`; no duplicate entry was needed.
 - Resolved: `.github/AGENTS.md` now records the CI/local Playwright smoke contract; `run_api.py` uses the product name; `README.md` documents the optional `ALPHA_VANTAGE_API_KEY`.

Verification audit — 2026-09-30

- Resolved the remaining live-session quote blocker: missing/stale timestamps for non-candidate symbols remain diagnostic evidence and no longer demote unrelated picks; every actionable candidate still needs its own fresh, parseable Moomoo quote timestamp while the session is open. Complete-union coverage stays independent.
- The first complete E2E run exposed a fixture-only `/api/watchlist` 500 that dashboard smoke had not asserted. Corrected the fixture manager to return the production route's list/union contract and added a browser assertion for both the endpoint and rendered panel.
- Verification: `uv run pytest tests/ -q` → 1,023 passed, 44 subtests; CI coverage gate → 80.75% (75% required); `npm test` → 115 passed; repo-wide Ruff lint and format checks passed; serial Playwright E2E → 15 passed, 1 live-market-only skip. E2E ran against the temporary fixture server, never the live account.
- Still owner-run: acceptance Sessions 1–3 in `docs/acceptance-runbook.md`. Phase 4 analytics remain gated on updating the owner's OpenD and rerunning the read-only capability probe; O3 still withholds permission to use `diluted_cost` as basis evidence. Keep external data fallback until the OpenD analytics endpoint gate passes.
- Follow-up date audit: expanded the AST guard to catch local-midnight truncation and local weekday calculations. Fixed the missed service, route, weekly-income, and IV-repository clocks; verified with deterministic US/Eastern timestamps against the Brisbane-next-day case.
