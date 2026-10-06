"""Moomoo-calibrated Monte Carlo NAV projection — pure, broker-free math.

Inputs are persisted portfolio snapshots (Moomoo/OpenD truth captured at each
completed run) plus optional raw option-fill rows for trade corroboration.
No network, no DB, no live broker calls: the route layer supplies the rows.

Method (deliberately simple so it stays auditable):
- keep only finite, positive NAV snapshots; the ``net_liquidation <= 0`` rows
  that a failed broker read can persist are excluded and counted;
- collapse intraday bursts to the last snapshot per calendar day so ten
  same-minute refreshes do not overweight one afternoon;
- fit a geometric Brownian motion to the daily series with irregular
  observation gaps: per-interval log returns ``r_i = ln(nav_t / nav_{t-1})``
  over ``dt_i`` days give ``mu = sum(r) / sum(dt)`` and
  ``sigma^2 = mean((r_i - mu*dt_i)^2 / dt_i)`` (MLE for GBM);
- simulate forward one-day log increments ``N(mu, sigma^2)`` with a
  deterministic seed (same inputs always give the same bands).

What this is NOT: it does not separate deposits/withdrawals from trading
profit (same caveat as ``growth_pace`` — this is NAV change, not strategy
return), it does not model assignment/early-exercise jumps, and with only a
handful of independent daily moves the volatility estimate is noisy. Every
payload therefore carries its sample size, calibration window, seed, and
warnings so the panel can label the fan chart honestly.
"""

from __future__ import annotations

import math
import random
from datetime import datetime

# Minimum independent evidence before a fan chart is drawn: at least this many
# daily observations spanning at least this many days. Below it the route
# returns ``insufficient`` with the reason instead of a fabricated trajectory.
MIN_DAILY_POINTS = 4
MIN_TOTAL_DAYS = 7.0

# Deterministic default seed: same snapshot history always yields the same
# bands (no resampling noise masquerading as new information). Callers may
# pass an explicit seed to explore resampling variability.
DEFAULT_SEED = 20260822

# Cap on chart points so a 5-year horizon does not ship thousands of floats.
MAX_BAND_POINTS = 180


def _parse_ts(value) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def daily_series(history: list[dict]) -> tuple[list[tuple[datetime, float]], dict]:
    """Collapse snapshots to one (timestamp, NAV) per calendar day.

    Keeps the LAST snapshot of each calendar day (bursts within minutes carry
    no new market information). Returns (points_sorted, diagnostics).
    """
    excluded_nonpositive = 0
    excluded_unparseable = 0
    raw_usable = 0
    per_day: dict[str, tuple[datetime, float]] = {}
    for snap in history or []:
        if not isinstance(snap, dict):
            continue
        try:
            nav = float(snap.get("net_liquidation", 0) or 0)
        except (TypeError, ValueError):
            excluded_unparseable += 1
            continue
        if not math.isfinite(nav) or nav <= 0:
            excluded_nonpositive += 1
            continue
        ts = _parse_ts(snap.get("captured_at", ""))
        if ts is None:
            excluded_unparseable += 1
            continue
        raw_usable += 1
        day_key = ts.date().isoformat()
        if day_key not in per_day or ts >= per_day[day_key][0]:
            per_day[day_key] = (ts, nav)
    points = sorted(per_day.values(), key=lambda item: item[0])
    diagnostics = {
        "raw_usable": raw_usable,
        "daily_points": len(points),
        "burst_collapsed": max(0, raw_usable - len(points)),
        "excluded_nonpositive": excluded_nonpositive,
        "excluded_unparseable": excluded_unparseable,
    }
    return points, diagnostics


def estimate_gbm(points: list[tuple[datetime, float]]) -> dict:
    """Fit GBM drift/vol to daily points with exact inter-observation gaps."""
    intervals = []
    for (t0, n0), (t1, n1) in zip(points[:-1], points[1:]):
        dt = (t1 - t0).total_seconds() / 86_400.0
        if dt <= 0 or n0 <= 0 or n1 <= 0:
            continue
        intervals.append((math.log(n1 / n0), dt))
    total_days = sum(dt for _, dt in intervals)
    total_log = sum(r for r, _ in intervals)
    mu = total_log / total_days if total_days > 0 else 0.0
    if intervals:
        var = sum(((r - mu * dt) ** 2) / dt for r, dt in intervals) / len(intervals)
        sigma = math.sqrt(max(var, 0.0))
    else:
        sigma = 0.0
    if not math.isfinite(mu):
        mu = 0.0
    if not math.isfinite(sigma):
        sigma = 0.0
    annualized_median = math.exp(mu * 365.25) - 1.0 if math.isfinite(mu * 365.25) else 0.0
    try:
        annualized_median = float(annualized_median)
    except (OverflowError, ValueError):
        annualized_median = 0.0
    if not math.isfinite(annualized_median):
        annualized_median = 0.0
    return {
        "mu_log_daily": mu,
        "sigma_daily": sigma,
        "annualized_vol": sigma * math.sqrt(365.25),
        "annualized_median_growth": annualized_median,
        "n_intervals": len(intervals),
        "total_days": total_days,
        "total_log_return": total_log,
    }


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return sorted_values[int(rank)]
    frac = rank - low
    return sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac


def project_nav(
    history: list[dict],
    target_multiple: float = 5.0,
    horizon_days: int = 1460,
    n_paths: int = 2000,
    seed: int = DEFAULT_SEED,
) -> dict:
    """Monte Carlo NAV fan chart calibrated on persisted Moomoo snapshots."""
    try:
        horizon_days = int(horizon_days)
    except (TypeError, ValueError):
        raise ValueError("horizon_days must be an integer")
    try:
        n_paths = int(n_paths)
    except (TypeError, ValueError):
        raise ValueError("n_paths must be an integer")
    if not 1 <= horizon_days <= 1825:
        raise ValueError("horizon_days must be within 1..1825")
    if not 100 <= n_paths <= 5000:
        raise ValueError("n_paths must be within 100..5000")
    try:
        target_multiple = float(target_multiple)
    except (TypeError, ValueError):
        target_multiple = 5.0
    if not math.isfinite(target_multiple) or target_multiple <= 1.0:
        target_multiple = 5.0

    points, diag = daily_series(history)
    if not points:
        return {
            "status": "no_data",
            "reason": "No usable portfolio snapshots (all rows missing, zero, or unparseable).",
            "diagnostics": diag,
        }
    baseline_nav = points[0][1]
    current_nav = points[-1][1]
    target_nav = baseline_nav * target_multiple
    elapsed_days = (points[-1][0] - points[0][0]).total_seconds() / 86_400.0
    elapsed_days = max(elapsed_days, 0.0)

    if len(points) < MIN_DAILY_POINTS or elapsed_days < MIN_TOTAL_DAYS:
        return {
            "status": "insufficient",
            "reason": (
                f"Only {len(points)} independent daily observations over "
                f"{elapsed_days:.1f} days — need at least {MIN_DAILY_POINTS} daily "
                f"points spanning {MIN_TOTAL_DAYS:.0f} days before drawing a trajectory."
            ),
            "baseline_nav": round(baseline_nav, 2),
            "current_nav": round(current_nav, 2),
            "target_nav": round(target_nav, 2),
            "target_multiple": target_multiple,
            "daily_points": len(points),
            "elapsed_days": round(elapsed_days, 2),
            "diagnostics": diag,
        }

    calib = estimate_gbm(points)
    mu = calib["mu_log_daily"]
    sigma = calib["sigma_daily"]
    reached = current_nav >= target_nav

    stride = max(1, horizon_days // MAX_BAND_POINTS)
    checkpoints = list(range(0, horizon_days + 1, stride))
    if checkpoints[-1] != horizon_days:
        checkpoints.append(horizon_days)

    rng = random.Random(seed)
    checkpoint_values: dict[int, list[float]] = {day: [] for day in checkpoints}
    checkpoint_set = set(checkpoints)
    first_hit_days: list[int] = []
    ever_hit = 0
    finals: list[float] = []

    for _ in range(n_paths):
        nav = current_nav
        log_nav = math.log(nav)
        hit_day = None
        if reached:
            hit_day = 0
        else:
            pass
        if 0 in checkpoint_set:
            checkpoint_values[0].append(nav)
        for day in range(1, horizon_days + 1):
            log_nav += rng.gauss(mu, sigma) if sigma > 0 else mu
            nav = math.exp(log_nav)
            if not math.isfinite(nav) or nav <= 0:
                nav = 0.0
                log_nav = float("-inf")
            if hit_day is None and nav >= target_nav:
                hit_day = day
            if day in checkpoint_set:
                checkpoint_values[day].append(nav)
            if nav <= 0.0:
                # Absorbed at zero: pad remaining checkpoints with zero.
                for future in checkpoints:
                    if future > day:
                        checkpoint_values[future].append(0.0)
                break
        finals.append(checkpoint_values[horizon_days][-1] if checkpoint_values[horizon_days] else 0.0)
        if hit_day is not None:
            ever_hit += 1
            first_hit_days.append(hit_day)

    bands = []
    for day in checkpoints:
        vals = sorted(checkpoint_values[day])
        bands.append(
            {
                "day": day,
                "p10": round(_percentile(vals, 10), 2),
                "p50": round(_percentile(vals, 50), 2),
                "p90": round(_percentile(vals, 90), 2),
            }
        )
    finals_sorted = sorted(finals)
    prob_at_end = sum(1 for v in finals if v >= target_nav) / len(finals) if finals else 0.0
    prob_ever = ever_hit / n_paths if n_paths else 0.0
    first_hit_days_sorted = sorted(first_hit_days)
    median_eta = _percentile(first_hit_days_sorted, 50) if first_hit_days_sorted else None

    warnings = []
    if diag["excluded_nonpositive"]:
        warnings.append(
            f"Excluded {diag['excluded_nonpositive']} zero/negative-NAV snapshot(s) from a failed broker read."
        )
    if diag["burst_collapsed"]:
        warnings.append(f"Collapsed {diag['burst_collapsed']} intraday burst snapshot(s) to one point per day.")
    if calib["n_intervals"] < 6:
        warnings.append(f"Only {calib['n_intervals']} independent NAV moves — volatility is indicative, not measured.")
    warnings.append("NAV change includes market moves and any deposits/withdrawals, not strategy return alone.")

    return {
        "status": "reached" if reached else "ok",
        "method": "gbm_bootstrap_gaussian",
        "seed": seed,
        "baseline_nav": round(baseline_nav, 2),
        "current_nav": round(current_nav, 2),
        "target_nav": round(target_nav, 2),
        "target_multiple": target_multiple,
        "reached": reached,
        "daily_points": len(points),
        "elapsed_days": round(elapsed_days, 2),
        "first_captured_at": points[0][0].isoformat(),
        "last_captured_at": points[-1][0].isoformat(),
        "calibration": {
            "mu_log_daily": round(mu, 8),
            "sigma_daily": round(sigma, 8),
            "annualized_median_growth": round(calib["annualized_median_growth"], 6),
            "annualized_vol": round(calib["annualized_vol"], 6),
            "n_intervals": calib["n_intervals"],
            "total_days": round(calib["total_days"], 2),
        },
        "horizon_days": horizon_days,
        "n_paths": n_paths,
        "bands": bands,
        "final": {
            "p10": round(_percentile(finals_sorted, 10), 2),
            "p50": round(_percentile(finals_sorted, 50), 2),
            "p90": round(_percentile(finals_sorted, 90), 2),
            "mean": round(sum(finals) / len(finals), 2) if finals else 0.0,
        },
        "prob_target_at_horizon": round(prob_at_end, 4),
        "prob_target_ever": round(prob_ever, 4),
        "median_eta_days": None if median_eta is None else round(float(median_eta), 1),
        "hit_count": ever_hit,
        "warnings": warnings,
        "diagnostics": diag,
    }


def summarize_option_fills(fills: list[dict] | None) -> dict:
    """Honest trade corroboration from raw broker fill rows (option-leg only).

    Totals only — per-trade win/loss attribution with assignment/share legs is
    the outcome endpoint's job (``/api/options/analytics/outcomes``). Rows
    with a zero price are broker assignment/expiry movements, not premium, and
    are counted separately rather than booked as profit.
    """
    rows = [f for f in (fills or []) if isinstance(f, dict)]
    opt = [f for f in rows if str(f.get("security_type", "") or "").upper() == "OPT"]
    sell_gross = 0.0
    buy_gross = 0.0
    fees_known_total = 0.0
    unknown_fee_count = 0
    zero_price_count = 0
    identities = set()
    for fill in opt:
        try:
            qty = float(fill.get("qty", 0) or 0)
        except (TypeError, ValueError):
            qty = 0.0
        try:
            price = float(fill.get("price", 0) or 0)
        except (TypeError, ValueError):
            price = 0.0
        side = str(fill.get("side", "") or "").upper()
        identities.add(
            (
                str(fill.get("ticker", "") or "").upper(),
                str(fill.get("expiration", "") or ""),
                str(fill.get("option_type", "") or "").upper(),
                str(fill.get("strike", "") or ""),
            )
        )
        if price == 0:
            zero_price_count += 1
            continue
        gross = price * 100.0 * qty
        if side == "SELL":
            sell_gross += gross
        elif side == "BUY":
            buy_gross += gross
        fee = fill.get("fees", None)
        if fee is None:
            unknown_fee_count += 1
        else:
            try:
                fees_known_total += float(fee)
            except (TypeError, ValueError):
                unknown_fee_count += 1
    return {
        "n_option_fills": len(opt),
        "n_contract_identities": len(identities),
        "gross_sell_premium": round(sell_gross, 2),
        "gross_buyback_cost": round(buy_gross, 2),
        "fees_known_total": round(fees_known_total, 2),
        "unknown_fee_count": unknown_fee_count,
        "zero_price_movement_count": zero_price_count,
        "scope": "option_leg",
        "note": (
            "Option-leg cash only; assignment deliveries, share legs, and zero-price "
            "broker movements are excluded. See /api/options/analytics/outcomes for "
            "attributed per-trade P&L with unknowns called out."
        ),
    }
