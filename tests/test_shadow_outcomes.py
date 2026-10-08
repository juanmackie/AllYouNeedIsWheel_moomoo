from moomoo import AuType

from api.services.shadow_service import ShadowOutcomeService, _candidate_outcome, _expiry_date


def test_shadow_put_outcome_uses_intrinsic_at_expiry_close():
    outcome = _candidate_outcome(
        {"option_type": "PUT", "strike": 50, "bid": 2.0, "cash_required": 5000, "dte": 10}, 46.0
    )

    assert outcome["intrinsic_per_share"] == 4.0
    assert outcome["net_per_share"] == -2.0
    assert outcome["capital_return_per_day"] == -0.004
    assert outcome["outcome_label"] == "put_net"


def test_shadow_call_outcome_labels_intrinsic_as_upside_forgone():
    outcome = _candidate_outcome({"option_type": "CALL", "strike": 50, "bid": 1.5, "stock_price": 48, "dte": 5}, 54.0)

    assert outcome["intrinsic_per_share"] == 4.0
    assert outcome["net_per_share"] == -2.5
    assert outcome["outcome_label"] == "upside_forgone"


def test_expiration_parsing_accepts_compact_and_iso_dates():
    assert _expiry_date("20260925").isoformat() == "2026-09-25"
    assert _expiry_date("2026-09-25T00:00:00").isoformat() == "2026-09-25"
    assert _expiry_date("invalid") is None


class _Frame:
    def to_dict(self, orient):
        assert orient == "records"
        return [{"time_key": "2020-01-17", "close": 45.0}]


class _Connection:
    def __init__(self):
        self.history_calls = []

    def get_history_kl_quota(self):
        return 0, (1, 20, [])

    def get_history_kline(self, ticker, **kwargs):
        self.history_calls.append((ticker, kwargs))
        return 0, _Frame(), None


class _Database:
    def __init__(self):
        self.rows = [
            {
                "candidate_id": "candidate-1",
                "run_id": "run-1",
                "ticker": "XYZ",
                "option_type": "PUT",
                "strike": 50,
                "expiration": "20200117",
                "dte": 14,
                "bid": 2,
                "cash_required": 5000,
                "status": "pending",
            }
        ]
        self.settlements = []

    def get_shadow_candidates(self, **kwargs):
        return self.rows

    def settle_shadow_outcomes(self, rows):
        self.settlements.extend(rows)
        return len(rows)


def test_settle_expired_uses_unadjusted_moomoo_daily_close():
    db = _Database()
    connection = _Connection()
    service = ShadowOutcomeService(db, connection_provider=lambda: connection)

    result = service.settle_expired(env="REAL", account_id="123")

    assert result["settled"] == 1
    assert result["settlement_source"] == "moomoo_unadjusted_daily_close"
    assert db.settlements[0]["expiry_close"] == 45.0
    assert db.settlements[0]["outcome_label"] == "put_net"
    assert connection.history_calls[0][1]["autype"] == AuType.NONE
