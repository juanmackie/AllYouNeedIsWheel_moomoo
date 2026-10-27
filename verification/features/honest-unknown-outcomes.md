# Honest unknown outcomes

User-visible behavior: outcome summaries report unmeasured totals as
`unknown`, never as `$0.00`. The Outcomes headline is "Option-leg net
(realized)" with a note that share results are in Account P&L, and the
net-per-capital-day rate uses the same measured records in its numerator and
its denominator. Signal cards do not color gross yield green: they say the
rank is gross premium, not expected profit.

## Sub-features

- Unmeasured totals shown as `unknown`, not `$0.00`
- Outcomes headline names realized option-leg net
- Signal cards label rank as gross premium, not expected profit

## How to get to it (user POV)

1. Open the dashboard Outcomes and Account P&L areas.
2. Where a total cannot be measured (missing history, failed read), the label
   says `unknown` — and no card is colored as profit without realized basis.

## Driving it with the control

Not yet driven. Planned drive: boot the app via `tests/e2e/fixture_server.py`,
open the Outcomes section (HTML markers from `frontend/`), and assert:
`unknown` appears for an unmeasured total and no gross-yield card carries a
profit color. Add the drive to `verification/run.sh` behind a stage flag once
the selectors are pinned.

## Gotchas

- Must use fixture scenes that leave a total unmeasured; the fixture's
  `persisted-broker` evidence source exists for this.
- Do not weaken the assertion: `unknown` is the contract, not a fallback label.