"""Tests for Ape Wisdom social momentum service."""

from unittest.mock import MagicMock, patch

from api.services.apewisdom_service import ApeWisdomService

# ---------------------------------------------------------------------------
# _parse_entry
# ---------------------------------------------------------------------------


class TestParseEntry:
    def test_normalizes_string_numbers(self):
        raw = {
            "rank": "42",
            "ticker": "GME",
            "name": "GameStop",
            "mentions": "150",
            "upvotes": "320",
            "rank_24h_ago": "80",
            "mentions_24h_ago": "60",
        }
        entry = ApeWisdomService._parse_entry(raw)
        assert entry["ticker"] == "GME"
        assert entry["mentions"] == 150
        assert entry["upvotes"] == 320
        assert entry["rank"] == 42
        assert entry["rank_24h_ago"] == 80
        assert entry["mentions_24h_ago"] == 60

    def test_handles_missing_24h_fields(self):
        raw = {"rank": "1", "ticker": "AAPL", "mentions": "10"}
        entry = ApeWisdomService._parse_entry(raw)
        assert entry["rank_24h_ago"] == 0
        assert entry["mentions_24h_ago"] == 0
        assert entry["upvotes"] == 0

    def test_handles_none_and_empty_string(self):
        raw = {"rank": None, "ticker": "", "mentions": "", "upvotes": None}
        entry = ApeWisdomService._parse_entry(raw)
        assert entry["ticker"] == ""
        assert entry["mentions"] == 0
        assert entry["rank"] == 0

    def test_uppercases_ticker(self):
        raw = {"ticker": "amzn", "mentions": "5"}
        entry = ApeWisdomService._parse_entry(raw)
        assert entry["ticker"] == "AMZN"


# ---------------------------------------------------------------------------
# _compute_momentum
# ---------------------------------------------------------------------------


class TestComputeMomentum:
    def test_growing_mentions_scores_higher(self):
        e1 = {"mentions": 100, "mentions_24h_ago": 10}  # growth
        e2 = {"mentions": 100, "mentions_24h_ago": 100}  # flat
        assert ApeWisdomService._compute_momentum(e1) > ApeWisdomService._compute_momentum(e2)

    def test_zero_24h_mentions_uses_floor_of_1(self):
        e = {"mentions": 50, "mentions_24h_ago": 0}
        score = ApeWisdomService._compute_momentum(e)
        assert score == 50 * (1 + 50 / 1)

    def test_basic_computation(self):
        e = {"mentions": 10, "mentions_24h_ago": 5}
        expected = round(10 * (1 + 10 / 5), 2)
        assert ApeWisdomService._compute_momentum(e) == expected


# ---------------------------------------------------------------------------
# _apply_filters
# ---------------------------------------------------------------------------


class TestApplyFilters:
    def test_filters_by_min_mentions(self):
        svc = ApeWisdomService(config={"min_mentions": 10})
        entries = [
            {"ticker": "A", "mentions": 15},
            {"ticker": "B", "mentions": 3},
            {"ticker": "C", "mentions": 10},
        ]
        result = svc._apply_filters(entries)
        tickers = [e["ticker"] for e in result]
        assert "B" not in tickers
        assert "A" in tickers
        assert "C" in tickers

    def test_excludes_tickers(self):
        svc = ApeWisdomService(config={"exclude_tickers": ["SPY", "QQQ"]})
        entries = [
            {"ticker": "SPY", "mentions": 100},
            {"ticker": "GME", "mentions": 100},
            {"ticker": "QQQ", "mentions": 50},
        ]
        result = svc._apply_filters(entries)
        tickers = [e["ticker"] for e in result]
        assert tickers == ["GME"]


# ---------------------------------------------------------------------------
# get_momentum_candidates
# ---------------------------------------------------------------------------


class TestGetMomentumCandidates:
    def test_returns_empty_when_disabled(self):
        svc = ApeWisdomService(config={"enabled": False})
        assert svc.get_momentum_candidates() == []

    def test_caps_at_max_boost_tickers(self):
        svc = ApeWisdomService(config={"enabled": True, "max_boost_tickers": 2, "min_mentions": 1})
        raw_response = {
            "results": [
                {"ticker": "A", "mentions": "100", "rank": "1"},
                {"ticker": "B", "mentions": "90", "rank": "2"},
                {"ticker": "C", "mentions": "80", "rank": "3"},
            ]
        }
        with patch.object(svc, "fetch_all_stocks", return_value=raw_response):
            result = svc.get_momentum_candidates()
        assert len(result) == 2
        assert result[0]["ticker"] == "A"

    def test_empty_results(self):
        svc = ApeWisdomService(config={"enabled": True, "min_mentions": 1})
        with patch.object(svc, "fetch_all_stocks", return_value={"results": []}):
            result = svc.get_momentum_candidates()
        assert result == []

    def test_fetch_failure_returns_empty(self):
        svc = ApeWisdomService(config={"enabled": True, "min_mentions": 1})
        with patch.object(svc, "fetch_all_stocks", return_value=None):
            result = svc.get_momentum_candidates()
        assert result == []

    def test_failure_does_not_poison_cache(self):
        svc = ApeWisdomService(config={"enabled": True, "min_mentions": 1})
        raw_response = {
            "results": [
                {"ticker": "GME", "mentions": "200", "rank": "1"},
            ]
        }
        with patch.object(
            svc,
            "fetch_all_stocks",
            side_effect=[None, raw_response],
        ) as mock_fetch:
            first = svc.get_momentum_candidates()
            second = svc.get_momentum_candidates()
        assert first == []
        assert len(second) == 1
        assert mock_fetch.call_count == 2

    def test_cache_reuse_within_ttl(self):
        svc = ApeWisdomService(config={"enabled": True, "min_mentions": 1, "cache_ttl": 600})
        raw_response = {
            "results": [
                {"ticker": "GME", "mentions": "200", "rank": "1"},
            ]
        }
        with patch.object(svc, "fetch_all_stocks", return_value=raw_response) as mock_fetch:
            first = svc.get_momentum_candidates()
            second = svc.get_momentum_candidates()
        assert mock_fetch.call_count == 1  # only called once
        assert len(first) == 1
        assert first == second

    def test_cache_expired_refetches(self):
        svc = ApeWisdomService(config={"enabled": True, "min_mentions": 1, "cache_ttl": 0})
        raw_response = {
            "results": [
                {"ticker": "AAPL", "mentions": "50", "rank": "5"},
            ]
        }
        with patch.object(svc, "fetch_all_stocks", return_value=raw_response) as mock_fetch:
            svc.get_momentum_candidates()
            svc.get_momentum_candidates()
        assert mock_fetch.call_count == 2


# ---------------------------------------------------------------------------
# fetch_all_stocks
# ---------------------------------------------------------------------------


class TestFetchAllStocks:
    def test_successful_fetch(self):
        svc = ApeWisdomService()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"results": []}'
        mock_resp.json.return_value = {"results": []}
        with patch("requests.get", return_value=mock_resp) as mock_get:
            result = svc.fetch_all_stocks(page=1)
        mock_get.assert_called_once()
        assert "results" in result

    def test_uses_configured_filter_in_url(self):
        svc = ApeWisdomService(config={"filter": "wallstreetbets"})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"results": []}'
        mock_resp.json.return_value = {"results": []}
        with patch("requests.get", return_value=mock_resp) as mock_get:
            svc.fetch_all_stocks(page=3)
        assert "/wallstreetbets/page/3" in mock_get.call_args.args[0]

    def test_http_error_returns_empty(self):
        svc = ApeWisdomService()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        with patch("requests.get", return_value=mock_resp):
            result = svc.fetch_all_stocks()
        assert result is None

    def test_network_error_returns_empty(self):
        svc = ApeWisdomService()
        with patch("requests.get", side_effect=ConnectionError("timeout")):
            result = svc.fetch_all_stocks()
        assert result is None
