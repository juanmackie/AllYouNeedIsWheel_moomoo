"""Replay synthetic option-chain and quote inputs through the real CSP scanner."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from api.routes.run import evaluate_copy_check
from api.services.recommendations import RecommendationEngine
from api.services.watchlist_manager import WatchlistManager
from core.presets import get_preset
from core.quote_cache import OptionChainCache
from core.run_model import recompute_effective_snapshot
from core.wheel_runner import WheelRunner
from tests.fixtures.raw_scan_replay import build_preset_flow_replay, build_raw_scan_replay


class _FixedDateTime(datetime):
    current = None

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.current.replace(tzinfo=None)
        return cls.current.astimezone(tz)


class _FixtureWatchlist:
    def __init__(self, scenario):
        self.scenario = scenario
        self.profile_provider = WatchlistManager(SimpleNamespace(config={"wheel_preset": scenario["preset_key"]}))
        self.screening_profiles = []

    def get_scan_universe(self, **_kwargs):
        return {
            "status": "ok",
            "group_name": "Synthetic replay",
            "explanation": "",
            "groups_available": ["Synthetic replay"],
            "tickers": list(self.scenario["watchlist"]),
            "raw_codes": {ticker: f"US.{ticker}" for ticker in self.scenario["watchlist"]},
            "groups_raw": {"Synthetic replay": [f"US.{ticker}" for ticker in self.scenario["watchlist"]]},
            "fetched_at": self.scenario["now_utc"].isoformat(),
        }

    @staticmethod
    def preflight_scan_feasibility(watchlist_size, **_kwargs):
        return {
            "feasible": True,
            "watchlist_size": watchlist_size,
            "estimated_scan_sec": 1,
            "freshness_window_sec": 300,
            "chain_calls": watchlist_size,
            "chain_quota_ok": True,
            "recommended_max_size": 25,
        }

    def get_screening_profile(self, *args, **kwargs):
        profile = self.profile_provider.get_screening_profile(*args, **kwargs)
        self.screening_profiles.append(profile)
        return profile


class _PresetSettingsDatabase:
    def __init__(self):
        self.settings = {"wheel_preset": "balanced"}

    def get_setting(self, key):
        return self.settings.get(key)

    def set_setting(self, key, value):
        self.settings[key] = value


def _build_connection(scenario):
    conn = MagicMock()
    conn._quote_cache = OptionChainCache()
    conn._format_symbol.side_effect = lambda symbol: symbol if str(symbol).startswith("US.") else f"US.{symbol}"
    conn._option_chain_rate_limiter.get_stats.return_value = {"api_calls_count": 0}
    conn.get_price_snapshot.return_value = scenario["prices"]
    conn.get_security_types.return_value = {}
    conn.get_option_contracts.side_effect = lambda code, start, end: [
        dict(contract)
        for contract in scenario["contracts"]
        if contract["code"].startswith(f"{code}-") and start <= contract["expiration"] <= end
    ]
    conn.get_option_quotes.side_effect = lambda codes: {
        code: dict(scenario["quotes"][code]) for code in codes if code in scenario["quotes"]
    }
    return conn


def _build_engine(scenario):
    conn = _build_connection(scenario)

    connection_provider = MagicMock()
    connection_provider._ensure_connection.return_value = conn
    portfolio_provider = MagicMock()
    portfolio_provider.get_portfolio_context.return_value = scenario["portfolio"]
    config_provider = SimpleNamespace(config={"wheel_preset": scenario["preset_key"], "cash_reserve_enabled": True})
    database = MagicMock()
    database.get_contracts.return_value = None
    iv_service = MagicMock()
    iv_service.get_iv_environment_score.return_value = (0, 0.5, "normal")
    iv_service.get_earnings_score_impact.return_value = (0, None)
    iv_service.get_earnings_info.return_value = {"fetch_status": "success", "days_to_earnings": 100}

    engine = RecommendationEngine(
        connection_provider,
        config_provider,
        database,
        iv_service,
        portfolio_provider,
        None,
        _FixtureWatchlist(scenario),
        MagicMock(),
        MagicMock(),
    )
    return engine, conn, database


def _build_options_service_engine(scenario, persisted_preset, configured_preset="balanced"):
    from api.services.options_service import OptionsService

    conn = _build_connection(scenario)
    database = MagicMock()
    database.get_setting.side_effect = lambda key: persisted_preset if key == "wheel_preset" else None
    database.get_contracts.return_value = None
    config = {
        "wheel_preset": configured_preset,
        "cash_reserve_enabled": True,
        "db_path": "synthetic.sqlite",
    }
    earnings = MagicMock()
    earnings.get_iv_environment_score.return_value = (0, 0.5, "normal")
    earnings.get_earnings_score_impact.return_value = (0, None)
    earnings.get_earnings_info.return_value = {"fetch_status": "success", "days_to_earnings": 100}
    with (
        patch("api.services.config.get_config", return_value=config),
        patch("db.database.OptionsDatabase", return_value=database),
        patch("api.services.iv_earnings_service.IVEarningsService", return_value=earnings),
    ):
        service = OptionsService()

    engine = service.recommendation_engine
    service._ensure_connection = lambda: conn
    engine._portfolio_context_provider = SimpleNamespace(get_portfolio_context=lambda: scenario["portfolio"])
    engine._watchlist_provider = _FixtureWatchlist(scenario)
    engine.iv_earnings_service = earnings
    return engine, conn, database


def _scan(scenario, engine=None):
    if engine is None:
        engine, conn, database = _build_engine(scenario)
    else:
        conn = engine._connection_provider._ensure_connection()
        database = engine.db
    fixed_now = scenario["now_utc"]
    _FixedDateTime.current = fixed_now
    with (
        patch("api.services.recommendations.datetime", _FixedDateTime),
        patch("api.services.recommendations.market_now", return_value=scenario["now_et"]),
        patch("api.services.recommendations.is_market_open", return_value=True),
        patch("api.services.options_data.market_now", return_value=scenario["now_et"]),
        patch("core.quote_cache.market_now", return_value=scenario["now_et"]),
        patch("core.scoring_factors.datetime", _FixedDateTime),
        patch("core.wheel_decision.market_now", return_value=scenario["now_et"]),
        patch("core.wheel_decision.is_market_open", return_value=True),
        patch("core.scan_ledger.ScanLedger.record", return_value=None),
    ):
        from api.services.options_data import score_contract

        expected_window = (
            (scenario["now_et"].date() + timedelta(days=engine._preset.csp_min_dte)).isoformat(),
            (scenario["now_et"].date() + timedelta(days=engine._preset.csp_max_dte)).isoformat(),
        )
        assert engine._csp_discovery_window() == expected_window
        assert score_contract.__globals__["market_now"]() == scenario["now_et"]
        result = engine.get_top_recommendations(limit=3)
    return result, conn, database


def _activate_preset_through_settings(engine, preset_key):
    from api import create_app

    app = create_app({"TESTING": True, "SECRET_KEY": "test"})
    settings_db = _PresetSettingsDatabase()
    app.config["database"] = settings_db
    service = SimpleNamespace(recommendation_engine=engine)
    with patch("api.routes.settings._get_options_service", return_value=service):
        response = app.test_client().post("/api/settings/preset", json={"preset": preset_key})
    assert response.status_code == 200, response.get_json()
    assert settings_db.get_setting("wheel_preset") == preset_key
    assert response.get_json()["active"] == preset_key
    assert response.get_json()["effective"] == get_preset(preset_key).to_dict()


def _snapshot_view(result, scenario):
    runner = WheelRunner(
        db=MagicMock(),
        options_service=MagicMock(),
        config={"portfolio_env": "SIMULATE", "account_id": ""},
        max_tradeable_age_sec=300,
    )
    generated_at = scenario["now_utc"].isoformat()
    with (
        patch("core.wheel_runner.is_market_open", return_value=True),
        patch("core.wheel_runner.utc_now_iso", return_value=generated_at),
    ):
        snapshot = runner._build_snapshot(
            env="SIMULATE",
            opaque_account="",
            attempt_started=generated_at,
            result=result,
            portfolio=scenario["portfolio"],
            roll_decisions=[],
        )
    return recompute_effective_snapshot(
        snapshot.to_dict(),
        now=scenario["now_utc"],
        now_et=scenario["now_et"],
    )


def _copy_check(view, candidate, scenario, *, now_utc=None):
    checked_at = now_utc or scenario["now_utc"]
    return evaluate_copy_check(
        view,
        {
            "run_id": view["run"]["run_id"],
            "ticker": candidate["ticker"],
            "option_type": candidate["option_type"],
            "expiration": candidate["expiration"],
            "strike": candidate["strike"],
        },
        now_utc=checked_at,
        now_et=checked_at.astimezone(scenario["now_et"].tzinfo),
    )


def test_raw_quote_replay_orders_candidates_and_rechecks_copy_eligibility():
    scenario = build_raw_scan_replay()
    result, conn, database = _scan(scenario)

    assert result["success"] is True
    assert result["scan_coverage"] == {"scanned": 2, "total": 2, "complete": True}
    candidates = result["watchlist_csps"]["signals"]
    assert [candidate["ticker"] for candidate in candidates] == ["BBB", "AAA"], result
    assert [candidate["bid_premium_per_contract"] for candidate in candidates] == [100.0, 150.0]
    assert all(candidate["chain_source"] == "broker" for candidate in candidates)
    assert all(candidate["copy_eligible"] for candidate in candidates)
    assert conn.get_option_contracts.call_count == 4
    assert database.save_contracts.call_count == 4

    view = _snapshot_view(result, scenario)
    copy_check = _copy_check(view, candidates[0], scenario)
    assert copy_check["matched_contract"] is True
    assert copy_check["mode"] == "live"


def test_stale_quote_at_copy_time_keeps_candidate_visible_but_fails_closed():
    scenario = build_raw_scan_replay()
    result, _conn, _database = _scan(scenario)

    assert result["scan_coverage"] == {"scanned": 2, "total": 2, "complete": True}
    candidate = next(item for item in result["watchlist_csps"]["signals"] if item["ticker"] == "BBB")
    assert candidate["copy_eligible"] is True
    assert candidate["quote_update_time"] == scenario["quote_update_time"]

    view = _snapshot_view(result, scenario)
    copy_check = _copy_check(view, candidate, scenario, now_utc=scenario["now_utc"] + timedelta(minutes=10))
    assert copy_check["matched_contract"] is True
    assert copy_check["mode"] == "review_only"
    assert any("staged evidence must be re-checked" in reason for reason in copy_check["reasons"])


@pytest.mark.parametrize(
    ("preset_key", "expected_tickers"),
    [
        ("conservative", {"COMMON"}),
        ("balanced", {"COMMON", "BALANCED"}),
        ("aggressive", {"COMMON", "BALANCED", "AGGRESSIVE"}),
    ],
)
def test_settings_preset_flows_through_scan_snapshot_and_copy(preset_key, expected_tickers):
    scenario = build_preset_flow_replay()
    engine, _conn, _database = _build_engine(scenario)
    _activate_preset_through_settings(engine, preset_key)
    assert engine._preset.key == preset_key

    result, _conn, _database = _scan(scenario, engine)
    candidates = result["watchlist_csps"]["signals"]
    assert {candidate["ticker"] for candidate in candidates} == expected_tickers
    assert result["preset"]["key"] == preset_key
    assert result["preset"]["version"] == 7

    preset = get_preset(preset_key)
    used_profile = engine._watchlist_provider.screening_profiles[0]
    assert used_profile["min_dte"] == preset.csp_min_dte
    assert used_profile["max_dte"] == preset.csp_max_dte
    assert used_profile["min_otm_pct"] == preset.csp_min_otm_pct
    assert used_profile["target_delta"] == preset.csp_target_delta
    assert used_profile["delta_tolerance"] == preset.csp_delta_tolerance

    view = _snapshot_view(result, scenario)
    assert view["run"]["preset_key"] == preset_key
    assert view["preset"]["version"] == 7
    assert view["preset"]["screener_profile"] == preset.to_screener_profile()
    assert view["preset"]["screener_profile"]["csp_min_dte"] == preset.csp_min_dte
    assert view["preset"]["screener_profile"]["max_account_exposure_pct_per_underlying"] == 25.0
    if preset_key == "aggressive":
        aggressive_only = next(candidate for candidate in candidates if candidate["ticker"] == "AGGRESSIVE")
        copy_check = _copy_check(view, aggressive_only, scenario)
        assert copy_check["mode"] == "live"
        assert copy_check["matched_contract"] is True
        assert copy_check["contract"]["copy_eligible"] is True
    else:
        assert "AGGRESSIVE" not in {candidate["ticker"] for candidate in candidates}


def test_persisted_aggressive_preset_is_hydrated_by_options_service_before_scan():
    scenario = build_preset_flow_replay()
    engine, _conn, _database = _build_options_service_engine(scenario, "aggressive")

    assert engine._preset.key == "aggressive"
    result, _conn, _database = _scan(scenario, engine)
    candidates = result["watchlist_csps"]["signals"]
    assert {candidate["ticker"] for candidate in candidates} == {"COMMON", "BALANCED", "AGGRESSIVE"}
    assert result["preset"]["key"] == "aggressive"
    view = _snapshot_view(result, scenario)
    assert view["run"]["preset_key"] == "aggressive"
    assert view["preset"]["version"] == 7
    assert view["preset"]["screener_profile"] == get_preset("aggressive").to_screener_profile()
    aggressive_candidate = next(item for item in candidates if item["ticker"] == "AGGRESSIVE")
    assert _copy_check(view, aggressive_candidate, scenario)["mode"] == "live"


@pytest.mark.parametrize("persisted_preset", [None, "unknown"])
def test_missing_or_invalid_persisted_preset_falls_back_to_config(persisted_preset):
    engine, _conn, _database = _build_options_service_engine(
        build_preset_flow_replay(), persisted_preset, configured_preset="aggressive"
    )

    assert engine._preset.key == "aggressive"
    assert engine._preset_profile == get_preset("aggressive").to_screener_profile()


@pytest.mark.parametrize("preset_key", ["conservative", "balanced", "aggressive"])
def test_zero_cash_remains_research_only_for_every_preset(preset_key):
    scenario = build_preset_flow_replay()
    scenario["watchlist"] = ["COMMON"]
    scenario["contracts"] = [
        contract for contract in scenario["contracts"] if contract["code"].startswith("US.COMMON-")
    ]
    scenario["quotes"] = {contract["code"]: scenario["quotes"][contract["code"]] for contract in scenario["contracts"]}
    scenario["prices"] = {"US.COMMON": scenario["prices"]["US.COMMON"]}
    scenario["portfolio"].update(
        cash_balance=0.0,
        available_cash=0.0,
        cash_available_for_csp=0.0,
        broker_buying_power=0.0,
        broker_buying_power_source="available_cash",
    )
    engine, _conn, _database = _build_engine(scenario)
    _activate_preset_through_settings(engine, preset_key)

    result, _conn, _database = _scan(scenario, engine)
    assert result["scan_coverage"] == {"scanned": 1, "total": 1, "complete": True}
    candidate = result["watchlist_csps"]["signals"][0]
    assert candidate["research_only"] is True
    assert candidate["copy_eligible"] is False
    assert candidate["recommended_contracts"] == 0

    view = _snapshot_view(result, scenario)
    copy_check = _copy_check(view, candidate, scenario)
    assert copy_check["mode"] == "review_only"
    assert "candidate is not copy eligible" in copy_check["reasons"]


@pytest.mark.parametrize("preset_key", ["conservative", "balanced", "aggressive"])
def test_account_exposure_cap_blocks_same_contract_for_every_preset(preset_key):
    scenario = build_preset_flow_replay()
    scenario["watchlist"] = ["COMMON"]
    scenario["contracts"] = [
        contract for contract in scenario["contracts"] if contract["code"].startswith("US.COMMON-")
    ]
    scenario["quotes"] = {contract["code"]: scenario["quotes"][contract["code"]] for contract in scenario["contracts"]}
    scenario["prices"] = {"US.COMMON": scenario["prices"]["US.COMMON"]}
    scenario["portfolio"]["underlying_capital_exposure"] = {"COMMON": 16_001.0}
    engine, _conn, _database = _build_engine(scenario)
    _activate_preset_through_settings(engine, preset_key)

    result, _conn, _database = _scan(scenario, engine)
    assert result["scan_coverage"]["complete"] is True
    assert result["watchlist_csps"]["signals"] == []
    assert result["blocked_reason_counts"]["underlying_exposure_cap"] > 0
