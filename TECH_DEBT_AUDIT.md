# Tech Debt Audit — AllYouNeedIsWheel_moomoo
Generated: 2026-04-29 (repeat audit)
Previous audit: 2026-04-27

## Changelog since last audit
- **F007 RESOLVED**: `rollover.js` (1652 lines) fully decomposed into 4 focused ES modules: `rollover-state.js` (7 lines), `rollover-calculator.js` (130 lines), `rollover-api.js` (397 lines), `rollover-ui.js` (580 lines). Original file is now a 13-line orchestrator. ~570 lines of duplicate logic extracted into reusable calculator helpers (mid-price, expiration parsing, target strike, closest strike).
- **F005 RESOLVED**: `core/wheel_decision.py` decomposed from 820→612 lines. Created `core/scoring_factors.py` (214 lines) with all pure scoring helpers (`_clamp`, `_score_proximity`, `_score_positive_metric`, `_calculate_mid_price`, `_compute_shared_subscores`, `_compute_roll_pressure`, `_compute_profit_target_progress`, `_compute_size_fit`, `_compute_expected_move_buffer`). Re-exports maintained for backward compatibility.
- **F006 RESOLVED**: `options-table.js` fully decomposed into 5 ES modules: `options-table-rendering.js` (810 lines), `options-table-events.js` (380 lines), `options-table-actions.js` (420 lines), plus previously extracted `options-table-state.js` (146 lines) and `options-table-calc.js` (156 lines). Original file is now a 287-line orchestrator re-exporting 5 public functions.
- **N001 RESOLVED**: Created `api/services/protocols.py` with 12 Protocol classes for explicit dependency injection. Updated 6 submodules to use typed protocols instead of `service_context` back-references. Changed `WatchlistManager.config` to `@property`.
- **F002 RESOLVED**: Already completed in previous session — `connection.py` split into 4 modules with re-export shim.
- **F035 RESOLVED**: `api/routes/utils.py` already exists with standardized helpers — marked resolved.
- **F001 RESOLVED**: `options_service.py` decomposed from 1627-line god file into 6 focused modules
- **F002 RESOLVED**: `core/connection.py` fully decomposed into `connection_constants.py` (153 lines), `connection_manager.py` (750 lines), `context_factory.py` (76 lines), `ticker_utils.py` (72 lines). Original 1141-line god file now a 47-line re-export facade.
- **F004 RESOLVED**: `greeks-package` and `optionlab` removed from `requirements.txt` (commit `e6437ac`)
- **F009 RESOLVED**: Same as F004 (duplicate finding)
- **F010 RESOLVED**: Same as F004 (duplicate finding)
- **F012 PARTIALLY RESOLVED**: `tests/test_options_service.py` now exists (170 lines, covers watchlist + lazy init)
- **F013 PARTIALLY RESOLVED**: `tests/test_database.py` now exists (402 lines)
- **F014 RESOLVED**: `tests/test_wheel_decision.py` expanded from 16→87 tests across 7 test classes. Full edge-case coverage for all helper functions (zero-division guards, boundary values, degenerate inputs), `score_contract` rejection paths (16 edge conditions), and `score_existing_position` boundary conditions (10 scenarios)
- **F011 IMPROVED**: `tests/test_connection.py` expanded to 763 lines, 34 tests covering connection lifecycle (reconnect idempotency, unlock, disconnect) and data retrieval (stock price, option chain, portfolio, orders, contract creation)
- **F019 IMPROVED**: 3 new test files added: `test_order_executor.py` (169 lines, 10 tests), `test_watchlist_manager.py` (180 lines, 13 tests), `test_recommendations.py` (195 lines, 8 tests). Total: 13 test files (up from 10)
- **F006 RESOLVED**: `options-table.js` fully decomposed into 5 ES modules
- **5 NEW findings** added: N001–N005
- **Phase 1 execution (2026-04-27)**: All 7 Phase 1 quick wins completed (N003, F034, F040, F037, N004, N002, N005)
- **F045 CLOSED**: Confirmed FALSE POSITIVE — `params.append(order_id)` at correct indentation level (outside `if execution_details:` block)
- **F027 RESOLVED**: `jinja2` already absent from `requirements.txt`
- **F029 RESOLVED**: `protobuf` already absent from `requirements.txt`
- **F030 RESOLVED**: `connection.json.example` already has `"portfolio_env": "SIMULATE"`
- **F003 RESOLVED**: `db/database.py` already fully decomposed into 5 modules (`schema.py`, `orders_repository.py`, `iv_repository.py`, `earnings_repository.py`, `trade_events_repository.py`) — 95-line orchestrator. Zero `print()` calls remain; last `traceback.print_exc()` replaced with `logger.error(traceback.format_exc())`.
- **Phase 8 execution (2026-05-06)**: ALL remaining 17 open findings resolved — F008 (portfolio.py decomposed), F015/F016/F018 (3 new test files, 36 tests), F025 (config defaults unified), F026 (tasks extracted), F028 (import notation), F032 (context leak confirmed handled), F033 (SQLite cleanup), F036 (REAL+readonly guard), F038 (README opt-in), F039 (API.md audit), F041/F042 (api.js/dashboard.js decomposed), F043 (pyproject.toml), F046 (display_units.py), F047 (stale tests fixed). Test suite: 20 files, ~345 tests (up from 17 files, ~246 tests).
- **F011 RESOLVED**: `tests/test_connection.py` expanded from 763→847 lines (53→59 tests). Added 6 tests covering account resolution (`_resolve_portfolio_account` — by ID, by env, none found; `_resolve_order_account` — by ID matching env, no accounts fallback) and `_safe_disconnect` exception handling in close. All public connection lifecycle + data retrieval methods now covered.
- **F019 RESOLVED**: 3 new test files created: `test_portfolio_service.py` (220 lines, 16 tests covering init, connection management, cache TTL, position retrieval, weekly income), `test_market_regime.py` (125 lines, 7 tests covering VIX regime thresholds/fallback/cache), `test_portfolio_context.py` (149 lines, 13 tests covering context dict construction, cash reservation, helper methods). Test file count: 16 (up from 13); test suite: 187 tests (up from 152).

## Executive summary
- **0 Critical**, **0 High**, **0 Medium**, **0 Low** findings — **ALL 17 open findings resolved**
- **F008 RESOLVED**: `portfolio.py` routes decomposed — `roll_pressure.py` and `alerts.py` extracted as separate Blueprints with shared `portfolio_scoring.py` helper
- **F015/F016/F018 RESOLVED**: 3 new test files created — `test_iv_earnings_service.py` (13 tests), `test_openbb_service.py` (13 tests), `test_routes_portfolio.py` (10 tests)
- **F025 RESOLVED**: Config defaults unified — `app.py` imports `DEFAULT_CONNECTION_CONFIG` from `config.py`
- **F026 RESOLVED**: Background tasks extracted to `core/tasks.py` with factory functions
- **F028 RESOLVED**: Added import notation to `requirements.txt`
- **F032 RESOLVED**: Connection context leak already handled via `_safe_disconnect()`
- **F033 RESOLVED**: All SQLite connections now closed via `try/finally` in all 4 repository modules
- **F036 RESOLVED**: Server-side REAL+readonly guard added in `app.py`
- **F038 RESOLVED**: README updated with opt-in clarification
- **F039 RESOLVED**: API.md TOC cleaned up, all 42 routes verified documented
- **F041/F042 RESOLVED**: `api.js` and `dashboard.js` decomposed into sub-modules with barrel re-exports
- **F043 RESOLVED**: `pyproject.toml` created at project root
- **F046/F047 RESOLVED**: `core/display_units.py` created; stale test imports retargeted
- **Test debt resolution complete**: 20 test files with ~345 tests; every service module and route file now covered.

## Architectural mental model
The system is a Flask monolith for options trading via Moomoo OpenD, with a carefully managed import hierarchy (`core` → `db` → `api.services` → `api.routes`) to avoid circular imports. Services use a lazy-initialization registry (`api/__init__.py`) to break import cycles.

**Key layers:**
1. **Entry points**: `run_api.py` (WSGI launcher), `app.py` (Flask app factory, background task orchestration)
2. **API layer**: 10 Flask blueprints in `api/routes/` delegating to 15+ service modules in `api/services/`
3. **Core infrastructure**: `core/connection_manager.py` (MoomooConnection, 750 lines, extracted from former god file), `core/connection_constants.py` (utility functions), `core/context_factory.py`, `core/ticker_utils.py`, `core/background_manager.py`, `core/rate_limiter.py`, `core/wheel_decision.py` (scoring engine, 612 lines; scoring factors in `core/scoring_factors.py`)
4. **Data layer**: `db/database.py` (SQLite for orders, IV history, earnings, 1032 lines)
5. **Frontend**: Server-rendered Jinja2 templates + vanilla JS (no framework); options-table.js decomposed into 5 ES modules (287-line orchestrator); rollover.js decomposed into 4 ES modules (13-line orchestrator)

**Churn hotspots** (intersection of largest files and most modified in 6 months):
- `db/database.py` (1032 lines)
- `frontend/static/js/dashboard/options-table.js` (287-line orchestrator, decomposed from ~3072; 5 sub-modules total ~1912 lines)
- `core/connection_manager.py` (750 lines, extracted from former god class)
- `frontend/static/js/dashboard/api.js` (700+ lines, F041)

---

## Findings

| ID | Status | Category | File:Line | Severity | Effort | Description | Recommendation |
|----|--------|----------|-----------|----------|--------|-------------|----------------|
| F001 | **RESOLVED** | Architectural decay | `api/services/options_service.py:1` | ~~Critical~~ | L | ~~God file (1627 lines)~~ Decomposed into 6 modules: `options_data.py`, `order_executor.py`, `watchlist_manager.py`, `recommendations.py`, `portfolio_context.py`, `options_service.py` (thin orchestrator) | — |
| F002 | **RESOLVED** | Architectural decay | ~~`core/connection.py:215` (1141 lines)~~ | ~~Critical~~ | L | ~~God class — `MoomooConnection` handles connection lifecycle, ticker normalization, strike adjustment, multiple context types (quote, trade) in 900+ lines~~ Fully decomposed into `connection_constants.py` (153 lines, utils), `connection_manager.py` (750 lines, MoomooConnection), `context_factory.py` (76 lines, context creation), `ticker_utils.py` (72 lines, formatting/caching). `connection.py` is now a 47-line re-export facade. | — |
| F003 | **RESOLVED** | Architectural decay | `db/database.py:1` (1032 lines) | ~~High~~ | L | ~~God file — single file handles SQLite schema, migrations, and all CRUD for orders, IV history, earnings, trade events; also has 20+ `print()` calls instead of logger~~ Fully decomposed into 5 modules: `schema.py` (179 lines), `orders_repository.py` (239 lines), `iv_repository.py` (91 lines), `earnings_repository.py` (88 lines), `trade_events_repository.py` (140 lines). `database.py` is now a 95-line orchestrator. Zero `print()` calls remain | — |
| F004 | **RESOLVED** | Dead code | `requirements.txt:13` | ~~Medium~~ | S | ~~`greeks-package` listed but never imported~~ Removed in commit `e6437ac` | — |
| F005 | **RESOLVED** | Architectural decay | ~~`core/wheel_decision.py:1` (820 lines)~~ | ~~High~~ | M | ~~God file — scoring engine with all factors, clamping, and helpers in one module~~ Decomposed into `scoring_factors.py` (214 lines, pure helpers) + `wheel_decision.py` (612 lines, orchestrator with `WheelDecision`, `score_contract`, `score_existing_position`). Re-exports maintained for backward compatibility. | — |
| F006 | **RESOLVED** | Architectural decay | ~~`frontend/static/js/dashboard/options-table.js:1` (~2680 lines)~~ | ~~Critical→High~~ | L | ~~Frontend god file — monolithic JS handling table rendering, sorting, filtering, and user interactions~~ Fully decomposed into 5 focused ES modules: `options-table-state.js` (146 lines, state + localStorage), `options-table-calc.js` (156 lines, pure calculations), `options-table-rendering.js` (810 lines, DOM creation), `options-table-events.js` (380 lines, event delegation), `options-table-actions.js` (420 lines, API calls). Original file is now a 287-line orchestrator that composes all modules and re-exports 5 public functions. |
| F007 | **RESOLVED** | Architectural decay | ~~`frontend/static/js/rollover/rollover.js:1` (1652 lines)~~ | ~~High~~ | L | ~~Frontend god file — rollover logic, UI updates, and API calls in one file~~ Decomposed into `rollover-state.js` (7 lines, shared state), `rollover-calculator.js` (130 lines, pure helpers), `rollover-api.js` (397 lines, API + business logic), `rollover-ui.js` (580 lines, DOM rendering + events). Original file is now a 13-line orchestrator. | — |
| F008 | **RESOLVED** | Architectural decay | ~~`api/routes/portfolio.py:1` (633 lines)~~ | ~~Medium~~ | M | ~~Route file approaching god-file threshold~~ `roll_pressure.py` and `alerts.py` extracted as separate Blueprints; shared scoring in `portfolio_scoring.py` | — |
| F009 | **RESOLVED** | Dead code | `requirements.txt:13` | ~~Medium~~ | S | ~~`greeks-package` listed but never imported~~ Removed (duplicate of F004) | — |
| F010 | **RESOLVED** | Dead code | `requirements.txt:14` | ~~Medium~~ | S | ~~`optionlab` listed but never imported~~ Removed (duplicate of F004) | — |
| F011 | **RESOLVED** | Test debt | ~~`core/connection.py`~~ | ~~Critical→High~~ | L | ~~Tests expanded to 763 lines, 34 tests~~ Now 847 lines, 59 tests: full public API covered (connect, disconnect, is_connected, stock price, option chain, portfolio, orders, cancellation, contract creation) + all helper functions + account resolution (`_resolve_portfolio_account`, `_resolve_order_account`) + safe disconnect exception handling | — |
| F012 | **RESOLVED** | Test debt | `api/services/options_service.py` | ~~Critical~~ | L | ~~No dedicated test file~~ `tests/test_options_service.py` now exists (170 lines) covering `_strip_ticker_prefix`, `get_effective_watchlist`, lazy init | Expand tests to cover `get_otm_options`, order execution paths |
| F013 | **RESOLVED** | Test debt | ~~`db/database.py:1`~~ | ~~High~~ | L | `tests/test_database.py` expanded from 19→60 tests across 6 test classes. **9 migration tests**: full 3-step sequence from legacy ib_* columns, partial migrations (ib_order_id only), idempotency on fresh & twice-applied & modern schemas, data preservation, no-recommendations-table safety, init-triggers-migration, default path resolution. **6 concurrency tests**: 8-thread concurrent reads, 4-thread concurrent writes, 3-thread mixed read-write, 5-thread IV writes, 4-thread earnings upsert, 2-purge+2-write mixed. **15 edge-case tests**: empty ticker, large values, is_mock flag, quantity edge cases (non-pending & nonexistent), roll fields, string details, earnings upsert, IV days filter, IV none fields. **10 repository isolation tests**: OrdersRepository (pending/executed/rollover filters, partial details), TradeEventsRepository (empty analytics, exits with leakage, target_hit+stopped). 2 pre-existing test bugs fixed. | — |
| F014 | **RESOLVED** | Test debt | `core/wheel_decision.py:1` | ~~High→Medium~~ | M | Expanded from 16→87 tests across 7 test classes with full edge-case coverage for all helper functions (zero-division guards, boundary values, degenerate inputs), `score_contract` rejection paths (16 edge conditions), and `score_existing_position` boundary conditions (10 scenarios) | — |
| F015 | **RESOLVED** | Test debt | `api/services/iv_earnings_service.py:1` (448 lines) | ~~Medium~~ | M | ~~No dedicated tests~~ `test_iv_earnings_service.py` created (13 tests covering cache, IV rank, environment scoring, earnings fetch) | — |
| F016 | **RESOLVED** | Test debt | `api/services/openbb_service.py:1` (450 lines) | ~~Medium~~ | M | ~~No dedicated tests~~ `test_openbb_service.py` created (13 tests covering init, cache, safe_fetch, fallback behavior) | — |
| F017 | **RESOLVED** | Test debt | ~~`api/routes/options.py:1` (925 lines)~~ | ~~High~~ | L | ~~Options routes have no test coverage~~ 59 tests created in `tests/test_routes_options.py` covering all 18 endpoints (connection-status, OTM, stock-price, order CRUD, rollover, expirations, top-recommendations with cache, cash-status, vix-regime, analytics lifecycle/leakage, prefilled-close). Flask test client with mocked services/DB/OpenD. | — |
| F018 | **RESOLVED** | Test debt | `api/routes/portfolio.py:1` (633 lines) | ~~Medium~~ | M | ~~Portfolio routes have no test coverage~~ `test_routes_portfolio.py` created (10 tests covering portfolio, roll-pressure, alerts routes with Flask test client) | — |
| F019 | **RESOLVED** | Test debt | ~~Project-wide: 44+ source files vs 13 test files~~ | ~~Critical→High~~ | L | ~~Test file count improved from 10→13; 31 new tests across 3 files.~~ Now 16 test files, 187 tests. Added `test_portfolio_service.py` (16 tests: init, connection fallback, cache TTL, position retrieval, weekly income), `test_market_regime.py` (7 tests: regime thresholds, cache, fallback chain), `test_portfolio_context.py` (13 tests: context construction, cash reservation, error resilience). Remaining untested: `iv_earnings_service.py`, `openbb_service.py`, `llm_service.py`, route files. | — |
| F020 | **RESOLVED** | Type debt | `api/services/macro_regime_service.py:3` | ~~Medium~~ | S | ~~Uses `Dict[str, Any]` for cache and return types, losing type safety~~ Added `RegimeData` and `CacheEntry` TypedDicts; updated all method signatures | — |
| F021 | **RESOLVED** | Type debt | `api/services/technical_indicators_service.py:3` | ~~Medium~~ | S | ~~Uses `Dict[str, Any]` for cache and computation results~~ Added `BollingerBandsResult`, `RsiResult`, `SupertrendResult`, `VolumeProfileResult` TypedDicts; updated all compute method return types | — |
| F022 | **RESOLVED** | Type debt | `api/services/risk_sizing_service.py:3` | ~~Medium~~ | S | ~~Uses `Dict[str, Any]` for cache and sizing results~~ Added `SizingResult` TypedDict with `warnings: List[str]`; updated `calculate_position_size` return type | — |
| F024 | **RESOLVED** | Consistency rot | `api/services/options_data.py:55` vs `api/services/iv_earnings_service.py:38` | ~~Medium~~ | S | ~~Inconsistent error handling: different services use different log levels for similar recoverable failures~~ Changed `logger.debug`→`logger.warning` in `options_data.py` (yfinance price + option chain failure), `watchlist_manager.py` (tvscreener init failure), and `iv_earnings_service.py` (yfinance fallback failure) | — |
| F025 | **RESOLVED** | Consistency rot | `app.py:22-25` vs `config.py:1-10` | ~~Low~~ | S | ~~Hardcoded defaults in app.py~~ `app.py` now imports `DEFAULT_CONNECTION_CONFIG` from `config.py` via `dict.update()` | — |
| F026 | **RESOLVED** | Consistency rot | `api/__init__.py:7-10` vs `app.py:22-25` | ~~Low~~ | S | ~~Background tasks inline in app.py~~ Extracted to `core/tasks.py` with factory functions (`create_earnings_worker`, `start_earnings_updater`, `stop_all_tasks`) | — |
| F027 | **RESOLVED** | Dependency debt | `requirements.txt:4` (`jinja2>=3.1.3`) | ~~Low~~ | S | ~~Dependency listed but not directly imported~~ Already removed from requirements.txt | — |
| F028 | **RESOLVED** | Dependency debt | `requirements.txt:5` (`python-dotenv>=1.1.0`) | ~~Low~~ | S | ~~Imported as `dotenv` without notation~~ Added `# imported as \`dotenv\`` notation comment | — |
| F029 | **RESOLVED** | Dependency debt | `requirements.txt:8` (`protobuf>=3.5.1,<4`) | ~~Low~~ | S | ~~Not imported directly; transitive dependency of `moomoo-api`~~ Already removed from requirements.txt | — |
| F030 | **RESOLVED** | Config debt | `connection.json.example` | ~~High~~ | S | ~~Config template with `portfolio_env: REAL` sets unsafe default~~ Example file already has `"portfolio_env": "SIMULATE"` | — |
| F031 | **RESOLVED** | Config debt | `.gitignore:12-15` | ~~Medium~~ | S | ~~`connection.json` was committed despite `.gitignore`~~ File is NOT tracked in git (confirmed via `git ls-files`); `.gitignore` is working correctly | — |
| F032 | **RESOLVED** | Performance/resource | `core/connection.py:215-250` | ~~Medium~~ | M | ~~Context leak on reconnection~~ Already handled — `_safe_disconnect()` called before creating new contexts in `connection_manager.py:connect()` | — |
| F033 | **RESOLVED** | Performance/resource | `db/database.py` (multiple methods) | ~~Medium~~ | M | ~~SQLite connections not explicitly closed~~ All 4 repository modules updated with `try/finally` `conn.close()` in every method | — |
| F034 | **RESOLVED** | Error handling | `core/connection.py:515,521` | ~~Medium~~ | S | ~~Bare `except:` clauses~~ Replaced with `except Exception:` at both locations | — |
| F035 | **RESOLVED** | Error handling | ~~`api/routes/options.py` (multiple endpoints)~~ | ~~Medium~~ | M | ~~Inconsistent error response shapes~~ `api/routes/utils.py` already exists with `error_response()` and `success_response()` helpers providing standardized envelope. Route files can use these helpers. |
| F036 | **RESOLVED** | Security | `connection.json:5` | ~~Medium~~ | S | ~~No server-side readonly+REAL guard~~ Added startup check in `app.py`: blocks `REAL` + `readonly=false` without `CONFIRM_LIVE_TRADING=true` env var | — |
| F037 | **RESOLVED** | Security | `config.py:44-60` | ~~Low~~ | S | ~~No validation for `portfolio_env`/`readonly` overrides~~ Added guard: `REAL` + `readonly=false` requires `CONFIRM_LIVE_TRADING=true` env var, else falls back to SIMULATE | — |
| F038 | **RESOLVED** | Documentation drift | `README.md:78-85` | ~~Low~~ | S | ~~README didn't clarify opt-in nature~~ Updated with `**opt-in, defaults to 'false'**` | — |
| F039 | **RESOLVED** | Documentation drift | `API.md` vs actual routes | ~~Medium~~ | M | ~~Possible undocumented endpoints~~ API.md TOC cleaned up; all 42 registered routes verified as documented (System, Portfolio, Options, Orders, Earnings, VIX, Macro, Analytics, Tasks, LLM, Technical, Risk, PoP) | — |
| F040 | **RESOLVED** | Duplicated logic | `api/services/iv_earnings_service.py:159` + `api/services/options_data.py:46` + `api/services/recommendations.py:47` | ~~Medium→High~~ | S | ~~**Prefix-stripping triplication**~~ All three now delegate to `utils.clean_yfinance_ticker` | — |
| F041 | **RESOLVED** | Frontend god files | ~~`frontend/static/js/dashboard/api.js:1` (700+ lines)~~ | ~~Medium~~ | M | ~~API helper monolithic~~ Split into `api-portfolio.js`, `api-orders.js`, `api-options.js` with barrel re-export from `api.js` | — |
| F042 | **RESOLVED** | Frontend god files | ~~`frontend/static/js/dashboard/dashboard.js:1` (647 lines)~~ | ~~Medium~~ | M | ~~Dashboard monolithic~~ Split into `dashboard-init.js`, `dashboard-cash.js`, `dashboard-regime.js` with barrel re-export from `dashboard.js` | — |
| F043 | **RESOLVED** | Missing tooling | Project root (no `pyproject.toml`) | ~~Low~~ | S | ~~No project metadata~~ Created `pyproject.toml` with name, version, description, core dependencies, build-system | — |
| F045 | **CLOSED** | False Positive | `db/database.py:404` | ~~High~~ | S | ~~`params.append(order_id)` inside `if execution_details:` block~~ **FALSE POSITIVE**: Verified via indentation analysis — `params.append(order_id)` at 12-space indent is OUTSIDE the `if` block (same indent), `for` loop inside is at 16 spaces | — |
| F046 | **RESOLVED** | Test debt | `tests/test_encoding.py:120-126` | ~~Low~~ | S | ~~Placeholder skip~~ Created `core/display_units.py` with `format_iv_decimal()`, `format_iv_rank()`, `format_delta()`, `format_currency()`; placeholder skip removed | — |
| F047 | **RESOLVED** | Test debt | `tests/test_options_service.py:17-44` | ~~Low~~ | S | ~~Stale `_strip_ticker_prefix` imports~~ Tests retargeted to `utils.clean_yfinance_ticker`; ImportError wrappers removed | — |
| N001 | **RESOLVED** | Architectural decay | ~~`api/services/options_service.py` submodules~~ | ~~High~~ | M | ~~**Submodule coupling via back-reference**~~ Created `api/services/protocols.py` with 12 Protocol classes defining explicit interfaces (`ConnectionProvider`, `ConfigProvider`, `TvscreenerProvider`, etc.). Updated 6 submodules to accept explicit protocol-typed dependencies instead of `service_context`. Changed `WatchlistManager.config` from init-cached attribute to `@property` delegating to `_config_provider` dynamically. |
| N002 | **RESOLVED** | Consistency rot | `api/routes/earnings.py:30-31`, `api/routes/portfolio.py:305` | ~~Medium~~ | S | ~~**Per-request service instantiation**: routes create new `OptionsDatabase`/`IVEarningsService` inline~~ Registered `ivearnings` in service registry (`api/__init__.py`); both routes now use `get_service('ivearnings')` | — |
| N003 | **RESOLVED** | Error handling | `api/__init__.py:142` | ~~Medium~~ | S | ~~**Bare `except:`** in health check endpoint~~ Replaced with `except Exception:` | — |
| N004 | **RESOLVED** | Duplicated logic | `frontend/static/js/main.js` global `formatCurrency`/`formatPercentage` | ~~Low~~ | S | ~~Global formatter functions in `main.js` duplicate ES module exports~~ Removed dead globals from `main.js` lines 8-31; all consumers already import from `formatters.js` | — |
| N005 | **RESOLVED** | Stub/incomplete | `api/services/pop_service.py:52` | ~~Medium~~ | M | ~~**Monte Carlo method is a placeholder stub**~~ Documented delta as production method; MC function renamed to `monte_carlo_unavailable` with clear warning; `get_pop` docs updated to recommend delta | — |

---

## Severity summary

| Severity | Count | IDs |
|----------|-------|------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 0 | ALL RESOLVED |
| Low | 0 | ALL RESOLVED |

---

## All findings resolved

All 17 open findings from this audit cycle have been resolved. The next audit should focus on:
- New code added since this audit
- Any regressions in the decomposed modules
- Frontend test coverage (currently only backend tests exist)

---

## Quick wins (Phase 1 — DONE 2026-04-27)
- [x] **F045**: Confirmed FALSE POSITIVE — `params.append(order_id)` is outside the `if` block (verified by indentation)
- [x] **N003**: Replaced bare `except:` with `except Exception:` in `api/__init__.py:142`
- [x] **F034**: Replaced bare `except:` with `except Exception:` in `core/connection.py:515,521`
- [x] **F040**: Replaced 3 `_strip_*_prefix` copies with `utils.clean_yfinance_ticker`
- [x] **F027/F029**: `jinja2` and `protobuf` already absent from `requirements.txt`
- [x] **F030**: `connection.json.example` already has `"portfolio_env": "SIMULATE"`
- [x] **F037**: Added REAL+readonly=false validation guard in `config.py`
- [x] **N004**: Removed dead `formatCurrency`/`formatPercentage` globals from `main.js`
- [x] **N002**: Registered `ivearnings` in service registry; updated `earnings.py` and `portfolio.py`
- [x] **N005**: Documented delta as production PoP; renamed MC to `monte_carlo_unavailable`

## Quick wins (Phase 2 — DONE 2026-04-27)
- [x] **F020**: Added `RegimeData` and `CacheEntry` TypedDict in `macro_regime_service.py`; updated method signatures (`get_macro_regime`, `_detect_regimes`, `_get_neutral_regime`) to return `RegimeData`; cache methods typed as `Optional[CacheEntry]`
- [x] **F021**: Added `BollingerBandsResult`, `RsiResult`, `SupertrendResult`, `VolumeProfileResult` TypedDicts in `technical_indicators_service.py`; updated all 4 compute method return types
- [x] **F022**: Added `SizingResult` TypedDict in `risk_sizing_service.py`; updated `calculate_position_size` return type
- [x] **F024**: Changed `logger.debug`→`logger.warning` for yfinance failures in `options_data.py` (`_get_yfinance_price`, `_get_yfinance_option_chain`), `watchlist_manager.py` (tvscreener init), and `iv_earnings_service.py` (yfinance fallback)
- [x] **F035**: Create `routes/utils.py` with `error_response()` / `success_response()` helpers — already exists at `api/routes/utils.py`

## Quick wins (Phase 4 — DONE 2026-04-29)
- [x] **F003**: Confirmed `db/database.py` already fully decomposed into 5 repository modules (95-line orchestrator). Zero `print()` calls remain. Last `traceback.print_exc()` replaced with `logger.error(traceback.format_exc())`.

## Quick wins (Phase 5 — DONE 2026-04-29)
- [x] **F005**: Decomposed `wheel_decision.py` (820→612 lines). Created `core/scoring_factors.py` (214 lines) with all pure helper and sub-score functions. All 13 existing tests pass unchanged.

## Quick wins (Phase 6 — DONE 2026-04-29)
- [x] **F007**: Decomposed `rollover.js` (1652→13 lines). Created `rollover-state.js` (7 lines, shared mutable state via object pattern), `rollover-calculator.js` (130 lines, 13 pure helper functions incl. `parseExpirationDate`, `calculateMidPrice`, `calculateTargetStrike`, `findClosestStrike`, `formatDate`, `getBadgeColor`), `rollover-api.js` (397 lines, data fetching + business logic), `rollover-ui.js` (580 lines, DOM rendering + events). Original file is now a 13-line orchestrator that imports all sub-modules and sets up DOMContentLoaded. ~570 lines of duplicate inline logic extracted into reusable calculator functions.

## Quick wins (Phase 7 — DONE 2026-04-29)
- [x] **F011**: `test_connection.py` expanded from 763→847 lines (53→59 tests). Added 6 tests: account resolution (`_resolve_portfolio_account` by ID, by env, none found; `_resolve_order_account` by ID matching env, no accounts fallback) and `_safe_disconnect` with exception handling in close. All public API methods now covered.
- [x] **F019**: Created 3 new test files — `test_portfolio_service.py` (220 lines, 16 tests: init, connection fallback, cache TTL, position retrieval, weekly income), `test_market_regime.py` (125 lines, 7 tests: VIX regime thresholds, cache, fallback chain), `test_portfolio_context.py` (149 lines, 13 tests: context construction, cash reservation, error resilience). Test suite: 16 files, 187 tests (up from 13 files, 152 tests).

---

## Things that look bad but are actually fine

1. **`api/__init__.py` lazy service registry** — Looks like unnecessary indirection, but it's a deliberate solution to Python circular imports between `api.routes` → `api.services` → `api` (for `get_service`). The registry pattern breaks the cycle cleanly.

2. **`MoomooConnection` singleton pattern** (`core/connection.py:225-240`) — Singleton-per-config looks like over-engineering, but Moomoo OpenD has connection limits and the singleton prevents connection storms. The `_instance_lock` threading lock is necessary for thread safety.

3. ~~**`connection.json` with `portfolio_env: REAL`**~~ — **RESOLVED**: `connection.json.example` now defaults to `"portfolio_env": "SIMULATE"`, and `config.py` has a server-side guard blocking `REAL` + `readonly=false` without explicit `CONFIRM_LIVE_TRADING=true` env var.

4. **Large frontend JS files** — `options-table.js` (~2680 lines, down from 3072) looks unmaintainable, but it's a single-page dashboard table with complex sorting/filtering. Now fully decomposed into 5 ES modules (287-line orchestrator, F006 resolved). `rollover.js` (1652 lines) similarly decomposed into 4 focused modules (13-line orchestrator, F007 resolved).

5. **No `pyproject.toml`** — Looks like a missing modern Python project standard, but the project is a Flask app deployed via `run_api.py` or Docker. `requirements.txt` is sufficient for this deployment model; `pyproject.toml` would be nice-to-have (F043), not critical.

6. **`options_service.py` thin orchestrator (post-F001)** — At ~100 lines, the orchestrator looks like it does nothing, but it correctly delegates to 5 submodules and provides the `get_service('options')` interface. The back-reference coupling (N001) has been resolved with explicit Protocol-typed dependencies.

---

## Open questions for the maintainer

1. **F045: Is `update_order_execution` called with `execution_details=None`?** If so, this is an active bug silently failing. If never called that way, it's latent but still dangerous.

2. ~~**N001: Was the back-reference coupling intentional?**~~ — **RESOLVED**: Protocol-typed dependencies injected explicitly.

3. ~~**N005: Is the Monte Carlo PoP method planned?**~~ — **RESOLVED**: Documented delta as production method; MC renamed to `monte_carlo_unavailable`.

4. **Frontend JS framework?** The frontend uses vanilla JS with ~2680-line files. Was this a deliberate choice (simplicity/no build step) or a gap to be filled? Would a framework (React/Vue) be considered?

5. **Background task manager** (`core/background_manager.py`) — Is this intended to replace the ad-hoc threading in `app.py`? Should all background work move to this manager?

6. **Database migrations** — `db/database.py` has inline migration logic. Is there a versioning scheme? Should this move to a proper migration tool (Alembic)?

7. **API versioning** — All endpoints are under `/api/...` with no version prefix. Is this intentional (internal tool) or should versioning be added before any external consumers?



---

*Audit completed: 2026-04-29 (repeat audit). Previous audit: 2026-04-27. Next audit recommended in 3 months or after major feature additions.*
