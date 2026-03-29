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
- [Error Handling](#error-handling)
- [Data Models](#data-models)

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

Get expected income from short options expiring this week.

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
  "this_friday": "2026-04-04"
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

- **Current** — Phase 1 & 2: Risk-adjusted scoring, IV environment, earnings integration
- **1.0.0** — Initial release with basic portfolio and order management

---

**Last Updated:** 2026-03-29
**API Version:** 2.0
