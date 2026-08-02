# Moomoo Wheel Migration Ledger

Date: 2026-08-02
Source donor: `Moomoo Signal v2` (preserved at
`Documents/TradingProjectArchive/Moomoo-Signal-v2-safety-20260802`,
branch `preservation/dirty-worktree-20260802`, bundle
`Moomoo-Signal-v2-20260802-113432.bundle`).
Target: `AllYouNeedIsWheel_moomoo` (retained daily wheel app).

Every entry below was ported only after its contract was pinned by a fixture
or parity test in `tests/test_wheel_parity.py` / `tests/test_run_model.py` /
`tests/test_query_only_broker.py` / `tests/test_no_execution_surface.py`.

## Accepted (ported/adapted with tests)

| Capability | Source (v2) | Target | Contract test |
|---|---|---|---|
| Immutable completed run snapshot (run id, timestamps, env, account identity, preset, market state, freshness, coverage, picks, rejections) | `adapter/models.py` `RunMetadata`/`RunSnapshot` | `core/run_model.py` `WheelRunSnapshot` | `test_run_model.py`, `test_wheel_parity.py` |
| Refresh attempts separated from completed runs | `adapter/app.py` refresh lifecycle | `core/run_model.py` `RefreshAttempt` | `test_run_model.py` |
| Tradeable gate: ready + complete coverage + fresh Moomoo quotes only | `RunSnapshot.tradeable` | `WheelRunSnapshot.tradeable` | `test_wheel_parity.py` |
| Persist-before-publish; failed attempts never relabel old data | `adapter/store.py` + `app.py` | `core/wheel_runner.py` refresh() | `TestRunnerFailurePreservesLastSnapshot` |
| Opaque, non-sensitive account identity | `RunMetadata.account_id` | `opaque_account_id()` | `test_run_model.py` |
| Explicit account identity required; never first account | `adapter/opend_client.py` account resolution | `resolve_account()` | `test_run_model.py` |
| Quote age/freshness diagnostics surfaced in UI | v2 operational strip | `run_strip.js` | frontend tests |
| Unknown optional metadata stays `unknown`; never zero/neutral | v2 unknown-data policy | tiered ranking (step 15) | `TestRiskTierRanking` |
| Query-only broker surface enforced structurally | v2 `TrdMarket.NONE` posture (hardened) | `core/broker_protocol.py` + `readonly=False` rejection | `test_query_only_broker.py`, `test_no_execution_surface.py` |
| DB backup/verify-before-touch workflow | `scripts/backup_db.py` | Phase 0 verified backup + restore of `options.db` | recorded in archive manifests |

## Rejected (not ported, with reason)

| Capability | Reason |
|---|---|
| FastAPI/React architecture | Flask/Jinja retained; single runtime, no second stack |
| LLM advisory layer | Out of scope (prune-by-default contract) |
| Directional long options, debit spreads, exit signals | Out of scope; wheel app covers CSP/CC/roll only |
| Spread support | Out of scope |
| Permissive unknown earnings/sector/IV defaults | Replaced by stricter unknown-tier ranking policy |
| REAL + aggressive default posture | Replaced: Balanced preset default, explicit REAL identity requirement |
| TanStack Query / React Aria frontend stack | Out of scope for the Jinja dashboard |
| aiosqlite | Existing SQLite pool retained (serialized access already) |
| Multi-strategy/LLM/catalyst UI surfaces | Removed in step 11 |
| Order-capable SDK examples (`opend-skills`) | Removed from both repos; official docs referenced only |

## Migration rule

No behavior is considered migrated until its parity/contract test passes in
the target repo and the donor copy is untouched (archived, read-only).
