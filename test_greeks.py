"""Quick test to verify enrich_option_with_greeks works."""
from core.greeks import enrich_option_with_greeks

option = {
    'strike': 95.0,
    'expiration': '20260527',
    'option_type': 'PUT',
    'bid': 2.0,
    'ask': 2.20,
    'last': 2.10,
    'delta': 0,
    'gamma': 0,
    'theta': 0,
    'vega': 0,
    'implied_volatility': 0.30,
}

stock_price = 100.0
enrich_option_with_greeks(option, stock_price)

print(f"Delta: {option.get('delta')}")
print(f"Gamma: {option.get('gamma')}")
print(f"Theta: {option.get('theta')}")
print(f"Vega: {option.get('vega')}")
