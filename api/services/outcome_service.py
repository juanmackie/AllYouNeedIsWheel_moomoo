"""Broker-verified outcome summary service (read-only serving).

Turns ingested broker fills (``option_fills``, schema v10) plus the immutable
published run snapshots (recommendation signals) into owner-facing outcome
aggregates:

- quoted (recommended) credit vs. filled credit, and the slippage between them;
- option-leg net results after fees (unknown fees ⇒ unknown outcome, never zero);
- capital-days and net P&L / capital-day (owner efficiency) via the pure
  ``core.outcome_attribution`` helpers;
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
from datetime import datetime, timedelta

from api.services.taken_links import read_taken_links
from core.outcome_attribution import (
    broker_price_to_contract_credit,
    capital_base_cc,
    capital_base_csp,
    capital_days_from_lots,
    owner_efficiency,
    owner_summary,
    parse_timestamp,
    slippage_dollars,
)
from core.utils import safe_float

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
    if not candidates:
        return None, "unattributed"
    if not fills:
        return min(candidates, key=lambda c: str(c.get("generated_at", "") or "")), ""
    first_ts = min((str(f.get("captured_at", "") or "") for f in fills), default="")
    qualifiers = [c for c in candidates if _recommendation_precedes_first_fill(c, first_ts)]
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


def build_outcome_records(run_snapshots, option_fills, now=None, taken_links=None) -> list[dict]:
    """Join recommendation signals to option fills and attribute outcomes.

    ``run_snapshots``: published snapshot dicts (any order; recommendations
    kept at full cardinality per identity so attribution can detect
    ambiguity). ``option_fills``: fill rows for the same identity scope.
    ``now``: as-of for open-lot capital days. ``taken_links``: owner-recorded
    links stating which recommendation a manual trade came from; an
    unambiguous link that resolves to a stored recommendation attributes the
    traded contract to it (``owner-linked``). Returns one record per unique
    contract identity — the signal identity when a recommendation can be
    attributed, otherwise an unattributed evidence record for orphan fills
    (never dropped).
    """
    now = now or datetime.now()

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

    # Option fills grouped by contract identity.
    fills_by_identity: dict[tuple, list] = {}
    for fill in option_fills or []:
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
                linked_meta["signal"], linked_meta, ticker, expiration, option_type, strike, fills, now
            )
            record["attribution"] = ATTRIBUTION_OWNER_LINKED
        else:
            meta, attribution = _select_candidate(candidates, fills)
            if attribution != "unattributed" and meta is not None:
                record = _build_record(meta["signal"], meta, ticker, expiration, option_type, strike, fills, now)
                record["attribution"] = attribution
            else:
                # Orphan/unattributed fills are still real transactions: surfaced
                # as matched evidence only, never silently dropped, never given a
                # fabricated quoted price or a recommendation identity.
                empty_meta = {"signal": None, "preset_key": "", "generated_at": "", "run_id": ""}
                record = _build_record(None, empty_meta, ticker, expiration, option_type, strike, fills, now)
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


def _build_record(signal, meta, ticker, expiration, option_type, strike, fills, now) -> dict:
    quoted = quoted_credit_per_contract(signal) if signal is not None else None
    dte = signal.get("dte") if signal is not None else None

    entry_fills = [f for f in fills if str(f.get("side", "") or "").upper() == "SELL"]
    close_fills = [f for f in fills if str(f.get("side", "") or "").upper() == "BUY"]

    contracts_sold = sum(float(f.get("qty", 0) or 0) for f in entry_fills)
    contracts_bought = sum(float(f.get("qty", 0) or 0) for f in close_fills)

    lots, open_contracts, closed_qty = _fifo_lots(entry_fills, close_fills)

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

        cash_result: dict = {"ok": True, "ingested": 0}
        try:
            cash_days = max(0, int(cash_flow_days))
        except (TypeError, ValueError):
            cash_days = 0
        if cash_days:
            dates = [(datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(cash_days)]
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
        snapshots = self._db.get_run_snapshots(env=env, account_id=account_id, limit=max(1, int(snapshot_limit)))
        taken_links = read_taken_links(self._db, env, account_id)
        records = build_outcome_records(snapshots, fills, taken_links=taken_links)
        records = filter_records(
            records, ticker=ticker, preset=preset, event_tier=event_tier, dte_bucket_filter=dte_bucket
        )

        event_order = sorted({str(r.get("event_tier", "")) for r in records} - {_UNTIERED})
        event_order.append(_UNTIERED)

        return {
            "generated_at": datetime.now().isoformat(),
            "taken_link_count": len(taken_links),
            "filters": {
                "ticker": ticker or "",
                "preset": preset or "",
                "event_tier": event_tier or "",
                "dte_bucket": dte_bucket or "",
            },
            "totals": aggregate_group(records),
            "groups": {
                "by_preset": group_records(records, lambda r: str(r.get("preset_key", "")) or _UNTIERED),
                "by_dte_bucket": group_records(records, lambda r: str(r.get("dte_bucket", "")), order=_DTE_ORDER),
                "by_ticker": group_records(records, lambda r: str(r.get("ticker", ""))),
                "by_event_tier": group_records(records, lambda r: str(r.get("event_tier", "")), order=event_order),
            },
            "outcomes": records,
            "count": len(records),
        }
