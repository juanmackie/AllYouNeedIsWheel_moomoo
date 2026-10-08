"""Broker-verified outcome summary service (read-only serving).

Turns ingested broker fills (``option_fills``, schema v10) plus the immutable
published run snapshots (recommendation signals) into owner-facing outcome
aggregates:

- quoted (recommended) credit vs. filled credit, and the slippage between them;
- option-leg net results after fees (unknown fees ⇒ unknown outcome, never zero);
- capital-days and net P&L / capital-day (owner efficiency) via the pure
  ``core.outcome_attribution`` helpers;
- wheel-cycle option + share P&L when a full Moomoo fill chain proves it;
- groups by preset, DTE bucket, ticker, and event tier (display-only tiers —
  they never gate or reorder anything).

Field semantics (all money is option-leg, dollars-per-contract unless stated):

- Units: ``quoted_credit_per_contract``/``filled_credit_per_contract`` and
  ``slippage_per_contract`` are dollars-per-contract. Broker fill prices are
  per-share and are converted exactly once in ``_fill_credit_per_contract``
  (via ``core.outcome_attribution.broker_price_to_contract_credit``), so a $200
  quoted contract credit filled at $2/share is $200 of credit, never -$198.
- Realization: ``net_pnl``/``gross_premium_pnl`` are REALIZED option-leg
  results only. A position with any unclosed short obligation contributes its
  collected premium as ``unrealized_premium_dollars`` context (and
  ``collected_premium_dollars``), never into ``net_pnl``.
- Unsupported legs stay unknown (never 0, never fabricated): expiration
  payoff, assignment, share-basis (underlying) P&L, and open-position
  mark-to-market losses. ``pnl_scope="option_leg"`` marks every supported
  result so the panel can label it.
- Attribution: a fill run is tied to a recommendation only when that
  recommendation's timestamp precedes the first fill (``attribution=
  "inferred"``). Ambiguous (multiple candidate recommendations) or
  fill-before-any-recommendation runs are ``attribution="unattributed"`` and
  surfaced as matched evidence only, like the existing unmatched path. An
  owner-recorded taken link (``api.services.taken_links``) is explicit owner
  evidence rather than inference: when exactly one link resolves to a stored
  recommendation, the traded contract is attributed to it with
  ``attribution="owner-linked"`` — including when the owner traded a different
  strike. Ambiguous or unresolvable links never attribute; the link itself is
  still surfaced on the record so nothing is silently dropped.

Every aggregate exposes sample size, coverage %, and unknown-outcome count,
and every outcome record carries the supporting fill transactions for
drill-down. Attribution here covers the option leg (fills-only); share-leg
attribution (assignment cost basis) lands with the position-diff
reconciliation stage and is never fabricated in the meantime.

All matching/aggregation helpers at module level are pure and deterministic —
unit-testable without Flask, the database, or a broker connection.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from api.services.recommendation_ranking import rank_candidates
from api.services.taken_links import read_taken_links
from core.outcome_attribution import (
    broker_price_to_contract_credit,
    capital_base_cc,
    capital_base_csp,
    capital_days_from_lots,
    combined_outcome,
    owner_efficiency,
    owner_summary,
    parse_timestamp,
    slippage_dollars,
)
from core.utils import market_now, safe_float
from core.wheel_cycle import classify_fills, realized_cycle_outcome

logger = logging.getLogger("api.services.outcomes")

# Fixed DTE bucket edges (inclusive upper bound, label).
_DTE_BUCKET_EDGES = (
    (7, "0-7"),
    (14, "8-14"),
    (30, "15-30"),
    (45, "31-45"),
    (60, "46-60"),
    (90, "61-90"),
)

_UNTIERED = "untiered"
TRADE_LINK_SUGGESTION_WINDOW_DAYS = 7
_ET = ZoneInfo("America/New_York")

# Explicit owner evidence that a manual trade came from a recommendation
# (``api.services.taken_links``) — distinct from the temporal "inferred" match.
ATTRIBUTION_OWNER_LINKED = "owner-linked"


def dte_bucket(dte) -> str:
    """Fixed DTE bucket label; unknown DTE never falls into a real bucket."""
    if dte is None:
        return "unknown"
    try:
        value = int(dte)
    except (TypeError, ValueError):
        return "unknown"
    for edge, label in _DTE_BUCKET_EDGES:
        if value <= edge:
            return label
    return "90+"


def normalize_expiration_key(value) -> str:
    """Canonical yyyymmdd expiration for join keys (mirrors options_data)."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10].replace("-", "")
    return text.replace("-", "")


def signal_identity(signal) -> tuple | None:
    """Canonical contract identity for a recommendation signal, or None.

    Identity is (ticker, expiration, option_type, strike) with numeric strike
    comparison so string-formatting drift (``70`` vs ``70.0``) can never
    split one contract into two outcomes.
    """
    if not isinstance(signal, dict):
        return None
    ticker = str(signal.get("ticker", "") or "").strip().upper()
    option_type = str(signal.get("option_type", "") or "").strip().upper()
    expiration = normalize_expiration_key(signal.get("expiration"))
    strike = safe_float(signal.get("strike"), default=None)
    if not ticker or option_type not in ("CALL", "PUT") or len(expiration) != 8 or strike is None:
        return None
    return (ticker, expiration, option_type, round(strike, 4))


def fill_identity(fill) -> tuple | None:
    """Canonical contract identity for an option fill row, or None."""
    if not isinstance(fill, dict):
        return None
    if str(fill.get("security_type", "") or "").upper() != "OPT":
        return None
    ticker = str(fill.get("ticker", "") or "").strip().upper()
    option_type = str(fill.get("option_type", "") or "").strip().upper()
    expiration = normalize_expiration_key(fill.get("expiration"))
    strike = safe_float(fill.get("strike"), default=None)
    if not ticker or option_type not in ("CALL", "PUT") or len(expiration) != 8 or strike is None:
        return None
    return (ticker, expiration, option_type, round(strike, 4))


def build_link_suggestions(
    run_snapshots, option_fills, taken_links=None, window_days=TRADE_LINK_SUGGESTION_WINDOW_DAYS
):
    """Suggest, but never record, links for nearby same-ticker/side short fills.

    A human must confirm each suggestion through the existing owner-link route.
    Missing timestamps, non-option fills, non-SELL fills, and existing links are
    excluded rather than guessed. Partial fills of the same contract are grouped.
    """
    try:
        window = max(0.0, float(window_days))
    except (TypeError, ValueError):
        return []
    already_linked = set()
    for link in taken_links or []:
        if not isinstance(link, dict):
            continue
        identity = signal_identity(link.get("recommendation"))
        run_id = str(link.get("run_id") or "").strip()
        if identity is not None and run_id:
            already_linked.add((run_id, identity))

    candidate_runs = []
    for snapshot in run_snapshots or []:
        if not isinstance(snapshot, dict):
            continue
        run = snapshot.get("run") or {}
        run_id = str(run.get("run_id") or "").strip()
        generated_at = str(run.get("generated_at") or "")
        run_time = parse_timestamp(generated_at)
        if not run_id or run_time is None:
            continue
        candidates = {}
        for lane in ("signals", "csp_picks", "cc_decisions"):
            signals = snapshot.get(lane)
            if not isinstance(signals, list):
                continue
            for signal in signals:
                identity = signal_identity(signal)
                if identity is not None and (run_id, identity) not in already_linked:
                    candidates.setdefault(identity, signal)
        if candidates:
            candidate_runs.append((run_id, generated_at, run_time, candidates))

    grouped = {}
    for fill in option_fills or []:
        identity = fill_identity(fill)
        if identity is None or str(fill.get("side") or "").upper() != "SELL":
            continue
        fill_time = parse_timestamp(fill.get("captured_at"))
        if fill_time is None:
            continue
        ticker, _expiration, option_type, _strike = identity
        for run_id, generated_at, run_time, candidates in candidate_runs:
            delta_days = (fill_time.date() - run_time.date()).days
            if abs(delta_days) > window:
                continue
            for recommendation_identity, recommendation in candidates.items():
                if (recommendation_identity[0], recommendation_identity[2]) != (ticker, option_type):
                    continue
                if recommendation_identity == identity:
                    continue
                key = (run_id, recommendation_identity, identity)
                suggestion = grouped.setdefault(
                    key,
                    {
                        "run_id": run_id,
                        "run_generated_at": generated_at,
                        "recommendation": {
                            "ticker": recommendation_identity[0],
                            "expiration": recommendation_identity[1],
                            "option_type": recommendation_identity[2],
                            "strike": recommendation_identity[3],
                        },
                        "traded": {
                            "ticker": identity[0],
                            "expiration": identity[1],
                            "option_type": identity[2],
                            "strike": identity[3],
                        },
                        "fill_count": 0,
                        "_qty": 0.0,
                        "_weighted_price": 0.0,
                        "_best_distance": None,
                    },
                )
                qty = abs(safe_float(fill.get("qty"), 0.0))
                price = safe_float(fill.get("price"), 0.0)
                suggestion["fill_count"] += 1
                suggestion["_qty"] += qty
                suggestion["_weighted_price"] += qty * price
                if suggestion["_best_distance"] is None or abs(delta_days) < abs(suggestion["_best_distance"]):
                    suggestion["_best_distance"] = delta_days

    results = []
    for suggestion in grouped.values():
        qty = suggestion.pop("_qty")
        weighted_price = suggestion.pop("_weighted_price")
        delta_days = suggestion.pop("_best_distance") or 0.0
        if qty > 0:
            suggestion["traded"]["qty"] = qty
            suggestion["traded"]["price"] = weighted_price / qty
        suggestion["days_from_run"] = round(delta_days, 2)
        results.append(suggestion)
    return sorted(
        results,
        key=lambda item: (
            abs(item["days_from_run"]),
            item["run_generated_at"],
            item["recommendation"]["ticker"],
            item["recommendation"]["option_type"],
            item["recommendation"]["expiration"],
            item["recommendation"]["strike"],
        ),
    )


SAVED_CANDIDATE_DTE_MIN = 21
SAVED_CANDIDATE_DTE_MAX = 45


def build_dte_comparisons(run_snapshots, option_fills, window_days=TRADE_LINK_SUGGESTION_WINDOW_DAYS) -> list[dict]:
    """Compare recent owner short fills with saved candidates in the DTE window.

    This uses only candidates retained in each published snapshot; it never
    fetches chains or claims to rank against contracts the run did not retain.
    A missing saved candidate is returned explicitly as unavailable.
    """
    try:
        window = max(0, int(window_days))
    except (TypeError, ValueError):
        return []

    comparisons = []
    seen = set()
    for fill in option_fills or []:
        identity = fill_identity(fill)
        if identity is None or str(fill.get("side") or "").upper() != "SELL":
            continue
        fill_time = parse_timestamp(fill.get("captured_at"))
        if fill_time is None:
            continue
        try:
            expiration_date = datetime.strptime(identity[1], "%Y%m%d").date()
        except ValueError:
            continue
        traded_dte = (expiration_date - fill_time.date()).days
        if not SAVED_CANDIDATE_DTE_MIN <= traded_dte <= SAVED_CANDIDATE_DTE_MAX:
            continue

        for snapshot in run_snapshots or []:
            if not isinstance(snapshot, dict):
                continue
            run = snapshot.get("run") or {}
            run_id = str(run.get("run_id") or "").strip()
            generated_at = str(run.get("generated_at") or "")
            run_time = parse_timestamp(generated_at)
            if not run_id or run_time is None:
                continue
            days_from_run = (fill_time.date() - run_time.date()).days
            if abs(days_from_run) > window:
                continue
            fill_key = (run_id, identity)
            if fill_key in seen:
                continue
            seen.add(fill_key)

            candidates_by_identity = {}
            for lane in ("signals", "csp_picks", "cc_decisions"):
                candidates = snapshot.get(lane)
                if not isinstance(candidates, list):
                    continue
                for candidate in candidates:
                    candidate_identity = signal_identity(candidate)
                    if candidate_identity is not None:
                        candidates_by_identity.setdefault(candidate_identity, candidate)

            matching = []
            ticker, _expiration, option_type, _strike = identity
            for candidate in candidates_by_identity.values():
                candidate_dte = safe_float(candidate.get("dte"), default=None)
                if (
                    str(candidate.get("ticker") or "").upper() == ticker
                    and str(candidate.get("option_type") or "").upper() == option_type
                    and candidate_dte is not None
                    and SAVED_CANDIDATE_DTE_MIN <= candidate_dte <= SAVED_CANDIDATE_DTE_MAX
                ):
                    matching.append(candidate)
            ranked = rank_candidates(matching)
            best = None
            if ranked:
                candidate = ranked[0]
                best = {
                    "ticker": str(candidate.get("ticker") or "").upper(),
                    "option_type": str(candidate.get("option_type") or "").upper(),
                    "expiration": normalize_expiration_key(candidate.get("expiration")),
                    "strike": safe_float(candidate.get("strike"), default=0.0),
                    "dte": int(safe_float(candidate.get("dte"), default=0.0)),
                    "saved_sample_rank": 1,
                    "capital_velocity_per_day": safe_float(candidate.get("capital_velocity_per_day"), default=None),
                }
            comparisons.append(
                {
                    "run_id": run_id,
                    "run_generated_at": generated_at,
                    "days_from_run": days_from_run,
                    "traded": {
                        "ticker": ticker,
                        "option_type": option_type,
                        "expiration": identity[1],
                        "strike": identity[3],
                        "dte": traded_dte,
                    },
                    "candidate": best,
                    "reason": "" if best else "no_saved_candidate_in_21_45_dte_window",
                    "sample_scope": "saved_shortlist_only",
                }
            )
    return sorted(comparisons, key=lambda item: (item["run_generated_at"], item["traded"]["ticker"]))


def quoted_credit_per_contract(signal):
    """The recommended executable credit: bid → limit target → premium."""
    for key in ("bid_premium_per_contract", "limit_target_per_contract", "premium_per_contract"):
        value = safe_float((signal or {}).get(key), default=None)
        if value is not None and value > 0:
            return value
    return None


def signal_type_of(signal) -> str:
    """Display strategy label from the signal (csp / covered_call / other)."""
    if not isinstance(signal, dict):
        return ""
    label = str(signal.get("signal_type", "") or "").strip().lower()
    if label:
        return label
    option_type = str(signal.get("option_type", "") or "").strip().upper()
    return {"CALL": "covered_call", "PUT": "csp"}.get(option_type, option_type.lower())


def _capital_per_contract(signal, option_type: str, strike: float):
    """Per-contract deployed capital, or None when it cannot be known.

    CSP: strike × 100 (cash secured). Covered call: share price × 100 —
    requires the signal's stock_price; without it capital-days stay unknown.
    """
    if option_type == "PUT":
        return capital_base_csp(strike, 1)
    stock_price = safe_float((signal or {}).get("stock_price"), default=None)
    if stock_price is None or stock_price <= 0:
        return None
    return capital_base_cc(stock_price, 1)


def _fifo_lots(entry_fills, close_fills):
    """Match closing BUY fills against entry SELL fills FIFO.

    Returns (lots, open_qty, closed_qty) where lots are
    (entry_ts, exit_ts | None, qty): one lot per exited tranche plus one open
    lot for any remainder — per-tranche capital-days, never a single average.
    Buy quantity beyond what was sold (exercise/assignment movement) is not
    fabricated into a close.
    """
    entries = deque(
        (str(fill.get("captured_at", "") or ""), float(fill.get("qty", 0) or 0))
        for fill in sorted(
            entry_fills, key=lambda f: (str(f.get("captured_at", "") or ""), str(f.get("fill_id", "") or ""))
        )
        if float(fill.get("qty", 0) or 0) > 0
    )
    lots = []
    closed_qty = 0.0
    for close in sorted(
        close_fills, key=lambda f: (str(f.get("captured_at", "") or ""), str(f.get("fill_id", "") or ""))
    ):
        remaining = float(close.get("qty", 0) or 0)
        exit_ts = str(close.get("captured_at", "") or "")
        if not exit_ts:
            continue
        while remaining > 0 and entries:
            entry_ts, entry_qty = entries[0]
            matched = min(remaining, entry_qty)
            lots.append((entry_ts, exit_ts, matched))
            closed_qty += matched
            remaining -= matched
            if matched >= entry_qty:
                entries.popleft()
            else:
                entries[0] = (entry_ts, entry_qty - matched)
    open_qty = sum(qty for _, qty in entries)
    lots.extend((entry_ts, None, qty) for entry_ts, qty in entries)
    return lots, open_qty, closed_qty


def _fill_transaction(fill) -> dict:
    """Drill-down view of one supporting fill.

    ``price`` stays the raw broker per-share price (fidelity);
    ``price_per_contract`` is the standardized dollars-per-contract conversion
    (the same single conversion used for every credit figure) so the drill-down
    renders in the panel's per-contract unit.
    """
    return {
        "fill_id": fill.get("fill_id", ""),
        "order_id": fill.get("order_id", ""),
        "captured_at": fill.get("captured_at", ""),
        "side": fill.get("side", ""),
        "qty": fill.get("qty", 0),
        "price": fill.get("price", 0),
        "price_per_contract": _fill_credit_per_contract(fill),
        "fees": fill.get("fees"),
        "security_type": fill.get("security_type", ""),
    }


def _fill_credit_per_contract(fill):
    """Dollars-per-contract credit of one option fill.

    THE single per-share → per-contract conversion for ingested broker fill
    prices. Every downstream credit/slippage/premium figure derives from this
    exact function so units can never mix — a $200 quoted contract credit
    filled at $2/share is $200, never a -$198 slippage.
    """
    return broker_price_to_contract_credit(fill.get("price"))


def _sort_fills(fills):
    """Chronological fill order (stable by captured_at, then fill_id)."""
    return sorted(fills, key=lambda f: (str(f.get("captured_at", "") or ""), str(f.get("fill_id", "") or "")))


def _realization_split(entry_fills, close_fills, closed_qty):
    """Split option-leg premiums and fees into realized vs. unrealized dollars.

    Entries are matched FIFO to closes (same ordering as ``_fifo_lots``): the
    first ``closed_qty`` contracts sold are realized (their entry credit is
    realized premium-in); the remainder stay open obligations whose collected
    premium is unrealized context and never enters realized P&L. On partial
    closes an entry fill's fee is split proportionally to the consumed qty;
    all buyback fees are realized (buybacks only ever close realized tranches).

    Returns (realized_premium_in, realized_premium_out, realized_fees,
    unrealized_premium) in dollars.
    """
    realized_in = 0.0
    realized_fees = 0.0
    unrealized = 0.0
    remaining = closed_qty

    for fill in _sort_fills(entry_fills):
        qty = float(fill.get("qty", 0) or 0)
        if qty <= 0:
            continue
        credit = _fill_credit_per_contract(fill)
        fee = float(fill.get("fees") or 0.0)
        if remaining > 0:
            take = min(remaining, qty)
            realized_in += credit * take
            realized_fees += fee * (take / qty)
            remaining -= take
            qty -= take
        if qty > 0:
            unrealized += credit * qty

    realized_out = sum(_fill_credit_per_contract(f) * float(f.get("qty", 0) or 0) for f in _sort_fills(close_fills))
    realized_fees += sum(float(f.get("fees") or 0.0) for f in _sort_fills(close_fills))
    return realized_in, realized_out, realized_fees, unrealized


def _terminalize_open_lots(lots, terminal_at, terminal_contracts):
    """Stop matched open option lots at an evidenced assignment/expiry horizon."""
    remaining = max(0.0, safe_float(terminal_contracts, default=0.0))
    result = []
    for entry_at, exit_at, quantity in lots:
        qty = max(0.0, safe_float(quantity, default=0.0))
        if exit_at is not None or remaining <= 0:
            result.append((entry_at, exit_at, qty))
            continue
        ended = min(qty, remaining)
        if ended > 0:
            result.append((entry_at, terminal_at, ended))
            remaining -= ended
        if qty > ended:
            result.append((entry_at, None, qty - ended))
    open_remaining = sum(qty for _, exit_at, qty in result if exit_at is None)
    return result, open_remaining


def _cycle_outcome(terminal_state, event, premium_in, fees):
    if terminal_state not in {"assigned_put", "called_away"}:
        return {"status": "unknown", "net_pnl": None}
    option_leg = None
    if fees is not None:
        option_leg = {"premium_in": premium_in, "premium_out": 0.0, "fees": fees}
    share_leg = {
        "basis_price": None,
        "exit_price": None,
        "shares": safe_float((event or {}).get("shares"), default=0.0),
        "fees": None,
    }
    return combined_outcome(option_leg, share_leg)


def _recommendation_precedes_first_fill(candidate, first_fill_timestamp) -> bool:
    """True only when the recommendation's timestamp strictly precedes the
    first fill of the run. Unparseable timestamps can never confirm precedence
    (never attribute on a guess)."""
    generated = parse_timestamp(candidate.get("generated_at"))
    first = parse_timestamp(first_fill_timestamp)
    if generated is None or first is None:
        return False
    return generated < first


def _select_candidate(candidates, fills) -> tuple[dict | None, str]:
    """Pick the recommendation that may explain a fill run, or None.

    Returns (selected_meta, attribution) where attribution is:
    - "inferred": exactly one candidate whose timestamp precedes the first
      fill (a temporal match — labelled inferred, not direct evidence);
    - "unattributed": no fills (pending keeps its candidate with empty
      attribution), no candidates, several candidates that could explain the
      run (ambiguous), or every candidate comes after / at the first fill
      (fills precede any recommendation). Unattributed runs are surfaced as
      matched evidence only, exactly like the unmatched path.
    """
    candidate_dicts = [candidate for candidate in candidates if isinstance(candidate, dict)]
    if not candidate_dicts:
        return None, "unattributed"
    if not fills:
        return min(candidate_dicts, key=lambda c: str(c.get("generated_at", "") or "")), ""
    first_ts = min((str(f.get("captured_at", "") or "") for f in fills), default="")
    qualifiers = [c for c in candidate_dicts if _recommendation_precedes_first_fill(c, first_ts)]
    if len(qualifiers) == 1:
        return qualifiers[0], "inferred"
    return None, "unattributed"


def _owner_link_index(taken_links) -> dict[tuple, list]:
    """Map each fill identity an owner-recorded link can explain to its link(s).

    A link explains the contract the owner stated they traded, otherwise the
    recommended contract it names. Multiple links for one identity are kept —
    ambiguity must be detected, never resolved by guessing.
    """
    index: dict[tuple, list] = {}
    for link in taken_links or []:
        if not isinstance(link, dict):
            continue
        traded = link.get("traded")
        target = None
        if isinstance(traded, dict) and all(
            traded.get(field) not in (None, "") for field in ("ticker", "option_type", "expiration", "strike")
        ):
            target = signal_identity(traded)
        if target is None:
            recommendation = link.get("recommendation")
            target = signal_identity(recommendation if isinstance(recommendation, dict) else {})
        if target is None:
            continue
        index.setdefault(target, []).append(link)
    return index


def _best_owner_candidate(candidates) -> dict | None:
    """Most evidenced stored recommendation for one identity (deterministic)."""
    if not candidates:
        return None
    with_credit = [c for c in candidates if quoted_credit_per_contract(c.get("signal")) is not None]
    return (with_credit or candidates)[0]


def _resolve_owner_link(links, candidates_by_run_identity) -> tuple[dict | None, dict | None]:
    """Resolve one unambiguous owner link to the recommendation it names.

    Returns ``(link, meta)``. ``meta`` is None when the link cannot be resolved
    to a stored recommendation (its run or contract is no longer in the
    snapshot history) or when several links claim the same fill: the link is
    still surfaced as context, but nothing is attributed.
    """
    if len(links) != 1:
        return None, None
    link = links[0]
    recommendation = link.get("recommendation")
    identity = signal_identity(recommendation) if isinstance(recommendation, dict) else None
    run_id = str(link.get("run_id") or "")
    if identity is None or not run_id:
        return link, None
    return link, _best_owner_candidate(candidates_by_run_identity.get((run_id, identity)))


def _link_context(link) -> dict:
    """Owner-link context attached to a record (invents nothing)."""
    if not isinstance(link, dict):
        return {}
    recommendation = link.get("recommendation")
    traded = link.get("traded")
    return {
        "run_id": str(link.get("run_id") or ""),
        "recommendation": recommendation if isinstance(recommendation, dict) else {},
        "traded": traded if isinstance(traded, dict) else None,
        "traded_differs_from_recommendation": link.get("traded_differs_from_recommendation"),
        "recorded_at": str(link.get("recorded_at") or ""),
    }


def build_outcome_records(
    run_snapshots, option_fills, now=None, taken_links=None, portfolio_snapshots=None
) -> list[dict]:
    """Join recommendation signals to option fills and attribute outcomes.

    ``run_snapshots``: published snapshot dicts (any order; recommendations
    kept at full cardinality per identity so attribution can detect
    ambiguity). ``option_fills``: fill rows for the same identity scope. Stock
    fills may be included as assignment evidence; they are never included in
    option outcome records.
    ``now``: as-of for open-lot capital days. ``taken_links``: owner-recorded
    links stating which recommendation a manual trade came from; an
    unambiguous link that resolves to a stored recommendation attributes the
    traded contract to it (``owner-linked``). Returns one record per unique
    contract identity — the signal identity when a recommendation can be
    attributed, otherwise an unattributed evidence record for orphan fills
    (never dropped).
    """
    now = now or market_now().replace(tzinfo=None)

    # All candidate recommendations per identity (across signals + pick
    # lists), kept at full cardinality — never deduped early — because
    # attribution must detect ambiguity (multiple recs before the first fill).
    # The same candidates are indexed by (run, identity) so an owner link can
    # resolve the exact recommendation it names, including when the traded
    # contract differs from the recommended one.
    signals_by_identity: dict[tuple, list] = {}
    candidates_by_run_identity: dict[tuple, list] = {}
    snapshots = sorted(
        [s for s in run_snapshots if isinstance(s, dict)],
        key=lambda s: str((s.get("run") or {}).get("generated_at", "") or ""),
    )
    for snapshot in snapshots:
        run = snapshot.get("run") or {}
        preset_key = str(run.get("preset_key", "") or "")
        generated_at = str(run.get("generated_at", "") or "")
        run_id = str(run.get("run_id", "") or "")
        candidates = []
        for key in ("signals", "csp_picks", "cc_decisions"):
            value = snapshot.get(key)
            if isinstance(value, list):
                candidates.extend(item for item in value if isinstance(item, dict))
        for signal in candidates:
            identity = signal_identity(signal)
            if identity is None:
                continue
            meta = {
                "signal": signal,
                "preset_key": preset_key,
                "generated_at": generated_at,
                "run_id": run_id,
            }
            signals_by_identity.setdefault(identity, []).append(meta)
            candidates_by_run_identity.setdefault((run_id, identity), []).append(meta)

    all_fills = list(option_fills or [])
    assignment_buy_ids = _assignment_zero_buy_ids(all_fills)
    terminal_events = {}
    for event in classify_fills(all_fills, today=market_now().date()):
        if event.get("event_type") not in {"assigned_put", "called_away"}:
            continue
        identity = _event_identity(event)
        if identity is not None:
            terminal_events[identity] = event
    for event in _expired_snapshot_events(portfolio_snapshots):
        identity = _event_identity(event)
        if identity is not None and identity not in terminal_events:
            terminal_events[identity] = event

    # Option fills grouped by contract identity. Stock fills above are only
    # pairing evidence; they never become option outcome rows.
    fills_by_identity: dict[tuple, list] = {}
    for fill in all_fills:
        identity = fill_identity(fill)
        if identity is None:
            continue
        fills_by_identity.setdefault(identity, []).append(fill)

    links_by_identity = _owner_link_index(taken_links)

    def _record_for(identity, fills, candidates) -> dict:
        """Build one record, preferring explicit owner evidence over inference."""
        ticker, expiration, option_type, strike = identity
        links = links_by_identity.get(identity) or []
        link, linked_meta = _resolve_owner_link(links, candidates_by_run_identity)
        if linked_meta is not None:
            record = _build_record(
                linked_meta["signal"],
                linked_meta,
                ticker,
                expiration,
                option_type,
                strike,
                fills,
                now,
                assignment_buy_ids,
                terminal_events.get(identity),
            )
            record["attribution"] = ATTRIBUTION_OWNER_LINKED
        else:
            meta, attribution = _select_candidate(candidates, fills)
            if attribution != "unattributed" and meta is not None:
                record = _build_record(
                    meta["signal"],
                    meta,
                    ticker,
                    expiration,
                    option_type,
                    strike,
                    fills,
                    now,
                    assignment_buy_ids,
                    terminal_events.get(identity),
                )
                record["attribution"] = attribution
            else:
                # Orphan/unattributed fills are still real transactions: surfaced
                # as matched evidence only, never silently dropped, never given a
                # fabricated quoted price or a recommendation identity.
                empty_meta = {"signal": None, "preset_key": "", "generated_at": "", "run_id": ""}
                record = _build_record(
                    None,
                    empty_meta,
                    ticker,
                    expiration,
                    option_type,
                    strike,
                    fills,
                    now,
                    assignment_buy_ids,
                    terminal_events.get(identity),
                )
                record["signal_type"] = "unmatched"
                record["attribution"] = "unattributed"
        if link is not None:
            # The owner's claim is surfaced even when it could not be resolved to
            # a stored recommendation — visible, never silently promoted.
            record["owner_link"] = _link_context(link)
        elif len(links) > 1:
            record["owner_link_conflict"] = sorted(str(entry.get("run_id") or "") for entry in links)
        return record

    records = []
    for identity, candidates in list(signals_by_identity.items()):
        records.append(_record_for(identity, fills_by_identity.pop(identity, []), candidates))

    # Fills with no stored signal at all are still real transactions; they are
    # surfaced as unattributed evidence, never silently dropped and never
    # given a fabricated quoted price. An owner link may still attribute them.
    for identity, fills in fills_by_identity.items():
        records.append(_record_for(identity, fills, []))

    records.sort(key=lambda r: (str(r.get("first_recommended_at", "") or ""), r["identity"]))
    return records


def _assignment_zero_buy_ids(fills) -> set[int]:
    """Find zero-price option buys paired with a nearby underlying share buy.

    Assignment transfers 100 shares per option contract. Requiring the same
    underlying, a five-second timestamp window, and matching share quantity
    avoids treating an unrelated stock purchase as evidence for a zero-price
    option close.
    """
    share_buys_by_ticker: dict[str, list[dict]] = {}
    for fill in fills:
        if (
            str(fill.get("security_type", "") or "").upper() != "STK"
            or str(fill.get("side", "") or "").upper() != "BUY"
        ):
            continue
        ticker = str(fill.get("ticker", "") or "").strip().upper()
        timestamp = parse_timestamp(fill.get("captured_at"))
        quantity = safe_float(fill.get("qty"), default=0.0)
        if ticker and timestamp is not None and quantity > 0:
            share_buys_by_ticker.setdefault(ticker, []).append({"timestamp": timestamp, "remaining_qty": quantity})

    paired_buys = set()
    option_buys = sorted(
        fills,
        key=lambda fill: parse_timestamp(fill.get("captured_at")) or datetime.min,
    )
    for fill in option_buys:
        if (
            str(fill.get("security_type", "") or "").upper() != "OPT"
            or str(fill.get("side", "") or "").upper() != "BUY"
            or safe_float(fill.get("price"), default=None) != 0
        ):
            continue
        ticker = str(fill.get("ticker", "") or "").strip().upper()
        timestamp = parse_timestamp(fill.get("captured_at"))
        contracts = safe_float(fill.get("qty"), default=0.0)
        if not ticker or timestamp is None or contracts <= 0:
            continue
        shares_required = contracts * 100
        candidates = [
            stock_buy
            for stock_buy in share_buys_by_ticker.get(ticker, [])
            if stock_buy["remaining_qty"] >= shares_required
            and abs((stock_buy["timestamp"] - timestamp).total_seconds()) <= 5
        ]
        if candidates:
            match = min(candidates, key=lambda stock_buy: abs((stock_buy["timestamp"] - timestamp).total_seconds()))
            paired_buys.add(id(fill))
            match["remaining_qty"] -= shares_required
    return paired_buys


def _event_identity(event):
    ticker = str(event.get("ticker") or "").strip().upper()
    option_type = str(event.get("option_type") or "").strip().upper()
    expiration = normalize_expiration_key(event.get("expiration"))
    strike = safe_float(event.get("strike"), default=None)
    if not ticker or option_type not in {"CALL", "PUT"} or len(expiration) != 8 or strike is None:
        return None
    return ticker, expiration, option_type, round(strike, 4)


def _snapshot_market_datetime(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(_ET).replace(tzinfo=None)


def _position_identity(position):
    if not isinstance(position, dict) or str(position.get("security_type") or "").upper() != "OPT":
        return None
    if safe_float(position.get("qty"), default=0.0) >= 0:
        return None
    ticker = str(position.get("underlying") or "").strip().upper()
    if not ticker:
        ticker = str(position.get("symbol") or "").strip().upper()
    return _event_identity(
        {
            "ticker": ticker,
            "option_type": position.get("option_type"),
            "expiration": position.get("expiration"),
            "strike": position.get("strike"),
        }
    )


def _expired_snapshot_events(portfolio_snapshots):
    """Infer a capital-days stop only from a short present before expiry and absent after it.

    The result deliberately remains ``expired_unverified``: portfolio-book
    absence closes the position horizon but cannot prove its expiry payoff.
    """
    snapshots = [row for row in portfolio_snapshots or [] if isinstance(row, dict)]
    snapshots.sort(key=lambda row: _snapshot_market_datetime(row.get("captured_at")) or datetime.min)
    events = []
    for previous, current in zip(snapshots, snapshots[1:]):
        previous_at = _snapshot_market_datetime(previous.get("captured_at"))
        current_at = _snapshot_market_datetime(current.get("captured_at"))
        current_positions = current.get("positions")
        if (
            previous_at is None
            or current_at is None
            or not isinstance(current_positions, list)
            or not current_positions
        ):
            continue
        current_ids = {_position_identity(row) for row in current_positions}
        current_ids.discard(None)
        previous_positions = previous.get("positions")
        if not isinstance(previous_positions, list):
            continue
        for position in previous_positions:
            identity = _position_identity(position)
            if identity is None or identity in current_ids:
                continue
            ticker, expiration, option_type, strike = identity
            try:
                expiration_date = datetime.strptime(expiration, "%Y%m%d").date()
            except ValueError:
                continue
            cutoff = datetime.combine(expiration_date, time(16, 0))
            if previous_at >= cutoff or current_at < cutoff:
                continue
            events.append(
                {
                    "event_type": "expired_unverified",
                    "ticker": ticker,
                    "option_type": option_type,
                    "expiration": expiration,
                    "strike": strike,
                    "shares": 0,
                    "contracts": abs(safe_float(position.get("qty"), default=0.0)),
                    "timestamp": cutoff.isoformat(),
                    "fees_known": False,
                    "provisional": True,
                }
            )
    return events


def _build_record(
    signal, meta, ticker, expiration, option_type, strike, fills, now, assignment_buy_ids=None, terminal_event=None
) -> dict:
    quoted = quoted_credit_per_contract(signal) if signal is not None else None
    dte = signal.get("dte") if signal is not None else None

    entry_fills = [f for f in fills if str(f.get("side", "") or "").upper() == "SELL"]
    assignment_buy_ids = assignment_buy_ids or set()
    close_fills = [
        f for f in fills if str(f.get("side", "") or "").upper() == "BUY" and id(f) not in assignment_buy_ids
    ]

    contracts_sold = sum(float(f.get("qty", 0) or 0) for f in entry_fills)
    contracts_bought = sum(float(f.get("qty", 0) or 0) for f in close_fills)

    lots, open_contracts, closed_qty = _fifo_lots(entry_fills, close_fills)
    terminal_state = ""
    terminal_at = None
    if open_contracts > 0 and isinstance(terminal_event, dict):
        event_type = str(terminal_event.get("event_type") or "")
        event_contracts = safe_float(terminal_event.get("contracts"), default=None)
        if event_contracts is None and event_type in {"assigned_put", "called_away"}:
            event_contracts = safe_float(terminal_event.get("shares"), default=0.0) / 100.0
        terminal_at = str(terminal_event.get("timestamp") or "")
        if event_type in {"assigned_put", "called_away", "expired_unverified"} and terminal_at and event_contracts:
            lots, open_contracts = _terminalize_open_lots(lots, terminal_at, event_contracts)
            if open_contracts == 0:
                terminal_state = event_type

    # All option-leg money derives from the single per-share → per-contract
    # conversion in ``_fill_credit_per_contract``; never mix units again.
    filled_credit_per_contract = None
    collected_premium_dollars = 0.0
    if contracts_sold > 0:
        collected_premium_dollars = sum(
            _fill_credit_per_contract(f) * float(f.get("qty", 0) or 0) for f in _sort_fills(entry_fills)
        )
        filled_credit_per_contract = collected_premium_dollars / contracts_sold

    realized_premium_in, realized_premium_out, realized_fees, unrealized_premium_dollars = _realization_split(
        entry_fills, close_fills, closed_qty
    )

    slippage_per_contract = None
    slippage_dollars_total = None
    if filled_credit_per_contract is not None and quoted is not None:
        # Both operands are dollars-per-contract, so slippage_dollars is exact.
        slippage_per_contract = filled_credit_per_contract - quoted
        slippage_dollars_total = slippage_dollars(filled_credit_per_contract, quoted, contracts_sold, "SELL")

    fees_known = bool(fills) and all(f.get("fees") is not None for f in fills)
    fees_total = sum(float(f.get("fees", 0) or 0) for f in fills) if fees_known else None

    capital_per = _capital_per_contract(signal, option_type, strike) if signal is not None else None
    capital_days = None
    if fills:
        if capital_per is not None:
            capital_days = capital_days_from_lots(lots, capital_per, as_of=now)

    # Realized option-leg outcome (never fabricated):
    #  - no fills                     → pending (signal awaits evidence)
    #  - unknown fees                 → unknown (unknown ≠ zero)
    #  - orphan fills (no realizable  → unknown
    #    entry, e.g. assignment buyback movement)
    #  - fully open short obligation  → open; net stays None — the collected
    #    premium is unrealized context, never realized profit
    #  - partially closed             → open with the realized tranche's net
    #  - fully closed                 → measured (realized)
    gross_premium_pnl = None
    net_pnl = None
    if not fills:
        status = "pending"
    elif not fees_known:
        status = "unknown"
    elif terminal_state:
        status = "unknown"
    elif closed_qty <= 0 and open_contracts <= 0:
        status = "unknown"
    elif closed_qty <= 0:
        status = "open"  # open obligation: premium collected is unrealized context
    else:
        gross_premium_pnl = realized_premium_in - realized_premium_out
        net_pnl = gross_premium_pnl - realized_fees
        status = "open" if open_contracts > 0 else "measured"

    return {
        "identity": "|".join([ticker, expiration, option_type, f"{strike:g}"]),
        "ticker": ticker,
        "expiration": expiration,
        "option_type": option_type,
        "strike": strike,
        "signal_type": signal_type_of(signal) if signal is not None else "unmatched",
        "preset_key": meta.get("preset_key", ""),
        "event_tier": str((signal or {}).get("event_tier", "") or "").strip() or _UNTIERED,
        "quality_tier": str((signal or {}).get("quality_tier", "") or "").strip() or _UNTIERED,
        "dte": dte,
        "dte_bucket": dte_bucket(dte),
        "first_recommended_at": meta.get("generated_at", ""),
        "run_id": meta.get("run_id", ""),
        "quoted_credit_per_contract": quoted,
        "filled_credit_per_contract": filled_credit_per_contract,
        "contracts_sold": contracts_sold,
        "contracts_bought_back": contracts_bought,
        "open_contracts": open_contracts,
        "slippage_per_contract": slippage_per_contract,
        "slippage_dollars": slippage_dollars_total,
        "fees_known": fees_known,
        "fees_total": fees_total,
        "gross_premium_pnl": gross_premium_pnl,  # realized option-leg gross (before fees)
        "net_pnl": net_pnl,  # realized option-leg net (after fees); None while unclosed/unknown
        "outcome_status": status,
        "pnl_scope": "option_leg" if fills else None,  # supported scope only; share/expiration/assignment stay unknown
        "collected_premium_dollars": collected_premium_dollars,
        "unrealized_premium_dollars": unrealized_premium_dollars,
        "capital_days": capital_days,
        "owner_efficiency": owner_efficiency(net_pnl, capital_days) if net_pnl is not None else None,
        "open": bool(fills) and open_contracts > 0,
        "realization_state": terminal_state
        or ("open" if open_contracts > 0 else "closed" if closed_qty > 0 else "pending"),
        "cycle_outcome": _cycle_outcome(terminal_state, terminal_event, collected_premium_dollars, fees_total)
        if terminal_state
        else None,
        "attribution": "",  # set by build_outcome_records
        "fills": [_fill_transaction(f) for f in sorted(fills, key=lambda f: str(f.get("captured_at", "") or ""))],
    }


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return sum(values) / len(values)


def aggregate_group(records) -> dict:
    """Aggregate outcome records with mandatory evidence metadata.

    Every aggregate exposes sample_size, coverage_pct (share of the sample
    with fill evidence), and unknown_count so a number is never read without
    its evidence quality.

    Counts follow realization:
    - ``measured_count`` = records with any REALIZED net P&L (closed or
      partially closed option legs);
    - ``owner_linked_count`` = records an owner-recorded taken link attributes
      to a recommendation (explicit owner evidence, whatever the realization
      state);
    - ``unknown_count`` = records with fill evidence but nothing realized
      (fully open obligations, or unresolved fees);
    - ``pending_count`` = signals still waiting for fill evidence.
    """
    records = [r for r in records if isinstance(r, dict)]
    sample_size = len(records)
    evidenced = [r for r in records if r.get("outcome_status") in ("measured", "open", "unknown")]
    realized = [r for r in records if r.get("net_pnl") is not None]
    unknown = [r for r in records if r.get("outcome_status") in ("open", "unknown") and r.get("net_pnl") is None]
    pending = [r for r in records if r.get("outcome_status") == "pending"]

    # The summary walks every evidenced record: net_dollars sums only realized
    # (non-None) pnl while capital-days includes open positions' deployed
    # capital — the denominator must not flatter results by dropping open run.
    owner = owner_summary(
        [
            {
                "net_pnl": r.get("net_pnl"),
                "capital_days": r.get("capital_days"),
                "open": bool(r.get("open")),
            }
            for r in evidenced
        ]
    )

    fees_known_sum = sum(r.get("fees_total") or 0.0 for r in evidenced if r.get("fees_known"))
    fees_unknown_count = sum(1 for r in evidenced if not r.get("fees_known"))
    owner_linked = [r for r in records if r.get("attribution") == ATTRIBUTION_OWNER_LINKED]

    return {
        "sample_size": sample_size,
        "matched_count": len(evidenced),
        "pending_count": len(pending),
        "measured_count": len(realized),
        "unknown_count": len(unknown),
        "owner_linked_count": len(owner_linked),
        "coverage_pct": round(len(evidenced) * 100.0 / sample_size, 1) if sample_size else 0.0,
        "net_dollars": owner["net_dollars"],
        "capital_days": owner["capital_days"],
        "owner_efficiency": owner["owner_efficiency"],
        "open_losses": owner["open_losses"],
        "drawdown": owner["drawdown"],
        "quoted_credit_avg_per_contract": _mean([r.get("quoted_credit_per_contract") for r in records]),
        "filled_credit_avg_per_contract": _mean([r.get("filled_credit_per_contract") for r in evidenced]),
        "avg_slippage_per_contract": _mean([r.get("slippage_per_contract") for r in evidenced]),
        "fees_total_known": fees_known_sum,
        "fees_unknown_count": fees_unknown_count,
    }


def build_wheel_cycle_summary(cycles, ticker=None) -> dict:
    """Summarize persisted wheel cycles without treating unknown legs as zero."""
    rows = [cycle for cycle in (cycles or []) if isinstance(cycle, dict)]
    if ticker:
        ticker_filter = str(ticker).upper()
        rows = [cycle for cycle in rows if str(cycle.get("ticker") or "").upper() == ticker_filter]
    outcomes = []
    measured = 0
    unknown = 0
    open_count = 0
    net_dollars = 0.0
    for cycle in rows:
        result = realized_cycle_outcome(cycle)
        status = str(result.get("status") or "unknown")
        measured += status == "measured"
        unknown += status == "unknown"
        open_count += status == "open"
        if status == "measured" and result.get("net_pnl") is not None:
            net_dollars += float(result["net_pnl"])
        outcomes.append(
            {
                "cycle_id": cycle.get("cycle_id"),
                "ticker": cycle.get("ticker"),
                "origin": cycle.get("origin"),
                "opened_at": cycle.get("opened_at"),
                "closed_at": cycle.get("closed_at"),
                "state": cycle.get("state"),
                "cycle_outcome": result,
            }
        )
    return {
        "sample_size": len(outcomes),
        "measured_count": measured,
        "unknown_count": unknown,
        "open_count": open_count,
        "net_dollars": round(net_dollars, 2) if measured else None,
        "outcomes": outcomes,
    }


def group_records(records, key_fn, order=None) -> list[dict]:
    """Group records by a key function into [{key, ...aggregate}] entries."""
    groups: dict[str, list] = {}
    for record in records:
        key = key_fn(record)
        groups.setdefault(key, []).append(record)
    keys = list(groups.keys())
    if order is not None:
        keys.sort(key=lambda k: order.index(k) if k in order else len(order))
    else:
        keys.sort()
    return [{"key": key, **aggregate_group(groups[key])} for key in keys]


_DTE_ORDER = [label for _, label in _DTE_BUCKET_EDGES] + ["90+", "unknown"]


def filter_records(records, ticker=None, preset=None, event_tier=None, dte_bucket_filter=None) -> list[dict]:
    """Apply drill-down filters; None/empty values pass everything through."""
    out = []
    for record in records:
        if ticker and str(record.get("ticker", "")).upper() != str(ticker).upper():
            continue
        if preset and str(record.get("preset_key", "")) != str(preset):
            continue
        if event_tier and str(record.get("event_tier", "")) != str(event_tier):
            continue
        if dte_bucket_filter and str(record.get("dte_bucket", "")) != str(dte_bucket_filter):
            continue
        out.append(record)
    return records if all(v in (None, "") for v in (ticker, preset, event_tier, dte_bucket_filter)) else out


class OutcomeService:
    """Serves broker-verified outcome summaries; ingests on explicit request."""

    def __init__(self, database, connection_provider=None):
        self._db = database
        self._connection_provider = connection_provider

    # -- ingestion ---------------------------------------------------------

    def ingest_broker_evidence(self, days: int = 90, cash_flow_days: int = 7) -> dict:
        """Pull fills + fees (and recent cash movements) from OpenD.

        Query-only; reuses the shared connection via the injected provider.
        Returns a combined result; failures are reported, never swallowed.
        """
        from api.services.fills_service import FillsService

        connection = self._connection_provider() if self._connection_provider else None
        if connection is None:
            return {"ok": False, "error": "Broker connection unavailable", "ingested": 0}

        fills_service = FillsService(connection, self._db)
        fills_result = fills_service.ingest_history_fills(days=days)
        if fills_result.get("ok"):
            try:
                from api.services.wheel_ledger import WheelLedgerService

                env, account_id = connection.resolve_portfolio_identity()
                snapshot = self._db.get_latest_portfolio_snapshot(env=env, account_id=account_id)
                stocks = {
                    str(position.get("symbol") or "").upper(): {"position": position.get("qty")}
                    for position in (snapshot or {}).get("positions", [])
                    if isinstance(position, dict) and position.get("security_type") == "STK"
                }
                if stocks:
                    WheelLedgerService(self._db).recompute(env, account_id, positions=stocks)
            except Exception as exc:
                logger.warning("Wheel-cycle refresh after fill ingest failed (ignored): %s", exc)

        cash_result: dict = {"ok": True, "ingested": 0}
        try:
            cash_days = max(0, int(cash_flow_days))
        except (TypeError, ValueError):
            cash_days = 0
        if cash_days:
            today = market_now().date()
            dates = [(today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(cash_days)]
            cash_result = fills_service.ingest_cash_flows(dates)

        ok = bool(fills_result.get("ok")) and bool(cash_result.get("ok", True))
        return {
            "ok": ok,
            "fills": fills_result,
            "cash_flows": cash_result,
        }

    # -- serving -----------------------------------------------------------

    def get_outcome_summary(
        self,
        env,
        account_id,
        ticker=None,
        preset=None,
        event_tier=None,
        dte_bucket=None,
        snapshot_limit=500,
        fill_limit=5000,
    ) -> dict:
        """Build the outcome summary payload for the current identity.

        Reads local SQLite only (fills + published run snapshots + owner-taken
        links); never gates on live OpenD and never touches the ranking path.
        """
        fills = self._db.get_fills(
            env=env,
            account_id=account_id,
            security_type="OPT",
            limit=max(1, int(fill_limit)),
        )
        stock_fills = self._db.get_fills(
            env=env,
            account_id=account_id,
            security_type="STK",
            limit=max(1, int(fill_limit)),
        )
        snapshots = self._db.get_run_snapshots(env=env, account_id=account_id, limit=max(1, int(snapshot_limit)))
        history_reader = getattr(self._db, "get_portfolio_history", None)
        portfolio_snapshots = (
            history_reader(env=env, account_id=account_id, unbounded=True) if callable(history_reader) else []
        )
        taken_links = read_taken_links(self._db, env, account_id)
        cycle_reader = getattr(self._db, "get_wheel_cycles", None)
        cycles = (
            cycle_reader(env=env, account_id=account_id, ticker=ticker, limit=1000) if callable(cycle_reader) else []
        )
        link_suggestions = build_link_suggestions(snapshots, fills, taken_links=taken_links)
        dte_comparisons = build_dte_comparisons(snapshots, fills)
        if ticker:
            ticker_filter = str(ticker).upper()
            link_suggestions = [item for item in link_suggestions if item["recommendation"]["ticker"] == ticker_filter]
            dte_comparisons = [item for item in dte_comparisons if item["traded"]["ticker"] == ticker_filter]
        records = build_outcome_records(
            snapshots,
            [*fills, *stock_fills],
            taken_links=taken_links,
            portfolio_snapshots=portfolio_snapshots,
        )
        records = filter_records(
            records, ticker=ticker, preset=preset, event_tier=event_tier, dte_bucket_filter=dte_bucket
        )

        event_order = sorted({str(r.get("event_tier", "")) for r in records} - {_UNTIERED})
        event_order.append(_UNTIERED)

        return {
            "generated_at": market_now().isoformat(),
            "taken_link_count": len(taken_links),
            "link_suggestions": link_suggestions,
            "dte_comparisons": dte_comparisons,
            "filters": {
                "ticker": ticker or "",
                "preset": preset or "",
                "event_tier": event_tier or "",
                "dte_bucket": dte_bucket or "",
            },
            "totals": aggregate_group(records),
            "wheel_cycles": build_wheel_cycle_summary(cycles, ticker=ticker),
            "groups": {
                "by_preset": group_records(records, lambda r: str(r.get("preset_key", "")) or _UNTIERED),
                "by_dte_bucket": group_records(records, lambda r: str(r.get("dte_bucket", "")), order=_DTE_ORDER),
                "by_ticker": group_records(records, lambda r: str(r.get("ticker", ""))),
                "by_event_tier": group_records(records, lambda r: str(r.get("event_tier", "")), order=event_order),
            },
            "outcomes": records,
            "count": len(records),
        }
