"""Owner-recorded "taken" links between a recommendation and a manual trade.

Signals-only journal evidence. Fills alone cannot carry this link: this
account's own fills show trades whose strike differs from the suggested one, so
attributing realized P&L to a recommendation needs an explicit, owner-asserted
statement of which recommendation was acted on.

Stored in ``trade_events`` as ``event_type='taken'`` with **no schema change**:
the NOT NULL columns carry the RECOMMENDED contract identity (ticker, option
type, strike, expiration) and ``details`` carries the run id, the lane, and the
traded contract when it was stated.

Provenance is ``owner_recorded`` — never ``verified`` (that means broker
evidence) and never ``inferred`` (that means a temporal guess). A taken link is
a claim by the owner and is labelled as one. ``pnl`` stays NULL: a link is not
income, and an unfilled link must never look like realized P&L.
"""

from __future__ import annotations

import logging
from datetime import datetime

from core.utils import normalize_expiration

logger = logging.getLogger("api.services.taken_links")

TAKEN_EVENT_TYPE = "taken"
PROVENANCE_OWNER_RECORDED = "owner_recorded"
LINK_SCHEMA_VERSION = 1
_OPTION_TYPES = ("CALL", "PUT")
_TRADED_IDENTITY_FIELDS = ("ticker", "option_type", "expiration", "strike")


class TakenLinkError(ValueError):
    """Invalid taken-link input or unusable stored link (route maps to 400)."""


def normalize_contract(ticker, option_type, expiration, strike) -> dict:
    """Canonicalize a contract identity, or raise ``TakenLinkError``.

    Same identity fields the outcome engine keys signals and fills on
    (ticker, expiration, option type, numeric strike) so a link can never be
    split from its recommendation by string formatting drift.
    """
    canonical_ticker = str(ticker or "").strip().upper()
    canonical_type = str(option_type or "").strip().upper()
    canonical_expiration = normalize_expiration(expiration)
    try:
        canonical_strike = float(strike or 0)
    except (TypeError, ValueError):
        canonical_strike = 0.0

    if not canonical_ticker:
        raise TakenLinkError("ticker is required")
    if canonical_type not in _OPTION_TYPES:
        raise TakenLinkError("option_type must be CALL or PUT")
    if len(canonical_expiration) != 8 or not canonical_expiration.isdigit():
        raise TakenLinkError("expiration must be a yyyymmdd date")
    if canonical_strike <= 0:
        raise TakenLinkError("strike must be greater than zero")

    return {
        "ticker": canonical_ticker,
        "option_type": canonical_type,
        "expiration": canonical_expiration,
        "strike": canonical_strike,
    }


def _optional_float(value, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise TakenLinkError(f"traded.{field} must be a number")


def _optional_int(value, field: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise TakenLinkError(f"traded.{field} must be a number")


def normalize_traded(block) -> dict | None:
    """Normalize the owner's traded contract when stated.

    Every field is optional: the owner may record only what they know. Returns
    None when nothing was stated, and only ever returns fields the owner
    actually provided — nothing is invented.
    """
    if block in (None, "", {}):
        return None
    if not isinstance(block, dict):
        raise TakenLinkError("traded must be an object")

    traded: dict = {}
    if block.get("ticker") not in (None, ""):
        traded["ticker"] = str(block["ticker"]).strip().upper()
    if block.get("option_type") not in (None, ""):
        option_type = str(block["option_type"]).strip().upper()
        if option_type not in _OPTION_TYPES:
            raise TakenLinkError("traded.option_type must be CALL or PUT")
        traded["option_type"] = option_type
    if block.get("expiration") not in (None, ""):
        expiration = normalize_expiration(block["expiration"])
        if len(expiration) != 8 or not expiration.isdigit():
            raise TakenLinkError("traded.expiration must be a yyyymmdd date")
        traded["expiration"] = expiration
    if block.get("strike") not in (None, ""):
        strike = _optional_float(block["strike"], "strike")
        if strike <= 0:
            raise TakenLinkError("traded.strike must be greater than zero")
        traded["strike"] = strike
    if block.get("qty") not in (None, ""):
        traded["qty"] = _optional_int(block["qty"], "qty")
    if block.get("price") not in (None, ""):
        price = _optional_float(block["price"], "price")
        if price < 0:
            raise TakenLinkError("traded.price must not be negative")
        traded["price"] = price

    return traded or None


def traded_differs_from_recommendation(recommendation: dict, traded: dict | None) -> bool | None:
    """True/False when the traded contract identity was fully stated, else None.

    ``None`` means "not stated", never "same as recommended" — an unstated trade
    must not be reported as an exact match.
    """
    if not traded:
        return None
    if any(field not in traded for field in _TRADED_IDENTITY_FIELDS):
        return None
    return not (
        traded["ticker"] == recommendation["ticker"]
        and traded["option_type"] == recommendation["option_type"]
        and traded["expiration"] == recommendation["expiration"]
        and abs(float(traded["strike"]) - float(recommendation["strike"])) < 1e-9
    )


def link_key(run_id: str, recommendation: dict) -> str:
    """Deterministic idempotency key for (run, recommended contract)."""
    return "|".join(
        (
            str(run_id).strip(),
            recommendation["ticker"],
            recommendation["option_type"],
            recommendation["expiration"],
            f"{float(recommendation['strike']):.4f}",
        )
    )


def build_link_row(
    *,
    run_id: str,
    lane: str,
    recommendation: dict,
    traded: dict | None,
    env: str,
    account_id: str,
    now_iso: str,
) -> dict:
    """Build the ``trade_events`` row for one taken link (pure)."""
    differs = traded_differs_from_recommendation(recommendation, traded)
    strike_label = f"{recommendation['option_type'][:1]}{recommendation['strike']:g}"
    return {
        "timestamp": now_iso,
        "event_type": TAKEN_EVENT_TYPE,
        "ticker": recommendation["ticker"],
        "option_type": recommendation["option_type"],
        "strike": recommendation["strike"],
        "expiration": recommendation["expiration"],
        "premium_in": 0.0,
        "premium_out": 0.0,
        "pnl": None,  # a link is not income; NULL never becomes a fabricated 0.0
        "leakage": 0.0,
        "reason": (
            f"Owner recorded acting on {recommendation['ticker']} "
            f"{recommendation['expiration']} {strike_label} from run {run_id}"
        ),
        "env": env or "",
        "account_id": account_id or "",
        "provenance": PROVENANCE_OWNER_RECORDED,
        "details": {
            "schema": LINK_SCHEMA_VERSION,
            "link_key": link_key(run_id, recommendation),
            "run_id": str(run_id).strip(),
            "lane": str(lane or ""),
            "recommendation": dict(recommendation),
            "traded": dict(traded) if traded else None,
            "traded_differs_from_recommendation": differs,
            "recorded_at": now_iso,
        },
    }


def link_from_row(event: dict) -> dict | None:
    """Normalize a stored ``taken`` row into a link, or None when unusable.

    A row without a run id or without a decodable recommended contract is
    skipped rather than guessed at: an unattributable link must never silently
    become an attribution.
    """
    if not isinstance(event, dict):
        return None
    details = event.get("details")
    if not isinstance(details, dict):
        return None
    run_id = str(details.get("run_id") or "").strip()
    if not run_id:
        return None
    try:
        recommendation = normalize_contract(
            event.get("ticker"),
            event.get("option_type"),
            event.get("expiration"),
            event.get("strike"),
        )
    except TakenLinkError:
        return None

    traded = details.get("traded")
    if not isinstance(traded, dict):
        traded = None
    key = str(details.get("link_key") or "").strip() or link_key(run_id, recommendation)
    recorded_at = str(details.get("recorded_at") or event.get("timestamp") or "")
    return {
        "link_key": key,
        "run_id": run_id,
        "lane": str(details.get("lane") or ""),
        "recommendation": recommendation,
        "traded": traded,
        "traded_differs_from_recommendation": details.get("traded_differs_from_recommendation"),
        "recorded_at": recorded_at,
        "provenance": str(event.get("provenance") or ""),
        "env": str(event.get("env") or ""),
        "account_id": str(event.get("account_id") or ""),
    }


def read_taken_links(db, env: str, account_id: str, limit: int = 500) -> list[dict]:
    """Read owner-recorded links for one env/account identity (newest first).

    Never raises: a database failure yields no links, and unusable rows are
    counted and skipped rather than reinterpreted.
    """
    if db is None:
        return []
    try:
        rows = db.get_trade_events(
            event_type=TAKEN_EVENT_TYPE,
            limit=max(1, int(limit)),
            env=env,
            account_id=account_id,
        )
    except Exception as exc:  # pragma: no cover - defensive: db read must not abort a read path
        logger.warning("Taken-link read failed: %s", exc)
        return []

    links = []
    skipped = 0
    for row in rows or []:
        link = link_from_row(row)
        if link is None:
            skipped += 1
            continue
        links.append(link)
    if skipped:
        logger.warning("Skipped %d unusable taken-link row(s) for env=%s", skipped, env)
    return links


def record_taken_link(
    db,
    *,
    run_id: str,
    recommendation,
    lane: str = "",
    traded=None,
    env: str = "",
    account_id: str = "",
    now: datetime | None = None,
) -> dict:
    """Persist one owner-recorded link; idempotent per (run, contract).

    ``recommendation`` may be a dict of the signal's contract fields (as stored
    on a snapshot candidate). Returns ``{"link": {...}, "idempotent": bool}``.
    Raises ``TakenLinkError`` for invalid input or an unavailable database.
    """
    if db is None:
        raise TakenLinkError("database unavailable")
    run_id = str(run_id or "").strip()
    if not run_id:
        raise TakenLinkError("run_id is required")
    if isinstance(recommendation, dict):
        canonical = normalize_contract(
            recommendation.get("ticker"),
            recommendation.get("option_type"),
            recommendation.get("expiration"),
            recommendation.get("strike"),
        )
    else:
        raise TakenLinkError("recommendation must be an object")

    traded_normalized = normalize_traded(traded)
    key = link_key(run_id, canonical)

    for existing in read_taken_links(db, env, account_id):
        if existing["link_key"] == key:
            return {"link": existing, "idempotent": True}

    now_iso = (now or datetime.now()).isoformat()
    row = build_link_row(
        run_id=run_id,
        lane=lane,
        recommendation=canonical,
        traded=traded_normalized,
        env=env,
        account_id=account_id,
        now_iso=now_iso,
    )
    db.save_trade_event(row)
    logger.info(
        "Taken link recorded: run=%s contract=%s %s %s%s env=%s",
        run_id,
        canonical["ticker"],
        canonical["expiration"],
        canonical["option_type"][:1],
        canonical["strike"],
        env,
    )
    link = link_from_row(row)
    return {"link": link, "idempotent": False}
