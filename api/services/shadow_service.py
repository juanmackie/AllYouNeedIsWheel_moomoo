"""Settle archived recommendation candidates from verified daily closes."""

from __future__ import annotations

import logging
from datetime import date, time

from core.shadow_rank import rank_checkpoint
from core.utils import market_now

logger = logging.getLogger("api.services.shadow")


class ShadowOutcomeService:
    def __init__(self, db, connection_provider=None):
        self.db = db
        self.connection_provider = connection_provider

    def get_analytics(self, env=None, account_id=None, alternative_score_key="capital_velocity_per_day"):
        records = self.db.get_shadow_candidates(env=env, account_id=account_id)
        checkpoint = rank_checkpoint(records, alternative_score_key)
        status_counts = {}
        for row in records:
            status = str(row.get("status") or "pending")
            status_counts[status] = status_counts.get(status, 0) + 1
        return {
            "candidate_count": len(records),
            "status_counts": status_counts,
            "checkpoint": checkpoint,
            "alternative_score_key": alternative_score_key,
        }

    def settle_expired(self, env=None, account_id=None, limit=500):
        if self.connection_provider is None:
            return {"ok": False, "error": "OpenD connection is unavailable"}
        connection = self.connection_provider()
        if connection is None:
            return {"ok": False, "error": "OpenD connection is unavailable"}

        candidates = self.db.get_shadow_candidates(env=env, account_id=account_id, limit=limit)
        now = market_now()
        today = now.date()
        eligible = [
            row
            for row in candidates
            if row.get("status") != "settled"
            and _expiry_date(row.get("expiration")) is not None
            and (
                _expiry_date(row.get("expiration")) < today
                or (_expiry_date(row.get("expiration")) == today and now.time() >= time(16, 0))
            )
        ]
        if not eligible:
            return {"ok": True, "settled": 0, "pending": len(candidates), "reason": "no_expired_candidates"}

        quota = connection.get_history_kl_quota()
        if not isinstance(quota, tuple) or len(quota) < 2 or quota[0] != 0:
            return {"ok": False, "error": "Could not verify historical K-line quota"}
        quota_data = quota[1]
        if not isinstance(quota_data, tuple) or len(quota_data) < 2:
            return {"ok": False, "error": "Historical K-line quota response was incomplete"}
        try:
            remaining_quota = int(quota_data[1])
        except (TypeError, ValueError):
            return {"ok": False, "error": "Historical K-line quota response was invalid"}

        pairs = sorted(
            {
                (str(row.get("ticker") or "").upper(), _expiry_date(row.get("expiration")).isoformat())
                for row in eligible
                if row.get("ticker") and _expiry_date(row.get("expiration")) is not None
            }
        )
        if len(pairs) > remaining_quota:
            return {
                "ok": True,
                "settled": 0,
                "pending": len(candidates),
                "reason": "insufficient_history_quota",
                "required_calls": len(pairs),
                "remaining_quota": remaining_quota,
            }

        closes = {}
        try:
            from moomoo import KL_FIELD, RET_OK, AuType, KLType
        except ImportError:
            return {"ok": False, "error": "Moomoo SDK is unavailable"}
        for ticker, expiry in pairs:
            result = connection.get_history_kline(
                ticker,
                start=expiry,
                end=expiry,
                ktype=KLType.K_DAY,
                autype=AuType.NONE,
                fields=[KL_FIELD.CLOSE],
                max_count=2,
            )
            if not isinstance(result, tuple) or not result or result[0] != RET_OK or len(result) < 2:
                continue
            close = _close_for_day(result[1], expiry)
            if close is not None:
                closes[(ticker, expiry)] = close

        settled_at = now.isoformat()
        settlements = []
        for row in eligible:
            ticker = str(row.get("ticker") or "").upper()
            expiry_date = _expiry_date(row.get("expiration"))
            close = closes.get((ticker, expiry_date.isoformat())) if expiry_date else None
            if close is None:
                continue
            outcome = _candidate_outcome(row, close)
            if outcome is None:
                continue
            settlements.append(
                {
                    "candidate_id": row["candidate_id"],
                    "settled_at": settled_at,
                    "expiry_close": close,
                    "settlement_source": "moomoo_unadjusted_daily_close",
                    "net_per_share": outcome["net_per_share"],
                    "outcome_label": outcome["outcome_label"],
                    "outcome": outcome,
                }
            )
        count = self.db.settle_shadow_outcomes(settlements)
        return {
            "ok": True,
            "settled": count,
            "pending": max(0, len(candidates) - count),
            "attempted_expirations": len(pairs),
            "missing_close_count": max(0, len(eligible) - len(settlements)),
            "settlement_source": "moomoo_unadjusted_daily_close",
        }


def _expiry_date(value):
    raw = str(value or "").strip()
    try:
        if len(raw) == 8 and raw.isdigit():
            return date.fromisoformat(f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}")
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _close_for_day(frame, expiry):
    if frame is None:
        return None
    try:
        records = frame.to_dict("records")
    except (AttributeError, TypeError):
        return None
    for row in records:
        day = str(row.get("time_key") or row.get("time") or "")[:10]
        close = _finite(row.get("close"))
        if day == expiry and close is not None:
            return close
    return None


def _candidate_outcome(candidate, expiry_close):
    strike = _finite(candidate.get("strike"))
    bid = _finite(candidate.get("bid"))
    option_type = str(candidate.get("option_type") or "").upper()
    if strike is None or bid is None or option_type not in {"PUT", "CALL"}:
        return None
    intrinsic = max(0.0, strike - expiry_close) if option_type == "PUT" else max(0.0, expiry_close - strike)
    net = bid - intrinsic
    if option_type == "PUT":
        capital_per_share = _finite(candidate.get("cash_required"))
        capital_per_share = capital_per_share / 100 if capital_per_share is not None else strike
        label = "put_net"
    else:
        capital_per_share = _finite(candidate.get("stock_price"))
        label = "upside_forgone"
    dte = _finite(candidate.get("dte"))
    if capital_per_share is None or capital_per_share <= 0 or dte is None or dte <= 0:
        return None
    return {
        "net_per_share": round(net, 8),
        "intrinsic_per_share": round(intrinsic, 8),
        "outcome_label": label,
        "capital_return_per_day": net / capital_per_share / dte,
    }


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None
