# TECH DEBT AUDIT

Date: 2026-06-17

Scope: current repo state, with emphasis on source-of-truth boundaries, security/safety, testability, and maintainability.

## Summary

The repo is functional but carries a few high-value debt items:

- One real command-execution risk in `run_api.py`.
- Insecure default app config in `api/__init__.py`.
- Several architecture boundary breaks between `core`, `api`, and `app.py`.
- Frontend rendering paths that still use `innerHTML` with API-fed content.
- Tooling/test drift: broken e2e dependency, frontend dependency vulnerabilities, and stale docs.
- Repository hygiene debt from tracked generated artifacts.

## Progress

- [x] Remove shell command execution risk in `run_api.py`.
- [x] Harden default app secret and credentialed CORS in `api/__init__.py`.
- [x] Move remaining `app.py` API handlers into `api/routes/` or make `app.py` compose only.
- [x] Remove `core` -> `api` dependency leaks.
- [x] Replace remaining API-fed `innerHTML` rendering paths with escaped DOM updates.
- [x] Fix frontend tooling/test drift: missing Playwright package added; Vitest/Vite/esbuild vulnerabilities accepted for now because `npm audit fix --force` requires a breaking Vitest 4.x upgrade.
- [x] Break frontend circular dependencies reported by madge.
- [ ] Refresh stale docs and remove tracked generated artifacts from history/state.

## Findings

| Severity | Finding | Evidence | Why it matters | Scoped fix |
|---|---|---|---|---|
| High | Shell command execution is built from env vars | `run_api.py:104-110` | `os.system(cmd)` executes a string command. Even if today’s values are usually local ports/workers, this is unnecessary shell risk in a startup path. | Replace with `subprocess.run([...], shell=False)` and validate `PORT`/`WORKERS` as ints before use. |
| High | Default app secret is `dev`, and CORS allows credentialed requests | `api/__init__.py:96-106` | A weak default `SECRET_KEY` plus `supports_credentials=True` raises the blast radius if the app is exposed beyond localhost or copied into a real environment without overrides. | Require an explicit non-default secret outside test/dev, and narrow credentialed CORS to only trusted origins. |
| Medium | Route ownership is split between `app.py` and `api/routes/` | `app.py:177-260`, `api/__init__.py:121-141` | Earnings endpoints live in the entrypoint instead of a route module, which makes imports harder to reason about and weakens the blueprints-as-contracts model. | Move those handlers into `api/routes/earnings.py` or make `app.py` purely compose the factory and blueprints. |
| Medium | Core modules import API services | `core/greeks.py:173-176`, `core/scheduler.py:101-153` | `core` should not depend on Flask/service orchestration. These imports create layering leaks and make pure logic harder to test. | Push the API-dependent calls upward into services, or pass the needed helpers into `core` from the caller. |
| Medium | Frontend still renders API-fed content with `innerHTML` | `frontend/static/js/dashboard/dashboard-cash.js:27-30`, `dashboard-regime.js:16-20`, `top-recommendations.js:908-913`, `utils/state-model.js:16-23`, `:35-40`, `:59-65` | Most values come from API responses, not hardcoded strings. That is enough to justify escaping/sanitizing before HTML injection. | Switch to DOM node creation or escape all interpolated text before assigning `innerHTML`. |
| Medium | Frontend test/tooling contract is incomplete | `package.json:5-12`, `tests/e2e/smoke.spec.js:1-11` | `npm audit` reports 4 vuln paths through `vitest/vite/esbuild`, and `depcheck` reports missing `@playwright/test` for the e2e smoke spec. | Add/install the missing Playwright package and decide whether to accept, pin, or upgrade the Vitest/Vite chain. |
| Medium | Frontend module graph has circular dependencies | `frontend/static/js/dashboard/api.js:1-7`, `api-options.js:5-6`, `api-portfolio.js:5-6`, `options-table-actions.js:1-7`, `options-table-events.js:1-7`, `rollover/rollover-api.js:1-5`, `rollover/rollover-ui.js:1-5` | `madge` found 4 cycles. These make refactors fragile and often hide initialization-order bugs. | Split shared helpers into non-barrel modules and collapse the mutual imports one pair at a time. |
| Low | Docs are stale relative to code and tests | `README.md:52-54`, `README.md:230`, `README.md:283`, `API.md:782`, `API.md:1148`, `API.md:1205`, `tests/README.md:20-29`, `config.py:81` | Public docs still mention missing files and routes, and the OpenD port variable name differs between docs and code. That confuses setup and support. | Refresh README/API/tests docs after code changes, and standardize the env-var name in one place. |
| Low | Tracked generated artifacts are still in history | `.gitignore:27-34`, plus `git ls-tree -r --name-only HEAD` shows `node_modules/` and `options.db-shm` / `options.db-wal` | These files should not live in source control. The staged deletions suggest cleanup is already underway, but the repo history still carries the noise. | Finish removing them from the repo and keep them ignored. |

## Top Fixes

1. Remove `os.system()` from `run_api.py`.
2. Make `SECRET_KEY` and credentialed CORS explicit, not defaulted.
3. Move the remaining `app.py` API handlers into `api/routes/`.
4. Break the `core` -> `api` imports.
5. Replace the remaining `innerHTML` interpolation paths with escaped DOM updates.

## Looks Bad But Is Fine

- `db/sqlite_pool.py:51-71` catches broad exceptions during cleanup. That is defensive resource handling, not a bug by itself.
- `db/schema.py:265-272` drops only a fixed list of retired tables. The f-string looks suspicious, but the table names are static and controlled.
- `frontend/static/js/dashboard/llm-advisor.js:38-51` escapes `&`, `<`, and `>` before rendering formatted HTML, so it is not the same risk as the other `innerHTML` sites.

## Verification Notes

- `npm audit --audit-level=low` reported 4 vulnerabilities via `vitest -> vite -> esbuild`.
- `npx --no-install depcheck` reported missing `@playwright/test` for `tests/e2e/smoke.spec.js`.
- `npx --no-install madge --circular .` reported 4 frontend circular dependencies.

## Open Questions

- Should the app keep the direct `app.py` earnings routes for startup simplicity, or should they be migrated into blueprints now?
- Do we want to treat the Vitest/Vite/esbuild chain as acceptable until a planned upgrade, or pin a remediation target?
- Should the frontend standardize on a tiny escaping helper before any more `innerHTML` work lands?
