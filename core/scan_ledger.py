"""
Wheel Scan Ledger — pure entry shape and hashes for every scan the app runs.

Each scan produces one ledger entry (like a Vibe-Trading run card) recording:
  - scan_type (recommendations, roll, watchlist)
  - timestamp
  - config_hash (deterministic hash of active config)
  - portfolio_hash (snapshot of positions + cash)
  - data_sources used
  - warnings emitted
  - top signals and blocked candidates
  - counts and scoring version

Persistence lives in ``db.scan_ledger_repository``; this module stays pure so
decision code can build entries without importing SQLite.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

SCORING_VERSION = "3.0-capital-return"


@dataclass
class ScanLedgerEntry:
    scan_type: str = "recommendations"
    timestamp: str = ""
    config_hash: str = ""
    portfolio_hash: str = ""
    scoring_version: str = SCORING_VERSION
    data_sources: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    top_signals: list[dict] = field(default_factory=list)
    blocked_candidates: list[dict] = field(default_factory=list)
    total_candidates: int = 0
    passed_count: int = 0
    blocked_count: int = 0
    elapsed_seconds: float = 0.0
    error_message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def compute_config_hash(config: dict) -> str:
    stable = _stable_json(config)
    return hashlib.sha256(stable.encode()).hexdigest()[:16]


def compute_portfolio_hash(portfolio_context: dict) -> str:
    try:
        positions = portfolio_context.get("positions", {})
        cash = portfolio_context.get("cash_balance", 0)
        account = portfolio_context.get("account_value", 0)
        parts = []
        for symbol, pos in sorted(positions.items()):
            qty = float(pos.get("position", 0) or 0)
            parts.append(f"{symbol}:{qty}")
        raw = "|".join(parts) + f"|CASH:{cash}|ACCT:{account}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
    except Exception:
        return "unknown"


def extract_data_sources(portfolio_context: dict, decisions: list | None = None) -> list[dict]:
    sources: set[str] = set()
    if portfolio_context.get("broker_buying_power"):
        sources.add("moomoo_portfolio")
    if decisions:
        for d in decisions:
            wd = d if hasattr(d, "to_dict") else d.get("wheel_decision", d)
            if isinstance(wd, dict):
                for key in (
                    "price_source",
                    "chain_source",
                    "greeks_source",
                    "iv_source",
                    "earnings_source",
                ):
                    val = wd.get(key, "")
                    if val and val not in ("missing", ""):
                        sources.add(val)
            elif hasattr(wd, "price_source"):
                for attr in (
                    "price_source",
                    "chain_source",
                    "greeks_source",
                    "iv_source",
                    "earnings_source",
                ):
                    val = getattr(wd, attr, "")
                    if val and val not in ("missing", ""):
                        sources.add(val)
    return [{"name": s, "status": "used"} for s in sorted(sources)]


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str)
