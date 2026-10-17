"""Deterministic next steps from published broker evidence; manual tickets only."""

from core.ticker_utils import earnings_underlying_ticker
from core.utils import safe_float


def _ticker(row):
    return earnings_underlying_ticker(str(row.get("ticker") or row.get("underlying") or "")).upper()


def _leg(row, side, quantity=None):
    price = safe_float(row.get("ask" if side == "BUY" else "bid"), None)
    return {
        "ticker": _ticker(row),
        "side": side,
        "option_type": row.get("option_type", "CALL"),
        "strike": row.get("strike"),
        "expiration": row.get("expiration"),
        "quantity": int(quantity if quantity is not None else row.get("recommended_contracts") or 1),
        "executable_price": price,
        "limit_target": price,
    }


def build_next_actions(
    *, roll_decisions=(), cc_decisions=(), roll_candidates=(), campaigns=(), deployment_plan=None, rejected=()
):
    """One decision per held leg/free lot plus one combined cash allocation."""
    actions = []
    covered = {}
    calls = {_ticker(row): row for row in cc_decisions or ()}
    for row in roll_decisions or ():
        ticker = _ticker(row)
        right = str(row.get("option_type") or "").upper()
        quantity = max(1, int(abs(safe_float(row.get("quantity", row.get("position", 1))))))
        if right in {"CALL", "C"}:
            covered[ticker] = covered.get(ticker, 0) + quantity
        verdict = row.get("exit_verdict") or "HOLD"
        target = row.get("roll_target")
        follow_up = calls.get(ticker) or next(
            (
                c
                for c in roll_candidates or ()
                if _ticker(c) == ticker and str(c.get("option_type")).upper() in {"CALL", "C"}
            ),
            None,
        )

        def same_contract(other):
            return (
                other
                and str(other.get("expiration")) == str(row.get("expiration"))
                and safe_float(other.get("strike")) == safe_float(row.get("strike"))
            )

        if verdict == "TAKE_PROFIT" and right in {"CALL", "C"} and (not follow_up or same_contract(follow_up)):
            verdict = "HOLD"
            reasons = ["profit target reached; no better call to move into"]
        else:
            reasons = list(row.get("exit_reasons") or row.get("reasons") or [])
        ticket = None
        if verdict in {"CLOSE", "TAKE_PROFIT", "ROLL", "ROTATE"}:
            legs = [_leg(row, "BUY", quantity)]
            kind = "close"
            if verdict in {"ROLL", "ROTATE"} and target:
                legs.append(_leg(target, "SELL", quantity))
                kind = "roll"
            ticket = {"kind": kind, "legs": legs, "lane": "roll_candidates", "net_credit": row.get("net_credit")}
        actions.append(
            {
                "underlying": ticker,
                "wheel_state": "SHARES_COVERED" if right in {"CALL", "C"} else "SHORT_PUT",
                "verdict": verdict,
                "held_contract": _leg(row, "BUY", quantity),
                "reasons": reasons,
                "reason_codes": list(row.get("exit_reason_codes") or []),
                "ticket": ticket,
                "dte": row.get("dte"),
                "campaign": next((c for c in campaigns or () if _ticker(c) == ticker), None),
                "follow_up": follow_up if follow_up and not same_contract(follow_up) else None,
            }
        )
    for campaign in campaigns or ():
        ticker = _ticker(campaign)
        shares = safe_float(campaign.get("shares", campaign.get("position")))
        free_lots = campaign.get("available_calls")
        if free_lots is None:
            free_lots = max(0, int(shares // 100) - covered.get(ticker, 0))
        odd_shares = shares % 100
        if odd_shares > 0:
            actions.append(
                {
                    "underlying": ticker,
                    "wheel_state": "ODD_LOT",
                    "verdict": "HOLD",
                    "reasons": [f"{odd_shares:g} odd shares; no covered-call lot"],
                    "ticket": None,
                    "campaign": campaign,
                }
            )
        if shares < 100:
            continue
        if free_lots <= 0:
            continue
        call = calls.get(ticker)
        rejection = next(
            (r for r in rejected or () if _ticker(r) == ticker and r.get("signal_type") == "covered_call"), {}
        )
        intent = (call or {}).get("covered_call_intent") or ((call or {}).get("wheel_decision") or {}).get(
            "covered_call_intent"
        )
        actions.append(
            {
                "underlying": ticker,
                "wheel_state": "SHARES_FREE",
                "verdict": ("SELL_REPAIR_CALL" if intent == "repair" else "SELL_CALL") if call else "WAIT",
                "reasons": [rejection.get("reason_text") or "No qualifying covered call"]
                if not call
                else list(call.get("warnings") or []),
                "reason_codes": [rejection["reason_code"]] if rejection.get("reason_code") else [],
                "ticket": {"kind": "open", "legs": [_leg(call, "SELL")], "lane": "cc_decisions", "net_credit": None}
                if call
                else None,
                "campaign": campaign,
                "loss_if_called": (call or {}).get("loss_if_called")
                or ((call or {}).get("wheel_decision") or {}).get("loss_if_called"),
                "if_called_pnl_vs_basis": (call or {}).get("if_called_pnl_vs_basis")
                or ((call or {}).get("wheel_decision") or {}).get("if_called_pnl_vs_basis"),
                "basis_source": (call or {}).get("basis_source")
                or ((call or {}).get("wheel_decision") or {}).get("basis_source"),
            }
        )
    plan = deployment_plan or {}
    if plan.get("lines"):
        actions.append(
            {
                "underlying": "CASH",
                "wheel_state": "CASH",
                "verdict": "DEPLOY_CASH",
                "reasons": ["Allocate free broker cash in rank order within reserve and concentration limits"],
                "ticket": {
                    "kind": "open",
                    "legs": [
                        _leg(line, "SELL", line.get("deployment_contracts", line.get("contracts", 0)))
                        for line in plan["lines"]
                    ],
                    "lane": "csp_picks",
                    "net_credit": plan.get("total_credit"),
                },
                "deployment_plan": plan,
            }
        )
    elif safe_float(plan.get("cash_left")) > 0:
        actions.append(
            {
                "underlying": "CASH",
                "wheel_state": "CASH",
                "verdict": "WAIT",
                "ticket": None,
                "reasons": [r.get("reason") or r.get("reason_code") for r in plan.get("skipped") or []]
                or ["No qualifying allocation within cash limits"],
                "deployment_plan": plan,
            }
        )
    priorities = {
        "CLOSE": 1,
        "TAKE_PROFIT": 3,
        "ROLL": 4,
        "ROTATE": 4,
        "SELL_CALL": 5,
        "SELL_REPAIR_CALL": 6,
        "DEPLOY_CASH": 7,
        "WAIT": 8,
        "HOLD": 9,
        "LET_ASSIGN": 9,
    }

    def key(action):
        if "repair_call_threatened" in action.get("reason_codes", []):
            priority = 0
        elif action.get("dte") is not None and safe_float(action["dte"]) <= 7 and action["verdict"] != "CLOSE":
            priority = 2
        else:
            priority = priorities.get(action["verdict"], 10)
        return priority, safe_float(action.get("dte"), 9999), action["underlying"]

    for rank, action in enumerate(sorted(actions, key=key), 1):
        action.update(
            rank=rank,
            action_id=f"{action['underlying']}:{action['wheel_state']}:{rank}",
            urgency="now"
            if key(action)[0] <= 1
            else "this_week"
            if key(action)[0] <= 4
            else "info"
            if action["wheel_state"] == "ODD_LOT"
            else "routine",
        )
        action.setdefault("reason_codes", [])
    return sorted(actions, key=lambda a: a["rank"])
