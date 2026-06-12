"""
Read-only catalyst flow anomaly detection.

Detects unusual options activity that may precede catalysts.
Framework: fresh volume ratio, premium notional, hedge mirroring, clustering.
"""

import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _premium_notional(volume: int, mid_price: float) -> float:
    return volume * max(mid_price, 0) * 100


def _fresh_ratio(volume: int, oi: int) -> float:
    return volume / max(oi, 1)


def _first_number(opt: dict, keys: tuple[str, ...], default=0):
    for key in keys:
        value = opt.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def _otm_pct(strike: float, stock_price: float, side: str) -> float:
    if stock_price <= 0:
        return 0.0
    if side == "CALL":
        return max(((strike - stock_price) / stock_price) * 100, 0)
    return max(((stock_price - strike) / stock_price) * 100, 0)


def _mirror_strike(stock_price: float, otm_pct: float, side: str) -> float:
    if side == "CALL":
        return stock_price * (1 - otm_pct / 100)
    return stock_price * (1 + otm_pct / 100)


ACTION_BUCKETS = {
    "CALL_RESEARCH": "Call Research",
    "PUT_RESEARCH": "Put Research",
    "CONFLICT_WATCH": "Conflict / Volatility Watch",
    "SPECULATIVE_ONLY": "Speculative Only",
    "REJECT": "Reject",
    "WATCH": "Watch",
}


@dataclass
class CatalystFlowSignal:
    ticker: str
    side: str
    signal: str
    label: str
    score: float
    premium_notional: float
    fresh_volume_ratio: float
    otm_pct: float
    strike: float
    is_hedged: bool
    cluster_expirations: list[str]
    direction: str
    rationale: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    research_only: bool = True
    earnings_dte: Optional[int] = None
    action_bucket: str = "WATCH"
    action_label: str = "Watch"
    action_reason: str = ""
    actionable: bool = False
    volume: int = 0
    open_interest: int = 0
    bid: float = 0.0
    ask: float = 0.0
    spread: float = 0.0
    implied_volatility: Optional[float] = None
    delta: Optional[float] = None
    expiry: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def classify_catalyst_flow(
    ticker: str,
    stock_price: float,
    option_list: list[dict],
    earnings_info: Optional[dict] = None,
    config: Optional[dict] = None,
) -> list[CatalystFlowSignal]:
    """
    Scan a ticker's option chain for anomalous fresh volume and cluster
    patterns that may indicate pre-catalyst positioning.

    Returns research-only CatalystFlowSignal objects sorted by score descending.
    """
    cfg = config or {}
    min_volume = int(cfg.get("min_volume", 500))
    min_notional = float(cfg.get("min_premium_notional", 1_000_000))
    min_fresh = float(cfg.get("min_fresh_volume_ratio", 5.0))
    max_expirations = int(cfg.get("max_expirations", 6))

    earnings = earnings_info or {}
    days_to_earnings = earnings.get("days_to_earnings")

    # -- Group options by (strike, side) --#
    groups: dict[tuple[float, str], dict] = {}
    for opt in option_list:
        strike = float(opt.get("strike", 0) or 0)
        side = str(opt.get("option_type", "") or "").upper()
        if side not in ("CALL", "PUT") or strike <= 0:
            continue

        volume = int(_first_number(opt, ("volume", "vol", "option_volume"), 0))
        if volume <= 0:
            continue

        oi = int(_first_number(opt, ("open_interest", "openInterest", "option_open_interest"), 0))
        bid = _first_number(opt, ("bid", "bid_price"), 0)
        ask = _first_number(opt, ("ask", "ask_price"), 0)
        last = _first_number(opt, ("last", "last_price", "lastPrice"), 0)
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else (bid or ask or last)
        expiration = str(opt.get("expiration", "") or "").replace("-", "")[:8]

        entry = groups.setdefault(
            (strike, side),
            {
                "volume": 0,
                "oi": 0,
                "premium": 0.0,
                "fresh": 0.0,
                "otm_pct": _otm_pct(strike, stock_price, side),
                "expirations": set(),
                "bid": 0.0,
                "ask": 0.0,
                "iv": None,
                "delta": None,
                "earliest_expiration": "",
            },
        )
        entry["volume"] += volume
        entry["oi"] += oi
        notional = _premium_notional(volume, mid)
        entry["premium"] += notional
        entry["fresh"] = max(entry["fresh"], _fresh_ratio(volume, oi))
        if expiration:
            entry["expirations"].add(expiration)
            if not entry["earliest_expiration"] or expiration < entry["earliest_expiration"]:
                entry["earliest_expiration"] = expiration
        if bid > entry["bid"]:
            entry["bid"] = bid
        if ask > entry["ask"]:
            entry["ask"] = ask
        iv_val = _first_number(opt, ("implied_volatility", "iv", "option_iv"), None)
        if iv_val is not None and entry["iv"] is None:
            entry["iv"] = iv_val
        delta_val = _first_number(opt, ("delta", "option_delta"), None)
        if delta_val is not None and entry["delta"] is None:
            entry["delta"] = delta_val

    if not groups:
        return []

    # -- Build side indices for hedge detection --#
    call_entries = {s: d for (s, side), d in groups.items() if side == "CALL"}
    put_entries = {s: d for (s, side), d in groups.items() if side == "PUT"}

    signals: list[CatalystFlowSignal] = []

    for (strike, side), entry in groups.items():
        if entry["premium"] < min_notional and entry["fresh"] < min_fresh:
            continue
        if entry["otm_pct"] <= 0:
            continue
        if entry["volume"] < min_volume:
            continue

        # -- Hedge detection: mirrored OTM strike on opposite side --#
        mirror_map = put_entries if side == "CALL" else call_entries
        target_mirror = _mirror_strike(stock_price, entry["otm_pct"], side)
        hedge_premium = 0.0
        for m_strike, m_entry in mirror_map.items():
            if abs(m_strike - target_mirror) / max(stock_price, 1) < 0.03:
                hedge_premium += m_entry["premium"]

        hedge_ratio = hedge_premium / max(entry["premium"], 1)
        is_hedged = 0.3 <= hedge_ratio <= 3.0 and hedge_premium >= min_notional * 0.5

        # -- Cluster score --#
        cluster_count = len(entry["expirations"])
        cluster_score = _clamp(cluster_count / max(max_expirations, 1) * 100)

        # -- Score --#
        premium_score = _clamp(math.log10(entry["premium"] / max(min_notional, 1) + 1) * 25, 0, 40)
        fresh_score = _clamp(entry["fresh"] / 20 * 30, 0, 30)
        score = premium_score + fresh_score + cluster_score * 0.3

        if is_hedged:
            score *= 0.5
            score = min(score, 50)

        if days_to_earnings is not None and 0 <= days_to_earnings <= 14:
            score *= 1.15

        score = _clamp(score)

        direction = "BULLISH" if side == "CALL" else "BEARISH"

        # -- Rationale --#
        rationale = []
        if entry["premium"] >= min_notional:
            rationale.append(f"${entry['premium']:,.0f} premium notional")
        if entry["fresh"] >= min_fresh:
            rationale.append(f"{entry['fresh']:.0f}x fresh vol/OI")
        if cluster_count > 1:
            rationale.append(f"{cluster_count} expiration cluster")
        if is_hedged:
            rationale.append("Mirrored opposite strike (hedged)")
        if days_to_earnings is not None and 0 <= days_to_earnings <= 14:
            rationale.append(f"{days_to_earnings}d to earnings")

        blockers = []
        if is_hedged:
            blockers.append("Hedged position — lower directional conviction")

        # -- Action bucket --#
        if entry["otm_pct"] > 50:
            action_bucket = "SPECULATIVE_ONLY"
            action_label = "Speculative Only"
            action_reason = f"OTM {entry['otm_pct']:.0f}% — lottery flow, not core signal"
        elif is_hedged:
            action_bucket = "WATCH"
            action_label = "Watch"
            action_reason = "Hedged position — directional conviction reduced"
        elif score < 30:
            action_bucket = "REJECT"
            action_label = "Reject"
            action_reason = f"Score {score:.0f} below threshold"
        else:
            action_bucket = f"{side}_RESEARCH"
            action_label = ACTION_BUCKETS[f"{side}_RESEARCH"]
            action_reason = "; ".join(rationale[:2]) if rationale else "Fresh flow detected"

        # -- Signal tier --#
        if score >= 65:
            signal_label = "GREEN"
            label_text = "Priority lead" if not is_hedged else "Moderate (hedged)"
        elif score >= 40:
            signal_label = "YELLOW"
            label_text = "Moderate" if not is_hedged else "Low (hedged)"
        else:
            signal_label = "WATCH"
            label_text = "Low significance"

        sig = CatalystFlowSignal(
            ticker=ticker,
            side=side,
            signal=signal_label,
            label=label_text,
            score=round(score, 1),
            premium_notional=round(entry["premium"], 2),
            fresh_volume_ratio=round(entry["fresh"], 1),
            otm_pct=round(entry["otm_pct"], 1),
            strike=strike,
            is_hedged=is_hedged,
            cluster_expirations=sorted(entry["expirations"]),
            direction=direction,
            rationale=rationale,
            blockers=blockers,
            research_only=True,
            earnings_dte=days_to_earnings,
            action_bucket=action_bucket,
            action_label=action_label,
            action_reason=action_reason,
            actionable=False,
            volume=entry["volume"],
            open_interest=entry["oi"],
            bid=round(entry["bid"], 4),
            ask=round(entry["ask"], 4),
            spread=round(entry["ask"] - entry["bid"], 4) if entry["ask"] > 0 and entry["bid"] > 0 else 0.0,
            implied_volatility=entry["iv"],
            delta=entry["delta"],
            expiry=entry["earliest_expiration"],
        )

        if score >= 20:
            signals.append(sig)

    if not signals:
        return []

    signals.sort(key=lambda s: s.score, reverse=True)
    seen_sides: set[str] = set()
    top: list[CatalystFlowSignal] = []
    for s in signals:
        side_key = f"{s.ticker}:{s.side}"
        if side_key not in seen_sides:
            top.append(s)
            seen_sides.add(side_key)
        if len(top) >= 4:
            break

    return top
