#!/usr/bin/env python
"""Walk-forward, out-of-sample replay of past cash-secured-put (CSP) signals.

Reads published run snapshots and option fills from options.db (read-only), prices
each selection rule with hold-to-expiry settlement, calibrated fees, and entry
slippage, and compares the rules with stock and SPY baselines. Unresolved trades
are excluded and counted with a reason; they are never booked as zero. The verdict
follows the predeclared rule in `verdict`.

Run:  .venv/Scripts/python.exe tools/replay_signals.py [--dry-run | --no-fetch]
      Add --yf-check for a diagnostic price comparison and --force to ignore the
      history quota. Outputs: _tmp/replay/report.json and _tmp/replay/report.md.
Query-only: the broker is asked for daily candles and the history quota only.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import statistics
import sys
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.services.recommendation_ranking import candidate_field, csp_tail_risk_gate, rank_key  # noqa: E402
from config import Config  # noqa: E402
from core.scoring_factors import parse_broker_timestamp  # noqa: E402
from core.utils import safe_float  # noqa: E402

NY = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
SLIPPAGE_LEVELS = (0.0, 0.25, 0.5)
BASE_SLIPPAGE = 0.25
CASH_RATES = (0.0, 0.04)
BASE_RATE = 0.04
FALLBACK_OPTION_FEE = 0.65
FALLBACK_STOCK_FEE = 1.00
CORP_ACTION_TOLERANCE = 0.05
MAX_STALE_DAYS = 4
MAX_DELTA = 0.20
TOP_N = 3
MIN_TRAIN_COHORTS = 2
MIN_OOS_COHORTS = 8
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 7
YF_TOLERANCE = 0.005
FILL_WINDOW_SESSIONS = 3
ENTRY_WINDOW_BEFORE = timedelta(days=5)
SPY_WINDOW_BEFORE = timedelta(days=10)
RULE_ORDER = ("R0_published", "R1_current", "R2_no_tail_gate", "R3_low_delta", "R4_model_ev", "POOL", "POOL_ALL")
WF_RULES = ("R1_current", "R2_no_tail_gate", "R3_low_delta", "R4_model_ev")
LIMITATIONS = (
    "Hold to expiry only: no early exits, rolls, or stop logic.",
    "Quotes are last-session quotes captured while the market was closed, not live fills.",
    "Entry credit is modeled as bid minus slippage times the spread; the slippage grid is 0, 0.25, 0.5.",
    "Covered calls are excluded; only cash-secured puts are replayed.",
    "Small sample: few cohorts, so confidence intervals are wide and tiny_n flags thin cohort counts.",
    "Owner selection bias is not modeled: the owner chose which signals to act on.",
    "Fees are calibrated from the owner's fills; a fallback is used only when no fills exist.",
    "Walk-forward training uses base-case trades only; purged by expiry before each test entry.",
)


@dataclass(frozen=True)
class Contract:
    """A put quote from a snapshot, normalized for replay."""

    ticker: str
    expiration: date
    strike: float
    bid: float
    ask: float
    dte: int
    delta: float | None
    expected_value: float | None
    raw: dict

    @property
    def key(self) -> tuple:
        return (self.ticker, self.expiration, round(self.strike, 4))


@dataclass(frozen=True)
class Cohort:
    """The last snapshot published in one entry session."""

    idx: int
    entry: date
    snapshot_id: str
    published_at: datetime
    decision_source: str
    signals: list
    candidates: list
    pool: list


class Bar(NamedTuple):
    open: float
    close: float
    adj_close: float | None


@dataclass(frozen=True)
class PriceSeries:
    dates: tuple
    bars: dict


@dataclass(frozen=True)
class Settlement:
    entry: date
    expiry: date
    days: int
    stock_open: float
    stock_exit: float
    spy_open: float
    spy_exit: float


@dataclass(frozen=True)
class Trade:
    """One resolved contract at one entry session. `pnl` maps slippage to option P&L."""

    cohort: int
    ticker: str
    expiration: date
    entry: date
    strike: float
    capital: float
    days: int
    pnl: dict
    assigned: bool
    stock_pnl: float
    spy_pnl: float


class Leg(NamedTuple):
    """One series observation (rule or baseline) used by the metrics."""

    cohort: int
    ticker: str
    expiration: date
    pnl: float
    capital: float
    days: int
    assigned: bool | None


# ---------------------------------------------------------------- contracts and selection


def _parse_expiration(value) -> date | None:
    text = str(value or "").strip()
    try:
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").date()
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def to_contract(raw: dict) -> Contract | None:
    """Normalize a candidate or published signal. Returns None without a valid expiry."""
    expiration = _parse_expiration(raw.get("expiration"))
    if expiration is None:
        return None
    dte = safe_float(candidate_field(raw, "dte"), 0.0) or 0.0
    return Contract(
        ticker=str(raw.get("ticker") or "").upper(),
        expiration=expiration,
        strike=safe_float(raw.get("strike"), 0.0) or 0.0,
        bid=safe_float(candidate_field(raw, "bid"), 0.0) or 0.0,
        ask=safe_float(candidate_field(raw, "ask"), 0.0) or 0.0,
        dte=int(dte),
        delta=safe_float(candidate_field(raw, "delta"), None),
        expected_value=safe_float(candidate_field(raw, "expected_value"), None),
        raw=raw,
    )


def dedupe(raws: list) -> list:
    """Keep the first raw candidate per (ticker, expiration, strike)."""
    seen: set = set()
    kept = []
    for raw in raws:
        contract = to_contract(raw)
        if contract is None or contract.key in seen:
            continue
        seen.add(contract.key)
        kept.append(raw)
    return kept


def _is_pool_candidate(raw: dict) -> bool:
    if str(raw.get("option_type") or "").upper() != "PUT":
        return False
    if candidate_field(raw, "held_position", False):
        return False
    return not (candidate_field(raw, "hard_blockers") or [])


def pool_contracts(raws: list) -> list[Contract]:
    """Open-market candidate pool: puts, not held, no hard blockers, valid quote."""
    pool = []
    for raw in raws:
        contract = to_contract(raw) if _is_pool_candidate(raw) else None
        if contract and contract.strike > 0 and contract.bid > 0 and contract.ask >= contract.bid:
            pool.append(contract)
    return pool


def top_distinct(contracts: list[Contract], n: int = TOP_N) -> list[Contract]:
    """First n contracts with distinct tickers, in the given order."""
    picks: list[Contract] = []
    seen: set = set()
    for contract in contracts:
        if contract.ticker in seen:
            continue
        seen.add(contract.ticker)
        picks.append(contract)
        if len(picks) == n:
            break
    return picks


def _is_csp_signal(signal: dict) -> bool:
    if str(signal.get("signal_type") or "").lower() == "csp":
        return True
    return str(signal.get("option_type") or "").upper() == "PUT" and not signal.get("held_position")


def select_published(signals: list, pool: list[Contract]) -> tuple[list[Contract], int]:
    """R0: the published CSP signals. Quotes come from the pool, else the signal's own bid and ask."""
    by_key = {contract.key: contract for contract in pool}
    picks: list[Contract] = []
    dropped = 0
    for signal in signals:
        if not _is_csp_signal(signal):
            continue
        contract = to_contract(signal)
        if contract is None:
            dropped += 1
        elif contract.key in by_key:
            picks.append(by_key[contract.key])
        elif contract.strike > 0 and contract.bid > 0 and contract.ask >= contract.bid:
            picks.append(contract)
        else:
            dropped += 1
    return picks, dropped


def select_rules(pool: list[Contract], leveraged) -> dict[str, list[Contract]]:
    """Frozen selection rules R1..R4, the gated POOL baseline, and the ungated POOL_ALL baseline."""
    gated = [c for c in pool if csp_tail_risk_gate(c.raw, leveraged) is None]
    ranked_gated = sorted(gated, key=lambda c: rank_key(c.raw))
    ranked_all = sorted(pool, key=lambda c: rank_key(c.raw))
    low_delta = [c for c in ranked_gated if c.delta is not None and abs(c.delta) <= MAX_DELTA]
    with_ev = [c for c in gated if c.expected_value is not None]
    by_ev = sorted(
        with_ev,
        key=lambda c: (-c.expected_value / (c.strike * 100 * max(c.dte, 1)), rank_key(c.raw)),
    )
    return {
        "R1_current": top_distinct(ranked_gated),
        "R2_no_tail_gate": top_distinct(ranked_all),
        "R3_low_delta": top_distinct(low_delta),
        "R4_model_ev": top_distinct(by_ev),
        "POOL": ranked_gated,
        "POOL_ALL": ranked_all,
    }


# ---------------------------------------------------------------- timing and cohorts


def _quote_time(raw: dict):
    return raw.get("quote_update_time") or (raw.get("wheel_decision") or {}).get("quote_update_time")


def decision_time(raws: list, published_at: datetime) -> tuple[datetime, str]:
    """Broker quote time (US Eastern) when present, else the publish time in Eastern."""
    stamps = [t for t in (parse_broker_timestamp(_quote_time(r)) for r in raws) if t is not None]
    if stamps:
        return max(stamps), "quote"
    return published_at.astimezone(NY), "published"


def entry_session(decision: datetime, source: str, trading_dates: list[date]) -> date | None:
    """First trading date after the decision session. A pre-open publish on a trading day counts as that day."""
    day = decision.date()
    if source == "published" and decision.time() < MARKET_OPEN:
        pos = bisect_left(trading_dates, day)
        if pos < len(trading_dates) and trading_dates[pos] == day:
            return day
    pos = bisect_right(trading_dates, day)
    return trading_dates[pos] if pos < len(trading_dates) else None


def build_cohorts(snapshots: list[dict], trading_dates: list[date]) -> tuple[list[Cohort], dict]:
    """Group snapshots by entry session. The last published snapshot of each session is the cohort."""
    latest: dict[date, tuple[dict, str]] = {}
    stats = {"empty": 0, "unplaced": 0}
    for snap in sorted(snapshots, key=lambda s: s["published_at"]):
        if not snap["candidates"] and not snap["signals"]:
            stats["empty"] += 1
            continue
        decision, source = decision_time(snap["candidates"], snap["published_at"])
        session = entry_session(decision, source, trading_dates)
        if session is None:
            stats["unplaced"] += 1
            continue
        latest[session] = (snap, source)
    cohorts = []
    for idx, session in enumerate(sorted(latest)):
        snap, source = latest[session]
        raws = dedupe(snap["candidates"])
        cohorts.append(
            Cohort(
                idx=idx,
                entry=session,
                snapshot_id=snap["snapshot_id"],
                published_at=snap["published_at"],
                decision_source=source,
                signals=list(snap["signals"]),
                candidates=raws,
                pool=pool_contracts(raws),
            )
        )
    return cohorts, stats


# ---------------------------------------------------------------- fees and prices


def calibrate_fees(fill_rows: list[dict]) -> dict:
    """Option fee per contract and stock sale fee from the owner's fills. Null fees are ignored."""
    opt_fees = opt_qty = 0.0
    opt_samples = 0
    stock_fees: list[float] = []
    for row in fill_rows:
        fee = row.get("fees")
        if fee is None:
            continue
        kind = str(row.get("security_type") or "").upper()
        side = str(row.get("side") or "").upper()
        qty = float(row.get("qty") or 0)
        price = float(row.get("price") or 0)
        if kind == "OPT" and qty > 0 and price > 0:
            opt_fees += float(fee)
            opt_qty += qty
            opt_samples += 1
        elif kind == "STK" and side == "SELL":
            stock_fees.append(float(fee))
    option_ok = opt_qty > 0
    stock_ok = bool(stock_fees)
    return {
        "option_fee_per_contract": opt_fees / opt_qty if option_ok else FALLBACK_OPTION_FEE,
        "stock_sale_fee": statistics.fmean(stock_fees) if stock_ok else FALLBACK_STOCK_FEE,
        "option_fill_samples": opt_samples,
        "stock_sell_samples": len(stock_fees),
        "option_fee_calibrated": option_ok,
        "stock_fee_calibrated": stock_ok,
        "calibrated": option_ok and stock_ok,
    }


def build_series(rows: list[dict]) -> PriceSeries:
    """Daily bars keyed by date. Each row needs date, open, close, and optional adj_close."""
    bars = {}
    for row in rows:
        day = date.fromisoformat(str(row["date"])[:10])
        adj = row.get("adj_close")
        bars[day] = Bar(float(row["open"]), float(row["close"]), None if adj is None else float(adj))
    return PriceSeries(tuple(sorted(bars)), bars)


def _exit_bar(series: PriceSeries, expiry: date):
    """Last bar on or before expiry, or None when it is more than MAX_STALE_DAYS old."""
    pos = bisect_right(series.dates, expiry) - 1
    if pos < 0:
        return None
    day = series.dates[pos]
    if (expiry - day).days > MAX_STALE_DAYS:
        return None
    return series.bars[day]


def _corporate_action(entry_bar: Bar, exit_bar: Bar) -> bool:
    """Flag a split or dividend: the unadjusted/adjusted ratio moves by more than the tolerance."""
    if entry_bar.adj_close is None or exit_bar.adj_close is None:
        return False
    if min(entry_bar.close, entry_bar.adj_close, exit_bar.close, exit_bar.adj_close) <= 0:
        return True
    ratio_entry = entry_bar.close / entry_bar.adj_close
    ratio_exit = exit_bar.close / exit_bar.adj_close
    return abs(ratio_exit / ratio_entry - 1) > CORP_ACTION_TOLERANCE


def settle_contract(contract: Contract, stock, spy, entry: date, last_date: date) -> Settlement | str:
    """Settlement inputs for a contract, or the reason it is unresolved."""
    if contract.expiration <= entry:
        return "expires_on_or_before_entry"
    if contract.expiration > last_date:
        return "not_expired"
    if stock is None or spy is None:
        return "no_price"
    stock_entry, spy_entry = stock.bars.get(entry), spy.bars.get(entry)
    stock_exit, spy_exit = _exit_bar(stock, contract.expiration), _exit_bar(spy, contract.expiration)
    if any(bar is None for bar in (stock_entry, spy_entry, stock_exit, spy_exit)):
        return "no_price"
    if _corporate_action(stock_entry, stock_exit) or _corporate_action(spy_entry, spy_exit):
        return "corporate_action"
    return Settlement(
        entry=entry,
        expiry=contract.expiration,
        days=max(1, (contract.expiration - entry).days),
        stock_open=stock_entry.open,
        stock_exit=stock_exit.close,
        spy_open=spy_entry.open,
        spy_exit=spy_exit.close,
    )


def make_trade(cohort: int, contract: Contract, settlement: Settlement, fees: dict) -> Trade:
    """Hold-to-expiry P&L at each slippage level, plus the stock and SPY baselines on the same capital."""
    capital = 100.0 * contract.strike
    option_fee = fees["option_fee_per_contract"]
    stock_fee = fees["stock_sale_fee"]
    s_exit = settlement.stock_exit
    assigned = s_exit < contract.strike
    intrinsic = 100.0 * max(0.0, contract.strike - s_exit)
    pnl = {}
    for slippage in SLIPPAGE_LEVELS:
        credit = contract.bid - slippage * (contract.ask - contract.bid)
        pnl[slippage] = 100.0 * credit - option_fee - intrinsic - (stock_fee if assigned else 0.0)
    stock_pnl = capital / settlement.stock_open * s_exit - capital - stock_fee
    spy_pnl = capital / settlement.spy_open * settlement.spy_exit - capital - stock_fee
    return Trade(
        cohort=cohort,
        ticker=contract.ticker,
        expiration=contract.expiration,
        entry=settlement.entry,
        strike=contract.strike,
        capital=capital,
        days=settlement.days,
        pnl=pnl,
        assigned=assigned,
        stock_pnl=stock_pnl,
        spy_pnl=spy_pnl,
    )


def cohort_trades(idx: int, entry: date, selections: dict, prices: dict, fees: dict, last_date: date):
    """Settle every selected contract in one cohort. Returns (trades, unresolved counts) by rule."""
    trades: dict[str, list[Trade]] = {rule: [] for rule in selections}
    unresolved: dict[str, Counter] = {rule: Counter() for rule in selections}
    for rule, contracts in selections.items():
        for contract in contracts:
            result = settle_contract(contract, prices.get(contract.ticker), prices.get("SPY"), entry, last_date)
            if isinstance(result, str):
                unresolved[rule][result] += 1
            else:
                trades[rule].append(make_trade(idx, contract, result, fees))
    return trades, unresolved


def replay(cohorts: list[Cohort], prices: dict, fees: dict, leveraged, last_date: date) -> dict:
    """Run every rule over every cohort. Trades and unresolved counts are kept per rule."""
    trades: dict[str, list[Trade]] = {rule: [] for rule in RULE_ORDER}
    unresolved: dict[str, Counter] = {rule: Counter() for rule in RULE_ORDER}
    cohort_rows = []
    r0_dropped = 0
    for cohort in cohorts:
        published, dropped = select_published(cohort.signals, cohort.pool)
        r0_dropped += dropped
        selections = {"R0_published": published, **select_rules(cohort.pool, leveraged)}
        got, missed = cohort_trades(cohort.idx, cohort.entry, selections, prices, fees, last_date)
        for rule in selections:
            trades[rule].extend(got[rule])
            unresolved[rule].update(missed[rule])
        cohort_rows.append(
            {
                "cohort": cohort.idx,
                "entry": cohort.entry.isoformat(),
                "snapshot_id": cohort.snapshot_id,
                "decision_source": cohort.decision_source,
                "candidates": len(cohort.candidates),
                "pool": len(cohort.pool),
                "selected": {rule: len(contracts) for rule, contracts in selections.items()},
            }
        )
    return {"trades": trades, "unresolved": unresolved, "cohorts": cohort_rows, "r0_dropped": r0_dropped}


# ---------------------------------------------------------------- metrics


def legs_for(trades: list[Trade], kind: str = "rule", slippage: float = BASE_SLIPPAGE) -> list[Leg]:
    """Series for the rule (kind 'rule') or a baseline ('STOCK' or 'SPY') on the same trades."""
    legs = []
    for trade in trades:
        if kind == "rule":
            pnl, assigned = trade.pnl[slippage], trade.assigned
        elif kind == "STOCK":
            pnl, assigned = trade.stock_pnl, None
        else:
            pnl, assigned = trade.spy_pnl, None
        legs.append(Leg(trade.cohort, trade.ticker, trade.expiration, pnl, trade.capital, trade.days, assigned))
    return legs


def _excess(leg: Leg, rate: float) -> float:
    return leg.pnl - rate * leg.capital * leg.days / 365


def pooled_rate(legs: list[Leg], rate: float) -> float | None:
    """Excess P&L per capital-day, pooled over all legs."""
    if not legs:
        return None
    capital_days = sum(leg.capital * leg.days for leg in legs)
    return sum(_excess(leg, rate) for leg in legs) / capital_days


def _cohort_pairs(legs: list[Leg], rate: float) -> list[tuple[float, float]]:
    sums: dict[int, tuple[float, float]] = {}
    for leg in legs:
        excess, capital_days = sums.get(leg.cohort, (0.0, 0.0))
        sums[leg.cohort] = (excess + _excess(leg, rate), capital_days + leg.capital * leg.days)
    return list(sums.values())


def bootstrap_ci(pairs: list[tuple[float, float]]) -> list[float] | None:
    """90% block-bootstrap interval of pooled excess per capital-day, resampling cohorts."""
    if not pairs:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(pairs)
    draws = []
    for _ in range(BOOTSTRAP_DRAWS):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        draws.append(sum(e for e, _ in sample) / sum(c for _, c in sample))
    draws.sort()
    return [draws[int(0.05 * BOOTSTRAP_DRAWS)], draws[int(0.95 * BOOTSTRAP_DRAWS) - 1]]


def _sharpe(rates: list[float]) -> float | None:
    if len(rates) < 2:
        return None
    spread = statistics.stdev(rates)
    return statistics.fmean(rates) / spread if spread > 0 else None


def _sortino(rates: list[float]) -> float | None:
    if len(rates) < 2:
        return None
    downside = math.sqrt(statistics.fmean(min(r, 0.0) ** 2 for r in rates))
    return statistics.fmean(rates) / downside if downside > 0 else None


def _max_drawdown(legs: list[Leg]) -> float:
    running = peak = worst = 0.0
    for leg in sorted(legs, key=lambda item: (item.expiration, item.ticker)):
        running += leg.pnl
        peak = max(peak, running)
        worst = max(worst, peak - running)
    return worst


def summarize(legs: list[Leg], rate: float) -> dict:
    """Per-series metrics. Cohort-level ratios are None when they cannot be defined."""
    pairs = _cohort_pairs(legs, rate)
    rates = [excess / capital_days for excess, capital_days in pairs]
    returns = [leg.pnl / leg.capital for leg in legs]
    pnls = [leg.pnl for leg in legs]
    judged = [leg.assigned for leg in legs if leg.assigned is not None]
    pooled = pooled_rate(legs, rate)
    return {
        "n_trades": len(legs),
        "n_cohorts": len(pairs),
        "tiny_n": len(pairs) < 8,
        "win_rate": sum(p > 0 for p in pnls) / len(pnls) if pnls else None,
        "assignment_rate": sum(judged) / len(judged) if judged else None,
        "mean_return_on_capital": statistics.fmean(returns) if returns else None,
        "median_return_on_capital": statistics.median(returns) if returns else None,
        "std_return_on_capital": statistics.stdev(returns) if len(returns) >= 2 else None,
        "worst_return_on_capital": min(returns) if returns else None,
        "worst_pnl": min(pnls) if pnls else None,
        "total_pnl": sum(pnls),
        "max_drawdown": _max_drawdown(legs),
        "pooled_excess_per_capday": pooled,
        "annualized_excess": pooled * 365 if pooled is not None else None,
        "sharpe_like": _sharpe(rates),
        "sortino_like": _sortino(rates),
        "ci_90": bootstrap_ci(pairs),
    }


def _diff(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


# ---------------------------------------------------------------- walk-forward and verdict


def walk_forward(entries: list[date], trades_by_rule: dict) -> dict:
    """Pick one rule per test cohort from purged, base-case training trades; apply it to the test cohort."""
    folds, oos_trades, oos_cohorts = [], [], []
    for k, entry in enumerate(entries):
        train = {
            rule: [t for t in trades_by_rule.get(rule, []) if t.cohort < k and t.expiration < entry]
            for rule in WF_RULES
        }
        train_cohorts = {t.cohort for trades in train.values() for t in trades}
        fold = {
            "test_cohort": k,
            "entry": entry.isoformat(),
            "training_cohorts": len(train_cohorts),
            "training_trades": {rule: len(train[rule]) for rule in WF_RULES},
        }
        if len(train_cohorts) < MIN_TRAIN_COHORTS:
            folds.append({**fold, "status": "skipped_insufficient_training"})
            continue
        scores = {rule: pooled_rate(legs_for(train[rule]), BASE_RATE) for rule in WF_RULES}
        eligible = [rule for rule in WF_RULES if scores[rule] is not None]
        if not eligible:
            folds.append({**fold, "status": "skipped_no_eligible_rule"})
            continue
        chosen = max(eligible, key=lambda rule: (scores[rule], -WF_RULES.index(rule)))
        test = [t for t in trades_by_rule.get(chosen, []) if t.cohort == k]
        oos_trades.extend(test)
        # Only cohorts with a resolved test trade join the POOL/SPY comparison set.
        if test:
            oos_cohorts.append(k)
        folds.append(
            {
                **fold,
                "status": "ok",
                "chosen_rule": chosen,
                "training_score": scores[chosen],
                "test_trades": len(test),
                "test_excess_pooled": pooled_rate(legs_for(test), BASE_RATE),
            }
        )
    return {"folds": folds, "oos_trades": oos_trades, "oos_cohorts": oos_cohorts}


def verdict(oos_cohorts: int, ci: list[float] | None, diff_pool: float | None, diff_spy: float | None) -> dict:
    """Predeclared rule. Edge needs every condition; negative needs the CI upper bound below zero."""
    reasons = []
    if oos_cohorts < MIN_OOS_COHORTS:
        reasons.append(f"only {oos_cohorts} out-of-sample resolved cohorts (need at least {MIN_OOS_COHORTS})")
    if ci is None:
        reasons.append("no out-of-sample interval (no resolved trades)")
    elif ci[0] <= 0:
        reasons.append(f"90% interval lower bound {ci[0]:.6f} is not above 0")
    if diff_pool is None or diff_pool <= 0:
        reasons.append("out-of-sample excess does not beat the POOL baseline on the same cohorts")
    if diff_spy is None or diff_spy <= 0:
        reasons.append("out-of-sample excess does not beat SPY on the same trades")
    if not reasons:
        return {"verdict": "edge_supported", "reasons": []}
    if ci is not None and ci[1] < 0:
        return {"verdict": "negative", "reasons": reasons + ["90% interval upper bound is below 0"]}
    return {"verdict": "inconclusive", "reasons": reasons}


# ---------------------------------------------------------------- owner fill check


def _match_fill(fill: dict, cohorts: list[Cohort], contracts_by_cohort: dict, trading_dates: list[date]):
    try:
        fill_day = date.fromisoformat(str(fill["captured_at"])[:10])
    except ValueError:
        return None
    key = (
        str(fill.get("ticker") or "").upper(),
        _parse_expiration(fill.get("expiration")),
        round(float(fill.get("strike") or 0), 4),
    )
    fill_pos = bisect_left(trading_dates, fill_day)
    for cohort in sorted(cohorts, key=lambda c: c.entry, reverse=True):
        if not 0 <= fill_pos - bisect_left(trading_dates, cohort.entry) <= FILL_WINDOW_SESSIONS:
            continue
        contract = contracts_by_cohort[cohort.idx].get(key)
        if contract is None:
            continue
        price = float(fill["price"])
        spread = contract.ask - contract.bid
        return {
            "ticker": key[0],
            "expiration": key[1].isoformat() if key[1] else None,
            "strike": key[2],
            "cohort_entry": cohort.entry.isoformat(),
            "fill_date": fill_day.isoformat(),
            "fill_price": price,
            "bid": contract.bid,
            "ask": contract.ask,
            "fill_minus_bid": price - contract.bid,
            "fraction_of_spread": (price - contract.bid) / spread if spread > 0 else None,
        }
    return None


def owner_fill_check(fills: list[dict], cohorts: list[Cohort], trading_dates: list[date]) -> dict:
    """Diagnostic: where the owner's PUT sells filled relative to the snapshot bid and ask."""
    contracts_by_cohort = {c.idx: {to_contract(r).key: to_contract(r) for r in c.candidates} for c in cohorts}
    matches, unmatched = [], 0
    for fill in fills:
        if not (
            str(fill.get("security_type") or "").upper() == "OPT"
            and str(fill.get("side") or "").upper() == "SELL"
            and str(fill.get("option_type") or "").upper() == "PUT"
        ):
            continue
        found = _match_fill(fill, cohorts, contracts_by_cohort, trading_dates)
        if found is None:
            unmatched += 1
        else:
            matches.append(found)
    fractions = [m["fraction_of_spread"] for m in matches if m["fraction_of_spread"] is not None]
    return {
        "n_matched": len(matches),
        "n_unmatched": unmatched,
        "median_fraction_of_spread": statistics.median(fractions) if fractions else None,
        "fills": matches,
    }


# ---------------------------------------------------------------- report assembly


def _cell_key(slippage: float, rate: float) -> str:
    return f"s{slippage}_r{rate}"


def rule_cells(trades_by_rule: dict) -> dict:
    """Metrics per rule, slippage, and cash rate, with paired baseline differences on the same trades."""
    out = {}
    for rule, trades in trades_by_rule.items():
        cells = {}
        for slippage in SLIPPAGE_LEVELS:
            for rate in CASH_RATES:
                rule_legs = legs_for(trades, "rule", slippage)
                stock_legs, spy_legs = legs_for(trades, "STOCK"), legs_for(trades, "SPY")
                cell = summarize(rule_legs, rate)
                cell["stock_pooled"] = pooled_rate(stock_legs, rate)
                cell["spy_pooled"] = pooled_rate(spy_legs, rate)
                cell["vs_stock_pooled"] = _diff(cell["pooled_excess_per_capday"], cell["stock_pooled"])
                cell["vs_spy_pooled"] = _diff(cell["pooled_excess_per_capday"], cell["spy_pooled"])
                cells[_cell_key(slippage, rate)] = cell
        out[rule] = cells
    return out


def oos_cells(
    oos_trades: list[Trade], pool_trades: list[Trade], oos_cohorts: list[int], pool_all_trades: list[Trade] = ()
) -> dict:
    """Out-of-sample metrics per slippage and cash rate: chosen rules versus POOL, POOL_ALL, and SPY."""
    cohort_set = set(oos_cohorts)
    pool_in_cohorts = [t for t in pool_trades if t.cohort in cohort_set]
    pool_all_in_cohorts = [t for t in pool_all_trades if t.cohort in cohort_set]
    out = {}
    for slippage in SLIPPAGE_LEVELS:
        for rate in CASH_RATES:
            rule_legs = legs_for(oos_trades, "rule", slippage)
            pool_legs = legs_for(pool_in_cohorts, "rule", slippage)
            spy_legs = legs_for(oos_trades, "SPY")
            cell = summarize(rule_legs, rate)
            cell["pool_pooled"] = pooled_rate(pool_legs, rate)
            cell["pool_all_pooled"] = pooled_rate(legs_for(pool_all_in_cohorts, "rule", slippage), rate)
            cell["spy_pooled"] = pooled_rate(spy_legs, rate)
            cell["diff_vs_pool"] = _diff(cell["pooled_excess_per_capday"], cell["pool_pooled"])
            cell["diff_vs_pool_all"] = _diff(cell["pooled_excess_per_capday"], cell["pool_all_pooled"])
            cell["diff_vs_spy"] = _diff(cell["pooled_excess_per_capday"], cell["spy_pooled"])
            out[_cell_key(slippage, rate)] = cell
    return out


def _fee_note(fees: dict) -> str:
    state = "calibrated from fills" if fees["calibrated"] else "FALLBACK (not calibrated)"
    return f"{state}: option {fees['option_fee_per_contract']:.4f}/contract, stock sale {fees['stock_sale_fee']:.2f}"


def build_report(**parts) -> dict:
    """Assemble the JSON report from the pipeline parts. Keys are documented in render_markdown."""
    base = _cell_key(BASE_SLIPPAGE, BASE_RATE)
    oos = parts["oos"]
    verdict_result = verdict(
        oos[base]["n_cohorts"],
        oos[base]["ci_90"],
        oos[base]["diff_vs_pool"],
        oos[base]["diff_vs_spy"],
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": {
            "slippage_levels": SLIPPAGE_LEVELS,
            "base_slippage": BASE_SLIPPAGE,
            "cash_rates": CASH_RATES,
            "base_rate": BASE_RATE,
            "bootstrap": {"draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED, "level": 0.90},
            "walk_forward_rules": WF_RULES,
            "min_training_cohorts": MIN_TRAIN_COHORTS,
            "min_oos_cohorts": MIN_OOS_COHORTS,
        },
        "inputs": parts["inputs"],
        "fees": parts["fees"],
        "price_coverage": parts["price_coverage"],
        "unresolved": {rule: dict(counter) for rule, counter in parts["unresolved"].items()},
        "trade_counts": {rule: len(trades) for rule, trades in parts["trades"].items()},
        "rules": rule_cells(parts["trades"]),
        "walk_forward": {
            "folds": parts["walk_forward"]["folds"],
            "oos_cohorts": len(parts["walk_forward"]["oos_cohorts"]),
            "chosen_counts": dict(
                Counter(f["chosen_rule"] for f in parts["walk_forward"]["folds"] if "chosen_rule" in f)
            ),
            "oos": oos,
        },
        "verdict": verdict_result,
        "owner_fill_check": parts["fill_check"],
        "price_check": parts["price_check"],
        "cohorts": parts["cohorts"],
        "limitations": list(LIMITATIONS),
    }


def _fmt(value, spec: str = ".4f") -> str:
    return "n/a" if value is None else format(value, spec)


def _rule_row(rule: str, slippage: float, rate: float, cell: dict, unresolved: dict) -> str:
    unresolved_text = ", ".join(f"{k}={v}" for k, v in sorted(unresolved.items())) or "0"
    ci = cell["ci_90"]
    ci_text = "n/a" if ci is None else f"[{ci[0]:.6f}, {ci[1]:.6f}]"
    return (
        f"| {rule} | {slippage} | {rate} | {cell['n_trades']} | {cell['n_cohorts']} | {unresolved_text} | "
        f"{_fmt(cell['win_rate'], '.0%')} | {_fmt(cell['assignment_rate'], '.0%')} | "
        f"{_fmt(cell['mean_return_on_capital'], '.2%')} | {_fmt(cell['pooled_excess_per_capday'], '.6f')} | "
        f"{_fmt(cell['annualized_excess'], '.2%')} | {ci_text} | {_fmt(cell['vs_stock_pooled'], '.6f')} | "
        f"{_fmt(cell['vs_spy_pooled'], '.6f')} | {_fmt(cell['sharpe_like'], '.2f')} | "
        f"{_fmt(cell['sortino_like'], '.2f')} | {'yes' if cell['tiny_n'] else 'no'} |"
    )


def render_markdown(report: dict) -> str:
    """Readable report. Reads keys written by build_report."""
    verdict_result = report["verdict"]
    inputs, fees = report["inputs"], report["fees"]
    lines = [
        "# Signal replay: walk-forward, out-of-sample",
        "",
        f"Generated: {report['generated_at']}",
        "",
        f"## Verdict: {verdict_result['verdict']}",
        "",
        *[f"- {reason}" for reason in verdict_result["reasons"]],
        "",
        "## Coverage",
        "",
        f"- Snapshots: {inputs['snapshots']} (status {inputs['status_counts']}); empty skipped: "
        f"{inputs['empty']}; unplaced: {inputs['unplaced']}",
        f"- Cohorts: {inputs['cohorts']} ({inputs['first_entry']} to {inputs['last_entry']})",
        f"- Fees: {_fee_note(fees)}",
        f"- Price symbols missing from cache: {report['price_coverage']['missing'] or 'none'}",
        f"- R0 signals dropped (no usable quote): {inputs['r0_dropped']}",
        "",
        "## Rules, slippage, and cash rate",
        "",
        "| Rule | Slippage | Cash r | Trades | Cohorts | Unresolved | Win | Assigned | Mean ROC | "
        "Excess/cap-day | Annualized | 90% CI | vs STOCK | vs SPY | Sharpe | Sortino | Tiny n |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for rule, cells in report["rules"].items():
        for slippage in SLIPPAGE_LEVELS:
            for rate in CASH_RATES:
                cell = cells[_cell_key(slippage, rate)]
                lines.append(_rule_row(rule, slippage, rate, cell, report["unresolved"].get(rule, {})))
    lines += [
        "",
        "## Walk-forward folds",
        "",
        "| Test cohort | Entry | Status | Chosen | Training cohorts | Test trades | Test excess/cap-day |",
        "|---|---|---|---|---|---|---|",
    ]
    for fold in report["walk_forward"]["folds"]:
        lines.append(
            f"| {fold['test_cohort']} | {fold['entry']} | {fold['status']} | {fold.get('chosen_rule', '-')} | "
            f"{fold['training_cohorts']} | {fold.get('test_trades', '-')} | "
            f"{_fmt(fold.get('test_excess_pooled'), '.6f')} |"
        )
    base = report["walk_forward"]["oos"][_cell_key(BASE_SLIPPAGE, BASE_RATE)]
    lines += [
        "",
        "## Out-of-sample (base case)",
        "",
        f"- Resolved OOS cohorts: {base['n_cohorts']} (tiny_n={base['tiny_n']}); trades: {base['n_trades']}",
        f"- Pooled excess/cap-day: {_fmt(base['pooled_excess_per_capday'], '.6f')}; 90% CI: {base['ci_90']}",
        f"- Minus POOL (gated): {_fmt(base['diff_vs_pool'], '.6f')}; minus POOL_ALL (ungated): "
        f"{_fmt(base.get('diff_vs_pool_all'), '.6f')}; minus SPY: {_fmt(base['diff_vs_spy'], '.6f')}",
        "",
        "## Diagnostics",
        "",
        f"- Owner fills matched: {report['owner_fill_check']['n_matched']} "
        f"(unmatched {report['owner_fill_check']['n_unmatched']}); median fraction of spread: "
        f"{_fmt(report['owner_fill_check']['median_fraction_of_spread'], '.2f')}",
        f"- Yahoo cross-check: {report['price_check']}",
        "",
        "## Limitations",
        "",
        *[f"- {item}" for item in report["limitations"]],
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- I/O (main only)


class _Abort(RuntimeError):
    """A stop condition that main reports and turns into exit code 2."""


def _read_db(path: Path) -> tuple[list[dict], list[dict]]:
    """Run snapshots and option fills, read through a read-only SQLite connection."""
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA temp_store=memory")
        runs = conn.execute("SELECT run_id, published_at, status, snapshot_json FROM run_metadata").fetchall()
        cursor = conn.execute(
            "SELECT security_type, side, qty, price, fees, ticker, option_type, strike, expiration, captured_at "
            "FROM option_fills"
        )
        columns = [column[0] for column in cursor.description]
        fills = [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        conn.close()
    snapshots = []
    for run_id, published, status, blob in runs:
        snap = json.loads(blob)
        published_at = datetime.fromisoformat(published or (snap.get("run") or {}).get("published_at"))
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        snapshots.append(
            {
                "snapshot_id": run_id,
                "status": status,
                "published_at": published_at,
                "signals": snap.get("signals") or [],
                "candidates": snap.get("csp_picks") or [],
            }
        )
    return snapshots, fills


def _read_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _kline_rows(conn, symbol: str, start: str, end: str, autype, ktype) -> list[dict]:
    """All daily bars for one symbol and adjustment type, following the page keys."""
    from moomoo import RET_OK

    rows: list[dict] = []
    page_key = None
    while True:
        ret, frame, page_key = conn.get_history_kline(
            symbol, start=start, end=end, ktype=ktype, autype=autype, max_count=1000, page_req_key=page_key
        )
        if ret != RET_OK:
            raise _Abort(f"history candles failed for {symbol}: {frame}")
        rows.extend(
            {"date": str(stamp)[:10], "open": float(opened), "close": float(closed)}
            for stamp, opened, closed in zip(frame["time_key"], frame["open"], frame["close"])
        )
        if page_key is None:
            return rows


def _fetch_symbol(conn, symbol: str, start: str, end: str) -> list[dict]:
    """Unadjusted bars (the traded prices) plus the forward-adjusted close for corporate-action checks."""
    from moomoo import AuType, KLType

    raw = _kline_rows(conn, symbol, start, end, AuType.NONE, KLType.K_DAY)
    adjusted = {row["date"]: row["close"] for row in _kline_rows(conn, symbol, start, end, AuType.QFQ, KLType.K_DAY)}
    return [{**row, "adj_close": adjusted.get(row["date"])} for row in raw]


class CandleStore:
    """Daily candles cached in _tmp/replay/prices.json. Only missing or short windows are fetched."""

    def __init__(self, path: Path, conn, force: bool):
        self.path = path
        self.conn = conn
        self.force = force
        self.cache = _read_cache(path)
        self.fetched: list[str] = []

    def ensure(self, starts: dict[str, date], end: date) -> None:
        needed = {symbol: start for symbol, start in starts.items() if not self._covers(symbol, start, end)}
        if not needed or self.conn is None:
            return
        self._check_quota(len(needed))
        for symbol, start in sorted(needed.items()):
            rows = _fetch_symbol(self.conn, symbol, start.isoformat(), end.isoformat())
            self.cache[symbol] = {"start": start.isoformat(), "end": end.isoformat(), "rows": rows}
            self.fetched.append(symbol)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.cache), encoding="utf-8")

    def _covers(self, symbol: str, start: date, end: date) -> bool:
        record = self.cache.get(symbol)
        return bool(record) and record["start"] <= start.isoformat() and record["end"] >= end.isoformat()

    def _check_quota(self, uncached: int) -> None:
        from moomoo import RET_OK

        # get_history_kl_quota is allowlisted in core/broker_protocol.QUERY_SDK_MEMBERS.
        ret, quota = self.conn.quote_ctx.get_history_kl_quota(get_detail=False)
        if ret != RET_OK:
            raise _Abort(f"history quota query failed: {quota}")
        used, remaining = quota[0], quota[1]
        print(f"history candle quota: used={used} remaining={remaining}; symbols to fetch={uncached}")
        if remaining < uncached and not self.force:
            raise _Abort("history quota is below the number of uncached symbols; rerun later or pass --force")

    def series(self, symbol: str) -> PriceSeries | None:
        record = self.cache.get(symbol)
        return build_series(record["rows"]) if record else None


def _connect():
    """Query-only OpenD connection, built the same way as tools/probe_broker_history.py."""
    from core.connection_manager import MoomooConnection

    config = Config()
    conn = MoomooConnection(
        host=config.get("host", "127.0.0.1"),
        port=int(config.get("port", 11111) or 11111),
        readonly=True,
        account_id=config.get("account_id"),
        portfolio_env=config.get("portfolio_env"),
        security_firm=config.get("security_firm"),
    )
    if not conn.connect():
        print(f"OpenD unavailable: {conn.last_error or 'unknown error'}")
        return None
    return conn


def _yf_check(cache: dict, symbols: list[str], start: date, end: date) -> dict:
    """Diagnostic only: flag Moomoo closes that differ from unadjusted Yahoo closes beyond the tolerance."""
    import yfinance as yf

    # Keep the yfinance cache inside the git-ignored _tmp folder, not the user profile.
    cache_dir = REPO_ROOT / "_tmp" / "yf_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(cache_dir))

    compared = flagged = 0
    examples: list[dict] = []
    for symbol in symbols:
        record = cache.get(symbol)
        if not record:
            continue
        history = yf.Ticker(symbol).history(start=start.isoformat(), end=end.isoformat(), auto_adjust=False)
        if history.empty or "Close" not in history:
            continue
        closes = {stamp.date().isoformat(): float(value) for stamp, value in history["Close"].items()}
        for row in record["rows"]:
            other = closes.get(row["date"])
            if other is None or row["close"] <= 0:
                continue
            compared += 1
            if abs(row["close"] - other) / row["close"] > YF_TOLERANCE:
                flagged += 1
                if len(examples) < 5:
                    examples.append({"symbol": symbol, "date": row["date"], "moomoo": row["close"], "yahoo": other})
    return {"enabled": True, "tolerance": YF_TOLERANCE, "compared": compared, "flagged": flagged, "examples": examples}


def _dry_run(snapshots: list[dict], fees: dict, leveraged: list, cache: dict) -> int:
    """Cohorts, selections, and fees from the DB only. No broker or network call."""
    if "SPY" in cache:
        trading = [date.fromisoformat(row["date"]) for row in cache["SPY"]["rows"]]
        calendar = "SPY candles from cache"
    else:
        first = min(s["published_at"] for s in snapshots).date() - SPY_WINDOW_BEFORE
        last = max(s["published_at"] for s in snapshots).date() + timedelta(days=60)
        days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
        trading = [day for day in days if day.weekday() < 5]
        calendar = "weekday calendar (no price cache; holidays not excluded)"
    cohorts, stats = build_cohorts(snapshots, trading)
    print(f"calendar: {calendar}; snapshots={len(snapshots)} cohorts={len(cohorts)} stats={stats}")
    print(f"fees: {_fee_note(fees)}; leveraged tickers configured: {len(leveraged)}")
    for cohort in cohorts:
        picks, dropped = select_published(cohort.signals, cohort.pool)
        rules = select_rules(cohort.pool, leveraged)
        counts = " ".join(f"{name}={len(rules[name])}" for name in ("R1_current", "R2_no_tail_gate", "R3_low_delta"))
        counts += f" R4={len(rules['R4_model_ev'])} POOL={len(rules['POOL'])}"
        top = ",".join(c.ticker for c in rules["R1_current"])
        print(
            f"  {cohort.idx:>3} entry={cohort.entry} src={cohort.decision_source:<9} "
            f"snap={cohort.snapshot_id[:8]} raw={len(cohort.candidates):>3} pool={len(cohort.pool):>3} "
            f"R0={len(picks)}(dropped {dropped}) {counts} R1_top={top}"
        )
    return 0


def _cohort_symbols(cohorts: list[Cohort]) -> list[str]:
    symbols = {"SPY"}
    for cohort in cohorts:
        symbols.update(c.ticker for c in cohort.pool)
        symbols.update(str(s.get("ticker") or "").upper() for s in cohort.signals)
    symbols.discard("")
    return sorted(symbols)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Walk-forward replay of past CSP signals (read-only).")
    parser.add_argument("--db", help="options.db path (default: db_path from config)")
    parser.add_argument("--out-dir", help="output directory (default: _tmp/replay)")
    parser.add_argument("--dry-run", action="store_true", help="cohorts, selections, and fees from the DB only")
    parser.add_argument("--no-fetch", action="store_true", help="use the candle cache only; no broker call")
    parser.add_argument("--force", action="store_true", help="fetch even if the history quota is short")
    parser.add_argument("--yf-check", action="store_true", help="diagnostic: compare closes with yfinance")
    return parser.parse_args(argv)


def _print_summary(report: dict, paths: tuple[Path, Path]) -> None:
    base = report["walk_forward"]["oos"][_cell_key(BASE_SLIPPAGE, BASE_RATE)]
    print(f"cohorts={report['inputs']['cohorts']} trades={report['trade_counts']}")
    print(f"verdict: {report['verdict']['verdict']}")
    for reason in report["verdict"]["reasons"]:
        print(f"  - {reason}")
    print(
        f"OOS base case: cohorts={base['n_cohorts']} trades={base['n_trades']} "
        f"excess/cap-day={_fmt(base['pooled_excess_per_capday'], '.6f')} ci90={base['ci_90']} "
        f"vs POOL={_fmt(base['diff_vs_pool'], '.6f')} vs SPY={_fmt(base['diff_vs_spy'], '.6f')}"
    )
    print(f"wrote {paths[0]} and {paths[1]}")


def _write_outputs(out_dir: Path, report: dict) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = Config()
    db_path = Path(args.db) if args.db else REPO_ROOT / str(config.get("db_path", "options.db"))
    out_dir = Path(args.out_dir) if args.out_dir else REPO_ROOT / "_tmp" / "replay"
    snapshots, fills = _read_db(db_path)
    if not snapshots:
        print("no run snapshots found")
        return 1
    fees = calibrate_fees(fills)
    leveraged = list(config.get("leveraged_etf_tickers") or [])
    cache_path = out_dir / "prices.json"
    if args.dry_run:
        return _dry_run(snapshots, fees, leveraged, _read_cache(cache_path))

    conn = None if args.no_fetch else _connect()
    if conn is None and not args.no_fetch:
        return 2
    today = date.today()
    store = CandleStore(cache_path, conn, args.force)
    try:
        spy_start = min(s["published_at"] for s in snapshots).date() - SPY_WINDOW_BEFORE
        store.ensure({"SPY": spy_start}, today)
        spy = store.series("SPY")
        if spy is None:
            print("no SPY candles in the cache; run once without --no-fetch")
            return 1
        cohorts, stats = build_cohorts(snapshots, list(spy.dates))
        if not cohorts:
            print("no cohorts could be placed in a trading session")
            return 1
        symbols = _cohort_symbols(cohorts)
        window_start = cohorts[0].entry - ENTRY_WINDOW_BEFORE
        store.ensure({symbol: window_start for symbol in symbols}, today)
    except _Abort as exc:
        print(exc)
        return 2
    finally:
        if conn is not None:
            conn.disconnect()

    prices = {symbol: store.series(symbol) for symbol in symbols}
    prices = {symbol: series for symbol, series in prices.items() if series is not None}
    price_check = _yf_check(store.cache, symbols, window_start, today) if args.yf_check else {"enabled": False}
    last_date = spy.dates[-1]
    result = replay(cohorts, prices, fees, leveraged, last_date)
    walk = walk_forward([c.entry for c in cohorts], result["trades"])
    report = build_report(
        inputs={
            "snapshots": len(snapshots),
            "status_counts": dict(Counter(s["status"] for s in snapshots)),
            "empty": stats["empty"],
            "unplaced": stats["unplaced"],
            "cohorts": len(cohorts),
            "first_entry": cohorts[0].entry.isoformat(),
            "last_entry": cohorts[-1].entry.isoformat(),
            "fills": len(fills),
            "trading_dates": len(spy.dates),
            "last_candle": last_date.isoformat(),
            "leveraged_tickers": len(leveraged),
            "r0_dropped": result["r0_dropped"],
        },
        fees=fees,
        price_coverage={
            "symbols": symbols,
            "fetched": store.fetched,
            "missing": [s for s in symbols if s not in prices],
            "window_end": today.isoformat(),
        },
        unresolved=result["unresolved"],
        trades=result["trades"],
        walk_forward=walk,
        oos=oos_cells(walk["oos_trades"], result["trades"]["POOL"], walk["oos_cohorts"], result["trades"]["POOL_ALL"]),
        fill_check=owner_fill_check(fills, cohorts, list(spy.dates)),
        price_check=price_check,
        cohorts=result["cohorts"],
    )
    _print_summary(report, _write_outputs(out_dir, report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
