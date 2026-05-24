"""Tests for catalyst-watch route freshness metadata."""

from api.routes.options import _catalyst_empty_response, _add_freshness_metadata


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
