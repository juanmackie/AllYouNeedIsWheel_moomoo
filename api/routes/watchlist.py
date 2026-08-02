"""
Watchlist API — merged canonical union with origin labels.

Sources: named Moomoo/OpenD group, app-managed SQLite symbols, and the legacy
config list. Tickers are canonicalized and deduplicated; origins are labelled.
"""

from flask import Blueprint, jsonify, request

from core.ticker_utils import canonical_underlying

bp = Blueprint("watchlist", __name__, url_prefix="/api/watchlist")


def _get_db():
    from flask import current_app

    return current_app.config.get("database")


def _get_watchlist_manager():
    import api

    return api.get_service("options").watchlist_manager


@bp.route("", methods=["GET"])
def get_watchlist():
    """Return sources, the canonical union, and per-ticker origin labels."""
    manager = _get_watchlist_manager()
    sources = manager.get_watchlist_sources()
    union = manager.get_effective_watchlist_with_origins()
    return jsonify(
        {
            "sources": {key: sorted(set(value)) for key, value in sources.items()},
            "union": union,
            "count": len(union),
            "canonical": True,
        }
    )


@bp.route("", methods=["POST"])
def add_symbol():
    """Add an app-managed watchlist symbol."""
    payload = request.get_json(silent=True) or {}
    raw = str(payload.get("symbol", "") or "").strip().upper()
    if not raw:
        return jsonify({"success": False, "error": "symbol is required"}), 400
    symbol = canonical_underlying(raw)
    db = _get_db()
    if db is None:
        return jsonify({"success": False, "error": "Database unavailable"}), 503
    db.upsert_watchlist_symbol(symbol, origin="app")
    return jsonify({"success": True, "symbol": symbol})


@bp.route("/<symbol>", methods=["DELETE"])
def remove_symbol(symbol):
    """Remove an app-managed watchlist symbol."""
    db = _get_db()
    if db is None:
        return jsonify({"success": False, "error": "Database unavailable"}), 503
    removed = db.remove_watchlist_symbol(canonical_underlying(str(symbol).strip().upper()))
    return jsonify({"success": True, "removed": removed})
