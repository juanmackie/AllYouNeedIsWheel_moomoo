"""
Settings API — persisted wheel preset (read-only effective values).

The daily wheel workflow has exactly one tunable: the risk preset
(conservative | balanced | aggressive). Effective values are read-only;
there are no granular overrides.
"""

from flask import Blueprint, jsonify, request

from core.presets import DEFAULT_PRESET_KEY, WHEEL_PRESETS, all_presets, get_preset

bp = Blueprint("settings", __name__, url_prefix="/api/settings")


def _get_db():
    from flask import current_app

    return current_app.config.get("database")


def _get_options_service():
    import api

    return api.get_service("options")


def _active_preset_key():
    db = _get_db()
    if db is not None:
        try:
            persisted = db.get_setting("wheel_preset")
            if persisted in WHEEL_PRESETS:
                return persisted
        except Exception:
            pass
    from api.services.config import get_config

    cfg = get_config()
    key = (cfg or {}).get("wheel_preset", DEFAULT_PRESET_KEY)
    return key if key in WHEEL_PRESETS else DEFAULT_PRESET_KEY


@bp.route("", methods=["GET"])
def get_settings():
    """Return all presets, the active key, and the effective (read-only) values."""
    key = _active_preset_key()
    preset = get_preset(key)
    return jsonify(
        {
            "presets": all_presets(),
            "active": key,
            "effective": preset.to_dict(),
            "read_only": True,
        }
    )


@bp.route("/preset", methods=["POST"])
def set_preset():
    """Persist the selected preset. Values themselves are never user-editable."""
    payload = request.get_json(silent=True) or {}
    key = str(payload.get("preset", "") or "").strip().lower()
    if key not in WHEEL_PRESETS:
        return jsonify({"success": False, "error": f"Unknown preset: {key}"}), 400

    db = _get_db()
    if db is None:
        return jsonify({"success": False, "error": "Database unavailable"}), 503

    db.set_setting("wheel_preset", key)

    # Propagate to the live recommendation engine so the next run uses it.
    try:
        service = _get_options_service()
        engine = service.recommendation_engine
        engine._preset = get_preset(key)
        engine._preset_profile = engine._preset.to_screener_profile()
    except Exception:
        pass

    preset = get_preset(key)
    return jsonify({"success": True, "active": key, "effective": preset.to_dict(), "read_only": True})
