"""Tests for catalyst-watch route freshness metadata."""

from api.routes.options import _add_freshness_metadata, _catalyst_empty_response, CATALYST_SYNC_WAIT_SECONDS
from api.routes.source_policy import build_research_source_policy, detect_external_sources


def test_catalyst_empty_response_marks_scan_pending_and_preserves_thresholds():
    payload = _catalyst_empty_response(thresholds={
        "min_premium_notional": 250_000,
        "min_fresh_volume_ratio": 2,
        "min_volume": 100,
        "max_expirations": 1,
        "max_dte": 60,
        "max_scan_tickers": 4,
    })

    assert payload["scan_pending"] is True
    assert payload["message"] == "Catalyst scan is still waiting for broker flow data."
    assert payload["scanned"] == 0
    assert payload["elapsed_seconds"] == 0
    assert payload["thresholds"] == {
        "min_premium_notional": 250_000,
        "min_fresh_volume_ratio": 2,
        "min_volume": 100,
        "max_expirations": 1,
        "max_dte": 60,
        "max_scan_tickers": 4,
    }


def test_catalyst_empty_response_includes_freshness_metadata():
    payload = _catalyst_empty_response()

    assert payload["served_from_cache"] is False
    assert payload["cache_age_seconds"] is None
    assert payload["fresh_attempted"] is True
    assert payload["fresh_succeeded"] is False
    assert payload["last_successful_generated_at"] is None


def test_catalyst_empty_response_with_cache_params():
    payload = _catalyst_empty_response(served_from_cache=True, cache_age_seconds=120.5)

    assert payload["served_from_cache"] is True
    assert payload["cache_age_seconds"] == 120.5
    assert payload["fresh_attempted"] is True
    assert payload["fresh_succeeded"] is False
    assert payload["last_successful_generated_at"] is None


def test_catalyst_empty_response_default_thresholds_unchanged():
    payload = _catalyst_empty_response()

    assert payload["thresholds"]["min_premium_notional"] == 1_000_000
    assert payload["thresholds"]["min_fresh_volume_ratio"] == 5
    assert payload["thresholds"]["min_volume"] == 500
    assert payload["thresholds"]["max_expirations"] == 1
    assert payload["thresholds"]["max_dte"] == 60
    assert payload["thresholds"]["max_scan_tickers"] == 2


def test_add_freshness_metadata_fresh_response():
    base = {"signals": [], "count": 0, "generated_at": "2026-05-24T12:00:00"}
    result = _add_freshness_metadata(
        base,
        served_from_cache=False,
        cache_age_seconds=None,
        fresh_attempted=True,
        fresh_succeeded=True,
        last_successful_generated_at=base["generated_at"],
    )

    assert result["served_from_cache"] is False
    assert result["cache_age_seconds"] is None
    assert result["fresh_attempted"] is True
    assert result["fresh_succeeded"] is True
    assert result["last_successful_generated_at"] == "2026-05-24T12:00:00"
    assert result["signals"] == base["signals"]


def test_add_freshness_metadata_cached_response():
    base = {"signals": [{"ticker": "AAPL"}], "count": 1, "generated_at": "2026-05-24T10:00:00"}
    result = _add_freshness_metadata(
        base,
        served_from_cache=True,
        cache_age_seconds=300.0,
        fresh_attempted=False,
        fresh_succeeded=True,
        last_successful_generated_at=base["generated_at"],
    )

    assert result["served_from_cache"] is True
    assert result["cache_age_seconds"] == 300.0
    assert result["fresh_attempted"] is False
    assert result["fresh_succeeded"] is True
    assert result["last_successful_generated_at"] == "2026-05-24T10:00:00"


def test_add_freshness_metadata_background_refresh():
    base = {"signals": [], "count": 0, "generated_at": "2026-05-24T09:00:00"}
    result = _add_freshness_metadata(
        base,
        served_from_cache=True,
        cache_age_seconds=7200.0,
        fresh_attempted=True,
        fresh_succeeded=False,
        last_successful_generated_at=base["generated_at"],
    )

    assert result["served_from_cache"] is True
    assert result["cache_age_seconds"] == 7200.0
    assert result["fresh_attempted"] is True
    assert result["fresh_succeeded"] is False
    assert result["last_successful_generated_at"] == "2026-05-24T09:00:00"


def test_add_freshness_metadata_preserves_existing_fields():
    base = {
        "success": True,
        "enabled": True,
        "signals": [],
        "count": 0,
        "generated_at": "2026-05-24T12:00:00",
        "scan_pending": True,
        "thresholds": {"min_premium_notional": 1_000_000},
    }
    result = _add_freshness_metadata(
        base,
        served_from_cache=False,
        cache_age_seconds=None,
        fresh_attempted=True,
        fresh_succeeded=False,
        last_successful_generated_at=None,
    )

    for key in base:
        assert result[key] == base[key], f"Field {key} was modified"


# ---------------------------------------------------------------------------
# ApeWisdom metadata & source policy
# ---------------------------------------------------------------------------

def test_catalyst_service_result_includes_apewisdom_metadata():
    """get_signals() return dict always contains 'apewisdom' metadata."""
    from unittest.mock import MagicMock

    from api.services.catalyst_flow_service import CatalystFlowService

    svc = CatalystFlowService(
        config_provider={
            "db_path": ":memory:",
            "catalyst_flow": {
                "enabled": True,
                "min_volume": 100,
                "min_premium_notional": 100_000,
                "min_fresh_volume_ratio": 2,
                "max_expirations": 1,
                "max_dte": 90,
                "max_scan_tickers": 2,
                "apewisdom": {"enabled": False},
            },
            "evaluator": {"enabled": False},
            "watchlist": [],
            "watchlist_mode": "static",
        }
    )

    # Mock connection to prevent broker calls
    mock_conn = MagicMock()
    mock_conn.is_connected.return_value = True
    svc.connection = mock_conn

    result = svc.get_signals(limit=6)

    assert "apewisdom" in result
    assert result["apewisdom"]["enabled"] is False
    assert result["apewisdom"]["candidates_fetched"] == 0


def test_source_policy_detects_apewisdom_in_social_context():
    """detect_external_sources finds 'apewisdom' when social.source is present."""
    payload = {
        "signals": [
            {
                "ticker": "GME",
                "social": {"source": "apewisdom", "rank": 5},
            }
        ]
    }
    sources = detect_external_sources(payload)
    assert "apewisdom" in sources


def test_build_research_source_policy_includes_apewisdom():
    """build_research_source_policy lists apewisdom when detected in payload."""
    payload = {
        "signals": [
            {"ticker": "GME", "social": {"source": "apewisdom"}},
        ],
        "apewisdom": {"enabled": True, "candidates_fetched": 8},
    }
    policy = build_research_source_policy(
        "catalyst_watch",
        payload,
        fallback_sources_allowed=["yfinance"],
    )
    assert "apewisdom" in policy["external_fallback_sources_used"]


def test_add_freshness_metadata_preserves_apewisdom_key():
    base = {
        "success": True,
        "signals": [],
        "count": 0,
        "generated_at": "2026-05-24T12:00:00",
        "apewisdom": {"enabled": True, "candidates_fetched": 5},
    }
    result = _add_freshness_metadata(
        base,
        served_from_cache=False,
        cache_age_seconds=None,
        fresh_attempted=True,
        fresh_succeeded=True,
        last_successful_generated_at=base["generated_at"],
    )
    assert result["apewisdom"] == {"enabled": True, "candidates_fetched": 5}


# ---------------------------------------------------------------------------
# CATALYST_SYNC_WAIT_SECONDS constant
# ---------------------------------------------------------------------------

def test_catalyst_sync_wait_seconds_is_less_than_frontend_auto_timeout():
    """Backend sync wait (25s) must be less than frontend auto timeout (60s)."""
    assert CATALYST_SYNC_WAIT_SECONDS == 25
    assert CATALYST_SYNC_WAIT_SECONDS < 60


def test_catalyst_sync_wait_seconds_is_less_than_frontend_manual_timeout():
    """Backend sync wait (25s) must be less than frontend manual timeout (45s)."""
    assert CATALYST_SYNC_WAIT_SECONDS < 45


# ---------------------------------------------------------------------------
# Pending response shape
# ---------------------------------------------------------------------------

def test_catalyst_empty_response_pending_has_required_fields():
    """Pending response contains all fields the frontend expects."""
    payload = _catalyst_empty_response()

    assert payload["scan_pending"] is True
    assert "served_from_cache" in payload
    assert "fresh_attempted" in payload
    assert "fresh_succeeded" in payload
    assert "cache_age_seconds" in payload
    assert payload["fresh_attempted"] is True
    assert payload["fresh_succeeded"] is False


def test_catalyst_empty_response_pending_message():
    """Pending response has a user-facing waiting message."""
    payload = _catalyst_empty_response()
    assert "waiting" in payload["message"].lower() or "broker flow" in payload["message"].lower()
