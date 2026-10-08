from unittest.mock import MagicMock, patch


def test_get_watchlist_returns_canonical_union_and_sources():
    from api import create_app

    manager = MagicMock()
    manager.get_watchlist_sources.return_value = {"moomoo": ["AAPL", "AAPL"], "app": ["MSFT"]}
    manager.get_effective_watchlist_with_origins.return_value = [
        {"ticker": "AAPL", "origins": ["moomoo"]},
        {"ticker": "MSFT", "origins": ["app"]},
    ]
    app = create_app({"TESTING": True})

    with patch("api.routes.watchlist._get_watchlist_manager", return_value=manager):
        response = app.test_client().get("/api/watchlist")

    assert response.status_code == 200
    assert response.get_json() == {
        "sources": {"moomoo": ["AAPL"], "app": ["MSFT"]},
        "union": [
            {"ticker": "AAPL", "origins": ["moomoo"]},
            {"ticker": "MSFT", "origins": ["app"]},
        ],
        "count": 2,
        "canonical": True,
    }


def test_add_watchlist_symbol_requires_symbol_and_persists_canonical_value():
    from api import create_app

    app = create_app({"TESTING": True})
    db = MagicMock()
    app.config["database"] = db
    client = app.test_client()

    missing = client.post("/api/watchlist", json={})
    created = client.post("/api/watchlist", json={"symbol": "BRK.B"})

    assert missing.status_code == 400
    assert created.status_code == 200
    assert created.get_json() == {"success": True, "symbol": "BRK-B"}
    db.upsert_watchlist_symbol.assert_called_once_with("BRK-B", origin="app")
