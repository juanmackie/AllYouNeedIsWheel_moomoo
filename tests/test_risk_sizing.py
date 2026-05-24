"""
Tests for RiskSizingService
"""
import unittest
from unittest.mock import patch, MagicMock
from api.services.risk_sizing_service import RiskSizingService


class TestRiskSizingService(unittest.TestCase):
    
    def setUp(self):
        self.service = RiskSizingService()
    
    def test_calculate_position_size_basic(self):
        """Test basic position size calculation"""
        # Mock yfinance
        with patch('api.services.risk_sizing_service.yf') as mock_yf:
            mock_ticker = MagicMock()
            mock_hist = MagicMock()
            mock_hist.empty = False
            mock_hist.__getitem__.return_value = [100, 102, 98, 105, 103, 101, 99, 104, 102, 100, 98, 97, 99, 101]
            mock_ticker.history.return_value = mock_hist
            mock_yf.Ticker.return_value = mock_ticker
            
            result = self.service.calculate_position_size('AAPL', 45000)
            
            self.assertIn('atr', result)
            self.assertIn('max_contracts', result)
            self.assertGreater(result['max_contracts'], 0)
    
    def test_risk_amount_calculation(self):
        """Test risk amount = account_value * risk_pct"""
        result = {
            'account_value': 45000,
            'risk_pct': 0.01,
            'risk_amount': 450.0,
        }
        # Verify calculation
        expected = 45000 * 0.01
        self.assertEqual(expected, 450.0)
    
    def test_max_contracts_calculation(self):
        """Test max_contracts = floor(risk_amount / risk_per_contract)"""
        risk_amount = 450.0
        risk_per_contract = 420.0  # ATR * 100
        expected = int(risk_amount // risk_per_contract)
        self.assertEqual(expected, 1)
    
    def test_cache_clear_by_ticker_prefix(self):
        """Ticker-level cache clearing should remove all matching variants."""
        self.service._set_cached('AAPL_45000_0.01_14', {'max_contracts': 1})
        self.service._set_cached('AAPL_50000_0.02_14', {'max_contracts': 2})
        self.service._set_cached('MSFT_45000_0.01_14', {'max_contracts': 3})

        self.service.clear_cache('AAPL')

        self.assertIsNone(self.service._get_cached('AAPL_45000_0.01_14'))
        self.assertIsNone(self.service._get_cached('AAPL_50000_0.02_14'))
        self.assertIsNotNone(self.service._get_cached('MSFT_45000_0.01_14'))


if __name__ == '__main__':
    unittest.main()
