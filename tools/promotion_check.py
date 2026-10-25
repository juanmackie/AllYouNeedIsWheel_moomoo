"""Read-only promotion-gate report for the walk-forward replay.

Answers the predeclared OOS promotion rule (2026-10-25): an exit policy is
OOS-confirmed when it was chosen by >= 8 resolved walk-forward folds AND its
pooled OOS excess per capital-day exceeds the hold path's pooled OOS excess on
the same instrument.

With --rerun, runs tools/replay_signals.py --no-fetch first (needs the same
APPDATA/TEMP/TMP env overrides as every Windows python invocation). Without it,
reads the existing _tmp/replay/report.json. Manual, query-only; private output
goes to _tmp/replay/.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REPORT = REPO / "_tmp" / "replay" / "report.json"
PROMOTION_FOLDS = 8


def main() -> int:
    if "--rerun" in sys.argv:
        replay = REPO / "tools" / "replay_signals.py"
        proc = subprocess.run(
            [sys.executable, str(replay), "--no-fetch"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        print(proc.stdout[-2000:])
        if proc.returncode != 0:
            print(proc.stderr[-2000:], file=sys.stderr)
            return proc.returncode
    if not REPORT.exists():
        print(f"no report at {REPORT}; run once with --rerun (or replay_signals.py --no-fetch)")
        return 1
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    hold = report["policy_wf"]["hold"]
    print(
        f"hold path:   oos_cohorts={hold['oos_cohorts']} oos_trades={hold['oos_trades']} "
        f"excess/cap-day={hold['excess_pooled']:.6f}"
        if hold.get("excess_pooled") is not None
        else "hold path:   no resolved OOS folds yet"
    )

    for policy in ("R1_stop2", "R1_roll21"):
        cell = report["policy_wf"].get(policy) or {}
        excess = cell.get("excess_pooled")
        print(
            f"{policy:12s} chosen_folds={cell.get('chosen_folds')} oos_cohorts={cell.get('oos_cohorts')} "
            f"oos_trades={cell.get('oos_trades')} "
            f"excess/cap-day={excess:.6f}"
            if excess is not None
            else f"{policy:12s} chosen_folds={cell.get('chosen_folds')} oos_trades={cell.get('oos_trades')} "
            "no resolved OOS folds yet"
        )

    # Wf_policies feed summary (resolved early via trigger bars).
    feed = report.get("wf_policies_debug", {})
    if feed:
        print("wf feed trades:", feed.get("trades"), "| unresolved:", feed.get("unresolved"))

    folds = [f for f in report["policy_wf"].get("folds", []) if f.get("status") == "ok"]
    chosen_resolved = [f for f in folds if f.get("chosen_rule") in ("R1_stop2", "R1_roll21")]
    print(f"resolved folds: {len(folds)}; policy-chosen with resolved test trades: {len(chosen_resolved)}")
    for f in chosen_resolved:
        print("  fold", f["test_cohort"], "chose", f["chosen_rule"])

    hold_excess = hold.get("excess_pooled")
    resolved = sum(
        1
        for f in report["policy_wf"].get("folds", [])
        if f.get("status") == "ok" and f.get("chosen_rule") in ("R1_stop2", "R1_roll21")
    )
    for policy in ("R1_stop2", "R1_roll21"):
        cell = report["policy_wf"].get(policy) or {}
        excess = cell.get("excess_pooled")
        met = (
            cell.get("chosen_folds", 0) >= PROMOTION_FOLDS
            and excess is not None
            and hold_excess is not None
            and excess > hold_excess
        )
        print(
            f"promotion rule ({PROMOTION_FOLDS}+ resolved folds, excess > hold): {policy} -> {'MET' if met else 'not met'} "
            f"({cell.get('chosen_folds', 0)}/{PROMOTION_FOLDS} folds"
            + (f", excess {excess:.6f} vs hold {hold_excess:.6f})" if excess is not None else ", no OOS excess yet)")
        )
    print(
        f"summary: resolved_folds={resolved} (of {len(folds)} ok folds); rule status above; rerun weekly after new sessions."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
