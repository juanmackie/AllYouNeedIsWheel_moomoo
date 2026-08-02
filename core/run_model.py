"""Immutable wheel run model.

One refresh produces exactly one completed ``WheelRunSnapshot``, persisted to
SQLite and then atomically published. In-flight work is modeled separately as
``RefreshAttempt`` (queued/refreshing + progress + latest failure) so a failed
attempt can never overwrite or relabel the last successful snapshot.

Completed snapshot states:
- ready:    complete-union coverage, fresh Moomoo quote/chain data, no errors
- partial:  some symbols failed; diagnostics shown, no copy actions
- planning: preflight infeasible or market-closed preview; read-only
- stale:    last successful snapshot aged beyond the freshness window

Only ``ready`` permits copy-to-ticket actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

ACTIONABLE_STATES = ("ready",)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RunMetadata:
    run_id: str
    generated_at: str
    published_at: Optional[str]
    env: str  # REAL | SIMULATE
    account_id: str  # opaque, non-sensitive identity
    preset_key: str
    preset_version: int
    market_state: str  # open | closed | unknown
    status: str  # ready | partial | planning | stale
    errors: tuple[str, ...] = ()
    partial_symbols: tuple[str, ...] = ()
    stale_symbols: tuple[str, ...] = ()
    quote_fetched_at: dict[str, str] = field(default_factory=dict)
    max_tradeable_age_sec: int = 120
    coverage_scanned: int = 0
    coverage_total: int = 0
    schema_version: int = 1

    @property
    def coverage_complete(self) -> bool:
        return self.coverage_total > 0 and self.coverage_scanned >= self.coverage_total


@dataclass(frozen=True)
class WheelRunSnapshot:
    run: RunMetadata
    portfolio: Optional[dict]
    csp_picks: tuple[dict, ...]
    cc_decisions: tuple[dict, ...]
    roll_decisions: tuple[dict, ...]
    rejected: tuple[dict, ...]
    preset: dict
    watchlist_origins: dict
    signals: tuple[dict, ...] = ()

    @property
    def tradeable(self) -> bool:
        """Only a ready, complete-coverage, fresh-quote run is actionable."""
        if self.run.status not in ACTIONABLE_STATES or self.run.errors:
            return False
        if not self.run.coverage_complete:
            return False
        return self._quotes_fresh()

    def _quotes_fresh(self) -> bool:
        if not self.run.quote_fetched_at:
            return False
        now = datetime.now(timezone.utc)
        for fetched_at in self.run.quote_fetched_at.values():
            try:
                age = (now - datetime.fromisoformat(fetched_at)).total_seconds()
            except (TypeError, ValueError):
                return False
            if age > self.run.max_tradeable_age_sec:
                return False
        return True

    def to_dict(self) -> dict:
        return {
            "run": {
                "run_id": self.run.run_id,
                "generated_at": self.run.generated_at,
                "published_at": self.run.published_at,
                "env": self.run.env,
                "account_id": self.run.account_id,
                "preset_key": self.run.preset_key,
                "preset_version": self.run.preset_version,
                "market_state": self.run.market_state,
                "status": self.run.status,
                "errors": list(self.run.errors),
                "partial_symbols": list(self.run.partial_symbols),
                "stale_symbols": list(self.run.stale_symbols),
                "quote_fetched_at": self.run.quote_fetched_at,
                "max_tradeable_age_sec": self.run.max_tradeable_age_sec,
                "coverage_scanned": self.run.coverage_scanned,
                "coverage_total": self.run.coverage_total,
                "coverage_complete": self.run.coverage_complete,
                "schema_version": self.run.schema_version,
            },
            "tradeable": self.tradeable,
            "portfolio": self.portfolio,
            "csp_picks": list(self.csp_picks),
            "cc_decisions": list(self.cc_decisions),
            "roll_decisions": list(self.roll_decisions),
            "rejected": list(self.rejected),
            "preset": self.preset,
            "watchlist_origins": self.watchlist_origins,
            "signals": list(self.signals),
        }


@dataclass(frozen=True)
class RefreshAttempt:
    attempt_id: str
    run_id: Optional[str]  # set only when a NEW run is published
    state: str  # queued | refreshing | succeeded | failed
    stage: str = "idle"  # account | positions | watchlist | csp | cc | roll | publish
    progress: float = 0.0  # 0..1
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    latest_error: Optional[str] = None
    latest_failure_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "state": self.state,
            "stage": self.stage,
            "progress": round(self.progress, 3),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "latest_error": self.latest_error,
            "latest_failure_at": self.latest_failure_at,
        }
