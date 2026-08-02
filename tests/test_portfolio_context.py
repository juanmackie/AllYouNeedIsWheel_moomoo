"""
Tests for api/services/portfolio_context.py - PortfolioContext class
"""

import unittest
from unittest.mock import MagicMock, Mock


class TestPortfolioContextInit(unittest.TestCase):
    """Test PortfolioContext initialization"""

    def test_init_with_providers(self):
        from api.services.portfolio_context import PortfolioContext

        portfolio_provider = Mock()
        portfolio_provider.portfolio_service = Mock()
        vix_provider = Mock()
        config_provider = Mock()
        config_provider.config = {"db_path": ":memory:"}

        ctx = PortfolioContext(
            portfolio_service_provider=portfolio_provider,
            vix_regime_provider=vix_provider,
            config_provider=config_provider,
        )
        self.assertIs(ctx._portfolio_service_provider, portfolio_provider)
        self.assertEqual(ctx.config, {"db_path": ":memory:"})

    def test_init_without_config_provider(self):
        from api.services.portfolio_context import PortfolioContext

        portfolio_provider = Mock()
        ctx = PortfolioContext(portfolio_service_provider=portfolio_provider)
        self.assertEqual(ctx.config, {})


class TestPortfolioContextBuild(unittest.TestCase):
    """Test get_portfolio_context"""

    def setUp(self):
        from api.services.portfolio_context import PortfolioContext

        self.portfolio_provider = Mock()
        self.portfolio_service = Mock()
        self.portfolio_provider.portfolio_service = self.portfolio_service
        self.ctx = PortfolioContext(portfolio_service_provider=self.portfolio_provider)

    def test_default_context_when_service_returns_none(self):
        self.portfolio_service.get_portfolio_summary.return_value = None
        self.portfolio_service.get_positions.return_value = None

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context["cash_balance"], 0.0)
        self.assertEqual(context["account_value"], 0.0)
        self.assertEqual(context["positions"], {})
        self.assertEqual(context["vix_regime"]["regime"], "normal")

    def test_populates_cash_and_account_value(self):
        self.portfolio_service.get_portfolio_summary.return_value = {
            "available_cash": 5000.0,
            "account_value": 100000.0,
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context["cash_balance"], 5000.0)
        self.assertEqual(context["account_value"], 100000.0)

    def test_aggregates_short_calls_and_puts(self):
        self.portfolio_service.get_portfolio_summary.return_value = {
            "available_cash": 10000.0,
            "account_value": 50000.0,
        }
        self.portfolio_service.get_positions.side_effect = lambda t=None: {
            "STK": [{"symbol": "US.AAPL", "position": 100}],
            "OPT": [
                {"symbol": "US.AAPL240315C00200000", "position": -5, "option_type": "CALL"},
                {"symbol": "US.AAPL240315P00150000", "position": -3, "option_type": "PUT"},
            ],
        }.get(t, [])

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context["short_calls"]["AAPL240315C00200000"], 5)
        self.assertEqual(context["short_puts"]["AAPL240315P00150000"], 3)

    def test_ignores_long_positions(self):
        self.portfolio_service.get_portfolio_summary.return_value = {"cash_balance": 10000.0, "account_value": 50000.0}
        self.portfolio_service.get_positions.side_effect = lambda t=None: {
            "OPT": [
                {"symbol": "US.AAPL240315C00200000", "position": 5, "option_type": "CALL"},
            ]
        }.get(t, [])

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context["short_calls"], {})

    def test_cached_context_uses_snapshot_without_refresh(self):
        self.portfolio_service.peek_cached_portfolio.return_value = {
            "available_cash": 5000.0,
            "account_value": 100000.0,
            "excess_liquidity": 6000.0,
            "positions": {
                "US.AAPL": {"shares": 100, "security_type": "STK", "avg_cost": 150.0},
                "US.AAPL240315P00150000": {
                    "shares": -2,
                    "security_type": "OPT",
                    "option_type": "PUT",
                    "strike": 150.0,
                    "avg_cost": 3.0,
                },
            },
        }

        context = self.ctx.get_portfolio_context(refresh=False)

        self.portfolio_service.get_portfolio_summary.assert_not_called()
        self.portfolio_service.get_positions.assert_not_called()
        self.portfolio_service.peek_cached_portfolio.assert_called_once()
        self.assertEqual(context["cash_balance"], 5000.0)
        self.assertEqual(context["account_value"], 100000.0)
        self.assertEqual(context["broker_buying_power"], 6000.0)
        self.assertEqual(context["short_puts"]["AAPL240315P00150000"], 2)
        self.assertEqual(context["cash_reserved_for_csp"], 30000.0)
        self.assertEqual(context["cash_available_for_csp"], 0.0)
        self.assertEqual(context["vix_regime"]["regime"], "normal")


class TestPortfolioContextHelpers(unittest.TestCase):
    """Test helper methods"""

    def setUp(self):
        from api.services.portfolio_context import PortfolioContext

        self.provider = Mock()
        self.provider.portfolio_service = Mock()
        self.ctx = PortfolioContext(portfolio_service_provider=self.provider)

    def test_get_position_snapshot_found(self):
        context = {"positions": {"AAPL": {"shares": 10, "avg_cost": 150}}}
        result = self.ctx._get_position_snapshot(context, "AAPL")
        self.assertEqual(result["shares"], 10)

    def test_get_position_snapshot_not_found(self):
        context = {"positions": {}}
        result = self.ctx._get_position_snapshot(context, "AAPL")
        self.assertEqual(result, {})

    def test_get_fallback_stock_price_from_market_price(self):
        context = {"positions": {"AAPL": {"market_price": 155.5, "avg_cost": 150}}}
        result = self.ctx._get_fallback_stock_price(context, "AAPL")
        self.assertEqual(result, 155.5)

    def test_get_fallback_stock_price_from_avg_cost(self):
        context = {"positions": {"AAPL": {"market_price": 0, "avg_cost": 150.0}}}
        result = self.ctx._get_fallback_stock_price(context, "AAPL")
        self.assertEqual(result, 150.0)

    def test_calculate_cash_reserved_no_short_puts(self):
        context = {"short_puts": {}}
        result = self.ctx._calculate_cash_reserved(context)
        self.assertEqual(result, 0.0)

    def test_calculate_cash_reserved_with_short_puts(self):
        context = {"short_puts": {"dummy": 1}}
        self.provider.portfolio_service.get_positions.return_value = [
            {"symbol": "US.AAPL240315P00150000", "position": -2, "option_type": "PUT", "strike": 150}
        ]
        result = self.ctx._calculate_cash_reserved(context)
        self.assertEqual(result, 30000.0)

    def test_calculate_cash_reserved_service_error_returns_zero(self):
        context = {"short_puts": {"dummy": 1}}
        self.provider.portfolio_service.get_positions.side_effect = Exception("Service unavailable")
        result = self.ctx._calculate_cash_reserved(context)
        self.assertEqual(result, 0.0)


class TestPortfolioContextCSPFields(unittest.TestCase):
    """Test CSP-specific fields in portfolio context."""

    def setUp(self):
        from api.services.portfolio_context import PortfolioContext

        self.provider = MagicMock()
        self.portfolio_service = MagicMock()
        self.provider.portfolio_service = self.portfolio_service
        self.ctx = PortfolioContext(portfolio_service_provider=self.provider)

    def test_cash_available_for_csp_present(self):
        """Context should include cash_available_for_csp and cash_reserved_for_csp."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            "available_cash": 50000.0,
            "account_value": 100000.0,
            "excess_liquidity": 55000.0,
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertIn("cash_available_for_csp", context)
        self.assertIn("cash_reserved_for_csp", context)
        self.assertIn("available_cash", context)

    def test_csp_cash_reduced_by_open_short_puts(self):
        """CSP cash is broker capacity minus collateral already tied up by open short puts."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            "available_cash": 50000.0,
            "account_value": 100000.0,
            "excess_liquidity": 50000.0,
        }
        self.portfolio_service.get_positions.side_effect = lambda t=None: {
            "STK": [],
            "OPT": [
                {"symbol": "US.AAPL240315P00150000", "position": -2, "option_type": "PUT", "strike": 150.0},
            ],
        }.get(t, [])

        context = self.ctx.get_portfolio_context()
        # 2 short puts * 150 strike * 100 = 30000 open collateral
        self.assertEqual(context["cash_reserved_for_csp"], 30000.0)
        self.assertEqual(context["open_short_put_collateral"], 30000.0)
        self.assertEqual(context["broker_buying_power"], 50000.0)
        self.assertEqual(context["cash_available_for_csp"], 20000.0)

    def test_margin_buying_power_funds_csp_when_withdrawable_cash_is_zero(self):
        """Margin accounts can secure CSPs from broker cash power even when withdrawable cash is zero."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            "available_cash": 0.0,
            "account_value": 100000.0,
            "buying_power": 60000.0,
        }
        self.portfolio_service.get_positions.side_effect = lambda t=None: {
            "STK": [],
            "OPT": [
                {"symbol": "US.AAPL240315P00150000", "position": -2, "option_type": "PUT", "strike": 150.0},
            ],
        }.get(t, [])

        context = self.ctx.get_portfolio_context()

        self.assertEqual(context["available_cash"], 0.0)
        self.assertEqual(context["broker_buying_power"], 60000.0)
        self.assertEqual(context["cash_available_for_csp"], 30000.0)
        self.assertEqual(
            context["_cash_diagnostics"]["cash_available_for_csp_source"],
            "buying_power_minus_open_short_put_collateral",
        )

    def test_net_cash_power_does_not_double_subtract_open_short_put_collateral(self):
        """Moomoo net cash power is already collateral-adjusted and should be CSP cash truth."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            "available_cash": 57360.19,
            "account_value": 100000.0,
            "buying_power": 1209.66,
            "buying_power_source": "usd_net_cash_power",
        }
        self.portfolio_service.get_positions.side_effect = lambda t=None: {
            "STK": [],
            "OPT": [
                {"symbol": "US.ORCL240315P00150000", "position": -3, "option_type": "PUT", "strike": 150.0},
            ],
        }.get(t, [])

        context = self.ctx.get_portfolio_context()

        self.assertEqual(context["broker_buying_power"], 1209.66)
        self.assertEqual(context["broker_buying_power_source"], "usd_net_cash_power")
        self.assertEqual(context["open_short_put_collateral"], 45000.0)
        self.assertEqual(context["cash_available_for_csp"], 1209.66)
        self.assertEqual(
            context["_cash_diagnostics"]["cash_available_for_csp_source"],
            "usd_net_cash_power",
        )

    def test_csp_cash_with_no_short_puts(self):
        """cash_available_for_csp should use broker buying power when present."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            "available_cash": 50000.0,
            "account_value": 100000.0,
            "excess_liquidity": 60000.0,
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context["broker_buying_power"], 60000.0)
        self.assertEqual(context["cash_available_for_csp"], 60000.0)
        self.assertEqual(context["cash_reserved_for_csp"], 0.0)

    def test_available_cash_uses_true_cash_not_excess_liquidity(self):
        """available_cash should use cash-like fields, not excess liquidity."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            "available_cash": 30000.0,
            "account_value": 100000.0,
            "excess_liquidity": 60000.0,
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context["available_cash"], 30000.0)
        self.assertEqual(context["broker_buying_power"], 60000.0)

    def test_cash_fallback_prefers_available_cash(self):
        """available_cash is the first field checked — should win when positive."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            "available_cash": 50000.0,
            "cash_balance": 30000.0,
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context["available_cash"], 50000.0)

    def test_cash_fallback_second_field_cash_balance(self):
        """When available_cash is missing, cash_balance should be used."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            "cash_balance": 40000.0,
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context["available_cash"], 40000.0)

    def test_cash_fallback_third_field_cash_available(self):
        """When first two fields are missing, cash_available should be used."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            "cash_available": 35000.0,
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context["available_cash"], 35000.0)

    def test_cash_fallback_buying_power_is_csp_cash_not_true_cash(self):
        """buying_power should stay broker capacity and fund CSP cash."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            "buying_power": 25000.0,
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context["available_cash"], 0.0)
        self.assertEqual(context["broker_buying_power"], 25000.0)
        self.assertEqual(context["cash_available_for_csp"], 25000.0)

    def test_cash_fallback_excess_liquidity_is_csp_cash_not_true_cash(self):
        """excess_liquidity should stay broker capacity and fund CSP cash."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            "excess_liquidity": 20000.0,
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context["available_cash"], 0.0)
        self.assertEqual(context["broker_buying_power"], 20000.0)
        self.assertEqual(context["cash_available_for_csp"], 20000.0)

    def test_cash_fallback_skips_zero_values(self):
        """Should skip zero values and try the next field."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            "available_cash": 0.0,
            "cash_balance": 0.0,
            "cash_available": 15000.0,
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context["available_cash"], 15000.0)

    def test_cash_fallback_keeps_excess_liquidity_separate(self):
        """available_cash should not be maxed with excess_liquidity."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            "available_cash": 30000.0,
            "excess_liquidity": 60000.0,
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context["available_cash"], 30000.0)
        self.assertEqual(context["broker_buying_power"], 60000.0)

    def test_cash_diagnostics_in_context(self):
        """_cash_diagnostics should be present with broker_buying_power."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            "available_cash": 50000.0,
            "cash_balance": 45000.0,
            "excess_liquidity": 55000.0,
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertIn("_cash_diagnostics", context)
        diag = context["_cash_diagnostics"]
        self.assertIn("raw_summary_fields", diag)
        self.assertEqual(diag["raw_summary_fields"]["available_cash"], 50000.0)
        self.assertEqual(diag["raw_summary_fields"]["cash_balance"], 45000.0)
        self.assertEqual(diag["available_cash"], 50000.0)
        self.assertEqual(diag["broker_buying_power"], 55000.0)
        self.assertEqual(diag["available_cash_source"], "available_cash")
        self.assertEqual(diag["broker_buying_power_source"], "excess_liquidity")
        self.assertEqual(diag["excess_liquidity"], 55000.0)

    def test_stock_positions_are_keyed_by_prefixed_and_bare_symbol(self):
        """Scoring should find held shares for either US.AAPL or AAPL requests."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            "available_cash": 50000.0,
            "account_value": 100000.0,
            "excess_liquidity": 50000.0,
        }
        self.portfolio_service.get_positions.side_effect = lambda t=None: {
            "STK": [
                {"symbol": "US.UBER", "position": 100, "avg_cost": 70.0},
            ],
            "OPT": [],
        }.get(t, [])

        context = self.ctx.get_portfolio_context()

        self.assertIn("UBER", context["positions"])
        self.assertIn("US.UBER", context["positions"])
        self.assertEqual(context["positions"]["UBER"], context["positions"]["US.UBER"])


if __name__ == "__main__":
    unittest.main()
