"""
Tests for api/services/portfolio_context.py - PortfolioContext class
"""

import unittest
from unittest.mock import Mock, patch, MagicMock


class TestPortfolioContextInit(unittest.TestCase):
    """Test PortfolioContext initialization"""

    def test_init_with_providers(self):
        from api.services.portfolio_context import PortfolioContext
        portfolio_provider = Mock()
        portfolio_provider.portfolio_service = Mock()
        vix_provider = Mock()
        config_provider = Mock()
        config_provider.config = {'db_path': ':memory:'}

        ctx = PortfolioContext(
            portfolio_service_provider=portfolio_provider,
            vix_regime_provider=vix_provider,
            config_provider=config_provider
        )
        self.assertIs(ctx._portfolio_service_provider, portfolio_provider)
        self.assertEqual(ctx.config, {'db_path': ':memory:'})

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
        self.ctx = PortfolioContext(
            portfolio_service_provider=self.portfolio_provider
        )

    def test_default_context_when_service_returns_none(self):
        self.portfolio_service.get_portfolio_summary.return_value = None
        self.portfolio_service.get_positions.return_value = None

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context['cash_balance'], 0.0)
        self.assertEqual(context['account_value'], 0.0)
        self.assertEqual(context['positions'], {})
        self.assertEqual(context['vix_regime']['regime'], 'normal')

    def test_populates_cash_and_account_value(self):
        self.portfolio_service.get_portfolio_summary.return_value = {
            'available_cash': 5000.0,
            'account_value': 100000.0
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context['cash_balance'], 5000.0)
        self.assertEqual(context['account_value'], 100000.0)

    def test_aggregates_short_calls_and_puts(self):
        self.portfolio_service.get_portfolio_summary.return_value = {
            'available_cash': 10000.0,
            'account_value': 50000.0
        }
        self.portfolio_service.get_positions.side_effect = lambda t=None: {
            'STK': [{'symbol': 'US.AAPL', 'position': 100}],
            'OPT': [
                {'symbol': 'US.AAPL240315C00200000', 'position': -5, 'option_type': 'CALL'},
                {'symbol': 'US.AAPL240315P00150000', 'position': -3, 'option_type': 'PUT'},
            ]
        }.get(t, [])

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context['short_calls']['AAPL240315C00200000'], 5)
        self.assertEqual(context['short_puts']['AAPL240315P00150000'], 3)

    def test_ignores_long_positions(self):
        self.portfolio_service.get_portfolio_summary.return_value = {
            'cash_balance': 10000.0, 'account_value': 50000.0
        }
        self.portfolio_service.get_positions.side_effect = lambda t=None: {
            'OPT': [
                {'symbol': 'US.AAPL240315C00200000', 'position': 5, 'option_type': 'CALL'},
            ]
        }.get(t, [])

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context['short_calls'], {})


class TestPortfolioContextHelpers(unittest.TestCase):
    """Test helper methods"""

    def setUp(self):
        from api.services.portfolio_context import PortfolioContext
        self.provider = Mock()
        self.provider.portfolio_service = Mock()
        self.ctx = PortfolioContext(portfolio_service_provider=self.provider)

    def test_get_position_snapshot_found(self):
        context = {'positions': {'AAPL': {'shares': 10, 'avg_cost': 150}}}
        result = self.ctx._get_position_snapshot(context, 'AAPL')
        self.assertEqual(result['shares'], 10)

    def test_get_position_snapshot_not_found(self):
        context = {'positions': {}}
        result = self.ctx._get_position_snapshot(context, 'AAPL')
        self.assertEqual(result, {})

    def test_get_fallback_stock_price_from_market_price(self):
        context = {'positions': {'AAPL': {'market_price': 155.5, 'avg_cost': 150}}}
        result = self.ctx._get_fallback_stock_price(context, 'AAPL')
        self.assertEqual(result, 155.5)

    def test_get_fallback_stock_price_from_avg_cost(self):
        context = {'positions': {'AAPL': {'market_price': 0, 'avg_cost': 150.0}}}
        result = self.ctx._get_fallback_stock_price(context, 'AAPL')
        self.assertEqual(result, 150.0)

    def test_calculate_cash_reserved_no_short_puts(self):
        context = {'short_puts': {}}
        result = self.ctx._calculate_cash_reserved(context)
        self.assertEqual(result, 0.0)

    def test_calculate_cash_reserved_with_short_puts(self):
        context = {'short_puts': {'dummy': 1}}
        self.provider.portfolio_service.get_positions.return_value = [
            {'symbol': 'US.AAPL240315P00150000', 'position': -2, 'option_type': 'PUT', 'strike': 150}
        ]
        result = self.ctx._calculate_cash_reserved(context)
        self.assertEqual(result, 30000.0)

    def test_calculate_cash_reserved_service_error_returns_zero(self):
        context = {'short_puts': {'dummy': 1}}
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
            'available_cash': 50000.0,
            'account_value': 100000.0,
            'excess_liquidity': 55000.0,
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertIn('cash_available_for_csp', context)
        self.assertIn('cash_reserved_for_csp', context)
        self.assertIn('available_cash', context)

    def test_csp_buying_power_reduced_by_short_puts(self):
        """cash_available_for_csp should be reduced for existing short puts."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            'available_cash': 50000.0,
            'account_value': 100000.0,
            'excess_liquidity': 50000.0,
        }
        self.portfolio_service.get_positions.side_effect = lambda t=None: {
            'STK': [],
            'OPT': [
                {'symbol': 'US.AAPL240315P00150000', 'position': -2,
                 'option_type': 'PUT', 'strike': 150.0},
            ]
        }.get(t, [])

        context = self.ctx.get_portfolio_context()
        # 2 short puts * 150 strike * 100 = 30000 reserved
        self.assertEqual(context['cash_reserved_for_csp'], 30000.0)
        # 50000 - 30000 = 20000 available
        self.assertEqual(context['cash_available_for_csp'], 20000.0)

    def test_csp_buying_power_with_no_short_puts(self):
        """cash_available_for_csp should equal available_cash when no short puts."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            'available_cash': 50000.0,
            'account_value': 100000.0,
            'excess_liquidity': 60000.0,
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context['cash_available_for_csp'], 60000.0)
        self.assertEqual(context['cash_reserved_for_csp'], 0.0)

    def test_available_cash_uses_excess_liquidity(self):
        """available_cash should prefer excess_liquidity over cash_balance."""
        self.portfolio_service.get_portfolio_summary.return_value = {
            'available_cash': 30000.0,
            'account_value': 100000.0,
            'excess_liquidity': 60000.0,
        }
        self.portfolio_service.get_positions.return_value = []

        context = self.ctx.get_portfolio_context()
        self.assertEqual(context['available_cash'], 60000.0)


if __name__ == '__main__':
    unittest.main()
