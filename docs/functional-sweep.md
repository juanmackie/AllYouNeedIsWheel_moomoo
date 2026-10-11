# Functional sweep — 2026-10-11

The sweep used three GPT-6 Luna agents at high reasoning effort, synthetic
regressions, browser checks, and query-only reads from the configured local
OpenD account. It preserved the existing worktree. No orders or owner-confirmed
trade links were created, modified, or cancelled.

## Live broker results

| Published v7 preset | Watchlist coverage | CSP candidates | Qualified liquidity |
| --- | --- | --- | --- |
| Conservative | 67/67 | 60 | 13 |
| Balanced | 67/67 | 67 | 14 |
| Aggressive | 67/67 | 72 | 15 |

Each refresh completed successfully, retained the broker-derived capital
fields, respected its DTE window, and reported recommended quantities no larger
than its capped maximum. Aggressive was restored as both the saved selection and
the published strategy. A fresh Aggressive scan after restarting the application
also covered 67/67 without reselecting the preset.

The Aggressive top candidate passed `GET /api/run/copy-check` with a matching
contract, `staged` mode, and no blocker reasons. The US market was closed, so
these were last-session broker quotes and the published status was `planning`.
This does not establish current US-open quotes or execution. Market-open
acceptance remains in `acceptance-runbook.md`.

Portfolio, positions, weekly income, cash status, watchlist, roll pressure,
portfolio history, projection, outcome analytics, and earnings status returned
successful responses from the running application. No covered-call picks were
present; share encumbrance and broker-basis gates remain enforced.

## Defects reproduced and corrected

- **Saved Aggressive ignored at startup:** the settings retained Aggressive,
  but a new recommendation engine used the configured Balanced default. It now
  hydrates valid persisted settings, with a configured fallback for missing,
  invalid, or unreadable values. All three preset replay paths are tested.
- **Preset controls disappeared after switching:** the POST response omitted
  the preset catalog, and the selector replaced its whole state with that
  response. It now retains the catalog. Removing a separate settings request
  from run polling also prevents an old response overwriting a new selection.
- **Misleading CSP maximum:** a candidate could show two recommended contracts
  and four maximum even though the exposure cap allowed only two. Maximum
  capacity now respects net cash, the preset cash budget, and remaining
  per-underlying exposure. Buying power never enlarges CSP cash capacity.
- **Weekly-income null error:** a valid result containing `error: null` produced
  HTTP 500. Only a truthy service error now triggers the failure response.
- **Refresh attempt identity race:** POST could return the previous attempt
  before its worker persisted the new one. Acceptance now queues the new attempt
  synchronously and passes that identity to the worker.
- **Mobile overflow:** 67 watchlist tags expanded the page to thousands of
  pixels at a 320px viewport. The missing flex-wrap utility and section link
  wrapping are fixed. Live page width now equals the viewport at 320px and
  1440px with the exploration sections open or closed.
- **Unvalidated roll clipboard drafts:** a HOLD row with no eligible target
  copied a placeholder sell leg. Roll controls now display review-only with a
  visible reason; their existing endpoint cannot revalidate a draft against
  the current published run at copy time. Verdicts and targets remain visible.
- **Unnamed pressure meters:** roll-pressure bars now carry meaningful accessible
  names. The live expanded-dashboard axe scan reported zero violations
  (two checks require manual review); browser JavaScript errors were absent.

The sweep also corrected test-fixture watchlist/earnings API shapes and an
import-side-effect test that left duplicate core module objects behind. The
latter broke deterministic market-clock patches later in the full suite.

## Verification

- `uv run python scripts/ci_pytest.py tests/ -q`: full Python suite passed,
  exit code 0, including the structural no-execution and loopback checks.
- `npm test`: 138 passed across 11 files.
- `npm run test:e2e`: 22 passed, one real market-open test skipped.
- `uv run ruff check .`, `uv run ruff format --check .`, and
  `git diff --check`: passed.
- Final application restart and refresh: saved Aggressive v7, 67/67 coverage,
  72 CSP candidates, accepted refresh identity preserved through completion,
  and the current-run broker copy check passed in staged mode.

The live-market copy test remains conditional on an open US market. Fixture
journeys verify live/staged copying, stale evidence, partial coverage, fallback
sources, broker disconnect recovery, and unchanged clipboard contents when
copying is blocked.

Private account identifiers, holdings, balances, and raw broker logs are omitted
from this report. Local screenshots and tool output stay in ignored `_tmp`.
