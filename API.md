# API Documentation

Complete reference for all API endpoints in AllYouNeedIsWheel.

**Base URL:** `http://localhost:8000`

**Content-Type:** All requests should use `application/json` except where noted.

---

## Table of Contents

- [System](#system)
- [Portfolio](#portfolio)
- [Options Analysis](#options-analysis)
- [Orders](#orders)
- [Earnings & IV Tracking](#earnings--iv-tracking)
- [VIX Regime](#vix-regime)
- [Macro Regime Detection](#macro-regime-detection)
- [Analytics](#analytics)
- [System Tasks](#system-tasks)
- [LLM](#llm)
- [Error Handling](#error-handling)
- [Data Models](#data-models)
- [Technical Regime](#technical-regime)
- [Risk Sizing](#risk-sizing)
- [Probability of Profit](#probability-of-profit)
- [Earnings Lock](#earnings-lock)

---

## System

### Health Check

Check if the application is running.

**Endpoint:** `GET /health`

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2026-03-29T10:30:00Z"
}
```

### OpenD Status

Check the current OpenD connection and login state.

**Endpoint:** `GET /api/system/opend-status`

**Response:**
```json
{
  "status": "connected",
  "logged_in": true,
  "host": "127.0.0.1",
  "port": 11111,
  "message": "OpenD is connected and logged in"
}
```

**Status Values:**
- `connected` — OpenD running and connected
- `login_required` — OpenD connected but needs login
- `disconnected` — Cannot reach OpenD
- `error` — Connection error

### Connection Status (Debug)

Get detailed connection status for debugging connection cycling issues.

**Endpoint:** `GET /api/options/connection-status`

**Response:**
```json
{
  "success": true,
  "connection_pool": {...},
  "service_connection": {...},
  "service_initialized": true
}
```

---

## Portfolio

### Get Portfolio Summary

Retrieve account summary including cash balance, positions value, and margin metrics.

**Endpoint:** `GET /api/portfolio/`

**Response:**
```json
{
  "account_id": "12345678",
  "trading_env": "SIMULATE",
  "currency": "USD",
  "cash_balance": 25000.00,
  "account_value": 75000.00,
  "excess_liquidity": 22000.00,
  "initial_margin": 3000.00,
  "leverage_percentage": 0.0,
  "is_frozen": false
}
```

### Get Positions

Retrieve current positions (stocks and/or options).

**Endpoint:** `GET /api/portfolio/positions`

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `type` | string | No | Filter by type: `STK` (stocks) or `OPT` (options) |

**Response:**
```json
{
  "positions": [
    {
      "symbol": "AAPL",
      "position": 100,
      "market_price": 175.50,
      "market_value": 17550.00,
      "avg_cost": 170.00,
      "unrealized_pnl": 550.00,
      "security_type": "STK"
    },
    {
      "symbol": "TSLA",
      "position": -1,
      "market_price": 3.50,
      "market_value": -350.00,
      "avg_cost": 4.20,
      "unrealized_pnl": 70.00,
      "security_type": "OPT",
      "expiration": "20260417",
      "strike": 180.00,
      "option_type": "PUT"
    }
  ]
}
```

### Get Weekly Option Income

Get expected income from short options expiring this week, plus the total premium from all open short option positions.

**Endpoint:** `GET /api/portfolio/weekly-income`

**Response:**
```json
{
  "positions": [
    {
      "symbol": "AAPL",
      "option_type": "CALL",
      "strike": 180.00,
      "expiration": "20260404",
      "position": -2,
      "income": 350.00
    }
  ],
  "total_income": 350.00,
  "positions_count": 1,
  "open_short_positions_count": 3,
  "open_short_contracts_count": 13,
  "open_short_total_income": 2450.00,
  "this_friday": "2026-04-04"
}
```

---

### Get Roll Pressure

Analyze which open positions are approaching strike price and may need to be rolled.

**Endpoint:** `GET /api/portfolio/roll-pressure`

**Response:**
```json
{
  "positions": [
    {
      "ticker": "AAPL",
      "option_type": "CALL",
      "strike": 195.00,
      "expiration": "20260417",
      "dte": 5,
      "stock_price": 192.50,
      "roll_pressure": 72.3,
      "extrinsic_remaining": 0.15,
      "profit_target_progress": 68.0
    }
  ]
}
```

### Get Alerts

Retrieve portfolio alerts for positions needing attention.

**Endpoint:** `GET /api/portfolio/alerts`

**Response:**
```json
{
  "alerts": [
    {
      "ticker": "AAPL",
      "type": "roll",
      "severity": "warning",
      "message": "AAPL 195C approaching strike - 5 DTE remaining"
    }
  ]
}
```

---

## Options Analysis

### Get OTM Options

Analyze and rank out-of-the-money options for the Wheel Strategy.

**Endpoint:** `GET /api/options/otm`

**Query Parameters:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `tickers` | string | Yes | - | Comma-separated ticker symbols (e.g., `AAPL,MSFT,TSLA`) |
| `otm` | number | No | 10 | Desired OTM percentage (e.g., 10 for 10% OTM) |
| `option_type` | string | No | - | Filter by `CALL` or `PUT` (if not specified, returns both) |
| `expiration` | string | No | - | Specific expiration date (YYYYMMDD format) |
| `profile_type` | string | No | auto | Force profile: `weekly`, `monthly`, `quarterly` |

**Response:**
```json
{
  "data": {
    "AAPL": {
      "symbol": "AAPL",
      "stock_price": 175.50,
      "otm_percentage": 10,
      "position": 100,
      "avg_cost": 170.00,
      "calls": [
        {
          "symbol": "AAPL20260417C195",
          "strike": 195.00,
          "expiration": "20260417",
          "option_type": "CALL",
          "bid": 1.45,
          "ask": 1.55,
          "last": 1.50,
          "mid_price": 1.50,
          "delta": 0.22,
          "gamma": 0.03,
          "theta": -0.08,
          "vega": 0.15,
          "implied_volatility": 0.28,
          "dte": 19,
          "premium_per_contract": 150.00,
          "spread_pct": 6.67,
          "otm_pct": 11.1,
          "annualized_return": 16.4,
          "iv_adjusted_return": 58.6,
          "if_called_return": 11.7,
          "earnings_max_contracts": 1,
          "earnings_premium_per_contract": 150.00,
          "earnings_total_premium": 150.00,
          "score": 87.3,
          "iv_rank": 72.0,
          "iv_status": "high",
          "iv_env_adjustment": 10,
          "profile_type": "monthly",
          "earnings_date": null,
          "days_to_earnings": null,
          "earnings_adjustment": 0,
          "score_details": {
            "annualized": 68.3,
            "upside": 97.5,
            "liquidity": 95.0,
            "delta_fit": 87.8,
            "otm_fit": 85.2,
            "cost_basis_fit": 100.0,
            "iv_adjusted": 100.0,
            "theta_delta": 90.9,
            "expected_value": 78.5,
            "iv_environment": 75.0
          },
          "rationale": [
            "16.4% ann. yield (IV-adj: 58.6, rank: 72%)",
            "Theta/Delta: 0.0051 | EV: $28.50 | Profile: monthly",
            "11.1% OTM, 0.22δ | 450 OI / 120 vol"
          ],
          "warnings": []
        }
      ],
      "puts": [...]
    }
  }
}
```

**Response Fields:**

**Candidate Fields:**
- `symbol` — Option contract symbol
- `strike`, `expiration`, `option_type` — Contract specs
- `bid`, `ask`, `last`, `mid_price` — Pricing
- `delta`, `gamma`, `theta`, `vega`, `implied_volatility` — Greeks
- `dte` — Days to expiration
- `premium_per_contract` — Mid price × 100
- `spread_pct` — Bid-ask spread percentage
- `otm_pct` — Actual OTM percentage
- `annualized_return` — Annualized yield percentage
- `iv_adjusted_return` — Return normalized by IV
- `score` — Composite score (0-100)
- `iv_rank` — IV percentile over 30 days (0-100%)
- `iv_status` — IV environment status
- `iv_env_adjustment` — Score adjustment from IV (-20 to +20)
- `profile_type` — Detected expiration profile
- `earnings_date` — Next earnings date (if known)
- `days_to_earnings` — Days until earnings
- `earnings_adjustment` — Score penalty from earnings
- `score_details` — Breakdown of scoring components (0-100 each)
- `rationale` — Human-readable explanation
- `warnings` — Warning messages

**Warning Types:**
- `Wide bid/ask spread` — Spread > ideal threshold
- `Below ideal open interest` — OI < 500
- `Strike below stock cost basis` — CALL only
- `Cash required exceeds current cash balance` — PUT only
- `IV extremely low (X%) - poor risk/reward` — IV rank < 20%
- `IV extremely high (X%) - excellent premium` — IV rank > 80%
- `🚨 EARNINGS TODAY - extreme risk` — Earnings today
- `⚠️ Earnings in Xd - high assignment risk` — Earnings within 3 days

### Get Stock Prices

Get current stock prices for multiple tickers.

**Endpoint:** `GET /api/options/stock-price`

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tickers` | string | Yes | Comma-separated ticker symbols |

**Response:**
```json
{
  "AAPL": 175.50,
  "MSFT": 420.25,
  "TSLA": 180.00
}
```

### Get Option Expirations

Get available expiration dates for a ticker.

**Endpoint:** `GET /api/options/expirations`

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `ticker` | string | Yes | Ticker symbol |
| `option_type` | string | No | `CALL` or `PUT` (affects DTE range returned) |

**Response:**
```json
{
  "ticker": "AAPL",
  "expirations": [
    {"value": "20260404", "label": "2026-04-04", "dte": 6},
    {"value": "20260411", "label": "2026-04-11", "dte": 13},
    {"value": "20260417", "label": "2026-04-17", "dte": 19},
    {"value": "20260425", "label": "2026-04-25", "dte": 27}
  ]
}
```

**DTE Ranges by Option Type:**
- `CALL`: 5-35 days
- `PUT`: 7-45 days
- `null`: All future expirations

---

### Get Top Recommendations

Retrieve the top-ranked option plays across all watchlist tickers. Uses multi-threaded scoring with caching.

**Endpoint:** `GET /api/options/top-recommendations`

**Query Parameters:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `limit` | integer | No | 10 | Max recommendations to return |

**Response:**
```json
{
  "success": true,
  "recommendations": [
    {
      "ticker": "AAPL",
      "option_type": "PUT",
      "strike": 170.00,
      "expiration": "20260515",
      "score": 88.5,
      "annualized_return": 22.3,
      "iv_rank": 72.0,
      "rationale": "..."
    }
  ],
  "cache_status": "fresh",
  "cache_age": 45
}
```

### Get Cash Status

Check available cash for trading after margin requirements.

**Endpoint:** `GET /api/options/cash-status`

**Response:**
```json
{
  "success": true,
  "cash_balance": 25000.00,
  "buying_power": 22000.00,
  "cash_available_for_csp": 20000.00,
  "max_put_contracts_at_avg_strike": 5
}
```

---

## Orders

### Get Pending Orders

Retrieve pending and processing orders.

**Endpoint:** `GET /api/options/pending-orders`

**Query Parameters:**
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `executed` | boolean | No | false | Include executed orders if true |
| `isRollover` | boolean | No | - | Filter by rollover status |

**Response:**
```json
{
  "orders": [
    {
      "id": 1,
      "timestamp": "2026-03-29 10:30:00",
      "ticker": "AAPL",
      "option_type": "CALL",
      "action": "SELL",
      "strike": 195.00,
      "expiration": "20260417",
      "premium": 150.00,
      "quantity": 1,
      "status": "pending",
      "executed": false,
      "bid": 1.45,
      "ask": 1.55,
      "last": 1.50,
      "delta": 0.22,
      "gamma": 0.03,
      "theta": -0.08,
      "vega": 0.15,
      "implied_volatility": 0.28,
      "open_interest": 450,
      "volume": 120,
      "earnings_max_contracts": 1,
      "earnings_premium_per_contract": 150.00,
      "earnings_total_premium": 150.00,
      "earnings_return_on_capital": 16.4,
      "isRollover": false
    }
  ]
}
```

### Create Order

Create a new option order (saved to database, not yet executed).

**Endpoint:** `POST /api/options/order`

**Request Body:**
```json
{
  "ticker": "AAPL",
  "option_type": "CALL",
  "action": "SELL",
  "strike": 195.00,
  "expiration": "20260417",
  "premium": 150.00,
  "quantity": 1,
  "bid": 1.45,
  "ask": 1.55,
  "last": 1.50,
  "delta": 0.22,
  "gamma": 0.03,
  "theta": -0.08,
  "vega": 0.15,
  "implied_volatility": 0.28,
  "open_interest": 450,
  "volume": 120,
  "isRollover": false
}
```

**Response:**
```json
{
  "success": true,
  "order_id": 1,
  "message": "Order created successfully"
}
```

### Delete Order

Delete a pending order.

**Endpoint:** `DELETE /api/options/order/<id>`

**Response:**
```json
{
  "success": true,
  "message": "Order 1 deleted"
}
```

### Update Order Quantity

Update the quantity of a pending order.

**Endpoint:** `PUT /api/options/order/<id>/quantity`

**Request Body:**
```json
{
  "quantity": 2
}
```

**Response:**
```json
{
  "success": true,
  "order_id": 1,
  "quantity": 2,
  "message": "Quantity updated to 2"
}
```

### Execute Order

Send an order to Moomoo for execution.

**Endpoint:** `POST /api/options/execute/<id>`

**Response:**
```json
{
  "success": true,
  "order_id": 1,
  "moomoo_order_id": "123456789",
  "status": "processing",
  "message": "Order sent to moomoo",
  "execution_details": {
    "moomoo_order_id": "123456789",
    "moomoo_status": "Submitted",
    "filled": 0,
    "remaining": 1,
    "avg_fill_price": 0,
    "limit_price": 1.50
  }
}
```

**Note:** Only `pending` orders can be executed. Check OpenD status first.

### Cancel Order

Cancel a processing order in Moomoo.

**Endpoint:** `POST /api/options/cancel/<id>`

**Response:**
```json
{
  "success": true,
  "order_id": 1,
  "message": "Order canceled"
}
```

**Note:** Only `processing` orders (submitted to Moomoo) can be canceled.

### Check Orders Status

Sync order statuses with Moomoo and update database.

**Endpoint:** `POST /api/options/check-orders`

**Response:**
```json
{
  "success": true,
  "updated_orders": [
    {
      "id": 1,
      "status": "executed",
      "moomoo_status": "Filled",
      "filled": 1,
      "remaining": 0,
      "avg_fill_price": 1.48
    }
  ]
}
```

**Recommended:** Call this endpoint periodically or after executing orders.

### Create Rollover Orders

Create buy-to-close and sell-to-open orders for rolling a position.

**Endpoint:** `POST /api/options/rollover`

**Request Body:**
```json
{
  "original_order_id": 1,
  "close_premium": 0.75,
  "open_strike": 200.00,
  "open_expiration": "20260515",
  "open_premium": 2.50,
  "quantity": 1
}
```

**Response:**
```json
{
  "success": true,
  "close_order_id": 2,
  "open_order_id": 3,
  "message": "Rollover orders created",
  "net_credit": 175.00
}
```

---

## Earnings & IV Tracking

### Get Earnings Status

Check the background earnings updater status and cache statistics.

**Endpoint:** `GET /api/earnings/status`

**Response:**
```json
{
  "status": "running",
  "cache_stats": {
    "iv_cache_entries": 15,
    "earnings_cache_entries": 12,
    "iv_cache_valid": 12,
    "earnings_cache_valid": 12
  }
}
```

**Status Values:**
- `running` — Background thread active
- `stopped` — Background thread not running

### Refresh All Earnings

Trigger a global update for all active symbols (positions and pending orders) in the database.

**Endpoint:** `POST /api/earnings/refresh`

**Response:**
```json
{
  "success": true,
  "updated_count": 5,
  "failed_count": 1,
  "total_attempted": 6
}
```

### Update Single Earnings

Manually fetch and update earnings data for a specific ticker.

**Endpoint:** `GET /api/earnings/update/<ticker>`

**Example:** `GET /api/earnings/update/AAPL`

**Response:**
```json
{
  "success": true,
  "ticker": "AAPL",
  "earnings_info": {
    "earnings_date": "2026-04-28",
    "days_to_earnings": 30,
    "warning_level": "none",
    "fetch_status": "success",
    "error_message": null
  }
}
```

**Response (No Earnings Data):**
```json
{
  "success": true,
  "ticker": "AAPL",
  "earnings_info": {
    "earnings_date": null,
    "days_to_earnings": null,
    "warning_level": "none",
    "fetch_status": "success",
    "error_message": "No earnings data available"
  }
}
```

**Response (Fetch Failed):**
```json
{
  "success": false,
  "ticker": "INVALID",
  "earnings_info": {
    "earnings_date": null,
    "days_to_earnings": null,
    "warning_level": "error",
    "fetch_status": "error",
    "error_message": "Failed to fetch earnings from yfinance"
  }
}
```

### Get Pending Earnings

Get all tickers with earnings scheduled in the next 7 days.

**Endpoint:** `GET /api/earnings/pending`

**Response:**
```json
{
  "count": 3,
  "tickers": [
    {"ticker": "AAPL", "earnings_date": "2026-04-04"},
    {"ticker": "MSFT", "earnings_date": "2026-04-05"},
    {"ticker": "TSLA", "earnings_date": "2026-04-06"}
  ]
}
```

---

## VIX Regime

### Get VIX Regime

Returns the current VIX level, market regime classification, and delta/exposure adjustments applied to option screening.

**Endpoint:** `GET /api/options/vix-regime`

**Response:**
```json
{
  "success": true,
  "vix_regime": {
    "vix": 18.5,
    "regime": "normal",
    "delta_adjustment": 0.0,
    "exposure_multiplier": 1.0,
    "description": "Normal volatility (VIX 15-30) - standard delta targets"
  }
}
```

**Regime Classifications:**
| VIX Level | Regime | Delta Adjustment | Exposure Limit |
|-----------|--------|-----------------|----------------|
| < 15 | Complacency | +0.10 | 70% |
| 15-30 | Normal | 0.00 | 100% |
| > 30 | Fear | -0.05 | 50% |

---

## Macro Regime Detection

### Get Macro Regime

Returns the current macroeconomic regime based on FRED data (rates, credit stress, growth, inflation).

**Endpoint:** `GET /api/macro/regime`

**Response:**
```json
{
  "success": true,
  "regime": {
    "interest_rate_regime": "rising",
    "credit_stress": "low",
    "growth_regime": "expansion",
    "inflation_trend": "stable",
    "macro_multiplier": 1.05,
    "summary": "Favorable macro environment - consider slightly more aggressive plays"
  }
}
```

### Get Macro Cache Status

Check the age and validity of cached FRED data.

**Endpoint:** `GET /api/macro/cache/status`

**Response:**
```json
{
  "success": true,
  "cached": true,
  "age_seconds": 1200,
  "max_age_seconds": 3600
}
```

### Clear Macro Cache

Force a fresh fetch from FRED on the next request.

**Endpoint:** `POST /api/macro/cache/clear`

**Response:**
```json
{
  "success": true,
  "message": "Macro cache cleared"
}
```

---

## Analytics

### Get Option Lifecycle Analytics

Get lifecycle metrics for option positions (age, time decay, history).

**Endpoint:** `GET /api/options/analytics/lifecycle`

**Response:**
```json
{
  "success": true,
  "analytics": [
    {
      "ticker": "AAPL",
      "option_type": "CALL",
      "strike": 195.00,
      "expiration": "20260417",
      "days_open": 14,
      "decay_rate": 0.08,
      "theta_decayed": 60.5
    }
  ]
}
```

### Get Option Leakage Analytics

Analyze option spread leakage (slippage cost between bid/ask).

**Endpoint:** `GET /api/options/analytics/leakage`

**Response:**
```json
{
  "success": true,
  "leakage": [
    {
      "ticker": "AAPL",
      "option_type": "CALL",
      "strike": 195.00,
      "spread_cost": 10.50,
      "leakage_pct": 6.67
    }
  ]
}
```

### Prefill Close Order Data

Get prefilled data for closing a position, used for rollover workflows.

**Endpoint:** `POST /api/options/prefilled-close`

**Request Body:**
```json
{
  "ticker": "AAPL",
  "option_type": "CALL",
  "strike": 195.00,
  "expiration": "20260417"
}
```

**Response:**
```json
{
  "success": true,
  "prefilled": {
    "bid": 0.75,
    "ask": 0.85,
    "last": 0.80,
    "mid_price": 0.80,
    "delta": -0.18,
    "premium_per_contract": 80.00
  }
}
```

---

## System Tasks

### List Background Tasks

Get the status of all registered background tasks.

**Endpoint:** `GET /api/system/tasks`

**Response:**
```json
{
  "tasks": [
    {
      "name": "earnings_updater",
      "running": true,
      "uptime_seconds": 3600,
      "restart_count": 0
    }
  ]
}
```

### Restart Background Task

Restart a specific background task by name.

**Endpoint:** `POST /api/system/tasks/<name>/restart`

**Response:**
```json
{
  "success": true,
  "task": "earnings_updater",
  "message": "Task restarted"
}
```

### Get Task Status

Get detailed status of a specific background task.

**Endpoint:** `GET /api/system/tasks/<name>/status`

**Response:**
```json
{
  "name": "earnings_updater",
  "running": true,
  "uptime_seconds": 3600,
  "last_run": "2026-04-25T10:00:00Z",
  "next_run": "2026-04-25T16:00:00Z",
  "restart_count": 0,
  "last_error": null
}
```

---

## LLM

### Get LLM Suggestions

Get AI-driven suggestions for option plays based on current market conditions.

**Endpoint:** `POST /api/llm/suggestions`

**Request Body:**
```json
{
  "tickers": ["AAPL", "MSFT", "TSLA"],
  "context": {
    "market_regime": "normal",
    "risk_tolerance": "moderate"
  }
}
```

**Response:**
```json
{
  "success": true,
  "suggestions": [
    {
      "ticker": "AAPL",
      "strategy": "cash_secured_put",
      "strike": 170.00,
      "rationale": "Strong support level with elevated IV"
    }
  ],
  "enabled": true
}
```

---

## Error Handling

### Standard Error Response

```json
{
  "success": false,
  "error": "Error description",
  "message": "Human-readable explanation"
}
```

### HTTP Status Codes

| Status | Meaning |
|--------|---------|
| 200 | Success |
| 400 | Bad Request — Invalid parameters |
| 404 | Not Found — Resource doesn't exist |
| 500 | Server Error — Unexpected error |

### Common Errors

**OpenD Not Connected:**
```json
{
  "error": "Failed to connect to moomoo OpenD",
  "message": "Ensure OpenD is running and logged in"
}
```

**Invalid Parameters:**
```json
{
  "error": "Invalid option_type: INVALID. Must be 'CALL' or 'PUT'",
  "message": "Check your request parameters"
}
```

**Order Not Found:**
```json
{
  "error": "Order with ID 999 not found",
  "message": "Verify the order ID exists"
}
```

---

## Data Models

### OptionCandidate

Represents a single option play recommendation.

```typescript
interface OptionCandidate {
  symbol: string;              // Full option symbol (e.g., AAPL20260417C195)
  strike: number;              // Strike price
  expiration: string;          // YYYYMMDD format
  option_type: "CALL" | "PUT";
  
  // Pricing
  bid: number;
  ask: number;
  last: number;
  mid_price: number;
  
  // Greeks
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  implied_volatility: number;
  
  // Analysis
  dte: number;                 // Days to expiration
  premium_per_contract: number;
  spread_pct: number;
  otm_pct: number;
  
  // Returns
  annualized_return: number;   // Percentage
  iv_adjusted_return: number; // IV-normalized
  if_called_return?: number;   // CALL only
  breakeven?: number;          // PUT only
  breakeven_buffer_pct?: number; // PUT only
  cash_required?: number;      // PUT only
  
  // Earnings data
  earnings_max_contracts: number;
  earnings_premium_per_contract: number;
  earnings_total_premium: number;
  earnings_return_on_capital?: number; // CALL
  earnings_return_on_cash?: number;    // PUT
  
  // Scoring
  score: number;              // 0-100
  score_details: {
    annualized: number;
    upside?: number;          // CALL
    buffer?: number;          // PUT
    liquidity: number;
    delta_fit: number;
    otm_fit: number;
    cost_basis_fit?: number;   // CALL
    capital_fit?: number;      // PUT
    iv_adjusted: number;
    theta_delta: number;
    expected_value: number;
    capital_efficiency?: number; // PUT
    iv_environment: number;
  };
  
  // Phase 2 enhancements
  iv_rank: number;            // 0-100
  iv_status: string;
  iv_env_adjustment: number; // -20 to +20
  profile_type: "weekly" | "monthly" | "quarterly";
  earnings_date: string | null; // YYYY-MM-DD or null
  days_to_earnings: number | null;
  earnings_adjustment: number; // -30 to 0
  
  // Metadata
  rationale: string[];
  warnings: string[];
}
```

### Order

Represents an order in the system.

```typescript
interface Order {
  id: number;
  timestamp: string;          // ISO 8601 format
  ticker: string;
  option_type: "CALL" | "PUT";
  action: "BUY" | "SELL";
  strike: number;
  expiration: string;           // YYYYMMDD
  premium: number;
  quantity: number;
  status: "pending" | "processing" | "executed" | "canceled" | "error";
  executed: boolean;
  
  // Pricing at creation
  bid: number;
  ask: number;
  last: number;
  
  // Greeks at creation
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  implied_volatility: number;
  
  // Market data
  open_interest: number;
  volume: number;
  is_mock: boolean;
  
  // Analysis
  earnings_max_contracts: number;
  earnings_premium_per_contract: number;
  earnings_total_premium: number;
  earnings_return_on_cash?: number;
  earnings_return_on_capital?: number;
  
  // Execution data (populated after execution)
  moomoo_order_id?: string;
  moomoo_status?: string;
  filled?: number;
  remaining?: number;
  avg_fill_price?: number;
  
  // Rollover flag
  isRollover: boolean;
}
```

### PortfolioSummary

Account and portfolio overview.

```typescript
interface PortfolioSummary {
  account_id: string;
  trading_env: "SIMULATE" | "REAL";
  currency: string;
  cash_balance: number;
  account_value: number;
  excess_liquidity: number;
  initial_margin: number;
  leverage_percentage: number;
  is_frozen: boolean;
}
```

### Position

Individual position data.

```typescript
interface Position {
  symbol: string;
  position: number;           // Shares or contracts (negative = short)
  market_price: number;
  market_value: number;
  avg_cost: number;
  unrealized_pnl: number;
  security_type: "STK" | "OPT";
  
  // Option-specific
  expiration?: string;
  strike?: number;
  option_type?: "CALL" | "PUT";
}
```

---

## Rate Limits

The application has no explicit rate limiting, but:

1. **Moomoo API** — Subject to Moomoo's rate limits (not documented publicly)
2. **Yahoo Finance** — Be polite; background thread adds 1-second delays between requests
3. **Local SQLite** — Can handle hundreds of requests per second

**Best Practices:**
- Cache results client-side when possible
- Don't poll `/api/options/otm` more than once per minute
- Use `/api/options/check-orders` after order execution, not continuously

---

## WebSocket Support

Currently not implemented. All endpoints are REST-based.

For real-time updates, poll these endpoints:
- `/api/system/opend-status` — Every 5-10 seconds
- `/api/portfolio/` — Every 30-60 seconds  
- `/api/options/check-orders` — After executing orders

---

## Authentication

The API does not use API keys or tokens. Access control is through:

1. **Network Binding** — App binds to `127.0.0.1:8000` by default (localhost only)
2. **Moomoo Login** — Trading requires OpenD login credentials
3. **Environment Variables** — Credentials in `.env` file (not exposed via API)

**Security Note:** The API is intended for local use only. Do not expose port 8000 to the public internet.

---

## Version History

- **2.1** — Analytics, System Tasks, LLM endpoints; expanded Portfolio and Options endpoints
- **2.0** — Phase 1-4: Risk-adjusted scoring, IV environment, earnings integration, macro regime
- **1.0** — Initial release with basic portfolio and order management

---

**Last Updated:** 2026-04-25
**API Version:** 2.1


## Technical Regime

### Get Technical Regime
`GET /api/technical/regime?tickers=AAPL,MSFT`

Returns 200-day EMA regime and ADX trend strength for tickers.

**Response:**
```json
{
  "success": true,
  "data": {
    "AAPL": {
      "regime": "bullish",
      "ema200": 175.25,
      "price": 180.50,
      "distance_pct": 2.99,
      "adx": 28.5,
      "trend_strength": "trending",
      "summary": "🟢 Bullish | Price: $180.50 | EMA200: $175.25 (+2.99%)"
    }
  }
}
```

### Get Regime Summary
`GET /api/technical/regime/summary?tickers=AAPL,MSFT,TSLA`

Returns aggregated regime summary for watchlist.

---

## Risk Sizing

### Get Position Size
`GET /api/risk/sizing?ticker=AAPL&account_value=45000&risk_pct=0.01`

Returns ATR-based position size with 1% risk rule.

**Response:**
```json
{
  "success": true,
  "data": {
    "ticker": "AAPL",
    "atr": 4.20,
    "account_value": 45000,
    "risk_pct": 0.01,
    "risk_amount": 450,
    "risk_per_contract": 420,
    "max_contracts": 1
  }
}
```

---

## Probability of Profit

### Get PoP Estimate
`GET /api/pop/estimate?ticker=AAPL&strike=170&expiration=20260530&type=PUT&method=delta`

Returns PoP percentage using delta or Monte Carlo.

---

## Earnings Lock

### Get Locked Tickers
`GET /api/earnings/locked-tickers?lock_days=5`

Returns tickers with earnings within lock_days.

**Response:**
```json
{
  "success": true,
  "locked": [
    {"ticker": "AAPL", "earnings_date": "2026-05-03", "days_to_earnings": 3}
  ],
  "count": 1,
  "lock_days": 5
}
```


## Sizing Mode

The system supports Conservative (ATR-safe) and Aggressive (cash-max) sizing modes.
The default is Conservative. Aggressive mode uses all available cash or shares.
Set via card-level toggle or global Context Filter selector.

### Fields on Recommendation Objects
- : Conservative max (ATR-safe, default)
- : Maximum contracts allowed by available cash/shares
- : atr_max / cash_max (0.0-1.0 utilization)
- : "conservative" or "aggressive"

---

## Earnings Lock (Additional Endpoints)

### Get Lock Status
`GET /api/earnings/lock-status`

Returns the current earnings lock configuration.

**Response:**
```json
{
  "success": true,
  "lock_days": 5,
  "enabled": true
}
```

---

## Risk Sizing (Additional Endpoints)

### Batch Position Sizing
`POST /api/risk/sizing/batch`

Get ATR-based position sizing for multiple tickers.

**Request Body:**
```json
{
  "tickers": ["AAPL", "MSFT"],
  "account_value": 45000,
  "risk_pct": 0.01
}
```

### Clear Sizing Cache
`POST /api/risk/sizing/cache/clear`

Clears the risk sizing cache.

---

## Technical Regime (Additional Endpoints)

### Get Regime for Single Ticker
`GET /api/technical/regime/<ticker>`

Returns technical regime for a single ticker.

### Get Regime Summary
`GET /api/technical/regime/summary?tickers=AAPL,MSFT,TSLA`

Returns aggregated regime summary for watchlist.

### Clear Regime Cache
`POST /api/technical/regime/cache/clear`

Clears the technical regime cache.

---

**Last Updated:** 2026-04-26
**API Version:** 2.2 (Added earnings, risk, technical, pop endpoints)
