"""Offline checkpoint for comparing frozen and alternative recommendation ranks."""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import date

MIN_SETTLED = 100
MIN_EXPIRY_WEEKS = 8
MIN_TICKER_WEEK_CLUSTERS = 40


def rank_checkpoint(records, alternative_score_key, bootstrap_samples=2000, seed=11):
    """Compare top-three return/day by DTE bucket, bootstrapping expiry weeks.

    ``records`` must be candidate archive rows joined to settled shadow outcomes.
    Unsettled or missing outcomes are ignored. The alternative key is an
    explicit candidate field, so this helper cannot silently change rank_key.
    """
    by_bucket = defaultdict(list)
    for record in records or []:
        if not isinstance(record, dict) or record.get("status") != "settled":
            continue
        value = _finite(record.get("capital_return_per_day"))
        if value is None or not record.get("run_id"):
            continue
        candidate = {**record, "_outcome": value}
        candidate["_expiry_week"] = _expiry_week(record.get("expiration"))
        candidate["_alt_score"] = _finite(record.get(alternative_score_key))
        bucket = str(record.get("dte_bucket") or _dte_bucket(record.get("dte")))
        candidate["dte_bucket"] = bucket
        by_bucket[bucket].append(candidate)

    result = {}
    for bucket, candidates in sorted(by_bucket.items()):
        weeks = sorted({row["_expiry_week"] for row in candidates if row["_expiry_week"]})
        clusters = {
            (str(row.get("ticker") or "").upper(), row["_expiry_week"])
            for row in candidates
            if row["_expiry_week"] and row.get("ticker")
        }
        paired_diffs = _top3_diffs(candidates, alternative_score_key)
        ready = (
            len(candidates) >= MIN_SETTLED
            and len(weeks) >= MIN_EXPIRY_WEEKS
            and len(clusters) >= MIN_TICKER_WEEK_CLUSTERS
            and bool(paired_diffs)
        )
        low, high = _block_bootstrap_ci(paired_diffs, bootstrap_samples, seed) if paired_diffs else (None, None)
        result[bucket] = {
            "ready": ready,
            "settled_candidates": len(candidates),
            "expiry_weeks": len(weeks),
            "ticker_expiry_week_clusters": len(clusters),
            "runs_compared": len(paired_diffs),
            "frozen_top3_mean_return_per_day": _mean([pair[0] for pair in paired_diffs]),
            "alternative_top3_mean_return_per_day": _mean([pair[1] for pair in paired_diffs]),
            "alternative_minus_frozen": _mean([pair[1] - pair[0] for pair in paired_diffs]),
            "block_bootstrap_95_ci": [low, high],
        }
    return {"ready": bool(result) and all(row["ready"] for row in result.values()), "by_dte_bucket": result}


def _top3_diffs(candidates, score_key):
    by_run = defaultdict(list)
    for row in candidates:
        if row.get("_alt_score") is None:
            continue
        by_run[(str(row.get("run_id")), str(row.get("dte_bucket") or "unknown"))].append(row)
    comparisons = []
    for run_rows in by_run.values():
        frozen = sorted(
            run_rows, key=lambda row: (int(row.get("rank_position") or 10**9), row.get("candidate_id", ""))
        )[:3]
        alternative = sorted(
            run_rows,
            key=lambda row: (-float(row["_alt_score"]), str(row.get("ticker") or ""), row.get("candidate_id", "")),
        )[:3]
        if frozen and alternative:
            comparisons.append(
                (
                    _mean([row["_outcome"] for row in frozen]),
                    _mean([row["_outcome"] for row in alternative]),
                    frozen[0].get("_expiry_week", ""),
                )
            )
    return comparisons


def _block_bootstrap_ci(pairs, samples, seed):
    # Block each run comparison by its expiry week so adjacent candidates from
    # one expiration do not masquerade as independent evidence.
    blocks = defaultdict(list)
    for frozen, alternative, week in pairs:
        if week:
            blocks[week].append(alternative - frozen)
    weeks = sorted(blocks)
    if len(weeks) < 2:
        return None, None
    rng = random.Random(int(seed))
    boot = []
    for _ in range(max(100, int(samples))):
        sampled_weeks = [weeks[rng.randrange(len(weeks))] for _ in weeks]
        draws = [difference for week in sampled_weeks for difference in blocks[week]]
        boot.append(sum(draws) / len(draws))
    boot.sort()
    return boot[int(0.025 * (len(boot) - 1))], boot[int(0.975 * (len(boot) - 1))]


def _expiry_week(value):
    raw = str(value or "").replace("-", "")
    try:
        expiry = date.fromisoformat(f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}")
    except ValueError:
        return ""
    iso = expiry.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _dte_bucket(value):
    try:
        dte = int(value)
    except (TypeError, ValueError):
        return "unknown"
    for edge, label in ((7, "0-7"), (14, "8-14"), (30, "15-30"), (45, "31-45"), (60, "46-60"), (90, "61-90")):
        if dte <= edge:
            return label
    return "90+"


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _mean(values):
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else None
