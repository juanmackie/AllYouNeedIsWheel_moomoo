# Scoring Algorithm Documentation

This document provides detailed technical documentation of the option play scoring algorithm implemented in AllYouNeedIsWheel.

## Overview

The scoring system uses a multi-factor weighted approach to rank option plays (both CALLs for covered calls and PUTs for cash-secured puts). The goal is to identify plays that offer the best risk-adjusted returns while avoiding dangerous scenarios.

## Core Philosophy

The algorithm prioritizes:
1. **Risk-adjusted returns** over raw yield
2. **Income quality** (theta per unit of risk) over just premium
3. **Probability-weighted outcomes** over win rate alone
4. **Capital efficiency** for portfolio-level optimization
5. **IV environment awareness** to avoid selling in poor conditions

## Scoring Components

### Phase 1: Risk-Adjusted Metrics

#### 1. IV-Adjusted Return (25% weight)

**Purpose:** Filter out dangerous "picking up pennies" scenarios where high premiums in low IV environments don't compensate for the risk.

**Formula:**
```
annualized_return = (premium_per_contract / (stock_price * 100)) * (365 / dte) * 100
iv_adjusted_return = annualized_return / max(implied_volatility, 0.05)
iv_adjusted_score = clamp(iv_adjusted_return / target_iv_adjusted, 0, 1)
```

**Target Values:**
- Default target: 50
- CALLs: Uses stock_price * 100 as denominator
- PUTs: Uses strike * 100 (cash_required) as denominator

**Example:**
- Premium: $1.50 ($150 per contract)
- Stock price: $150
- DTE: 30
- IV: 0.20 (20%)
- Annualized: (150 / 15000) * (365/30) * 100 = 12.17%
- IV-Adjusted: 12.17 / 0.20 = 60.85
- Score: min(60.85 / 50, 1.0) = 1.0 (100%)

#### 2. Theta/Delta Risk Ratio (20% weight)

**Purpose:** Measure daily income per unit of directional risk. Higher theta relative to delta = better income for less directional exposure.

**Formula:**
```
theta_delta_ratio = abs(theta) / (abs(delta) * stock_price)
tdr_score = clamp(theta_delta_ratio / target_theta_delta_ratio, 0, 1)
```

**Target Value:** 0.005 (0.5% daily income per delta unit)

**Interpretation:**
- Ratio of 0.006 = 100% score (excellent)
- Ratio of 0.003 = 60% score (good)
- Ratio of 0.001 = 20% score (poor)

#### 3. Expected Value (15-18% weight)

**Purpose:** Account for both probability of profit and magnitude of potential losses.

**Formula:**
```
PoP = 1 - abs(delta)  # Probability of Profit (delta approximation)

if CALL:
    max_loss_estimate = stock_price * 100 * 0.05  # 5% drop assumption
else:  # PUT
    max_loss_estimate = strike * 100 * 0.10     # 10% assignment drop

expected_value = (PoP * premium_per_contract) - ((1 - PoP) * max_loss_estimate)
ev_score = clamp(expected_value / premium_per_contract, 0, 1)
```

**Why These Loss Assumptions?**
- CALL: If assigned, you keep premium + upside to strike. 5% buffer accounts for opportunity cost of called stock.
- PUT: If assigned, stock drops to strike. 10% accounts for typical post-assignment depreciation.

**Example:**
- Premium: $150
- Delta: 0.20 (CALL)
- PoP: 80%
- Max loss estimate: $750 (5% of $150 stock × 100)
- Expected value: (0.8 × 150) - (0.2 × 750) = 120 - 150 = -$30
- Score: clamp(-30 / 150, 0, 1) = 0 (poor expected value)

#### 4. Liquidity Score (15-18% weight)

**Purpose:** Ensure the play can be entered/exited without excessive slippage.

**Formula:**
```
oi_score = clamp(open_interest / ideal_open_interest, 0, 1)
volume_score = clamp(volume / ideal_volume, 0, 1)
spread_score = clamp(1 - (spread_pct / ideal_spread_pct), 0, 1)

liquidity_score = (oi_score * 0.45) + (volume_score * 0.2) + (spread_score * 0.35)
```

**Ideal Values:**
- Open Interest: 500 contracts
- Volume: 100 contracts
- Spread: < 12%

**Weight Adjustments by Profile:**
- Weeklies: Multiplier 1.5 (effective 27-35% weight)
- Monthlies: Multiplier 1.0 (standard weight)
- Quarterlies: Multiplier 0.75 (effective 11-15% weight)

### Phase 2: IV Environment & Additional Factors

#### 5. IV Environment Score (Phase 2 Enhancement)

**IV Rank Calculation:**
```
iv_rank = (current_iv - min_30d_iv) / (max_30d_iv - min_30d_iv)
```

**Score Adjustments:**
| IV Rank | Adjustment | Status |
|---------|------------|--------|
| < 20% | -20% | Extreme low (dangerous) |
| 20-30% | -10% | Low IV warning |
| 30-40% | -5% | Below average |
| 40-60% | 0 | Normal range |
| 60-70% | +5% | Above average |
| 70-80% | +10% | Good premium environment |
| > 80% | +20% | Excellent IV |

**Application:**
```
score = base_score * (1 + iv_env_adjustment / 100)
```

#### 6. Earnings Impact (Phase 2 Enhancement)

**Score Adjustments:**
| Days to Earnings | Adjustment | Warning Level |
|------------------|------------|---------------|
| 0 (today) | -30% | 🚨 Extreme |
| 1-3 days | -15% | ⚠️ Very soon |
| 4-7 days | -5% | Soon |
| > 7 days | 0 | None |

**Why Penalties?**
- Earnings week has elevated IV (good premium) but extreme assignment risk
- Post-earnings IV crush can make rolls difficult
- Binary event risk not captured by delta alone

### Option-Specific Components

#### CALL-Specific (Covered Calls)

**Upside Potential (12% weight):**
```
if_called_return = ((strike - stock_price) + mid_price) / stock_price * 100
upside_score = clamp(if_called_return / target_if_called, 0, 1)
```
Target: 12% total return if assigned

**Cost Basis Protection (Multiplier):**
```
if avg_cost > 0 and strike < avg_cost:
    cost_basis_score = clamp(1 - ((avg_cost - strike) / avg_cost) * 4, 0, 1)
else:
    cost_basis_score = 1.0

final_score = score * (0.65 + 0.35 * cost_basis_score)
```

Strike below cost basis significantly reduces score (protects against locking in losses).

#### PUT-Specific (Cash-Secured Puts)

**Breakeven Buffer (12% weight):**
```
breakeven = strike - mid_price
buffer_pct = ((stock_price - breakeven) / stock_price) * 100
buffer_score = clamp(buffer_pct / target_buffer, 0, 1)
```
Target: Same as desired OTM percentage

**Capital Efficiency (10% weight):**
```
capital_efficiency = annualized_return / (cash_required / account_value)
ce_score = clamp(capital_efficiency / target_capital_eff, 0, 1)
```
Target: 100

Prioritizes CSPs that use less total capital percentage (allows more diversification).

**Cash Fit (Multiplier):**
```
capital_fit = clamp(cash_balance / cash_required, 0, 1)
final_score = score * (0.75 + 0.25 * capital_fit)
```

## Dynamic Screening Profiles

### Profile Detection

System auto-detects expiration type by DTE:
```
if dte <= 14:
    profile_type = 'weekly'
elif dte <= 45:
    profile_type = 'monthly'
else:
    profile_type = 'quarterly'
```

### Profile Parameters

#### Weekly (0-14 DTE)
```python
{
    'min_dte': 3,
    'max_dte': 14,
    'preferred_dte': 7,
    'target_delta': 0.18,  # Tighter
    'delta_tolerance': 0.14,
    'min_premium_per_contract': 8,  # Lower bar
    'liquidity_weight_multiplier': 1.5,  # 35% effective
    'delta_fit_weight_multiplier': 0.5,  # 8% effective
}
```

**Rationale:** With only 7-14 days, execution slippage matters more. Tighter delta targeting ensures faster decay.

#### Monthly (15-45 DTE) — Default
```python
{
    'min_dte': 5,
    'max_dte': 35,
    'preferred_dte': 14,
    'target_delta': 0.24,
    'delta_tolerance': 0.18,
    'min_premium_per_contract': 12,
    'liquidity_weight_multiplier': 1.0,
    'delta_fit_weight_multiplier': 1.0,
}
```

**Rationale:** Standard wheel strategy sweet spot. 30-45 days gives time for rolls if needed.

#### Quarterly (46-90 DTE)
```python
{
    'min_dte': 46,
    'max_dte': 90,
    'preferred_dte': 60,
    'target_delta': 0.28,  # Wider
    'delta_tolerance': 0.22,
    'min_premium_per_contract': 25,  # Higher bar
    'liquidity_weight_multiplier': 0.75,  # 15% effective
    'delta_fit_weight_multiplier': 1.2,  # 18% effective
}
```

**Rationale:** With 60+ days, strike selection matters more than spreads. Higher premiums required to justify long hold.

## Complete Score Calculation

### CALL Example

```python
# Base Phase 1 score
base_score = (
    iv_adjusted_score * 0.25 +
    tdr_score * 0.20 +
    liquidity_score * 0.18 +
    ev_score * 0.15 +
    upside_score * 0.12 +
    otm_score * 0.10
) * 100

# Phase 2 adjustments
iv_adjusted = base_score * (1 + iv_env_adjustment / 100)  # -20 to +20
earnings_adjusted = iv_adjusted * (1 + earnings_adjustment / 100)  # -30 to 0
final_score = earnings_adjusted * (0.65 + 0.35 * cost_basis_score)
```

### PUT Example

```python
# Base Phase 1 score
base_score = (
    iv_adjusted_score * 0.25 +
    tdr_score * 0.20 +
    ev_score * 0.18 +
    liquidity_score * 0.15 +
    buffer_score * 0.12 +
    ce_score * 0.10
) * 100

# Phase 2 adjustments (same as CALL)
iv_adjusted = base_score * (1 + iv_env_adjustment / 100)
earnings_adjusted = iv_adjusted * (1 + earnings_adjustment / 100)
final_score = earnings_adjusted * (0.75 + 0.25 * capital_fit)
```

## Score Interpretation

| Score Range | Interpretation | Action |
|-------------|------------------|--------|
| 90-100 | Exceptional | Top-tier play, high priority |
| 80-89 | Excellent | Strong play, good risk/reward |
| 70-79 | Good | Solid play, consider position sizing |
| 60-69 | Fair | Marginal play, check warnings |
| 50-59 | Poor | Likely has issues, review carefully |
| < 50 | Avoid | Multiple red flags, skip this play |

**Note:** Score is just one factor. Always review warnings, IV rank, and earnings dates before executing.

## Implementation Details

### Source Code Location

Main scoring logic is in `api/services/options_service.py`:
- `_build_candidate()` — Main scoring method
- `_get_screening_profile()` — Profile parameters
- `_score_proximity()` — Target/tolerance scoring
- `_score_positive_metric()` — Linear ratio scoring

### IV/Earnings Integration

Phase 2 features implemented in `api/services/iv_earnings_service.py`:
- `get_iv_environment_score()` — IV rank and adjustment
- `get_earnings_score_impact()` — Earnings penalties
- `record_iv_data()` — IV history tracking
- `update_earnings_data()` — Earnings fetching

### Database Schema

**iv_history table:**
```sql
CREATE TABLE iv_history (
    id INTEGER PRIMARY KEY,
    ticker TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    implied_volatility REAL,
    stock_price REAL,
    option_type TEXT,
    expiration TEXT,
    dte INTEGER
);
CREATE INDEX idx_iv_history_ticker_timestamp ON iv_history(ticker, timestamp);
```

**earnings_calendar table:**
```sql
CREATE TABLE earnings_calendar (
    id INTEGER PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    earnings_date TEXT,
    last_updated TEXT,
    fetch_status TEXT,
    error_message TEXT
);
```

## Customization

### Configurable Targets

All targets are stored in `_get_screening_profile()` and can be modified:

```python
base_profile = {
    'target_iv_adjusted': 50,
    'target_theta_delta_ratio': 0.005,
    'target_capital_efficiency': 100,
    'min_iv_percentile_for_bonus': 60,
    'max_iv_percentile_for_penalty': 30,
    'earnings_warning_days': 7,
}
```

**Warning:** Changing weights requires modifying the code directly (not configurable at runtime).

### Manual Profile Override

You can force a specific profile by passing `profile_type`:

```
GET /api/options/otm?tickers=AAPL&profile_type=weekly
```

This bypasses auto-detection and uses weekly parameters regardless of DTE.

## Validation & Testing

### Score Verification

The algorithm includes detailed `score_details` in API responses:

```json
{
  "score": 85.3,
  "score_details": {
    "annualized": 92.5,
    "upside": 88.0,
    "liquidity": 95.0,
    "delta_fit": 78.5,
    "otm_fit": 82.0,
    "cost_basis_fit": 100.0,
    "iv_adjusted": 90.0,
    "theta_delta": 85.5,
    "expected_value": 72.0,
    "iv_environment": 75.0
  }
}
```

Each component (0-100) shows how it contributed to the final score.

### Rationale Messages

Hover over the ticker in the UI to see the rationale:
```
14.2% ann. yield (IV-adj: 45.2, rank: 72%)
Theta/Delta: 0.0062 | EV: $42 | Profile: monthly
12% OTM, 0.22δ | 450 OI / 120 vol
```

This transparency helps users understand why a play ranked where it did.

## Future Enhancements

Potential scoring improvements:
1. **Historical Win Rate** — Track actual outcomes to validate PoP assumptions
2. **Sector-Specific Adjustments** — Different IV norms by sector
3. **VIX Correlation** — Factor in overall market volatility
4. **Assignment History** — Penalize tickers with recent bad assignments
5. **Correlation Scoring** — Prefer uncorrelated underlyings for diversification

## References

- Research paper: "Beyond Anecdote: Quantifying Profitability in Covered Call and Cash-Secured Put Strategies"
- CBOE BuyWrite Index (BXM) — Benchmark for systematic covered calls
- CBOE PutWrite Index (PUT) — Benchmark for systematic CSPs

---

**Last Updated:** 2026-03-29
**Version:** Phase 1 & 2 Implementation
