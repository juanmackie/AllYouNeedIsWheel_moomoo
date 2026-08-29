"""
Wheel run API — immutable snapshot + refresh attempt state.

GET  /api/run              -> latest attempt + latest completed snapshot
POST /api/run/refresh      -> start one background refresh (serialized)
"""

from flask import Blueprint, current_app, jsonify

from core.run_model import recompute_effective_snapshot
from core.wheel_runner import maybe_start_open_refresh, start_background_refresh

bp = Blueprint("run", __name__, url_prefix="/api/run")


def _get_runner():
    import api

    return api.get_service("wheel_runner")


def _get_db():
    from flask import current_app

    return current_app.config.get("database")


@bp.route("", methods=["GET"])
def get_run_state():
    """Return the latest refresh attempt and the latest completed snapshot."""
    db = _get_db()
    attempt = db.get_latest_attempt() if db is not None else None
    snapshot = db.get_latest_snapshot() if db is not None else None
    config = current_app.config.get("connection_config", {})
    if config.get("auto_refresh_at_open", False):
        maybe_start_open_refresh(_get_runner(), config, snapshot=snapshot)
    return jsonify({"attempt": attempt, "snapshot": recompute_effective_snapshot(snapshot)})


@bp.route("/refresh", methods=["POST"])
def refresh():
    """Start one bounded background refresh; returns 202 with attempt state."""
    runner = _get_runner()
    started = start_background_refresh(runner)
    db = _get_db()
    attempt = db.get_latest_attempt() if db is not None else None
    return jsonify({"started": started, "attempt": attempt}), (202 if started else 409)
