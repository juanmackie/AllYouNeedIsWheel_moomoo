"""
Tests for PoP Service
"""
import unittest
from api.services.pop_service import calculate_pop_delta, calculate_pop_monte_carlo


class TestPoPService(unittest.TestCase):
    
    def test_pop_delta_call(self):
        """Delta-based PoP for CALL: PoP = 1 - delta"""
        # Delta = 0.22 means 22% chance of finishing ITM
        result = calculate_pop_delta('AAPL', 170, '20260530', 'CALL', 0.22, 0.25, 21)
        self.assertEqual(result['method'], 'delta')
        self.assertAlmostEqual(result['pop'], 0.78, places=2)  # 1 - 0.22 = 0.78
        self.assertAlmostEqual(result['pop_pct'], 78.0, places=1)
    
    def test_pop_delta_put(self):
        """Delta-based PoP for PUT: PoP = 1 - |delta|"""
        result = calculate_pop_delta('AAPL', 170, '20260530', 'PUT', -0.18, 0.25, 21)
        self.assertAlmostEqual(result['pop'], 0.82, places=2)  # 1 - 0.18 = 0.82
    
    def test_pop_delta_no_delta(self):
        """If no delta, return 50% default"""
        result = calculate_pop_delta('AAPL', 170, '20260530', 'CALL', None, 0.25, 21)
        self.assertEqual(result['pop'], 0.5)
    
    def test_pop_monte_carlo_fallback(self):
        """Monte Carlo falls back to delta if invalid params"""
        result = calculate_pop_monte_carlo('AAPL', 170, '20260530', 'PUT', None, None)
        self.assertEqual(result['method'], 'monte_carlo_fallback')


if __name__ == '__main__':
    unittest.main()
