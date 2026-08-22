<!-- As-built 2026-08-02 consolidation: run model, presets, Moomoo-only actionability, structural read-only. See root AGENTS.md + docs/migration-ledger.md. -->

# API DOX

## Purpose

`api/` owns Flask application setup, blueprint registration, HTTP routes, and service orchestration between UI/API callers and the core/db layers.

## Ownership

- `api/__init__.py` owns app factory setup, service registration, request logging, CORS (including `_supports_credentialed_cors` and `_is_trusted_cors_origin` helpers), secret-key resolution (`_resolve_secret_key`), health checks, and blueprint registration.
- `api/routes/` owns request parsing, response shaping, HTTP status handling, and delegation to services.
- `api/services/` owns broker-facing, market-data, portfolio, recommendation, LLM, and signal orchestration.
- `api/routes/portfolio.py::get_portfolio_history` serves persisted per-run portfolio snapshots plus growth-pace math from `core.growth_mode.growth_pace` (target multiple from the active preset); it reads local SQLite only and does not gate on live OpenD.
- `api/routes/roll_pressure.py` enriches each open position with the exit-playbook verdict (`exit_verdict`, `exit_reasons`) and Moomoo entry credit (`avg_cost`) for P&L display.
- `api/services/recommendations.py` attaches `entry_context` (intraday entry-window advice) and applies the portfolio-aware concentration guard (`existing_exposure_contracts`).

## Local Contracts

- Routes must remain thin. Put reusable portfolio, options, risk, scoring, and market-data behavior in services or `core`.
- Preserve the lazy service registry in `api/__init__.py`; avoid import-time service initialization that can reintroduce circular imports or external calls.
- API responses should use consistent `success`/`error` shapes where existing routes already do.
- Validate request input at the route boundary before calling services.
- Do not let optional external providers override Moomoo portfolio/account truth.
- Keep LLM behavior advisory and evidence-gated; it must not become a trading execution path.

## Work Guidance

- Add new blueprints by following the existing `bp = Blueprint(...)` route module pattern and registering them in `create_app`.
- Route modules should import from `api.services`, `core`, or `db` only as needed for their endpoint.
- Service classes should be testable with injected config/data dependencies when practical.
- Keep external API clients rate-limited, cached, and failure-tolerant.

## Verification

- Route changes: run targeted `tests/test_routes_*.py`, relevant feature tests, and `tests/test_api_health.py` when app setup changes.
- Service changes: run the matching `tests/test_*service*.py` or feature-specific tests.
- If blueprint registration or service registry changes, run `pytest tests/test_import_side_effects.py tests/test_api_health.py`.
- If secret-key or CORS configuration changes, run `pytest tests/test_api_config.py`.

## Child DOX Index

- `routes/AGENTS.md` - HTTP route blueprints and request/response contracts.
- `services/AGENTS.md` - Application services, external providers, recommendations, and signal orchestration.

