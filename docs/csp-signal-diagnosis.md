# CSP signal diagnosis — 2026-10-11

The supplied Dashboard markdown and Moomoo log show CSP discovery working,
but limited confidence in the surfaced contracts and several misleading
dashboard fields. This diagnosis uses the exported page and captured log;
it does not establish the outcome of the refresh that was still running.

## What the capture establishes

- The published CSP lane contains three featured contracts and 64 alternatives:
  67 candidates, rather than an empty CSP scan. The featured list allows up to
  two contracts per underlying, so three contracts need not mean three tickers.
- Of those 67 candidates, 14 carry the stronger `qualified` liquidity label and
  53 are `marginal`. Events are unknown for 55, before expiry for four, and not
  applicable for eight. These labels are separate from copy eligibility.
- The current preset is Aggressive while the retained published strategy is
  Balanced. The refresh is at contract discovery; the captured log ends without
  its completion result. Publication is atomic, so old cards remain visible.
- The market is closed and card controls say “Stage ticket.” A staged ticket is
  a manual draft using the last broker quote. Server checks still require a
  complete coverage, broker evidence, and positive capacity. Closed runs normally
  display PLANNING; that label alone does not block staged drafts.
- No covered calls appear. The position book already has short calls against
  three holdings; the remaining recovery comparison finds no qualifying call
  at or above broker basis. Removing share or basis gates would change risk.
- Rejections name real constraints: bid availability, spread, delta band,
  cash fit, and per-underlying exposure. Their displayed count covers rejection
  rows and can include multiple contracts for the same underlying.

## Why high return does not imply high confidence

The documented ranking puts executable return on deployed capital per day
first, then executable bid premium per day. Quality and event labels do not
reorder candidates. A contract with high return can therefore lead the list
while carrying marginal liquidity and an unknown earnings date.

The stronger liquidity label requires spread at most 12%, open interest at
least 500, and volume at least 100. Missing earnings evidence stays unknown;
it cannot be replaced with a reassuring assumption. Increasing the number of
displayed candidates or loosening a preset cannot manufacture broker evidence.

## Defects corrected

1. The immutable snapshot dropped the recommendation result's broker-derived
   CSP cash, buying power, and reserved put collateral. Cards consequently
   lacked cash-after-trade calculations and the cash header lacked values.
   New snapshots preserve all three fields. Older snapshots retain unavailable
   values, and the UI distinguishes missing data from a measured zero.
2. Capital recovery included short option positions as negative-share stocks.
   Explicit non-stock positions are now excluded.
3. Preset labels did not distinguish current settings from the published
   snapshot. The dashboard now labels both, including during refresh and
   failed attempts, and updates the current label after a preset change.
4. The state/action table incorrectly said only ready runs could stage.
   It now matches the session, coverage, and evidence gates: complete closed
   planning runs may stage after OpenD confirmation; quota-limited or persisted
   fallback planning runs remain review-only.

Focused regressions reproduced the cash, recovery, and label defects before
the changes. Date-sensitive tests also exposed Brisbane-versus-New-York fixture
drift; fixed market clocks make their DTE boundaries deterministic.

## Follow-up functional sweep

The subsequent query-only OpenD sweep established a separate startup defect:
SQLite retained Aggressive, but the newly constructed recommendation engine
used the configured Balanced default. A completed refresh therefore still
published Balanced. Startup now loads a valid persisted preset, with the
configured preset as the fallback for missing, invalid, or unreadable settings.
Replay tests exercise all three v7 profiles through scanning, snapshot
publication, and copy validation.

After explicitly selecting Aggressive, the live scan covered all 67 watchlist
symbols and published 72 put candidates: 15 qualified and 57 marginal. The top
contract passed the server's staged-copy check during the closed market. New
snapshots also contained the broker-derived cash fields. This verifies current
broker discovery and closed-market staging; it does not establish live-market
copy behavior or trade execution. See `functional-sweep.md` for the final sweep
results and `acceptance-runbook.md` for the market-open acceptance checks.
