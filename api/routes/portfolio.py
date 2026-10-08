"""
Portfolio API routes
"""

import json
import math
import uuid
from datetime import date, datetime

from flask import Blueprint, current_app, jsonify, request

from api.routes.source_policy import attach_source_policy, build_account_source_policy
from api.routes.utils import ensure_opend_available as _ensure_opend_available
from api.routes.utils import error_response
from core.logging_config import get_logger

bp = Blueprint("portfolio", __name__, url_prefix="/api/portfolio")


@bp.route("/holdings", methods=["GET"])
def get_holdings():
    """Return a local, last-snapshot view of stock holdings and wheel cycles."""
    db = current_app.config.get("database")
    if not db:
        return error_response("Database not available", status_code=503)
    try:
        from api.services.config import get_current_identity
        from core.utils import market_now

        env, account_id = get_current_identity()
        latest = db.get_latest_portfolio_snapshot(env=env, account_id=account_id)
        # When a paper account was selected unambiguously by a prior run but
        # is not explicitly configured, reuse only that run's persisted id.
        if latest and not account_id:
            account_id = latest.get("account_id") or ""
        if not latest or not account_id:
            return jsonify({"holdings": [], "changes": [], "source": "local_snapshot", "available": False}), 200

        try:
            since = request.args.get("since")
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00")) if since else None
            if since_dt and since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=market_now().tzinfo)
        except (TypeError, ValueError):
            return error_response("Invalid since timestamp; use ISO-8601", status_code=400)

        now = market_now()
        stocks = {}
        options = []
        for position in latest.get("positions", []) or []:
            if not isinstance(position, dict):
                continue
            if position.get("security_type") == "STK":
                stocks[str(position.get("symbol") or "").upper()] = position
            elif position.get("security_type") == "OPT":
                options.append(position)

        run = db.get_latest_snapshot(env=env, account_id=account_id) or {}
        run_signals = list(run.get("signals") or [])
        run_signals.extend((run.get("covered_calls") or {}).get("signals", []) or [])
        cycles = db.get_wheel_cycles(env=env, account_id=account_id, limit=1000)
        cycles_by_ticker = {}
        for cycle in cycles:
            cycles_by_ticker.setdefault(str(cycle.get("ticker") or "").upper(), []).append(cycle)

        holdings = []
        nav = float(latest.get("net_liquidation", 0) or 0)
        dead_money_value = 0.0
        for ticker, stock in stocks.items():
            qty = float(stock.get("qty", 0) or 0)
            avg_cost = stock.get("avg_cost")
            market_price = stock.get("market_price")
            market_value = float(stock.get("market_val", 0) or 0)
            basis, basis_source = avg_cost, "moomoo_avg_cost" if avg_cost else "missing"
            ticker_cycles = cycles_by_ticker.get(ticker, [])
            active_cycles = [c for c in ticker_cycles if c.get("state") in {"assigned_uncovered", "cc_open"}]
            cycle = active_cycles[0] if len(active_cycles) == 1 else None
            if cycle and cycle.get("completeness") == "proven" and cycle.get("adjusted_basis_per_share") is not None:
                basis, basis_source = cycle["adjusted_basis_per_share"], "ledger_adjusted"
            if avg_cost and market_price and float(market_price) < float(avg_cost):
                dead_money_value += max(0.0, market_value)

            open_legs = [
                {
                    "option_type": str(leg.get("option_type") or "").upper(),
                    "strike": leg.get("strike"),
                    "expiration": leg.get("expiration"),
                    "qty": leg.get("qty"),
                }
                for leg in options
                if str(leg.get("underlying") or "").upper() == ticker and float(leg.get("qty", 0) or 0) < 0
            ]
            calls = [
                candidate
                for candidate in run_signals
                if isinstance(candidate, dict)
                and str(candidate.get("ticker") or "").upper() == ticker
                and str(candidate.get("option_type") or "").upper() == "CALL"
                and basis is not None
                and float(candidate.get("strike", 0) or 0) >= float(basis)
            ]
            calls.sort(key=lambda item: float(item.get("capital_velocity_per_day", 0) or 0), reverse=True)
            holdings.append(
                {
                    "ticker": ticker,
                    "shares": qty,
                    "broker_avg_cost": avg_cost,
                    "adjusted_basis": cycle.get("adjusted_basis_per_share") if cycle else None,
                    "basis": basis,
                    "basis_source": basis_source,
                    "broker_sellable_shares": stock.get("can_sell_qty"),
                    "covered_call_capacity_crosscheck": (
                        None
                        if stock.get("can_sell_qty") is None
                        else {
                            "broker_sellable_shares": stock.get("can_sell_qty"),
                            "position_shares": qty,
                            "note": "Display-only Moomoo position field; does not determine covered-call eligibility.",
                        }
                    ),
                    "cycle_completeness": cycle.get("completeness") if cycle else "partial",
                    "cycle_state": cycle.get("state") if cycle else None,
                    "open_legs": open_legs,
                    "best_call_at_or_above_basis": calls[0] if calls else None,
                    "market_value": market_value,
                }
            )

        changes = []
        if since_dt:
            for event in db.get_trade_events(env=env, account_id=account_id, limit=1000):
                timestamp = _parse_iso_datetime(event.get("timestamp"))
                if timestamp and timestamp >= since_dt:
                    changes.append({"source": "trade_event", **event})
            for cycle in cycles:
                for event in cycle.get("events", []) or []:
                    timestamp = _parse_iso_datetime(event.get("timestamp"))
                    if timestamp and timestamp >= since_dt:
                        changes.append({"source": "wheel_cycle", **event})
            changes.sort(key=lambda item: str(item.get("timestamp") or ""))

        expiries = []
        for leg in options:
            try:
                expiry = datetime.strptime(str(leg.get("expiration") or ""), "%Y%m%d").date()
            except ValueError:
                continue
            days = (expiry - now.date()).days
            if 0 <= days <= 7:
                expiries.append({"ticker": leg.get("underlying"), "expiration": leg.get("expiration"), "days": days})

        latest_fill = db.get_latest_fill_captured_at(env=env, account_id=account_id)
        latest_fill_dt = _parse_iso_datetime(latest_fill)
        fill_age_seconds = max(0, int((now - latest_fill_dt).total_seconds())) if latest_fill_dt else None
        window_start = db.get_setting(f"fill_history_window_start:{env}:{account_id}")
        return jsonify(
            {
                "available": True,
                "source": "local_snapshot",
                "snapshot_captured_at": latest.get("captured_at"),
                "holdings": holdings,
                "changes": changes,
                "expiries_next_7_days": expiries,
                "dead_money_share_of_nav": dead_money_value / nav if nav > 0 else None,
                "fill_ingest_age_seconds": fill_age_seconds,
                "fill_history_window_start": window_start,
                "fill_history_window_warning": not bool(window_start),
            }
        )
    except Exception as exc:
        logger.error("Error loading local holdings: %s", exc)
        return error_response(str(exc), status_code=500)


def get_portfolio_service():
    """Get the registered portfolio service singleton (lazy registry)."""
    import api

    return api.get_service("portfolio")


logger = get_logger("api.routes.portfolio", "api")


def _load_goal_config(db, env, account_id, history):
    key = f"goal_pace_config:{env}:{account_id}"
    try:
        stored = db.get_setting(key)
        config = json.loads(stored) if stored else None
    except (TypeError, ValueError):
        config = None
    baseline = next((row for row in history if str(row.get("captured_at", ""))[:10] == "2026-08-22"), None)
    if not isinstance(config, dict):
        config = {
            "baseline_date": "2026-08-22",
            "baseline_nav": baseline.get("net_liquidation") if baseline else None,
            "target_multiple": 5.0,
            "horizon_end": "2029-08-22",
            "env": env,
            "account_id": account_id,
        }
    elif config.get("baseline_nav") is None and baseline:
        config["baseline_nav"] = baseline.get("net_liquidation")
    if account_id and baseline and config.get("baseline_nav"):
        db.set_setting(key, json.dumps(config, sort_keys=True))
    return key, config


def _flow_completeness(config, history):
    baseline_date = str(config.get("baseline_date") or "2026-08-22")[:10]
    latest_date = max((str(row.get("captured_at", ""))[:10] for row in history), default=baseline_date)
    required_through = max(baseline_date, latest_date)
    attestation = config.get("cash_flow_attestation")
    attested_through = str(attestation.get("through") or "")[:10] if isinstance(attestation, dict) else ""
    complete = bool(
        isinstance(attestation, dict) and attestation.get("complete") is True and attested_through >= required_through
    )
    return {
        "complete": complete,
        "required_through": required_through,
        "attested_through": attested_through or None,
        "confidence_state": "complete" if complete else "flows_incomplete",
    }


def _owner_flow_payload(row):
    return {
        "cashflow_id": row.get("cashflow_id"),
        "date": row.get("clearing_date") or row.get("settlement_date") or row.get("date"),
        "direction": row.get("direction"),
        "amount": row.get("amount"),
        "currency": row.get("currency"),
        "remark": row.get("remark"),
    }


def _parse_iso_datetime(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        from core.utils import market_now

        parsed = parsed.replace(tzinfo=market_now().tzinfo)
    return parsed


def _is_real_account_unavailable(message):
    if not message:
        return False

    return (
        "requested REAL account" in message
        or "No available real accounts" in message
        or "Nonexisting acc_id" in message
    )


def _service_unavailable_response(message, fallback_message):
    error_message = message or fallback_message
    extra = {}
    if _is_real_account_unavailable(error_message):
        extra["error_code"] = "real_account_unavailable"
        extra["opend_status"] = {"status": "real_account_unavailable", "message": error_message}
    return error_response(error_message, status_code=503, **extra)


@bp.route("/", methods=["GET"])
def get_portfolio():
    """Get the current portfolio information (broker truth only)."""
    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response

        results = get_portfolio_service().get_portfolio_summary()
        if results is None:
            return _service_unavailable_response(get_portfolio_service().last_error, "Failed to load portfolio summary")

        return jsonify(attach_source_policy(results, build_account_source_policy("portfolio")))
    except Exception as e:
        return error_response(str(e), status_code=500)


@bp.route("/positions", methods=["GET"])
def get_positions():
    """
    Get the current portfolio positions

    Query Parameters:
        type: Filter by position type (STK, OPT). If not provided, returns all positions.
    """
    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response

        # Get the position_type from query parameters
        position_type = request.args.get("type")
        # Validate position_type
        if position_type and position_type not in ["STK", "OPT"]:
            return error_response("Invalid position type. Supported types: STK, OPT", status_code=400)

        results = get_portfolio_service().get_positions(position_type)
        if results is None:
            return _service_unavailable_response(get_portfolio_service().last_error, "Failed to load positions")
        response = jsonify(results)
        response.headers["X-Source-Policy"] = "broker_only"
        response.headers["X-Source-Truth"] = "opend"
        return response
    except Exception as e:
        return error_response(str(e), status_code=500)


@bp.route("/weekly-income", methods=["GET"])
def get_weekly_income():
    """
    Get weekly option income from short options expiring this Friday.

    Returns:
        A JSON response containing weekly option income data:
        {
            "positions": [
                {
                    "symbol": "NVDA",
                    "option_type": "P",
                    "strike": 850.0,
                    "expiration": "20240510",
                    "position": 10,
                    "avg_cost": 15.5,
                    "current_price": 15.5,
                    "income": 155.0
                },
                ...
            ],
            "total_income": 155.0,
            "positions_count": 1,
            "this_friday": "20240510"
        }

        Error response:
        {
            "error": "Error message",
            "positions": [],
            "total_income": 0,
            "positions_count": 0
        }
    """
    try:
        unavailable_response = _ensure_opend_available()
        if unavailable_response:
            return unavailable_response

        results = get_portfolio_service().get_weekly_option_income()

        if "error" in results:
            payload = {"error": results["error"], "positions": [], "total_income": 0, "positions_count": 0}
            if _is_real_account_unavailable(results["error"]):
                payload["error_code"] = "real_account_unavailable"
                payload["opend_status"] = {"status": "real_account_unavailable", "message": results["error"]}
                return jsonify(payload), 503
            return jsonify(payload), 500

        return jsonify(attach_source_policy(results, build_account_source_policy("weekly_income"))), 200
    except Exception as e:
        payload = {"positions": [], "total_income": 0, "positions_count": 0}
        return error_response(str(e), status_code=500, **payload)


@bp.route("/history", methods=["GET"])
def get_portfolio_history():
    """
    Get persisted portfolio snapshot history (one per completed run).

    Serves equity-curve and growth-pace panels from the local snapshot store.
    Data originates from OpenD (captured at run publish time); this endpoint is
    read-only over SQLite and does not require a live OpenD connection.

    Query Parameters:
        limit: Max snapshots to return, oldest first. Default 180, max 1000.
    """
    try:
        db = current_app.config.get("database")
        if not db:
            return error_response("Database not available", status_code=503)

        raw_limit = request.args.get("limit")
        try:
            limit = int(raw_limit) if raw_limit is not None else 180
        except (TypeError, ValueError):
            return error_response("Invalid limit — must be an integer", status_code=400)

        from api.services.config import get_current_identity

        identity_env, identity_account = get_current_identity()
        history = db.get_portfolio_history(limit=limit, env=identity_env, account_id=identity_account)
        pace_history = db.get_portfolio_history(unbounded=True, env=identity_env, account_id=identity_account)
        from core.goal_pace import goal_pace
        from core.utils import market_now

        goal_config_key, goal_config = _load_goal_config(db, identity_env, identity_account, pace_history)
        flow_status = _flow_completeness(goal_config, pace_history)
        target_multiple = float(goal_config.get("target_multiple", 5.0) or 5.0)
        cash_flows = (
            db.get_cash_flows(
                env=identity_env,
                account_id=identity_account,
                start=str(goal_config.get("baseline_date") or "2026-08-22"),
                limit=20000,
            )
            if identity_account and goal_config.get("baseline_nav")
            else []
        )
        pace = goal_pace(
            pace_history,
            cash_flows,
            baseline_nav=goal_config.get("baseline_nav"),
            baseline_date=goal_config.get("baseline_date", "2026-08-22"),
            target_multiple=target_multiple,
            horizon_end=goal_config.get("horizon_end", "2029-08-22"),
            as_of=market_now().date(),
            flows_complete=flow_status["complete"],
        )

        # Chart-ready series plus the full snapshot payloads.
        series = [
            {
                "captured_at": snap["captured_at"],
                "run_id": snap["run_id"],
                "net_liquidation": snap["net_liquidation"],
                "cash_available": snap["cash_available"],
                "cash_available_for_csp": snap["cash_available_for_csp"],
                "cash_reserved_for_csp": snap["cash_reserved_for_csp"],
            }
            for snap in history
        ]
        payload = {
            "count": len(history),
            "series": series,
            "snapshots": history,
            "pace": pace,
            "target_multiple": target_multiple,
            "goal_configured": bool(goal_config.get("baseline_nav")),
            "flow_completeness": flow_status,
        }
        return jsonify(attach_source_policy(payload, build_account_source_policy("portfolio_history"))), 200
    except Exception as e:
        logger.error("Error loading portfolio history: %s", e)
        return error_response(str(e), status_code=500)


@bp.route("/goal-flows", methods=["GET"])
def get_goal_flows():
    """Return owner-entered capital flows and the current completeness status."""
    db = current_app.config.get("database")
    if not db:
        return error_response("Database not available", status_code=503)
    from api.services.config import get_current_identity

    env, account_id = get_current_identity()
    if not account_id:
        return error_response("Configure an account before managing goal cash flows", status_code=409)
    from core.utils import market_now

    history = db.get_portfolio_history(unbounded=True, env=env, account_id=account_id)
    _key, config = _load_goal_config(db, env, account_id, history)
    rows = db.get_cash_flows(env=env, account_id=account_id, limit=20000)
    owner_rows = [_owner_flow_payload(row) for row in rows if str(row.get("cashflow_id", "")).startswith("owner:")]
    owner_rows.sort(key=lambda row: (str(row.get("date") or ""), str(row.get("cashflow_id") or "")))
    return jsonify(
        {
            "flows": owner_rows,
            "flow_completeness": _flow_completeness(config, history),
            "baseline_date": str(config.get("baseline_date") or "2026-08-22"),
            "current_market_date": market_now().date().isoformat(),
        }
    )


@bp.route("/goal-flows", methods=["POST"])
def add_goal_flow():
    """Add a manually declared deposit or withdrawal for the active account."""
    db = current_app.config.get("database")
    if not db:
        return error_response("Database not available", status_code=503)
    from api.services.config import get_current_identity
    from core.utils import market_now

    env, account_id = get_current_identity()
    if not account_id:
        return error_response("Configure an account before managing goal cash flows", status_code=409)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error_response("Expected a JSON object", status_code=400)
    if isinstance(body.get("amount"), bool):
        return error_response("Provide a valid ISO date and positive amount", status_code=400)
    try:
        flow_date = date.fromisoformat(str(body.get("date") or ""))
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        return error_response("Provide a valid ISO date and positive amount", status_code=400)
    direction = str(body.get("direction") or "").upper()
    currency = str(body.get("currency") or "USD").strip().upper()
    if flow_date > market_now().date() or direction not in {"IN", "OUT"} or not math.isfinite(amount) or amount <= 0:
        return error_response(
            "Flow must have a past or current date, direction IN/OUT, and positive amount", status_code=400
        )
    if currency != "USD":
        return error_response("Goal-flow amounts must use USD, matching the persisted NAV series", status_code=400)

    history = db.get_portfolio_history(unbounded=True, env=env, account_id=account_id)
    config_key, config = _load_goal_config(db, env, account_id, history)
    baseline_date = str(config.get("baseline_date") or "2026-08-22")[:10]
    if flow_date < date.fromisoformat(baseline_date):
        return error_response("Flow date must be on or after the goal baseline", status_code=400)
    cashflow_id = f"owner:{uuid.uuid4()}"
    flow_type = "Deposit" if direction == "IN" else "Withdrawal"
    remark = str(body.get("remark") or "Owner-declared").strip()[:120]
    config.pop("cash_flow_attestation", None)
    db.set_setting(config_key, json.dumps(config, sort_keys=True))
    db.save_cash_flows(
        [
            {
                "cashflow_id": cashflow_id,
                "clearing_date": flow_date.isoformat(),
                "settlement_date": flow_date.isoformat(),
                "currency": currency,
                "direction": direction,
                "flow_type": flow_type,
                "amount": amount,
                "remark": remark or "Owner-declared",
                "env": env,
                "account_id": account_id,
                "raw_json": {"source": "owner_declared"},
            }
        ]
    )
    created = {
        "cashflow_id": cashflow_id,
        "date": flow_date.isoformat(),
        "direction": direction,
        "amount": amount,
        "currency": currency,
        "remark": remark or "Owner-declared",
    }
    return jsonify({"success": True, "flow": _owner_flow_payload(created)}), 201


@bp.route("/goal-flows/<path:cashflow_id>", methods=["DELETE"])
def delete_goal_flow(cashflow_id):
    """Delete only owner-declared rows and only inside the active account."""
    db = current_app.config.get("database")
    if not db:
        return error_response("Database not available", status_code=503)
    from api.services.config import get_current_identity

    env, account_id = get_current_identity()
    if not account_id:
        return error_response("Configure an account before managing goal cash flows", status_code=409)
    if not cashflow_id.startswith("owner:"):
        return error_response("Only owner-declared flows can be removed here", status_code=400)
    history = db.get_portfolio_history(unbounded=True, env=env, account_id=account_id)
    config_key, config = _load_goal_config(db, env, account_id, history)
    config.pop("cash_flow_attestation", None)
    db.set_setting(config_key, json.dumps(config, sort_keys=True))
    if not db.delete_cash_flow(cashflow_id, env=env, account_id=account_id):
        return error_response("Owner-declared flow not found for the active account", status_code=404)
    return jsonify({"success": True}), 200


@bp.route("/goal-flows/completeness", methods=["PUT"])
def set_goal_flow_completeness():
    """Record the owner's explicit statement that all capital flows are entered."""
    db = current_app.config.get("database")
    if not db:
        return error_response("Database not available", status_code=503)
    from api.services.config import get_current_identity
    from core.utils import market_now

    env, account_id = get_current_identity()
    if not account_id:
        return error_response("Configure an account before confirming cash-flow history", status_code=409)
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or type(body.get("complete")) is not bool:
        return error_response("Provide complete as a JSON boolean", status_code=400)
    history = db.get_portfolio_history(unbounded=True, env=env, account_id=account_id)
    config_key, config = _load_goal_config(db, env, account_id, history)
    if body["complete"]:
        try:
            through = date.fromisoformat(str(body.get("through") or ""))
        except ValueError:
            return error_response("Provide the confirmation date as ISO YYYY-MM-DD", status_code=400)
        status = _flow_completeness(config, history)
        required = date.fromisoformat(status["required_through"])
        if through > market_now().date() or through < required:
            return error_response(
                f"Confirmation date must cover the account history through {required.isoformat()}", status_code=400
            )
        config["cash_flow_attestation"] = {"complete": True, "through": through.isoformat()}
    else:
        config.pop("cash_flow_attestation", None)
    db.set_setting(config_key, json.dumps(config, sort_keys=True))
    return jsonify({"flow_completeness": _flow_completeness(config, history)}), 200


# roll-pressure endpoint extracted to api/routes/roll_pressure.py (F008)
