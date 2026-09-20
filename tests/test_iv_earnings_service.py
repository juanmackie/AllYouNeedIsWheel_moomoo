"""
Tests for api/services/iv_earnings_service.py — IV tracking and earnings.
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, PropertyMock, patch


def _reset_av_globals():
    import api.services.alpha_vantage_provider as av

    av._cache = None
    av._cache_timestamp = None
    av._last_request_time = 0
    av._cooldown_until = 0.0


class TestIVEarningsService(unittest.TestCase):
    """Core IV tracking and earnings service tests."""

    def setUp(self):
        from api.services.iv_earnings_service import IVEarningsService

        self.service = IVEarningsService(database=MagicMock())
        _reset_av_globals()

    def test_cache_invalid_when_empty(self):
        self.assertFalse(self.service._is_cache_valid(None, 4))

    def test_cache_valid_when_recent(self):
        entry = {"timestamp": datetime.now()}
        self.assertTrue(self.service._is_cache_valid(entry, 4))

    def test_cache_invalid_when_expired(self):
        entry = {"timestamp": datetime.now() - timedelta(hours=5)}
        self.assertFalse(self.service._is_cache_valid(entry, 4))

    def test_iv_rank_returns_neutral_without_db(self):
        # No database means no history at all: the honest status is
        # insufficient_history, which suppresses any score adjustment. It must
        # not masquerade as an observed "normal" environment.
        self.service.db = None
        rank, status = self.service._calculate_iv_rank("AAPL", 0.30)
        self.assertEqual(rank, 0.5)
        self.assertEqual(status, "insufficient_history")

    def test_iv_rank_unknown_when_zero_iv(self):
        """Zero IV should return unknown status."""
        self.service.db = MagicMock()
        rank, status = self.service._calculate_iv_rank("AAPL", 0.0)
        self.assertEqual(rank, 0.5)
        self.assertEqual(status, "unknown")

    def test_iv_rank_unknown_when_negative_iv(self):
        """Negative IV should return unknown status."""
        self.service.db = MagicMock()
        rank, status = self.service._calculate_iv_rank("AAPL", -0.1)
        self.assertEqual(rank, 0.5)
        self.assertEqual(status, "unknown")

    def test_iv_rank_with_historical_data(self):
        mock_db = MagicMock()
        mock_db.get_iv_history.return_value = _daily_rows(_RANGE_IVS)
        self.service.db = mock_db
        rank, status = self.service._calculate_iv_rank("AAPL", 0.30)
        self.assertAlmostEqual(rank, 0.5, places=2)
        self.assertEqual(status, "normal")

    def test_get_iv_environment_score_extreme_low(self):
        self.service.db.get_iv_history.return_value = [{"implied_volatility": 0.20 + i * 0.05} for i in range(10)]
        score, rank, status = self.service.get_iv_environment_score("AAPL", 0.1)
        self.assertEqual(status, "extreme_low")
        self.assertEqual(score, -20)

    def test_get_iv_environment_score_extreme_high(self):
        self.service._iv_cache["TEST"] = {
            "iv": 0.80,
            "iv_rank": 0.85,
            "timestamp": datetime.now(),
            "iv_status": "normal",
        }
        score, rank, status = self.service.get_iv_environment_score("TEST", 0.80)
        self.assertEqual(status, "extreme_high")
        self.assertEqual(score, 20)

    def test_get_iv_environment_score_zero_iv_returns_unknown(self):
        """Zero IV should return unknown status with neutral score."""
        score, rank, status = self.service.get_iv_environment_score("NOIV", 0.0)
        self.assertEqual(status, "unknown")
        self.assertEqual(score, 0)
        self.assertEqual(rank, 0.5)

    def test_record_iv_data_calls_db(self):
        mock_db = MagicMock()
        self.service.db = mock_db
        self.service.record_iv_data("AAPL", 0.30, stock_price=150.0)
        mock_db.save_iv_data.assert_called_once()

    def test_fetch_earnings_date_handles_no_earnings(self):
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.earnings_dates = None
            mock_ticker_cls.return_value = mock_ticker
            result = self.service.fetch_earnings_date("AAPL")
            self.assertIn("success", result)

    def test_strip_moomoo_prefix_delegates(self):
        with patch("api.services.iv_earnings_service.clean_yfinance_ticker") as mock_clean:
            mock_clean.return_value = "AAPL"
            result = self.service._strip_moomoo_prefix("US.AAPL")
            self.assertEqual(result, "AAPL")
            mock_clean.assert_called_once_with("US.AAPL")

    def test_fetch_earnings_date_normalizes_option_contract_symbol(self):
        self.service._alpha_vantage.api_key = ""
        mock_ticker = MagicMock()
        mock_ticker.get_earnings_dates.return_value = None
        mock_ticker.info = {}

        with (
            patch("api.services.iv_earnings_service.get_yfinance_ticker", return_value=mock_ticker) as mock_get_ticker,
            patch("api.services.iv_earnings_service.time.sleep", return_value=None),
        ):
            result = self.service.fetch_earnings_date("US.AAPL260618C77000")

        self.assertFalse(result["success"])
        mock_get_ticker.assert_called_once_with("AAPL")

    def test_batch_update_earnings_normalizes_and_dedupes_inputs(self):
        with patch.object(self.service, "update_earnings_data", return_value=True) as mock_update:
            result = self.service.batch_update_earnings(
                [
                    "US.AAPL260618C77000",
                    "AAPL",
                    "US.MSFT260621P450000",
                    "MSFT",
                    "",
                    None,
                ]
            )

        self.assertEqual(result["successful"], 2)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(mock_update.call_count, 2)
        self.assertEqual([call.args[0] for call in mock_update.call_args_list], ["AAPL", "MSFT"])

    def test_get_earnings_info_normalizes_option_contract_symbol(self):
        self.service.db.get_earnings_date.return_value = {
            "ticker": "AAPL",
            "earnings_date": "2026-05-15",
            "time_of_day": "post-market",
            "fiscal_date_ending": "2026-04-30",
            "estimate": 2.35,
            "currency": "USD",
            "earnings_source": "Alpha Vantage",
            "fetch_status": "success",
            "error_message": None,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        info = self.service.get_earnings_info("US.AAPL260618C77000")

        self.service.db.get_earnings_date.assert_called_once_with("AAPL")
        self.assertEqual(info["earnings_date"], "2026-05-15")
        # Legacy free-text source is normalized to the canonical token.
        self.assertEqual(info["earnings_source"], "alpha_vantage")


class TestAlphaVantageProvider(unittest.TestCase):
    """Alpha Vantage CSV parsing, caching, and availability."""

    def setUp(self):
        from api.services.alpha_vantage_provider import AlphaVantageEarningsProvider

        self.provider = AlphaVantageEarningsProvider(api_key="test_key")
        _reset_av_globals()

    def test_unavailable_without_key(self):
        provider = __import__(
            "api.services.alpha_vantage_provider", fromlist=["AlphaVantageEarningsProvider"]
        ).AlphaVantageEarningsProvider(api_key="")
        self.assertFalse(provider.available)

    def test_unavailable_with_none_key(self):
        provider = __import__(
            "api.services.alpha_vantage_provider", fromlist=["AlphaVantageEarningsProvider"]
        ).AlphaVantageEarningsProvider(api_key=None)
        self.assertFalse(provider.available)

    def test_parse_csv_recognises_timeOfTheDay(self):
        csv_text = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
            "AAPL,Apple Inc,2026-05-15,2026-04-30,2.35,USD,post-market\n"
            "MSFT,Microsoft Corp,2026-05-20,2026-05-10,3.10,USD,pre-market\n"
        )
        result = self.provider._parse_csv(csv_text)
        self.assertEqual(result["AAPL"]["timeOfDay"], "post-market")
        self.assertEqual(result["AAPL"]["reportDate"], "2026-05-15")
        self.assertEqual(result["AAPL"]["fiscalDateEnding"], "2026-04-30")
        self.assertEqual(result["AAPL"]["estimate"], 2.35)
        self.assertEqual(result["AAPL"]["currency"], "USD")
        self.assertEqual(result["MSFT"]["timeOfDay"], "pre-market")

    def test_parse_csv_fallback_timeOfDay(self):
        csv_text = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfDay\n"
            "AAPL,Apple Inc,2026-05-15,2026-04-30,,USD,after-market\n"
        )
        result = self.provider._parse_csv(csv_text)
        self.assertEqual(result["AAPL"]["timeOfDay"], "post-market")
        self.assertIsNone(result["AAPL"]["estimate"])

    def test_parse_csv_blank_timing(self):
        csv_text = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
            "AAPL,Apple Inc,2026-05-15,2026-04-30,,USD,\n"
        )
        result = self.provider._parse_csv(csv_text)
        self.assertEqual(result["AAPL"]["timeOfDay"], "")

    def test_parse_csv_malformed_skips_bad_rows(self):
        csv_text = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
            "AAPL,Apple Inc,2026-05-15,2026-04-30,2.35,USD,post-market\n"
            ",Bad Inc,,2026-04-30,,USD,\n"
            "NVDA,NVIDIA Corp,2026-06-01,2026-05-20,4.50,USD,pre-market\n"
        )
        result = self.provider._parse_csv(csv_text)
        self.assertIn("AAPL", result)
        self.assertIn("NVDA", result)
        self.assertNotIn("", result)

    def test_get_earnings_returns_none_without_key(self):
        provider = __import__(
            "api.services.alpha_vantage_provider", fromlist=["AlphaVantageEarningsProvider"]
        ).AlphaVantageEarningsProvider(api_key="")
        self.assertIsNone(provider.get_earnings("AAPL"))

    def test_cache_reused_on_second_call(self):
        csv_text = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
            "AAPL,Apple Inc,2026-05-15,2026-04-30,2.35,USD,post-market\n"
        )
        with patch.object(self.provider, "_fetch_csv", return_value=csv_text) as mock_fetch:
            r1 = self.provider.get_earnings("AAPL")
            r2 = self.provider.get_earnings("AAPL")
            self.assertEqual(r1, r2)
            mock_fetch.assert_called_once()

    def test_provider_priority_alpha_vantage_success_skips_fallbacks(self):
        """Alpha Vantage success should skip yfinance."""
        from api.services.alpha_vantage_provider import AlphaVantageEarningsProvider
        from api.services.iv_earnings_service import IVEarningsService

        svc = IVEarningsService(database=MagicMock())
        csv_text = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
            "AAPL,Apple Inc,2026-05-15,2026-04-30,2.35,USD,post-market\n"
        )
        with (
            patch.object(svc._alpha_vantage, "_fetch_csv", return_value=csv_text),
            patch.object(AlphaVantageEarningsProvider, "available", new_callable=PropertyMock(return_value=True)),
        ):
            result = svc.fetch_earnings_date("AAPL")
            self.assertTrue(result["success"])
            self.assertEqual(result["earnings_date"], "2026-05-15")
            self.assertEqual(result["time_of_day"], "post-market")
            self.assertEqual(result["earnings_source"], "alpha_vantage")
            self.assertEqual(result["estimate"], 2.35)
            self.assertEqual(result["currency"], "USD")

    def test_get_earnings_info_includes_richer_fields(self):
        """get_earnings_info returns time_of_day and earnings_source when available."""
        from api.services.iv_earnings_service import IVEarningsService

        mock_db = MagicMock()
        svc = IVEarningsService(database=mock_db)
        mock_db.get_earnings_date.return_value = {
            "ticker": "AAPL",
            "earnings_date": "2026-05-15",
            "time_of_day": "post-market",
            "fiscal_date_ending": "2026-04-30",
            "estimate": 2.35,
            "currency": "USD",
            "earnings_source": "Alpha Vantage",
            "fetch_status": "success",
            "error_message": None,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        info = svc.get_earnings_info("AAPL")
        self.assertEqual(info["time_of_day"], "post-market")
        self.assertEqual(info["earnings_source"], "alpha_vantage")
        self.assertEqual(info["estimate"], 2.35)
        self.assertEqual(info["currency"], "USD")
        self.assertEqual(info["fiscal_date_ending"], "2026-04-30")

    def test_get_earnings_info_returns_none_when_missing(self):
        """get_earnings_info returns None for newer fields when no data."""
        from api.services.iv_earnings_service import IVEarningsService

        mock_db = MagicMock()
        svc = IVEarningsService(database=mock_db)
        mock_db.get_earnings_date.return_value = None
        info = svc.get_earnings_info("UNKNOWN")
        self.assertIsNone(info["time_of_day"])
        self.assertIsNone(info["earnings_source"])
        self.assertIsNone(info["estimate"])
        self.assertIsNone(info["currency"])


class TestAlphaVantageProviderAdditional(unittest.TestCase):
    """Provider failure classification: timeouts, quota-exceeded, missing keys."""

    def _new_provider(self):
        import api.services.alpha_vantage_provider as av

        _reset_av_globals()
        return av.AlphaVantageEarningsProvider(api_key="test_key")

    def _fake_resp(self, status_code=200, text="", headers=None):
        class _FakeResp:
            pass

        resp = _FakeResp()
        resp.status_code = status_code
        resp.text = text
        resp.headers = headers or {}
        return resp

    def test_classify_error_message_200_invalid_key(self):
        provider = self._new_provider()
        outcome = provider._classify_response(
            self._fake_resp(200, '{"Error Message": "Invalid API call. Please retry or visit the documentation."}')
        )
        import api.services.alpha_vantage_provider as av

        self.assertEqual(outcome, av._STATUS_ERROR)
        self.assertEqual(provider.status, av._STATUS_ERROR)
        self.assertIn("Invalid API call", provider.last_error)

    def test_classify_information_200_quota_daily(self):
        provider = self._new_provider()
        outcome = provider._classify_response(
            self._fake_resp(
                200,
                '{"Information": "Thank you for using Alpha Vantage! Our standard API call frequency is 25 calls per day."}',
            )
        )
        import api.services.alpha_vantage_provider as av

        self.assertEqual(outcome, av._STATUS_QUOTA)
        self.assertEqual(provider.status, av._STATUS_QUOTA)

    def test_classify_note_200_quota_per_minute(self):
        provider = self._new_provider()
        outcome = provider._classify_response(
            self._fake_resp(200, '{"Note": "Our standard API call frequency is 5 calls per minute."}')
        )
        import api.services.alpha_vantage_provider as av

        self.assertEqual(outcome, av._STATUS_QUOTA)
        self.assertEqual(provider.status, av._STATUS_QUOTA)

    def test_classify_http_429_quota(self):
        provider = self._new_provider()
        outcome = provider._classify_response(self._fake_resp(429, "rate limited"))
        import api.services.alpha_vantage_provider as av

        self.assertEqual(outcome, av._STATUS_QUOTA)
        self.assertEqual(provider.status, av._STATUS_QUOTA)

    def test_classify_http_500_error(self):
        provider = self._new_provider()
        outcome = provider._classify_response(self._fake_resp(500, "internal error"))
        import api.services.alpha_vantage_provider as av

        self.assertEqual(outcome, av._STATUS_ERROR)
        self.assertEqual(provider.status, av._STATUS_ERROR)

    def test_fetch_csv_handles_timeout_without_raising(self):
        import requests

        provider = self._new_provider()
        with patch("requests.get", side_effect=requests.Timeout("timeout")):
            result = provider._fetch_csv()
        import api.services.alpha_vantage_provider as av

        self.assertIsNone(result)
        self.assertEqual(provider.status, av._STATUS_ERROR)
        self.assertIn("timed out", provider.last_error.lower())

    def test_fetch_csv_error_sets_error_status(self):
        provider = self._new_provider()
        with patch("requests.get", side_effect=RuntimeError("network down")):
            result = provider._fetch_csv()
        import api.services.alpha_vantage_provider as av

        self.assertIsNone(result)
        self.assertEqual(provider.status, av._STATUS_ERROR)
        self.assertIn("network down", provider.last_error)

    def test_missing_key_status_and_no_data(self):
        import api.services.alpha_vantage_provider as av

        provider = av.AlphaVantageEarningsProvider(api_key="")
        _reset_av_globals()
        status = provider.get_status()
        self.assertFalse(status["available"])
        self.assertEqual(status["status"], av._STATUS_MISSING_KEY)
        self.assertIn("ALPHA_VANTAGE_API_KEY", status["error"])
        self.assertIsNone(provider.get_earnings("AAPL"))
        self.assertEqual(provider.get_all_earnings(), {})

    def test_quota_cooldown_prevents_repeated_fetch(self):
        import api.services.alpha_vantage_provider as av

        provider = self._new_provider()
        provider.status = av._STATUS_QUOTA
        av._cooldown_until = 0.0
        with patch.object(provider, "_fetch_csv", return_value=None) as mock_fetch:
            provider._refresh_cache()  # sets the quota cooldown
            self.assertGreater(av._cooldown_until, 0)
            provider._refresh_cache()  # must be skipped by the cooldown
        mock_fetch.assert_called_once()

    def test_error_cooldown_is_shorter_than_quota(self):
        import api.services.alpha_vantage_provider as av

        provider = self._new_provider()
        provider.status = av._STATUS_ERROR
        av._cooldown_until = 0.0
        with patch.object(provider, "_fetch_csv", return_value=None):
            provider._refresh_cache()
        error_until = av._cooldown_until
        provider = self._new_provider()
        provider.status = av._STATUS_QUOTA
        with patch.object(provider, "_fetch_csv", return_value=None):
            provider._refresh_cache()
        quota_until = av._cooldown_until
        self.assertGreater(quota_until, error_until)

    def test_status_tracks_requests_and_cache_age(self):

        provider = self._new_provider()
        csv_text = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
            "AAPL,Apple Inc,2026-05-15,2026-04-30,2.35,USD,post-market\n"
        )
        resp = self._fake_resp(200, csv_text, headers={"Content-Type": "text/csv"})
        with patch("requests.get", return_value=resp):
            provider.get_earnings("AAPL")
        status = provider.get_status()
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["requests_today"], 1)
        self.assertEqual(status["daily_allowance"], 25)
        self.assertEqual(status["cache_entries"], 1)
        self.assertIsNotNone(status["cache_age_hours"])


class TestEarningsEventParsing(unittest.TestCase):
    """yfinance earnings-index parsing and ex-dividend calendar shapes."""

    def setUp(self):
        from api.services.iv_earnings_service import IVEarningsService

        self.service = IVEarningsService(database=MagicMock())
        self.service._alpha_vantage.api_key = ""
        _reset_av_globals()

    def test_parse_earnings_df_date_reads_index(self):
        """yfinance 1.5.x returns earnings dates in the DataFrame index."""
        import pandas as pd

        today = datetime.now().date()
        near = today + timedelta(days=10)
        far = today + timedelta(days=45)
        df = pd.DataFrame(
            {"EPS Estimate": [2.97, 3.00], "Reported EPS": [None, None], "Surprise(%)": [None, None]},
            index=pd.to_datetime([far, near]),
        )
        date_str, err = self.service._parse_earnings_df_date(df)
        self.assertIsNone(err)
        self.assertEqual(date_str, near.strftime("%Y-%m-%d"))

    def test_parse_earnings_df_date_skips_past_dates_when_future_exists(self):
        import pandas as pd

        today = datetime.now().date()
        past = today - timedelta(days=30)
        near = today + timedelta(days=5)
        df = pd.DataFrame({"a": [1, 2]}, index=pd.to_datetime([past, near]))
        date_str, _ = self.service._parse_earnings_df_date(df)
        self.assertEqual(date_str, near.strftime("%Y-%m-%d"))

    def test_parse_earnings_df_date_column_fallback(self):
        """Forward-compatible fallback when dates are a column, not the index."""
        import pandas as pd

        today = datetime.now().date()
        near = today + timedelta(days=7)
        df = pd.DataFrame(
            {"Earnings Date": [near.strftime("%Y-%m-%d"), (near + timedelta(days=40)).strftime("%Y-%m-%d")]}
        )
        date_str, err = self.service._parse_earnings_df_date(df)
        self.assertIsNone(err)
        self.assertEqual(date_str, near.strftime("%Y-%m-%d"))

    def test_parse_earnings_df_date_empty(self):
        import pandas as pd

        date_str, err = self.service._parse_earnings_df_date(pd.DataFrame())
        self.assertIsNone(date_str)
        self.assertIsNone(err)

    def test_parse_calendar_ex_dividend_dict_shape(self):
        """yfinance Ticker.calendar dict: 'Ex-Dividend Date' is a date object."""

        calendar = {
            "Dividend Date": datetime.now().date() + timedelta(days=40),
            "Ex-Dividend Date": datetime.now().date() + timedelta(days=10),
            "Earnings Date": [datetime.now().date() + timedelta(days=21)],
        }
        ex_div, err = self.service._parse_calendar_ex_dividend(calendar)
        self.assertIsNone(err)
        self.assertEqual(ex_div, (datetime.now().date() + timedelta(days=10)).strftime("%Y-%m-%d"))

    def test_parse_calendar_ex_dividend_none(self):
        ex_div, err = self.service._parse_calendar_ex_dividend(None)
        self.assertIsNone(err)
        self.assertIsNone(ex_div)

    def test_parse_calendar_ex_dividend_dataframe_shape(self):
        """Transposed DataFrame: index = event name, value in the row."""

        import pandas as pd

        frame = pd.DataFrame(
            {"value": [datetime.now().date() + timedelta(days=9), datetime.now().date() + timedelta(days=40)]},
            index=["Ex-Dividend Date", "Dividend Date"],
        )
        ex_div, err = self.service._parse_calendar_ex_dividend(frame)
        self.assertIsNone(err)
        self.assertEqual(ex_div, (datetime.now().date() + timedelta(days=9)).strftime("%Y-%m-%d"))

    def test_fetch_earnings_date_yfinance_index_repair(self):
        """Regression: yfinance earnings dates live in the index; ex-div from calendar."""
        import pandas as pd

        today = datetime.now().date()
        d1 = today + timedelta(days=21)
        df = pd.DataFrame(
            {"EPS Estimate": [2.97, 3.00], "Reported EPS": [None, None], "Surprise(%)": [None, None]},
            index=pd.to_datetime([d1, today + timedelta(days=90)]),
        )
        calendar_dict = {
            "Earnings Date": [d1],
            "Ex-Dividend Date": today + timedelta(days=10),
            "Dividend Date": today + timedelta(days=40),
        }
        mock_ticker = MagicMock()
        mock_ticker.get_earnings_dates.return_value = df
        mock_ticker.calendar = calendar_dict
        with (
            patch("api.services.iv_earnings_service.get_yfinance_ticker", return_value=mock_ticker),
            patch("api.services.iv_earnings_service.time.sleep", return_value=None),
        ):
            result = self.service.fetch_earnings_date("AAPL")
        self.assertTrue(result["success"])
        self.assertEqual(result["earnings_date"], d1.strftime("%Y-%m-%d"))
        self.assertEqual(result["earnings_source"], "yfinance")
        self.assertEqual(result["ex_dividend_date"], (today + timedelta(days=10)).strftime("%Y-%m-%d"))

    def test_fetch_earnings_date_surfaces_provider_error(self):
        """Provider failure must surface in provider_error, never raise."""
        mock_ticker = MagicMock()
        mock_ticker.get_earnings_dates.side_effect = RuntimeError("earnings service down")
        mock_ticker.calendar = {}
        with (
            patch("api.services.iv_earnings_service.get_yfinance_ticker", return_value=mock_ticker),
            patch("api.services.iv_earnings_service.time.sleep", return_value=None),
        ):
            result = self.service.fetch_earnings_date("AAPL")
        self.assertFalse(result["success"])
        self.assertIn("earnings service down", result["provider_error"])
        self.assertIsNone(result["earnings_date"])
        self.assertIn("provider_status", result)

    def test_get_earnings_info_includes_ex_dividend_and_provider_status(self):
        mock_db = MagicMock()
        mock_db.get_earnings_date.return_value = {
            "ticker": "AAPL",
            "earnings_date": "2026-05-15",
            "ex_dividend_date": "2026-05-08",
            "time_of_day": "post-market",
            "fiscal_date_ending": "2026-04-30",
            "estimate": 2.35,
            "currency": "USD",
            "earnings_source": "yfinance",
            "fetch_status": "success",
            "error_message": None,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.service.db = mock_db
        info = self.service.get_earnings_info("AAPL")
        self.assertEqual(info["ex_dividend_date"], "2026-05-08")
        self.assertEqual(info["ex_dividend_source"], "yfinance")
        self.assertIn("provider_status", info)
        self.assertIn("data_age_hours", info)
        # The date is in the past relative to any realistic run, so the day count
        # is explicitly None (never a negative count that could fire a rule).
        self.assertIsNone(info["days_to_ex_dividend"])

    def test_get_earnings_info_counts_days_to_upcoming_ex_dividend(self):
        mock_db = MagicMock()
        mock_db.get_earnings_date.return_value = {
            "ticker": "AAPL",
            "earnings_date": "2026-11-01",
            "ex_dividend_date": "2026-10-10",
            "time_of_day": "post-market",
            "earnings_source": "yfinance",
            "fetch_status": "success",
            "error_message": None,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.service.db = mock_db
        with patch("api.services.iv_earnings_service.market_now", return_value=datetime(2026, 9, 20, 13, 0)):
            info = self.service.get_earnings_info("AAPL")
        self.assertEqual(info["days_to_ex_dividend"], 20)

    def test_get_earnings_info_keeps_ex_dividend_when_earnings_unknown(self):
        """A dividend-only ticker must still expose the day count."""
        mock_db = MagicMock()
        mock_db.get_earnings_date.return_value = {
            "ticker": "SCHD",
            "earnings_date": None,
            "ex_dividend_date": "2026-09-25",
            "earnings_source": None,
            "fetch_status": "success",
            "error_message": None,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.service.db = mock_db
        with patch("api.services.iv_earnings_service.market_now", return_value=datetime(2026, 9, 20, 13, 0)):
            info = self.service.get_earnings_info("SCHD")
        self.assertIsNone(info["earnings_date"])
        self.assertEqual(info["days_to_ex_dividend"], 5)

    def test_days_to_ex_dividend_handles_unknown_and_past(self):
        from api.services.iv_earnings_service import IVEarningsService

        now = datetime(2026, 9, 20, 13, 0)
        self.assertIsNone(IVEarningsService._days_to_ex_dividend(None, now=now))
        self.assertIsNone(IVEarningsService._days_to_ex_dividend("", now=now))
        self.assertIsNone(IVEarningsService._days_to_ex_dividend("not-a-date", now=now))
        self.assertIsNone(IVEarningsService._days_to_ex_dividend("2026-09-19", now=now))
        self.assertEqual(IVEarningsService._days_to_ex_dividend("2026-09-20", now=now), 0)
        self.assertEqual(IVEarningsService._days_to_ex_dividend("2026-09-21", now=now), 1)


class TestStaleEventContextRefresh(unittest.TestCase):
    """Bounded, best-effort refresh of stale earnings/ex-dividend context."""

    def setUp(self):
        from api.services.iv_earnings_service import IVEarningsService

        self.service = IVEarningsService(database=MagicMock())
        _reset_av_globals()

    def test_refresh_stale_event_context_updates_only_stale(self):
        old = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
        fresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.service.db.get_all_earnings_dates.return_value = [
            {"ticker": "AAPL", "last_updated": old, "fetch_status": "success"},
            {"ticker": "MSFT", "last_updated": fresh, "fetch_status": "success"},
            {"ticker": "NVDA", "last_updated": old, "fetch_status": "success"},
        ]
        with patch.object(self.service, "update_earnings_data", return_value=True) as mock_update:
            result = self.service.refresh_stale_event_context(["AAPL", "MSFT", "NVDA", "AAPL"])
        self.assertEqual(result["refreshed"], 2)
        self.assertEqual(result["stale"], 2)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(sorted(c.args[0] for c in mock_update.call_args_list), ["AAPL", "NVDA"])

    def test_refresh_stale_event_context_counts_unknown_and_errored_as_stale(self):
        self.service.db.get_all_earnings_dates.return_value = []
        with patch.object(self.service, "update_earnings_data", return_value=False) as mock_update:
            result = self.service.refresh_stale_event_context(["NEW1", "NEW2"])
        self.assertEqual(result["refreshed"], 0)
        self.assertEqual(result["stale"], 2)
        self.assertEqual(result["errors"], 2)
        self.assertEqual(mock_update.call_count, 2)

    def test_refresh_stale_event_context_bounded_by_max_tickers(self):
        self.service.db.get_all_earnings_dates.return_value = []
        with patch.object(self.service, "update_earnings_data", return_value=True) as mock_update:
            result = self.service.refresh_stale_event_context(["A1", "A2", "A3", "A4", "A5", "A6"], max_tickers=3)
        self.assertEqual(result["refreshed"], 3)
        self.assertEqual(result["skipped"], 3)
        self.assertEqual(mock_update.call_count, 3)

    def test_refresh_stale_event_context_never_raises(self):
        self.service.db.get_all_earnings_dates.side_effect = RuntimeError("db down")
        result = self.service.refresh_stale_event_context(["AAPL"])
        self.assertEqual(result["refreshed"], 0)


class TestWheelRunnerEventContextHook(unittest.TestCase):
    """WheelRunner invokes the injected event-context refresher before the scan,
    and a provider failure there never aborts the broker scan."""

    def _build_runner(self, refresher=None):
        from core.wheel_runner import WheelRunner

        options = MagicMock()
        options._ensure_connection.return_value = MagicMock()
        options._get_portfolio_context.return_value = {
            "positions": {"AAPL": {}, "MSFT": {}},
            "short_calls": {},
            "short_puts": {},
            "cash_balance": 100.0,
        }
        options.recommendation_engine.get_top_recommendations.return_value = {
            "generated_at": "2026-01-01T00:00:00Z",
            "scan_coverage": {"scanned": 0, "total": 0, "complete": True},
            "errors": [],
            "signals": [],
            "blocked_signals": [],
            "watchlist_csps": {},
            "covered_calls": {},
            "quote_fetched_at": {},
            "watchlist_origins": {},
            "active_watchlist": {"tickers": [], "group_status": "ok"},
        }
        db = MagicMock()
        db.get_latest_portfolio_snapshot.return_value = None
        db.save_portfolio_transition.return_value = True
        runner = WheelRunner(
            db=db,
            options_service=options,
            config={"portfolio_env": "SIMULATE", "account_id": "SIM-0"},
            event_context_refresher=refresher,
        )
        return runner, options

    def test_event_context_refresher_invoked_once_before_scan(self):
        calls = []

        def refresher(pc):
            calls.append(pc)
            return {"refreshed": 1, "stale": 1, "skipped": 1, "errors": 0}

        runner, options = self._build_runner(refresher)
        with (
            patch("core.wheel_runner.resolve_account", return_value="SIM-0"),
            patch("core.wheel_runner.is_market_open", return_value=False),
        ):
            snapshot = runner.refresh()
        self.assertIsNotNone(snapshot)
        self.assertEqual(len(calls), 1)
        # The refresher is fed the freshly fetched portfolio context.
        self.assertIn("AAPL", calls[0]["positions"])
        options.recommendation_engine.get_top_recommendations.assert_called_once()

    def test_event_context_refresher_failure_never_aborts_scan(self):
        def refresher(pc):
            raise RuntimeError("provider is down")

        runner, options = self._build_runner(refresher)
        with (
            patch("core.wheel_runner.resolve_account", return_value="SIM-0"),
            patch("core.wheel_runner.is_market_open", return_value=False),
        ):
            snapshot = runner.refresh()
        self.assertIsNotNone(snapshot)
        # The broker scan still ran despite the refresher failing.
        options.recommendation_engine.get_top_recommendations.assert_called_once()

    def test_event_context_refresher_skipped_when_not_injected(self):
        runner, options = self._build_runner(None)
        with (
            patch("core.wheel_runner.resolve_account", return_value="SIM-0"),
            patch("core.wheel_runner.is_market_open", return_value=False),
        ):
            snapshot = runner.refresh()
        self.assertIsNotNone(snapshot)


def _daily_rows(ivs, start_day=1):
    """One observation per calendar day (the shape the rank reader expects)."""
    return [
        {
            "timestamp": f"2026-08-{start_day + i:02d} 15:30:00",
            "implied_volatility": iv,
            "stock_price": 150.0,
        }
        for i, iv in enumerate(ivs)
    ]


# Ten daily samples spanning 0.20-0.40, so a 0.30 current IV ranks at the midpoint.
_RANGE_IVS = [0.20, 0.22, 0.24, 0.26, 0.28, 0.32, 0.34, 0.36, 0.38, 0.40]


class TestIVNormalization(unittest.TestCase):
    """Test IV normalization for mixed decimal/percentage history."""

    def test_calculate_iv_rank_with_reliable_history(self):
        """IV rank should work correctly with pre-normalized history from repository."""
        from api.services.iv_earnings_service import IVEarningsService

        mock_db = MagicMock()
        # Repository normalizes on read, so _calculate_iv_rank receives normalized decimals
        mock_db.get_iv_history.return_value = _daily_rows(_RANGE_IVS)
        svc = IVEarningsService(database=mock_db)
        rank, status = svc._calculate_iv_rank("AAPL", 0.30)
        self.assertAlmostEqual(rank, 0.5, places=2)
        self.assertEqual(status, "normal")

    def test_calculate_iv_rank_with_all_high_values(self):
        """IV rank works correctly when current IV is at top of range."""
        from api.services.iv_earnings_service import IVEarningsService

        mock_db = MagicMock()
        mock_db.get_iv_history.return_value = _daily_rows(_RANGE_IVS)
        svc = IVEarningsService(database=mock_db)
        rank, status = svc._calculate_iv_rank("AAPL", 0.40)
        self.assertAlmostEqual(rank, 1.0, places=2)
        self.assertEqual(status, "normal")

    def test_zero_iv_returns_neutral_unknown_status(self):
        """Zero IV should return (0.5, 'unknown') instead of IV Rank 0%."""
        from api.services.iv_earnings_service import IVEarningsService

        mock_db = MagicMock()
        svc = IVEarningsService(database=mock_db)
        rank, status = svc._calculate_iv_rank("AAPL", 0.0)
        self.assertEqual(rank, 0.5)
        self.assertEqual(status, "unknown")

    def test_get_iv_environment_score_zero_iv_neutral(self):
        """Zero IV in environment score should return neutral score with unknown status."""
        from api.services.iv_earnings_service import IVEarningsService

        svc = IVEarningsService(database=MagicMock())
        score_adj, rank, status = svc.get_iv_environment_score("AAPL", 0.0)
        self.assertEqual(score_adj, 0)
        self.assertEqual(rank, 0.5)
        self.assertEqual(status, "unknown")

    def test_calculate_iv_rank_with_mixed_raw_history(self):
        """Mixed decimal/percentage raw history normalizes correctly to compute rank."""
        from api.services.iv_earnings_service import IVEarningsService

        mock_db = MagicMock()
        # Raw history with mixed decimal and percentage values — _calculate_iv_rank
        # must normalize each before computing rank.
        mixed = [0.20, 0.25, 30.0, 0.35, 40.0, 0.22, 0.24, 0.26, 0.28, 32.0]
        mock_db.get_iv_history.return_value = _daily_rows(mixed)
        svc = IVEarningsService(database=mock_db)
        # 0.30 (decimal) is at the midpoint of the normalized range [0.20, 0.40]
        rank, status = svc._calculate_iv_rank("AAPL", 0.30)
        self.assertAlmostEqual(rank, 0.5, places=2)
        self.assertEqual(status, "normal")

    def test_calculate_iv_rank_normalizes_current_iv(self):
        """current_iv passed as percentage is normalized before rank computation."""
        from api.services.iv_earnings_service import IVEarningsService

        mock_db = MagicMock()
        mock_db.get_iv_history.return_value = _daily_rows(_RANGE_IVS)
        svc = IVEarningsService(database=mock_db)
        # current_iv = 30.0 means 30% → normalized to 0.30
        rank, status = svc._calculate_iv_rank("AAPL", 30.0)
        self.assertAlmostEqual(rank, 0.5, places=2)
        self.assertEqual(status, "normal")


class TestIvDailyAtmSeries(unittest.TestCase):
    """The rank/percentile sample must describe the IV regime, not scan cadence.

    Raw rows are written per scored contract, so before this fix a 30-day
    min-max ran over dozens of same-day, different-strike observations.
    """

    def setUp(self):
        from api.services.iv_earnings_service import IVEarningsService

        self.service = IVEarningsService(database=MagicMock())

    def test_intraday_duplicates_collapse_to_one_sample_per_day(self):
        rows = [
            {"timestamp": "2026-08-01 09:35:00", "implied_volatility": 0.30, "strike": 100.0, "stock_price": 100.0},
            {"timestamp": "2026-08-01 11:00:00", "implied_volatility": 0.31, "strike": 105.0, "stock_price": 100.0},
            {"timestamp": "2026-08-01 14:00:00", "implied_volatility": 0.32, "strike": 95.0, "stock_price": 100.0},
        ]
        self.assertEqual(len(self.service._daily_atm_series(rows)), 1)

    def test_closest_to_atm_row_is_the_daily_sample(self):
        # Each day carries one ATM row (IV ~0.25) and one absurd deep-ITM row
        # (IV 0.90). If selection works, the 0.90 outliers never enter the series.
        rows = []
        for day in range(1, 11):
            rows.append(
                {
                    "timestamp": f"2026-08-{day:02d} 15:30:00",
                    "implied_volatility": 0.25,
                    "strike": 100.0,
                    "stock_price": 100.0,
                }
            )
            rows.append(
                {
                    "timestamp": f"2026-08-{day:02d} 15:30:00",
                    "implied_volatility": 0.90,
                    "strike": 60.0,
                    "stock_price": 100.0,
                }
            )
        series = self.service._daily_atm_series(rows)
        self.assertEqual(series, [0.25] * 10)

    def test_daily_median_fallback_without_strike(self):
        # Legacy rows (pre-v12) have no strike: the day's median is used, so a
        # single 0.90 outlier cannot distort the series.
        rows = []
        for day in range(1, 11):
            for iv in (0.10, 0.20, 0.90):
                rows.append({"timestamp": f"2026-08-{day:02d} 15:30:00", "implied_volatility": iv})
        self.assertEqual(self.service._daily_atm_series(rows), [0.20] * 10)

    def test_insufficient_history_suppresses_adjustment(self):
        self.service.db.get_iv_history.return_value = _daily_rows([0.20, 0.25, 0.30])
        rank, status = self.service._calculate_iv_rank("AAPL", 0.30)
        self.assertEqual(status, "insufficient_history")
        self.assertEqual((rank, status), (0.5, "insufficient_history"))
        score, rank, status = self.service.get_iv_environment_score("AAPL", 0.30)
        self.assertEqual(score, 0)
        self.assertEqual(status, "insufficient_history")

    def test_flat_observed_range_is_normal_not_missing(self):
        # Ten identical daily samples: history WAS observed and has no range.
        self.service.db.get_iv_history.return_value = _daily_rows([0.30] * 10)
        rank, status = self.service._calculate_iv_rank("AAPL", 0.30)
        self.assertEqual(rank, 0.5)
        self.assertEqual(status, "normal")

    def test_percentile_is_share_of_samples_below_current(self):
        series = [0.20, 0.20, 0.20, 0.40, 0.40, 0.40, 0.40, 0.40, 0.40, 0.40]
        self.assertAlmostEqual(self.service._calculate_iv_percentile(series, 0.30), 0.3, places=4)
        self.assertIsNone(self.service._calculate_iv_percentile([], 0.30))

    def test_percentile_exposed_and_cleared_when_history_insufficient(self):
        self.service.db.get_iv_history.return_value = _daily_rows(_RANGE_IVS)
        self.service.get_iv_environment_score("AAPL", 0.30)
        self.assertAlmostEqual(self.service.get_iv_percentile("AAPL"), 0.5, places=2)

        fresh = self.service._iv_cache["AAPL"]
        fresh["iv_status"] = "insufficient_history"
        self.assertIsNone(self.service.get_iv_percentile("AAPL"))

    def test_record_iv_data_does_not_seed_the_scoring_cache(self):
        # Seeding it published a rank computed from one contract's IV as the
        # ticker environment for every other contract until the TTL expired.
        self.service.record_iv_data("AAPL", 0.30, stock_price=150.0, strike=150.0)
        self.assertNotIn("AAPL", self.service._iv_cache)

    def test_record_iv_data_passes_strike_through(self):
        self.service.record_iv_data("AAPL", 0.30, stock_price=150.0, dte=30, strike=148.0)
        _args, kwargs = self.service.db.save_iv_data.call_args
        self.assertEqual(kwargs.get("strike"), 148.0)


if __name__ == "__main__":
    unittest.main()
