# Architecture contract

This document makes the architecture executable. The machine-readable source is
`docs/architecture/capabilities.json`; `scripts/architecture.py` checks it in CI
and generates the capability map below.

The goal is agent locality: an unfamiliar agent can name the capability, load a
small module set, change one bounded area, and run a narrow verification loop.

## Operating rules

1. **Capability first, then slice.** Each runtime module belongs to one business
   capability in the manifest. A capability may contain several vertical slices
   (for example `wheel-scan` owns scan evidence, scoring, ranking, publication,
   and copy eligibility). Put new behavior in the owning capability before
   creating a new package or a generic `utils` module.
2. **Dependency direction is enforced.** `core` is the pure decision layer and
   never imports `api` or `db`. `db` never imports `api`. `api.services` never
   imports the `api` package root or `api.routes`. Persistence and composition
   enter `core` as injected callables or values.
3. **Consumer-defined interfaces.** A service receives collaborators as
   arguments or factory-injected providers. Do not resolve the service registry
   from inside a service, and do not import private symbols across package
   boundaries.
4. **Complexity is ratcheted.** New modules are limited to 900 lines and new
   functions to 150 lines. Existing larger units are recorded in the manifest
   baseline; they may shrink but may not grow. When a baselined unit falls under
   the cap, remove its baseline entry.
5. **One owner per runtime module.** A new module without a capability owner
   fails CI. A test path named by a capability must exist.
6. **Minimum diff by default.** Do not move working code only to match a diagram.
   Move a slice when a change already needs the seam, or when a boundary check
   fails. Keep public routes, persisted data, and broker behavior unchanged.

## Change loop

1. Find the capability in the map below (or search the manifest by module path).
2. Read only that capability's owned modules, entry points, and focused tests.
3. Make the smallest change that satisfies the behavior.
4. Run the capability's `Verify` command. If a shared module changed, run
   `impact` to expand the test set.
5. If a runtime module was added, removed, or moved, update the manifest and run
   `docs`.
6. Run `check` before committing.

## Commands

```bash
# Fast structural gate (also runs in CI)
uv run python scripts/architecture.py check

# Map the current working-tree changes to capabilities and focused tests
uv run python scripts/architecture.py impact

# Map a branch or commit range (CI uses HEAD^1)
uv run python scripts/architecture.py impact --base HEAD^1

# Regenerate the capability section below after a manifest change
uv run python scripts/architecture.py docs

# Broaden only when the impact report says a shared contract moved
uv run python scripts/ci_pytest.py tests/ -q
npm test
```

## Adding, moving, or retiring a module

- **Add:** put the file in the owning capability directory, add its path to
  `owns`, add at least one focused test to `tests`, run `docs`, then `check`.
- **Move:** update `owns`, imports, and owning DOX files in the same change. Do
  not leave a compatibility shim unless a real external consumer exists.
- **Retire:** remove the file, its `owns` entry, dead tests, and any baseline
  entry. `check` fails on stale baseline or manifest paths, which keeps cleanup
  part of the change.

## Known hotspots

These units are above the current budgets and are baselined so they cannot grow.
Treat them as extraction candidates when a real change touches them; do not
rewrite them for their own sake.

- `api/services/recommendations.py` — scan orchestration, evidence collection,
  ranking, deployment, and ledger writes share one engine class.
- `core/connection_manager.py` — query-only broker adapter with discovery,
  quotes, fills, and account identity in one class.
- `api/services/iv_earnings_service.py` and `api/services/outcome_service.py` —
  provider enrichment and outcome aggregation respectively.
- `core/wheel_decision.py` — per-contract scoring with a large decision function.
- `api/__init__.py::create_app`, `api/routes/run.py::evaluate_copy_check`,
  `db/schema.py::migrate_database` — long composition, copy-validation, and
  migration functions.

## Frontend

Browser modules keep their own ownership map in `frontend/static/js/AGENTS.md`.
`impact` maps a changed `frontend/static/js/**/<name>.js` to
`tests/frontend/<name>.test.js` when that file exists, and otherwise asks for the
full `npm test` run. Template and CSS changes also ask for the full frontend
suite.

<!-- capability-map:start -->
## Capability map

Generated from `docs/architecture/capabilities.json` by `scripts/architecture.py docs`.
Do not edit this section by hand.

### `runtime-and-config`

Entry points, Flask app factory, service registry, environment/config, logging, and loopback trust boundary.

- **Owned modules:** `app.py`, `run_api.py`, `config.py`, `api/__init__.py`, `api/services/config.py`, `core/context_factory.py`, `core/logging_config.py`
- **Entry points:** `python app.py`, `python run_api.py`, `GET /health`, `GET /api/system/opend-status`
- **Focused tests:** `tests/test_app_startup.py`, `tests/test_api_health.py`, `tests/test_api_config.py`, `tests/test_config_env.py`, `tests/test_import_side_effects.py`, `tests/test_service_registry.py`, `tests/test_loopback_trust_boundary.py`, `tests/test_logging_config.py`
- **Verify:** `uv run python scripts/ci_pytest.py tests/test_app_startup.py tests/test_api_health.py tests/test_api_config.py tests/test_config_env.py tests/test_import_side_effects.py tests/test_service_registry.py tests/test_loopback_trust_boundary.py tests/test_logging_config.py -q`

### `broker-connectivity`

Read-only OpenD connection lifecycle, broker protocol guard, rate limits, and quote/chain caching.

- **Owned modules:** `core/broker_protocol.py`, `core/connection_manager.py`, `core/connection_constants.py`, `core/rate_limiter.py`, `core/quote_cache.py`
- **Entry points:** `core.connection_manager.MoomooConnection`, `core.broker_protocol.QUERY_SDK_MEMBERS`
- **Focused tests:** `tests/test_connection.py`, `tests/test_query_only_broker.py`, `tests/test_no_execution_surface.py`, `tests/test_rate_limiter.py`, `tests/test_import_side_effects.py`
- **Verify:** `uv run python scripts/ci_pytest.py tests/test_connection.py tests/test_query_only_broker.py tests/test_no_execution_surface.py tests/test_rate_limiter.py tests/test_import_side_effects.py -q`

### `shared-kernel`

Pure shared helpers for market time, ticker normalization, numbers, and provider-safe symbols. No domain decisions.

- **Owned modules:** `core/__init__.py`, `core/utils.py`, `core/ticker_utils.py`, `api/services/utils.py`
- **Entry points:** `core.utils`, `core.ticker_utils`, `api.services.utils`
- **Focused tests:** `tests/test_utils.py`, `tests/test_ticker_utils.py`, `tests/test_entry_window.py`
- **Verify:** `uv run python scripts/ci_pytest.py tests/test_utils.py tests/test_ticker_utils.py tests/test_entry_window.py -q`

### `persistence`

SQLite schema, migrations, connection pooling, and account-scoped repositories.

- **Owned modules:** `db/__init__.py`, `db/database.py`, `db/schema.py`, `db/sqlite_pool.py`, `db/earnings_repository.py`, `db/fills_repository.py`, `db/iv_repository.py`, `db/option_chain_repository.py`, `db/portfolio_snapshots_repository.py`, `db/trade_events_repository.py`, `db/scan_ledger_repository.py`
- **Entry points:** `db.database.OptionsDatabase`, `db.schema.migrate_database`, `db.sqlite_pool.pooled_connection`
- **Focused tests:** `tests/test_database.py`, `tests/test_fills_repository.py`, `tests/test_portfolio_snapshots.py`, `tests/test_scan_ledger.py`
- **Verify:** `uv run python scripts/ci_pytest.py tests/test_database.py tests/test_fills_repository.py tests/test_portfolio_snapshots.py tests/test_scan_ledger.py -q`

### `wheel-scan`

Watchlist-union scan, broker evidence gates, contract scoring, capital-return ranking, immutable run snapshots, and copy eligibility.

- **Owned modules:** `core/wheel_runner.py`, `core/run_model.py`, `core/wheel_decision.py`, `core/scoring_factors.py`, `core/sizing.py`, `core/greeks.py`, `core/scan_ledger.py`, `api/services/recommendations.py`, `api/services/recommendation_ranking.py`, `api/services/options_data.py`, `api/services/options_service.py`, `api/routes/run.py`
- **Entry points:** `POST /api/run/refresh`, `GET /api/run`, `GET /api/run/copy-check`, `api.services.recommendations.RecommendationEngine`, `core.wheel_runner.WheelRunner`
- **Focused tests:** `tests/test_recommendations.py`, `tests/test_score_regression.py`, `tests/test_run_model.py`, `tests/test_saved_run_regressions.py`, `tests/test_routes_run.py`, `tests/test_full_watchlist_discovery.py`, `tests/test_options_data.py`, `tests/test_options_service.py`, `tests/test_wheel_decision.py`, `tests/test_wheel_parity.py`, `tests/test_sizing.py`, `tests/test_greeks.py`, `tests/test_scan_ledger.py`, `tests/test_raw_scan_replay.py`
- **Verify:** `uv run python scripts/ci_pytest.py tests/test_recommendations.py tests/test_score_regression.py tests/test_run_model.py tests/test_saved_run_regressions.py tests/test_routes_run.py tests/test_full_watchlist_discovery.py tests/test_options_data.py tests/test_options_service.py tests/test_wheel_decision.py tests/test_wheel_parity.py tests/test_sizing.py tests/test_greeks.py tests/test_scan_ledger.py tests/test_raw_scan_replay.py -q`

### `portfolio-and-growth`

Portfolio context, holdings basis/campaigns, position monitor, exit playbook, growth pace, Monte Carlo projection, and next actions.

- **Owned modules:** `core/portfolio_snapshot.py`, `core/position_diff.py`, `core/position_scorer.py`, `core/exit_playbook.py`, `core/growth_mode.py`, `core/monte_carlo.py`, `core/action_plan.py`, `api/services/portfolio_service.py`, `api/services/portfolio_context.py`, `api/services/portfolio_scoring.py`, `api/services/roll_diagnostics.py`, `api/routes/portfolio.py`, `api/routes/roll_pressure.py`, `api/routes/alerts.py`
- **Entry points:** `GET /api/portfolio`, `GET /api/portfolio/history`, `GET /api/portfolio/projection`, `GET /api/alerts`, `GET /api/roll-pressure`
- **Focused tests:** `tests/test_portfolio_service.py`, `tests/test_portfolio_context.py`, `tests/test_portfolio_snapshots.py`, `tests/test_position_diff.py`, `tests/test_position_scorer.py`, `tests/test_exit_playbook.py`, `tests/test_growth_mode.py`, `tests/test_growth_pace.py`, `tests/test_monte_carlo.py`, `tests/test_action_plan.py`, `tests/test_routes_portfolio.py`, `tests/test_routes_alerts.py`, `tests/test_projection_route.py`
- **Verify:** `uv run python scripts/ci_pytest.py tests/test_portfolio_service.py tests/test_portfolio_context.py tests/test_portfolio_snapshots.py tests/test_position_diff.py tests/test_position_scorer.py tests/test_exit_playbook.py tests/test_growth_mode.py tests/test_growth_pace.py tests/test_monte_carlo.py tests/test_action_plan.py tests/test_routes_portfolio.py tests/test_routes_alerts.py tests/test_projection_route.py -q`

### `watchlist`

Moomoo watchlist discovery, merged scan universe, and active-watchlist API state.

- **Owned modules:** `api/services/watchlist_manager.py`, `api/routes/watchlist.py`
- **Entry points:** `GET /api/watchlist`, `api.services.watchlist_manager.WatchlistManager`
- **Focused tests:** `tests/test_watchlist_manager.py`
- **Verify:** `uv run python scripts/ci_pytest.py tests/test_watchlist_manager.py -q`

### `earnings-and-iv`

Earnings calendar enrichment, ex-dividend context, IV rank/percentile, and optional external providers.

- **Owned modules:** `api/services/iv_earnings_service.py`, `api/services/alpha_vantage_provider.py`, `api/routes/earnings.py`
- **Entry points:** `GET /api/earnings`, `api.services.iv_earnings_service.IVEarningsService`
- **Focused tests:** `tests/test_iv_earnings_service.py`, `tests/test_routes_earnings.py`
- **Verify:** `uv run python scripts/ci_pytest.py tests/test_iv_earnings_service.py tests/test_routes_earnings.py -q`

### `options-and-tickets`

Options HTTP surface, manual close/roll ticket revalidation, and taken-link state.

- **Owned modules:** `api/services/ticket_validation.py`, `api/services/taken_links.py`, `api/routes/options.py`
- **Entry points:** `GET /api/options`, `GET /api/options/analytics/outcomes`, `POST /api/options/analytics/outcomes/ingest`
- **Focused tests:** `tests/test_ticket_validation.py`, `tests/test_taken_links.py`, `tests/test_routes_options.py`
- **Verify:** `uv run python scripts/ci_pytest.py tests/test_ticket_validation.py tests/test_taken_links.py tests/test_routes_options.py -q`

### `fills-and-outcomes`

Query-only broker fill ingestion, reconciled campaign basis, and broker-verified outcome attribution.

- **Owned modules:** `api/services/fills_service.py`, `api/services/outcome_service.py`, `api/services/campaign_service.py`, `core/outcome_attribution.py`, `core/wheel_campaign.py`
- **Entry points:** `api.services.fills_service.refresh_fill_evidence`, `api.services.outcome_service.OutcomeService`, `core.outcome_attribution`
- **Focused tests:** `tests/test_outcome_attribution.py`, `tests/test_outcome_service.py`, `tests/test_outcome_integration.py`, `tests/test_campaign_service.py`, `tests/test_campaign_fills_service.py`, `tests/test_fills_repository.py`
- **Verify:** `uv run python scripts/ci_pytest.py tests/test_outcome_attribution.py tests/test_outcome_service.py tests/test_outcome_integration.py tests/test_campaign_service.py tests/test_campaign_fills_service.py tests/test_fills_repository.py -q`

### `settings-and-ledger`

Versioned wheel presets, settings persistence, and the read-only scan ledger API.

- **Owned modules:** `core/presets.py`, `api/routes/settings.py`, `api/routes/ledger.py`
- **Entry points:** `GET /api/settings`, `POST /api/settings`, `GET /api/ledger/scans`, `GET /api/ledger/scans/stats`
- **Focused tests:** `tests/test_presets.py`, `tests/test_routes_settings.py`, `tests/test_scan_ledger.py`
- **Verify:** `uv run python scripts/ci_pytest.py tests/test_presets.py tests/test_routes_settings.py tests/test_scan_ledger.py -q`

### `source-policy-and-http`

Shared HTTP response shapes and source-policy metadata for broker-authoritative versus research payloads.

- **Owned modules:** `api/routes/source_policy.py`, `api/routes/utils.py`
- **Entry points:** `api.routes.utils`, `api.routes.source_policy`
- **Focused tests:** `tests/test_response_consistency.py`
- **Verify:** `uv run python scripts/ci_pytest.py tests/test_response_consistency.py -q`

<!-- capability-map:end -->
